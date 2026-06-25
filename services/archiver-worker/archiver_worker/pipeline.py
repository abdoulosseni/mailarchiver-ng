"""Pipeline d'archivage d'un mail brut.

Étapes :  parse → dédup PJ → (zlib + AES-256-GCM) → stockage blobs → signature
          Ed25519 → métadonnées PostgreSQL → indexation OpenSearch.

Idempotence : la clé `archive_hash` est unique ; un mail rejoué (re-livraison
RabbitMQ) ne crée pas de doublon.
"""

from __future__ import annotations

import datetime as dt

import json

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from mailarchiver_common.queue import publish_event

from mailarchiver_common import crypto
from mailarchiver_common.models import (
    Attachment,
    Message,
    MessageDedup,
    get_sessionmaker,
    message_attachments,
)
from mailarchiver_common.storage import BlobStore

from .indexer import SearchIndexer
from .parser import ParsedMail, parse

log = structlog.get_logger()

# Limite de taille du corps INDEXÉ (la recherche plein-texte couvre ce préfixe ;
# le contenu intégral reste dans l'EML chiffré). Évite de gonfler/saturer
# l'index OpenSearch avec des corps volumineux (ex. 7 Mo).
_INDEX_BODY_MAX = 64 * 1024


class ArchivePipeline:
    def __init__(self, blobs: BlobStore, indexer: SearchIndexer, events_exchange=None) -> None:
        self._blobs = blobs
        self._indexer = indexer
        self._events = events_exchange
        self._sm = get_sessionmaker()

    async def process(self, raw: bytes) -> None:
        mail = parse(raw)

        # 1. Empreinte + signature de l'archive (inviolabilité)
        body_hash = crypto.content_hash(mail.text_body.encode())
        att_hashes = [crypto.content_hash(a.content) for a in mail.attachments]
        fingerprint = crypto.archive_fingerprint(mail.headers_canonical, body_hash, att_hashes)
        archive_hash = fingerprint.hex()
        signature = crypto.sign(fingerprint)
        signed_at = dt.datetime.now(dt.timezone.utc)

        index_doc = {
            "message_id": mail.message_id,
            "date": mail.date.isoformat(),
            "archived_at": signed_at.isoformat(),
            "from_addr": mail.from_addr,
            "to_addrs": mail.to_addrs,
            "cc_addrs": mail.cc_addrs,
            "subject": mail.subject,
            "body": mail.text_body[:_INDEX_BODY_MAX],
            "has_attachment": bool(mail.attachments),
            "attachment_names": [a.filename for a in mail.attachments],
            "size_bytes": len(mail.raw),
            "retention_class": "default",
        }

        async with self._sm() as session:
            # Idempotence — chemin rapide : déjà archivé ? (via l'index dédup)
            dup = await session.scalar(
                select(MessageDedup).where(MessageDedup.archive_hash == archive_hash)
            )
            if dup:
                # Ré-indexe (idempotent) dans l'index journalier d'origine →
                # cicatrise un index manquant (ex. OpenSearch indisponible avant).
                await self._reindex_existing(dup.message_id, dup.signed_at, index_doc)
                log.info("duplicate_skipped", archive_hash=archive_hash[:12], reason="precheck")
                return

            # 2. Stockage du corps brut (.eml) scellé — clé = hash de l'archive
            body_blob = crypto.seal(mail.raw)
            await self._blobs.put(archive_hash, body_blob.serialize())

            # 3. Pièces jointes : déduplication par sha256
            attachment_rows = await self._store_attachments(session, mail, att_hashes)

            # 4. Métadonnées (table partitionnée par signed_at)
            msg_row = Message(
                message_id=mail.message_id,
                date=mail.date,
                from_addr=mail.from_addr,
                to_addrs=mail.to_addrs,
                cc_addrs=mail.cc_addrs,
                subject=mail.subject,
                size_bytes=len(mail.raw),
                body_sha256=archive_hash,
                archive_hash=archive_hash,
                signature=signature,
                signed_at=signed_at,
            )
            # Associer les PJ AVANT le flush (objet transient → pas de lazy-load
            # async ; assigner après flush déclencherait un greenlet_spawn error).
            msg_row.attachments = attachment_rows
            session.add(msg_row)
            await session.flush()  # insère message + liens, affecte msg_row.id
            # L'unicité globale d'archive_hash est portée par message_dedup :
            # garde autoritaire contre deux workers traitant le même mail en
            # parallèle. Le perdant annule TOUTE la transaction (message inclus).
            session.add(MessageDedup(archive_hash=archive_hash, message_id=msg_row.id, signed_at=signed_at))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                dup = await session.scalar(
                    select(MessageDedup).where(MessageDedup.archive_hash == archive_hash)
                )
                if dup:
                    await self._reindex_existing(dup.message_id, dup.signed_at, index_doc)
                log.info("duplicate_skipped", archive_hash=archive_hash[:12], reason="unique_violation")
                return
            db_id = msg_row.id

        # 5. Indexation (hors transaction DB) dans l'index journalier du jour.
        index_doc["doc_id"] = db_id
        await self._indexer.index_message(db_id, index_doc)
        log.info("archived", id=db_id, archive_hash=archive_hash[:12], attachments=len(mail.attachments))

        # 6. Événement temps réel (best-effort : ne bloque pas l'archivage)
        if self._events is not None:
            event = {
                "id": db_id,
                "date": mail.date.isoformat(),
                "from_addr": mail.from_addr,
                "to_addrs": mail.to_addrs,
                "cc_addrs": mail.cc_addrs,
                "subject": mail.subject,
                "has_attachment": bool(mail.attachments),
                "attachment_names": [a.filename for a in mail.attachments],
            }
            try:
                await publish_event(self._events, json.dumps(event).encode())
            except Exception as exc:  # noqa: BLE001
                log.warning("event_publish_failed", error=str(exc))

    async def _reindex_existing(self, message_id: int, signed_at, index_doc: dict) -> None:
        """Ré-indexe un message déjà archivé dans SON index journalier d'origine
        (date d'archivage = signed_at d'origine), id = id du message."""
        doc = dict(index_doc)
        doc["doc_id"] = message_id
        if signed_at:
            doc["archived_at"] = signed_at.isoformat()
        await self._indexer.index_message(message_id, doc)

    async def _store_attachments(
        self, session, mail: ParsedMail, att_hashes: list[str]
    ) -> list[Attachment]:
        rows: list[Attachment] = []
        for att, sha in zip(mail.attachments, att_hashes):
            # Blob dédupliqué : put() est idempotent (no-op si déjà présent)
            sealed = crypto.seal(att.content)
            await self._blobs.put(sha, sealed.serialize())

            # Upsert de la ligne Attachment + incrément du compteur de références
            stmt = (
                pg_insert(Attachment)
                .values(
                    sha256=sha,
                    filename=att.filename,
                    content_type=att.content_type,
                    size_bytes=len(att.content),
                    ref_count=1,
                )
                .on_conflict_do_update(
                    index_elements=[Attachment.sha256],
                    set_={"ref_count": Attachment.ref_count + 1},
                )
                .returning(Attachment.id)
            )
            att_id = await session.scalar(stmt)
            row = await session.get(Attachment, att_id)
            rows.append(row)
        return rows
