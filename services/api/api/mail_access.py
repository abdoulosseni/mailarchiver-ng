"""Récupération du mail d'origine (export EML) et transfert via SMTP.

L'export reconstruit le `.eml` exact : on lit le blob scellé (clé = archive_hash),
on le déchiffre/décompresse et on vérifie la signature pour garantir l'intégrité.
"""

from __future__ import annotations

from email import message_from_bytes, policy
from email.utils import getaddresses

from sqlalchemy import select

from mailarchiver_common import crypto
from mailarchiver_common.models import Message, get_sessionmaker
from mailarchiver_common.storage import BlobStore


async def _get_message(session, message_db_id: int) -> Message | None:
    # `messages` a une PK composite (id, signed_at) → session.get(id) seul ne
    # convient pas ; l'id reste unique en pratique (séquence IDENTITY globale).
    return await session.scalar(select(Message).where(Message.id == message_db_id))


def _addresses(value: str | None) -> list[str]:
    if not value:
        return []
    return [addr for _, addr in getaddresses([value]) if addr]


def _parse_view(raw: bytes, integrity_ok: bool, message_db_id: int, archived_at: str | None) -> dict:
    """Extrait en-têtes, corps lisible et liste des pièces jointes d'un .eml."""
    msg = message_from_bytes(raw, policy=policy.default)

    text_body = ""
    html_body = ""
    attachments: list[dict] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        ctype = part.get_content_type()
        if disposition == "attachment" or (part.get_filename() and disposition != "inline"):
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                {
                    "filename": part.get_filename() or "sans-nom",
                    "content_type": ctype,
                    "size": len(payload),
                }
            )
        elif ctype == "text/plain" and not text_body:
            text_body = part.get_content()
        elif ctype == "text/html" and not html_body:
            html_body = part.get_content()

    # Toutes les en-têtes, dans l'ordre d'origine (doublons inclus, ex. Received).
    headers = [{"name": k, "value": str(v)} for k, v in msg.items()]

    return {
        "id": message_db_id,
        "subject": msg["Subject"] or "",
        "from": (_addresses(msg["From"]) or [""])[0],
        "to": _addresses(msg["To"]),
        "cc": _addresses(msg["Cc"]),
        "date": msg["Date"] or "",
        # Corps texte privilégié (sûr à afficher) ; on signale la présence d'un HTML.
        "body": text_body,
        "has_html": bool(html_body),
        "attachments": attachments,
        "headers": headers,
        "integrity_ok": integrity_ok,
        "archived_at": archived_at,
    }


class AccessDenied(Exception):
    """Le demandeur n'est ni admin ni partie prenante du mail."""


def _can_access(msg: Message, scope: list[str] | None) -> bool:
    """scope=None => accès total (admin). Sinon, l'une des adresses du périmètre
    doit figurer dans le mail (from/to/cc). Périmètre vide => aucun accès."""
    if scope is None:
        return True
    if not scope:
        return False
    parties = {(msg.from_addr or "").lower()}
    parties.update(a.lower() for a in (msg.to_addrs or []))
    parties.update(a.lower() for a in (msg.cc_addrs or []))
    return any(a.lower() in parties for a in scope)


class MailAccess:
    def __init__(self, blobs: BlobStore) -> None:
        self._blobs = blobs
        self._sm = get_sessionmaker()

    async def get_eml(self, message_db_id: int, *, scope: list[str] | None) -> tuple[bytes, bool]:
        """Retourne (contenu_eml, integrite_ok). Vérifie le droit d'accès."""
        async with self._sm() as session:
            msg = await _get_message(session, message_db_id)
            if msg is None:
                raise KeyError("message introuvable")
            if not _can_access(msg, scope):
                raise AccessDenied()
            sealed = crypto.SealedBlob.deserialize(await self._blobs.get(msg.body_sha256))
            raw = crypto.unseal(sealed)
            # Vérification de signature : l'archive_hash doit correspondre.
            integrity_ok = crypto.verify(bytes.fromhex(msg.archive_hash), msg.signature)
            return raw, integrity_ok

    async def get_view(self, message_db_id: int, *, scope: list[str] | None) -> dict:
        """Contenu lisible du mail (en-têtes + corps + PJ), avec contrôle d'accès."""
        raw, integrity_ok = await self.get_eml(message_db_id, scope=scope)
        async with self._sm() as session:
            msg = await _get_message(session, message_db_id)
            archived_at = msg.signed_at.isoformat() if msg and msg.signed_at else None
            legal_hold = bool(msg.legal_hold) if msg else False
        view = _parse_view(raw, integrity_ok, message_db_id, archived_at)
        view["legal_hold"] = legal_hold
        return view

