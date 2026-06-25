"""Stockage objet des blobs (corps de mail + pièces jointes) sur S3/MinIO.

La clé d'objet est l'empreinte SHA-256 du contenu en clair, shardée sur deux
niveaux (ab/cd/<sha256>) : naturellement déduplicant et idempotent.

Performance : le client S3 est créé UNE FOIS puis réutilisé (pools de connexions
persistants). `put` écrit directement (idempotent) sans HEAD préalable.
"""

from __future__ import annotations

import asyncio
import os

import aioboto3
from botocore.exceptions import ClientError

from .config import get_settings

# WORM (Write Once Read Many) : si > 0, le bucket est créé avec Object Lock +
# versioning et une rétention par défaut (jours). Les blobs deviennent immuables
# (pas de suppression/écrasement avant échéance) → exigence d'archivage légal.
# Le nombre de jours doit couvrir la politique de conservation applicative.
_OBJECT_LOCK_DAYS = int(os.environ.get("S3_OBJECT_LOCK_DAYS", "0"))
_OBJECT_LOCK_MODE = os.environ.get("S3_OBJECT_LOCK_MODE", "GOVERNANCE")  # ou COMPLIANCE


def _object_key(sha256: str) -> str:
    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}"


class BlobStore:
    def __init__(self) -> None:
        self._s = get_settings()
        self._session = aioboto3.Session()
        self._client = None
        self._cm = None
        self._lock = asyncio.Lock()

    async def _client_obj(self):
        """Client S3 partagé (créé à la première utilisation, puis réutilisé)."""
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._cm = self._session.client(
                        "s3",
                        endpoint_url=self._s.s3_endpoint,
                        aws_access_key_id=self._s.s3_access_key,
                        aws_secret_access_key=self._s.s3_secret_key,
                        region_name=self._s.s3_region,
                    )
                    self._client = await self._cm.__aenter__()
        return self._client

    async def ensure_bucket(self) -> None:
        s3 = await self._client_obj()
        try:
            await s3.head_bucket(Bucket=self._s.s3_bucket)
            return
        except ClientError:
            pass
        if _OBJECT_LOCK_DAYS > 0:
            # WORM : Object Lock activé à la création (irréversible, et impossible
            # à ajouter ensuite → nécessite un bucket neuf). Versioning implicite.
            await s3.create_bucket(Bucket=self._s.s3_bucket, ObjectLockEnabledForBucket=True)
            await s3.put_object_lock_configuration(
                Bucket=self._s.s3_bucket,
                ObjectLockConfiguration={
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": _OBJECT_LOCK_MODE, "Days": _OBJECT_LOCK_DAYS}},
                },
            )
        else:
            await s3.create_bucket(Bucket=self._s.s3_bucket)

    async def set_legal_hold(self, sha256: str, on: bool) -> None:
        """Pose/retire une conservation légale sur un blob (immuabilité indéfinie,
        indépendante de la rétention). Best-effort si Object Lock désactivé."""
        if _OBJECT_LOCK_DAYS <= 0:
            return
        s3 = await self._client_obj()
        try:
            await s3.put_object_legal_hold(
                Bucket=self._s.s3_bucket,
                Key=_object_key(sha256),
                LegalHold={"Status": "ON" if on else "OFF"},
            )
        except ClientError:
            pass

    async def ping(self) -> None:
        """Vérifie la connectivité au stockage objet (lève si indisponible)."""
        s3 = await self._client_obj()
        await s3.list_buckets()

    async def exists(self, sha256: str) -> bool:
        s3 = await self._client_obj()
        try:
            await s3.head_object(Bucket=self._s.s3_bucket, Key=_object_key(sha256))
            return True
        except ClientError:
            return False

    async def put(self, sha256: str, sealed: bytes) -> None:
        """Écrit un blob scellé (idempotent : ré-écrire un contenu identique est sûr)."""
        s3 = await self._client_obj()
        await s3.put_object(Bucket=self._s.s3_bucket, Key=_object_key(sha256), Body=sealed)

    async def get(self, sha256: str) -> bytes:
        s3 = await self._client_obj()
        obj = await s3.get_object(Bucket=self._s.s3_bucket, Key=_object_key(sha256))
        return await obj["Body"].read()

    async def delete(self, sha256: str) -> None:
        s3 = await self._client_obj()
        await s3.delete_object(Bucket=self._s.s3_bucket, Key=_object_key(sha256))
