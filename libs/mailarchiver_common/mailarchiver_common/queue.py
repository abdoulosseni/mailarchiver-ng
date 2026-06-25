"""Accès à la file de messages RabbitMQ (aio-pika).

La file `raw_mail` est durable et les messages persistants : aucun mail accepté
en SMTP n'est perdu même si les workers sont indisponibles (backpressure).

Robustesse :
- Erreurs transitoires (base de données indisponible) → remise en file (retry).
- Autres échecs → 1 réessai puis mise en **dead-letter queue** (`raw_mail_dead`)
  pour éviter toute boucle de message empoisonné. Les messages morts sont
  conservés (inspectables / rejouables), jamais perdus.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import aio_pika
import structlog
from sqlalchemy.exc import InterfaceError, OperationalError

from .config import get_settings

log = structlog.get_logger()

RAW_DLX = "raw_mail_dlx"  # exchange de dead-letter
RAW_DEAD_QUEUE = "raw_mail_dead"

# Files de travail durables en **quorum** (Raft) : répliquées sur un cluster
# RabbitMQ (tolérance à la panne d'un nœud) ; fonctionnent aussi en mono-nœud.
_QUORUM = {"x-queue-type": "quorum"}


async def connect() -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(get_settings().amqp_url)


# Exchange fanout pour diffuser les événements « mail archivé » en temps réel.
EVENTS_EXCHANGE = "mail_events"


async def declare_events_exchange(channel: aio_pika.abc.AbstractChannel) -> aio_pika.abc.AbstractExchange:
    return await channel.declare_exchange(EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True)


async def publish_event(exchange: aio_pika.abc.AbstractExchange, payload: bytes) -> None:
    await exchange.publish(aio_pika.Message(body=payload), routing_key="")


# ── File des jobs de restauration (durable → survit au redémarrage de l'API) ──

RESTORE_JOBS_QUEUE = "restore_jobs"


async def publish_restore_job(channel: aio_pika.abc.AbstractChannel, payload: bytes) -> None:
    await channel.declare_queue(RESTORE_JOBS_QUEUE, durable=True, arguments=_QUORUM)
    await channel.default_exchange.publish(
        aio_pika.Message(body=payload, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key=RESTORE_JOBS_QUEUE,
    )


async def consume_restore_jobs(
    channel: aio_pika.abc.AbstractChannel,
    handler: Callable[[bytes], Awaitable[None]],
    prefetch: int = 1,
) -> None:
    await channel.set_qos(prefetch_count=prefetch)
    queue = await channel.declare_queue(RESTORE_JOBS_QUEUE, durable=True, arguments=_QUORUM)
    async with queue.iterator() as it:
        async for message in it:
            # requeue=True : si l'API redémarre en plein job, le job est rejoué.
            async with message.process(requeue=True):
                await handler(message.body)


# ── File principale + dead-letter ──────────────────────────────────


async def _declare_main_queue(channel: aio_pika.abc.AbstractChannel) -> aio_pika.abc.AbstractQueue:
    return await channel.declare_queue(
        get_settings().raw_mail_queue,
        durable=True,
        arguments={"x-dead-letter-exchange": RAW_DLX, **_QUORUM},
    )


async def declare_dead_letter_topology(channel: aio_pika.abc.AbstractChannel) -> None:
    dlx = await channel.declare_exchange(RAW_DLX, aio_pika.ExchangeType.FANOUT, durable=True)
    dead = await channel.declare_queue(RAW_DEAD_QUEUE, durable=True, arguments=_QUORUM)
    await dead.bind(dlx)


async def publish_raw_mail(channel: aio_pika.abc.AbstractChannel, payload: bytes) -> None:
    """Publie un mail brut (.eml) dans la file durable."""
    await _declare_main_queue(channel)
    await channel.default_exchange.publish(
        aio_pika.Message(body=payload, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key=get_settings().raw_mail_queue,
    )


async def consume_raw_mail(
    channel: aio_pika.abc.AbstractChannel,
    handler: Callable[[bytes], Awaitable[None]],
    prefetch: int = 16,
) -> None:
    """Consomme la file et délègue chaque message brut à `handler`.

    Acquittement manuel : ack si succès ; sur erreur transitoire (DB) on remet
    en file ; sinon 1 réessai puis dead-letter (anti-boucle de poison)."""
    await channel.set_qos(prefetch_count=prefetch)
    await declare_dead_letter_topology(channel)
    queue = await _declare_main_queue(channel)

    async with queue.iterator() as it:
        async for message in it:
            try:
                await handler(message.body)
                await message.ack()
            except (OperationalError, InterfaceError) as exc:
                # Dépendance transitoirement indisponible → réessayer (pas de DLQ).
                await message.reject(requeue=True)
                log.warning("transient_error_requeued", error=str(exc))
            except Exception as exc:  # noqa: BLE001
                if message.redelivered:
                    await message.reject(requeue=False)  # → dead-letter queue
                    log.error("message_dead_lettered", error=str(exc))
                else:
                    await message.reject(requeue=True)  # 1er échec : un réessai
                    log.warning("processing_failed_retry", error=str(exc))
