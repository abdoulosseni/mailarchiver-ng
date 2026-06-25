"""Collecte de mails depuis une boîte IMAP ou POP3 (stdlib, synchrone).

Retourne les messages bruts (RFC822) à réinjecter dans la file d'archivage.
La déduplication par archive_hash du pipeline protège contre les doublons même
si un message est relevé plusieurs fois.
"""

from __future__ import annotations

import asyncio
import imaplib
import poplib
import time

from .queue import publish_raw_mail


def imap_append(cfg: dict, raw_messages: list[bytes]) -> int:
    """Dépose des mails bruts dans une boîte IMAP (restauration façon imapsync).

    cfg : {host, port, username, password, ssl, folder}. Synchrone (à lancer en
    thread depuis du code async). Retourne le nombre de messages déposés."""
    host, port = cfg["host"], int(cfg.get("port") or (993 if cfg.get("ssl", True) else 143))
    folder = cfg.get("folder") or "INBOX"
    M = imaplib.IMAP4_SSL(host, port) if cfg.get("ssl", True) else imaplib.IMAP4(host, port)
    try:
        M.login(cfg["username"], cfg["password"])
        # Crée le dossier si absent (ignore l'erreur s'il existe déjà).
        try:
            M.create(folder)
        except Exception:  # noqa: BLE001
            pass
        n = 0
        for raw in raw_messages:
            M.append(folder, "", imaplib.Time2Internaldate(time.time()), raw)
            n += 1
        return n
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


def fetch_imap(
    host: str, port: int, username: str, password: str, use_ssl: bool, folder: str, delete_after: bool
) -> list[bytes]:
    M = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
    try:
        M.login(username, password)
        M.select(folder)
        typ, data = M.search(None, "UNSEEN")  # uniquement les nouveaux
        ids = data[0].split() if data and data[0] else []
        msgs: list[bytes] = []
        for i in ids:
            typ, d = M.fetch(i, "(RFC822)")
            if not d or not d[0]:
                continue
            msgs.append(d[0][1])
            if delete_after:
                M.store(i, "+FLAGS", "\\Deleted")
            else:
                M.store(i, "+FLAGS", "\\Seen")
        if delete_after:
            M.expunge()
        return msgs
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


def fetch_pop3(
    host: str, port: int, username: str, password: str, use_ssl: bool, delete_after: bool
) -> list[bytes]:
    P = poplib.POP3_SSL(host, port) if use_ssl else poplib.POP3(host, port)
    try:
        P.user(username)
        P.pass_(password)
        count = len(P.list()[1])
        msgs: list[bytes] = []
        for n in range(1, count + 1):
            _, lines, _ = P.retr(n)
            msgs.append(b"\r\n".join(lines))
            if delete_after:
                P.dele(n)
        return msgs
    finally:
        try:
            P.quit()
        except Exception:  # noqa: BLE001
            pass


def fetch_source(src: dict, password: str) -> list[bytes]:
    """Relève une source décrite par un dict (voir modèle FetchSource)."""
    if src["protocol"] == "imap":
        return fetch_imap(
            src["host"], src["port"], src["username"], password,
            src["use_ssl"], src.get("folder") or "INBOX", src["delete_after"],
        )
    if src["protocol"] == "pop3":
        return fetch_pop3(
            src["host"], src["port"], src["username"], password, src["use_ssl"], src["delete_after"],
        )
    raise ValueError(f"protocole non supporté : {src['protocol']}")


async def run_and_publish(channel, src: dict, password: str) -> int:
    """Relève la source (collecte synchrone en thread) et publie chaque mail
    dans la file d'archivage. Retourne le nombre de mails relevés."""
    msgs = await asyncio.to_thread(fetch_source, src, password)
    for raw in msgs:
        await publish_raw_mail(channel, raw)
    return len(msgs)
