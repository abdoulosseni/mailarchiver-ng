"""Primitives cryptographiques de l'archive.

Pipeline de protection d'un blob :  données ──zlib──► compressé ──AES-256-GCM──► chiffré
La signature numérique (Ed25519) porte sur le *hash de l'archive* (métadonnées +
contenu) afin de garantir l'inviolabilité (WORM) et de permettre une vérification
indépendante du chiffrement.

Choix de conception :
- AES-256-**GCM** : chiffrement authentifié (confidentialité + intégrité du blob).
- *Envelope encryption* : une clé de données (DEK) aléatoire par blob, elle-même
  chiffrée par la clé maître (KEK). Permet la rotation de la clé maître sans
  re-chiffrer tous les blobs.
- La **déduplication** se calcule sur le contenu EN CLAIR (SHA-256 avant
  compression/chiffrement) : deux PJ identiques produisent le même hash.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization

from .config import get_settings

# ── Hash / déduplication ────────────────────────────────────────────


def content_hash(data: bytes) -> str:
    """Empreinte SHA-256 hex utilisée comme clé de déduplication et de stockage."""
    return hashlib.sha256(data).hexdigest()


# ── Compression ─────────────────────────────────────────────────────


def compress(data: bytes, level: int = 6) -> bytes:
    return zlib.compress(data, level)


def decompress(data: bytes) -> bytes:
    return zlib.decompress(data)


# ── Chiffrement (envelope encryption, AES-256-GCM) ──────────────────


@lru_cache(maxsize=1)
def _master_key() -> bytes:
    """Charge la clé maître (KEK) AES-256 depuis le fichier (base64 de 32 octets)."""
    with open(get_settings().master_key_file, "rb") as fh:
        key = base64.b64decode(fh.read().strip())
    if len(key) != 32:
        raise ValueError("La clé maître doit faire 32 octets (AES-256).")
    return key


@dataclass
class SealedBlob:
    """Blob scellé prêt à être stocké : format autoportant.

    Sérialisation : [len(wrapped_dek)][wrapped_dek][dek_nonce][data_nonce][ciphertext]
    """

    wrapped_dek: bytes  # DEK chiffrée par la KEK (nonce inclus en préfixe)
    data_nonce: bytes  # nonce GCM du contenu
    ciphertext: bytes  # contenu compressé puis chiffré

    def serialize(self) -> bytes:
        return (
            struct.pack(">H", len(self.wrapped_dek))
            + self.wrapped_dek
            + self.data_nonce
            + self.ciphertext
        )

    @classmethod
    def deserialize(cls, raw: bytes) -> "SealedBlob":
        (wlen,) = struct.unpack(">H", raw[:2])
        offset = 2
        wrapped_dek = raw[offset : offset + wlen]
        offset += wlen
        data_nonce = raw[offset : offset + 12]
        offset += 12
        return cls(wrapped_dek=wrapped_dek, data_nonce=data_nonce, ciphertext=raw[offset:])


def _random(n: int) -> bytes:
    import os

    return os.urandom(n)


def seal(plaintext: bytes) -> SealedBlob:
    """Compresse puis chiffre `plaintext` avec une DEK à usage unique."""
    compressed = compress(plaintext)

    dek = _random(32)
    data_nonce = _random(12)
    ciphertext = AESGCM(dek).encrypt(data_nonce, compressed, None)

    # Emballage de la DEK par la clé maître
    dek_nonce = _random(12)
    wrapped = dek_nonce + AESGCM(_master_key()).encrypt(dek_nonce, dek, None)

    return SealedBlob(wrapped_dek=wrapped, data_nonce=data_nonce, ciphertext=ciphertext)


def unseal(blob: SealedBlob) -> bytes:
    """Déchiffre puis décompresse un blob scellé."""
    dek_nonce, wrapped = blob.wrapped_dek[:12], blob.wrapped_dek[12:]
    dek = AESGCM(_master_key()).decrypt(dek_nonce, wrapped, None)
    compressed = AESGCM(dek).decrypt(blob.data_nonce, blob.ciphertext, None)
    return decompress(compressed)


# ── Chiffrement d'un petit secret (mots de passe de sources IMAP/POP) ──


def encrypt_secret(plaintext: str) -> str:
    """Chiffre une chaîne courte avec la clé maître ; retourne du base64."""
    return base64.b64encode(seal(plaintext.encode()).serialize()).decode()


def decrypt_secret(token_b64: str) -> str:
    return unseal(SealedBlob.deserialize(base64.b64decode(token_b64))).decode()


# ── Signature numérique (Ed25519) ──────────────────────────────────


@lru_cache(maxsize=1)
def _signing_key() -> Ed25519PrivateKey:
    with open(get_settings().signing_private_key_file, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


@lru_cache(maxsize=1)
def _verify_key() -> Ed25519PublicKey:
    with open(get_settings().signing_public_key_file, "rb") as fh:
        return serialization.load_pem_public_key(fh.read())


def archive_fingerprint(headers_canonical: bytes, body_hash: str, attachment_hashes: list[str]) -> bytes:
    """Empreinte stable de l'archive complète, base de la signature."""
    h = hashlib.sha256()
    h.update(headers_canonical)
    h.update(body_hash.encode())
    for ah in sorted(attachment_hashes):
        h.update(ah.encode())
    return h.digest()


def sign(fingerprint: bytes) -> str:
    """Signe l'empreinte ; retourne la signature en base64."""
    return base64.b64encode(_signing_key().sign(fingerprint)).decode()


def verify(fingerprint: bytes, signature_b64: str) -> bool:
    """Vérifie la signature d'une archive. False si altérée."""
    from cryptography.exceptions import InvalidSignature

    try:
        _verify_key().verify(base64.b64decode(signature_b64), fingerprint)
        return True
    except InvalidSignature:
        return False
