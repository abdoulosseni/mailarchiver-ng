"""Tests du pipeline cryptographique (sans infrastructure externe).

Lancer :  pip install ./libs/mailarchiver_common pytest  &&  pytest tests/
"""

from __future__ import annotations

import base64
import os

import pytest


@pytest.fixture(autouse=True)
def _keys(tmp_path, monkeypatch):
    # Clé maître AES-256
    master = tmp_path / "master.key"
    master.write_bytes(base64.b64encode(os.urandom(32)))

    # Paire de signature Ed25519
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    priv_pem = tmp_path / "sign_priv.pem"
    pub_pem = tmp_path / "sign_pub.pem"
    priv_pem.write_bytes(priv.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
    pub_pem.write_bytes(priv.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo))

    monkeypatch.setenv("MASTER_KEY_FILE", str(master))
    monkeypatch.setenv("SIGNING_PRIVATE_KEY_FILE", str(priv_pem))
    monkeypatch.setenv("SIGNING_PUBLIC_KEY_FILE", str(pub_pem))

    # Réinitialise les singletons/caches mémoïsés
    from mailarchiver_common import config, crypto

    config._settings = None
    crypto._master_key.cache_clear()
    crypto._signing_key.cache_clear()
    crypto._verify_key.cache_clear()
    yield


def test_seal_unseal_roundtrip():
    from mailarchiver_common import crypto

    data = b"Bonjour, ceci est un corps de mail." * 100
    sealed = crypto.seal(data)
    # Le ciphertext ne contient pas le clair
    assert data not in sealed.serialize()
    # Round-trip via (dé)sérialisation
    restored = crypto.unseal(crypto.SealedBlob.deserialize(sealed.serialize()))
    assert restored == data


def test_dedup_hash_is_content_addressed():
    from mailarchiver_common import crypto

    a = crypto.content_hash(b"piece-jointe identique")
    b = crypto.content_hash(b"piece-jointe identique")
    c = crypto.content_hash(b"autre contenu")
    assert a == b and a != c


def test_signature_detects_tampering():
    from mailarchiver_common import crypto

    fp = crypto.archive_fingerprint(b"From: a@b\nSubject: x", crypto.content_hash(b"body"), ["h1", "h2"])
    sig = crypto.sign(fp)
    assert crypto.verify(fp, sig) is True
    # Empreinte altérée => signature invalide
    tampered = crypto.archive_fingerprint(b"From: evil@b\nSubject: x", crypto.content_hash(b"body"), ["h1", "h2"])
    assert crypto.verify(tampered, sig) is False
