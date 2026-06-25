"""Worker d'archivage : consomme la file `raw_mail` et exécute le pipeline.

Scalabilité : lancer plusieurs réplicas de ce service (docker compose
`deploy.replicas`) pour augmenter le débit. Chaque worker traite `prefetch`
messages en parallèle ; la crypto (OpenSSL) libère le GIL.
"""

from __future__ import annotations

import asyncio

import structlog

from mailarchiver_common.models import ensure_schema, get_sessionmaker
from mailarchiver_common.logging import configure_logging
from mailarchiver_common.queue import connect, consume_raw_mail, declare_events_exchange
from mailarchiver_common.storage import BlobStore

from .indexer import SearchIndexer
from .pipeline import ArchivePipeline

log = structlog.get_logger()


async def _init_schema() -> None:
    """Crée les tables si absentes (dev). En prod : utiliser Alembic.

    Plusieurs réplicas démarrent simultanément : un verrou consultatif Postgres
    sérialise la création du schéma et évite la course
    « duplicate key ... pg_type_typname_nsp_index ».
    """
    sm = get_sessionmaker()
    await ensure_schema(sm.kw["bind"])


async def main() -> None:
    configure_logging()
    blobs = BlobStore()
    await blobs.ensure_bucket()

    indexer = SearchIndexer()
    await indexer.ensure_index()

    await _init_schema()

    connection = await connect()
    channel = await connection.channel()

    # Canal/exchange dédié à la diffusion des événements temps réel.
    events_channel = await connection.channel()
    events_exchange = await declare_events_exchange(events_channel)

    pipeline = ArchivePipeline(blobs=blobs, indexer=indexer, events_exchange=events_exchange)

    log.info("archiver_worker_started")
    try:
        await consume_raw_mail(channel, pipeline.process, prefetch=16)
    finally:
        await indexer.close()
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
