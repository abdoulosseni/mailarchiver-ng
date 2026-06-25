"""Handler SMTP : valide a minima puis publie le mail brut dans la queue.

Principe de débit : on ne fait AUCUN traitement lourd ici. On accepte le message,
on le pousse (persistant) dans RabbitMQ, et on répond 250 immédiatement. Tout le
pipeline (parse, dédup, crypto, signature, index) est délégué aux archiver-workers.
"""

from __future__ import annotations

import structlog
from aiosmtpd.smtp import SMTP, Envelope, Session

from mailarchiver_common.queue import publish_raw_mail

log = structlog.get_logger()


class ArchiveHandler:
    def __init__(self, channel, max_message_bytes: int) -> None:
        self._channel = channel
        self._max = max_message_bytes

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:
        raw: bytes = envelope.content if isinstance(envelope.content, bytes) else envelope.content.encode()

        if len(raw) > self._max:
            return "552 Message size exceeds maximum permitted"

        try:
            await publish_raw_mail(self._channel, raw)
        except Exception as exc:  # noqa: BLE001
            log.error("publish_failed", error=str(exc))
            # 4xx => le MTA émetteur réessaiera : pas de perte de mail.
            return "451 Temporary failure, please retry"

        log.info("accepted", mail_from=envelope.mail_from, rcpts=len(envelope.rcpt_tos), bytes=len(raw))
        return "250 Message accepted for archival"
