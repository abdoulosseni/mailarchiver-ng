"""Écriture du journal d'audit (append-only)."""

from __future__ import annotations

import datetime as dt

from mailarchiver_common.models import AuditEvent, get_sessionmaker


async def record(
    actor: str,
    action: str,
    target: str | None = None,
    detail: dict | None = None,
    source_ip: str | None = None,
) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        session.add(
            AuditEvent(
                ts=dt.datetime.now(dt.timezone.utc),
                actor=actor,
                action=action,
                target=target,
                detail=detail or {},
                source_ip=source_ip,
            )
        )
        await session.commit()
