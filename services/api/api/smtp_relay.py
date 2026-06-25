"""Envoi d'un mail brut via le relais SMTP configuré dans les Paramètres."""

from __future__ import annotations

import aiosmtplib


async def send_raw(cfg: dict, raw: bytes, recipients: list[str]) -> None:
    await aiosmtplib.send(
        raw,
        sender=cfg.get("from") or "archiver@localhost",
        recipients=recipients,
        hostname=cfg["host"],
        port=cfg["port"],
        username=cfg.get("username") or None,
        password=cfg.get("password") or None,
        start_tls=bool(cfg.get("starttls", True)),
    )
