"""Worker de rétention : purge planifiée selon la politique de conservation.

Politique : paramètre global `retention_days` (défaut 365 = 1 an), modifiable
par l'admin. Un message archivé depuis plus de `retention_days` jours et sans
`legal_hold` est purgé. `retention_days = 0` désactive la purge (illimité).

À la purge : décrément des `ref_count` des PJ (GC des blobs orphelins),
suppression du corps, suppression du document d'index OpenSearch, et
journalisation (audit) — exigence réglementaire.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from opensearchpy import AsyncOpenSearch
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

from mailarchiver_common.config import get_settings
from mailarchiver_common.logging import configure_logging
from mailarchiver_common.models import (
    AppSetting,
    Attachment,
    AuditEvent,
    Message,
    MessageDedup,
    get_sessionmaker,
)
from mailarchiver_common.storage import BlobStore

log = structlog.get_logger()

_DEFAULT_RETENTION_DAYS = 365


async def _retention_days(session) -> int:
    row = await session.get(AppSetting, "retention_days")
    try:
        return int(row.value) if row else _DEFAULT_RETENTION_DAYS
    except (TypeError, ValueError):
        return _DEFAULT_RETENTION_DAYS


async def _drop_old_indices(os_client, base: str, cutoff: dt.datetime) -> None:
    """Supprime les index journaliers messages-AAAA.MM.JJ dont le jour est
    antérieur au seuil de conservation (suppression d'index = instantanée)."""
    try:
        indices = await os_client.indices.get(index=f"{base}-*", ignore=[404])
    except Exception as exc:  # noqa: BLE001
        log.warning("retention_index_list_failed", error=str(exc))
        return
    cutoff_day = cutoff.date()
    for name in list(indices or {}):
        datestr = name[len(base) + 1:]
        try:
            idx_day = dt.datetime.strptime(datestr, "%Y.%m.%d").date()
        except ValueError:
            continue
        if idx_day < cutoff_day:
            try:
                await os_client.indices.delete(index=name)
                log.info("retention_index_dropped", index=name)
            except Exception as exc:  # noqa: BLE001
                log.warning("retention_index_drop_failed", index=name, error=str(exc))


async def run_retention() -> None:
    sm = get_sessionmaker()
    blobs = BlobStore()
    settings = get_settings()
    os_client = AsyncOpenSearch(settings.opensearch_url)
    now = dt.datetime.now(dt.timezone.utc)
    purged = 0

    try:
        async with sm() as session:
            days = await _retention_days(session)
        if days <= 0:
            log.info("retention_disabled", retention_days=days)
            return
        cutoff = now - dt.timedelta(days=days)

        # 1. Index journaliers : suppression d'index entiers (instantané) pour
        #    les jours antérieurs au seuil de conservation.
        await _drop_old_indices(os_client, settings.opensearch_index, cutoff)

        # 2. Purge des métadonnées + blobs (fondée sur signed_at).
        async with sm() as session:
            expired = (
                await session.scalars(
                    select(Message)
                    .options(selectinload(Message.attachments))  # eager : pas d'IO lazy en async
                    .where(
                        Message.signed_at < cutoff,
                        Message.legal_hold.is_(False),
                    )
                    .limit(5000)  # purge par lots pour borner la mémoire
                )
            ).all()

            for msg in expired:
                for att in msg.attachments:
                    await session.execute(
                        update(Attachment)
                        .where(Attachment.id == att.id)
                        .values(ref_count=Attachment.ref_count - 1)
                    )
                    refreshed = await session.get(Attachment, att.id)
                    if refreshed and refreshed.ref_count <= 0:
                        await blobs.delete(refreshed.sha256)
                        await session.delete(refreshed)

                await blobs.delete(msg.body_sha256)
                # Nettoyer l'index d'idempotence (sinon fuite + mail non
                # ré-archivable car traité comme doublon vers un message disparu).
                await session.execute(
                    delete(MessageDedup).where(MessageDedup.archive_hash == msg.archive_hash)
                )
                # (L'index de recherche a déjà été purgé par drop d'index, §1.)
                session.add(
                    AuditEvent(ts=now, actor="system", action="purge", target=str(msg.id),
                               detail={"archive_hash": msg.archive_hash, "retention_days": days})
                )
                await session.delete(msg)
                purged += 1

            await session.commit()

        log.info("retention_run_complete", purged=purged, retention_days=days)
    finally:
        await os_client.close()


async def main() -> None:
    configure_logging()
    cron = os.environ.get("RETENTION_SCAN_CRON", "0 2 * * *")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_retention, CronTrigger.from_crontab(cron))
    scheduler.start()
    log.info("retention_worker_started", cron=cron)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
