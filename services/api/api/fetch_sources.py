"""Gestion des sources de collecte IMAP/POP3 (CRUD).

Le mot de passe est chiffré au repos (clé maître) et n'est jamais renvoyé au client.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from mailarchiver_common import crypto
from mailarchiver_common.models import FetchSource, get_sessionmaker

VALID_PROTOCOLS = {"imap", "pop3"}


def _to_dict(s: FetchSource) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "protocol": s.protocol,
        "host": s.host,
        "port": s.port,
        "username": s.username,
        "use_ssl": s.use_ssl,
        "folder": s.folder,
        "interval_minutes": s.interval_minutes,
        "delete_after": s.delete_after,
        "active": s.active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "last_run": s.last_run.isoformat() if s.last_run else None,
        "last_status": s.last_status,
        "last_count": s.last_count,
    }


class FetchSourceRepo:
    def __init__(self) -> None:
        self._sm = get_sessionmaker()

    async def list(self) -> list[dict]:
        async with self._sm() as session:
            rows = (await session.scalars(select(FetchSource).order_by(FetchSource.id))).all()
            return [_to_dict(s) for s in rows]

    async def create(self, data: dict) -> dict:
        if data["protocol"] not in VALID_PROTOCOLS:
            raise ValueError("protocole invalide (imap ou pop3)")
        if not data.get("host") or not data.get("username") or not data.get("password"):
            raise ValueError("hôte, identifiant et mot de passe requis")
        async with self._sm() as session:
            s = FetchSource(
                name=data.get("name") or f"{data['protocol']}://{data['username']}",
                protocol=data["protocol"],
                host=data["host"],
                port=int(data["port"]),
                username=data["username"],
                password_enc=crypto.encrypt_secret(data["password"]),
                use_ssl=bool(data.get("use_ssl", True)),
                folder=data.get("folder") or "INBOX",
                interval_minutes=int(data.get("interval_minutes", 15)),
                delete_after=bool(data.get("delete_after", False)),
                active=True,
                created_at=dt.datetime.now(dt.timezone.utc),
            )
            session.add(s)
            await session.commit()
            return _to_dict(s)

    async def delete(self, source_id: int) -> None:
        async with self._sm() as session:
            s = await session.get(FetchSource, source_id)
            if s is None:
                raise KeyError("source introuvable")
            await session.delete(s)
            await session.commit()

    async def get_with_password(self, source_id: int) -> tuple[dict, str] | None:
        """Retourne (dict de la source, mot de passe déchiffré) ou None."""
        async with self._sm() as session:
            s = await session.get(FetchSource, source_id)
            if s is None:
                return None
            return _to_dict(s), crypto.decrypt_secret(s.password_enc)

    async def update_status(self, source_id: int, count: int, status: str) -> None:
        async with self._sm() as session:
            s = await session.get(FetchSource, source_id)
            if s is None:
                return
            s.last_run = dt.datetime.now(dt.timezone.utc)
            s.last_count = count
            s.last_status = status
            await session.commit()
