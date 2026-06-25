"""Suivi des jobs de transfert de périmètre (asynchrones)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from mailarchiver_common.models import TransferJob, get_sessionmaker


def _to_dict(j: TransferJob) -> dict:
    return {
        "id": j.id,
        "auditor": j.auditor_username,
        "recipient": j.recipient,
        "total": j.total,
        "sent": j.sent,
        "status": j.status,
        "error": j.error,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


class TransferJobRepo:
    def __init__(self) -> None:
        self._sm = get_sessionmaker()

    async def create(self, auditor_username: str, recipient: str, total: int) -> int:
        async with self._sm() as session:
            j = TransferJob(
                auditor_username=auditor_username,
                recipient=recipient,
                total=total,
                sent=0,
                status="running",
                created_at=dt.datetime.now(dt.timezone.utc),
            )
            session.add(j)
            await session.commit()
            return j.id

    async def set_progress(self, job_id: int, sent: int) -> None:
        async with self._sm() as session:
            j = await session.get(TransferJob, job_id)
            if j:
                j.sent = sent
                await session.commit()

    async def finish(self, job_id: int, status: str, error: str | None) -> None:
        async with self._sm() as session:
            j = await session.get(TransferJob, job_id)
            if j:
                j.status = status
                j.error = error
                j.finished_at = dt.datetime.now(dt.timezone.utc)
                await session.commit()

    async def list(self, limit: int = 20) -> list[dict]:
        async with self._sm() as session:
            rows = (await session.scalars(select(TransferJob).order_by(TransferJob.id.desc()).limit(limit))).all()
            return [_to_dict(j) for j in rows]
