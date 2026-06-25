"""Worker de collecte : relève périodiquement les sources IMAP/POP3 dues
et réinjecte les mails dans la file d'archivage (même pipeline que le SMTP).

Le périmètre de chaque source (intervalle) est respecté : une source n'est
relevée que si `last_run + interval` est dépassé.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from mailarchiver_common import crypto
from mailarchiver_common.fetch import run_and_publish
from mailarchiver_common.models import FetchSource, get_sessionmaker
from mailarchiver_common.logging import configure_logging
from mailarchiver_common.queue import connect

log = structlog.get_logger()
_sm = get_sessionmaker()
_channel = None


def _src_dict(s: FetchSource) -> dict:
    return {
        "protocol": s.protocol,
        "host": s.host,
        "port": s.port,
        "username": s.username,
        "use_ssl": s.use_ssl,
        "folder": s.folder,
        "delete_after": s.delete_after,
        "name": s.name,
    }


async def _scan() -> None:
    try:
        now = dt.datetime.now(dt.timezone.utc)
        due: list[tuple[int, dict, str]] = []
        async with _sm() as session:
            sources = (await session.scalars(select(FetchSource).where(FetchSource.active.is_(True)))).all()
            for s in sources:
                if s.last_run is None or (now - s.last_run).total_seconds() >= s.interval_minutes * 60:
                    due.append((s.id, _src_dict(s), crypto.decrypt_secret(s.password_enc)))

        for sid, src, password in due:
            try:
                count = await run_and_publish(_channel, src, password)
                status = f"ok: {count} mail(s) relevé(s)"
            except Exception as exc:  # noqa: BLE001
                count, status = 0, f"erreur: {exc}"
            async with _sm() as session:
                s = await session.get(FetchSource, sid)
                if s is not None:
                    s.last_run = dt.datetime.now(dt.timezone.utc)
                    s.last_count = count
                    s.last_status = status
                    await session.commit()
            log.info("fetch_done", source=src["name"], count=count, status=status)
    except Exception as exc:  # noqa: BLE001
        # Ne jamais laisser une exception tuer le scheduler.
        log.warning("fetch_scan_failed", error=str(exc))


async def main() -> None:
    configure_logging()
    global _channel
    connection = await connect()
    _channel = await connection.channel(publisher_confirms=True)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(_scan, "interval", seconds=60, next_run_time=dt.datetime.now())
    scheduler.start()
    log.info("fetch_worker_started")
    try:
        await asyncio.Event().wait()
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
