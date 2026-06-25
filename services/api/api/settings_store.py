"""Paramètres globaux de l'application (clé/valeur en base).

Valeurs par défaut si non enregistrées. Seul l'admin peut les modifier
(contrôle dans main.py). Le mot de passe SMTP est chiffré au repos.
"""

from __future__ import annotations

from sqlalchemy import select

from mailarchiver_common import crypto
from mailarchiver_common.models import AppSetting, get_sessionmaker

DEFAULTS = {
    "retention_days": "365",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_username": "",
    "smtp_starttls": "true",
    "smtp_from": "archiver@localhost",
    # Serveur SMTP entrant (SMTPD) — lu par la passerelle au démarrage.
    "smtpd_host": "0.0.0.0",
    "smtpd_port": "2525",
    "smtpd_require_starttls": "false",
    "smtpd_max_message_bytes": "52428800",
}
_PW_KEY = "smtp_password_enc"


def _as_bool(v: str) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


class SettingsStore:
    def __init__(self) -> None:
        self._sm = get_sessionmaker()

    async def _stored(self) -> dict:
        async with self._sm() as session:
            rows = (await session.scalars(select(AppSetting))).all()
        return {r.key: r.value for r in rows}

    async def get_all(self) -> dict:
        s = await self._stored()
        g = lambda k: s.get(k, DEFAULTS.get(k, ""))  # noqa: E731
        return {
            "retention_days": int(g("retention_days")),
            "smtp": {
                "host": g("smtp_host"),
                "port": int(g("smtp_port")),
                "username": g("smtp_username"),
                "starttls": _as_bool(g("smtp_starttls")),
                "from": g("smtp_from"),
                "password_set": bool(s.get(_PW_KEY)),  # ne jamais renvoyer le mot de passe
            },
            "smtpd": {
                "host": g("smtpd_host"),
                "port": int(g("smtpd_port")),
                "require_starttls": _as_bool(g("smtpd_require_starttls")),
                "max_message_bytes": int(g("smtpd_max_message_bytes")),
            },
        }

    async def get_smtp(self) -> dict | None:
        """Config SMTP avec mot de passe déchiffré, ou None si non configurée."""
        s = await self._stored()
        host = s.get("smtp_host", "")
        if not host:
            return None
        password = crypto.decrypt_secret(s[_PW_KEY]) if s.get(_PW_KEY) else ""
        return {
            "host": host,
            "port": int(s.get("smtp_port", DEFAULTS["smtp_port"])),
            "username": s.get("smtp_username", ""),
            "password": password,
            "starttls": _as_bool(s.get("smtp_starttls", "true")),
            "from": s.get("smtp_from", DEFAULTS["smtp_from"]),
        }

    async def _set(self, session, key: str, value: str) -> None:
        row = await session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value

    async def update(self, data: dict) -> None:
        """Met à jour les clés fournies. `smtp_password` non vide => (re)chiffré."""
        async with self._sm() as session:
            mapping = {
                "retention_days": "retention_days",
                "smtp_host": "smtp_host",
                "smtp_port": "smtp_port",
                "smtp_username": "smtp_username",
                "smtp_from": "smtp_from",
                "smtpd_host": "smtpd_host",
                "smtpd_port": "smtpd_port",
                "smtpd_max_message_bytes": "smtpd_max_message_bytes",
            }
            for field, key in mapping.items():
                if data.get(field) is not None:
                    await self._set(session, key, str(data[field]))
            if data.get("smtp_starttls") is not None:
                await self._set(session, "smtp_starttls", "true" if data["smtp_starttls"] else "false")
            if data.get("smtpd_require_starttls") is not None:
                await self._set(session, "smtpd_require_starttls", "true" if data["smtpd_require_starttls"] else "false")
            pw = data.get("smtp_password")
            if pw:  # vide/None => inchangé
                await self._set(session, _PW_KEY, crypto.encrypt_secret(pw))
            await session.commit()
