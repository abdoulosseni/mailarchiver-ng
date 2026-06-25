"""Point d'entrée du serveur SMTP avec STARTTLS.

Important : on N'UTILISE PAS aiosmtpd.Controller (qui démarre le serveur dans un
thread avec sa PROPRE boucle asyncio). La connexion RabbitMQ et le serveur SMTP
doivent partager la MÊME boucle, sinon les futures aio-pika lèvent
« attached to a different loop ». On fait donc tourner le protocole SMTP
directement dans la boucle courante via loop.create_server().
"""

from __future__ import annotations

import asyncio
import os
import ssl

import structlog
from aiosmtpd.smtp import SMTP

from mailarchiver_common.logging import configure_logging
from mailarchiver_common.queue import connect
from .handler import ArchiveHandler

log = structlog.get_logger()


def _tls_context() -> ssl.SSLContext | None:
    cert, key = os.environ.get("SMTP_TLS_CERT"), os.environ.get("SMTP_TLS_KEY")
    if cert and key and os.path.exists(cert) and os.path.exists(key):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx
    log.warning("tls_disabled", reason="certificat/clé introuvables — STARTTLS indisponible")
    return None


async def _load_config() -> dict:
    """Config SMTPD : valeurs d'environnement, surchargées par les paramètres en
    base (`app_settings.smtpd_*`) s'ils existent. Repli sur l'env si la base est
    indisponible (ex. tout premier démarrage)."""
    cfg = {
        "host": os.environ.get("SMTP_HOST", "0.0.0.0"),
        "port": int(os.environ.get("SMTP_PORT", "2525")),
        "require_tls": os.environ.get("SMTP_REQUIRE_STARTTLS", "false").lower() == "true",
        "max_bytes": int(os.environ.get("SMTP_MAX_MESSAGE_BYTES", str(50 * 1024 * 1024))),
    }
    try:
        from sqlalchemy import select

        from mailarchiver_common.models import AppSetting, get_sessionmaker

        async with get_sessionmaker()() as session:
            rows = (await session.scalars(select(AppSetting))).all()
        st = {r.key: r.value for r in rows}
        if st.get("smtpd_host"):
            cfg["host"] = st["smtpd_host"]
        if st.get("smtpd_port"):
            cfg["port"] = int(st["smtpd_port"])
        if "smtpd_require_starttls" in st:
            cfg["require_tls"] = st["smtpd_require_starttls"].lower() == "true"
        if st.get("smtpd_max_message_bytes"):
            cfg["max_bytes"] = int(st["smtpd_max_message_bytes"])
        log.info("smtpd_config_loaded", source="db+env")
    except Exception as exc:  # noqa: BLE001
        log.warning("smtpd_config_db_unavailable", error=str(exc))
    return cfg


async def main() -> None:
    configure_logging()
    cfg = await _load_config()
    host = cfg["host"]
    port = cfg["port"]
    require_tls = cfg["require_tls"]
    max_bytes = cfg["max_bytes"]

    connection = await connect()
    channel = await connection.channel(publisher_confirms=True)

    handler = ArchiveHandler(channel=channel, max_message_bytes=max_bytes)
    tls = _tls_context()

    loop = asyncio.get_running_loop()

    def factory() -> SMTP:
        # Un protocole SMTP par connexion cliente, tous sur la boucle courante.
        return SMTP(
            handler,
            require_starttls=require_tls and tls is not None,
            tls_context=tls,
            data_size_limit=max_bytes,
            ident="MailArchiver-NG",
        )

    server = await loop.create_server(factory, host=host, port=port)
    log.info("smtp_started", host=host, port=port, starttls=tls is not None, require_tls=require_tls)

    try:
        async with server:
            await server.serve_forever()
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
