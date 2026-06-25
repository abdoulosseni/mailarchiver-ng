#!/usr/bin/env python3
"""Test d'injection de mails par les trois voies d'ingestion : SMTP, IMAP, POP3.

Pour chaque protocole, on injecte un mail UNIQUE puis on vérifie qu'il est bien
archivé et cherchable via l'API (recherche par sujet).

- SMTP : envoi direct vers la passerelle SMTP de MailArchiver (STARTTLS).
- IMAP / POP3 : on dépose un mail dans une boîte d'un serveur de test, on crée
  une « source » de collecte via l'API admin, on déclenche la relève, puis on
  vérifie l'archivage. Les sources créées sont supprimées en fin de test.

Serveur mail de test (IMAP/POP3) : GreenMail convient parfaitement.
  docker run -d --name greenmail --network mailarchiver-ng_default \
    -p 3025:3025 -p 3143:3143 \
    -e GREENMAIL_OPTS='-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 \
       -Dgreenmail.auth.disabled -Dgreenmail.verbose' \
    greenmail/standalone:2.1.0

Usage :
  python loadtest/test_ingestion.py                 # les 3 protocoles
  python loadtest/test_ingestion.py --only smtp     # SMTP uniquement
  python loadtest/test_ingestion.py --skip pop      # tout sauf POP3
"""

from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import sys
import time
import urllib.request
from email.message import EmailMessage


# ── Client API minimal (stdlib) ────────────────────────────────────


def _api(base: str, path: str, method: str = "GET", token: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_error": e.read().decode(errors="replace")}


def login(base: str, user: str, pw: str) -> str:
    res = _api(base, "/auth/login", "POST", body={"username": user, "password": pw})
    if "token" not in res:
        sys.exit(f"Échec login admin : {res}")
    return res["token"]


def search_total(base: str, token: str, subject: str) -> int:
    res = _api(base, "/search/advanced?size=0", "POST", token, {"subject": subject})
    return res.get("total", 0)


def wait_archived(base: str, token: str, subject: str, timeout: int = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if search_total(base, token, subject) >= 1:
            return True
        time.sleep(2)
    return False


# ── Dépôt de mails ─────────────────────────────────────────────────


def make_mail(subject: str, to: str, frm: str = "ingestion@externe.test") -> EmailMessage:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = to
    m["Subject"] = subject
    m["Message-ID"] = f"<{subject}@externe.test>"
    m["Date"] = "Sun, 21 Jun 2026 09:00:00 +0000"
    m.set_content(f"Mail de test d'ingestion : {subject}")
    return m


def smtp_send(host: str, port: int, msg: EmailMessage, starttls: bool = True) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with smtplib.SMTP(host, port, timeout=15) as s:
        if starttls:
            s.starttls(context=ctx)
        s.send_message(msg)


# ── Scénarios ──────────────────────────────────────────────────────


def test_smtp(args, token: str) -> bool:
    subject = f"ingest-smtp-{int(time.time())}"
    print(f"[SMTP] envoi direct vers {args.gateway_host}:{args.gateway_port} (STARTTLS)…")
    smtp_send(args.gateway_host, args.gateway_port, make_mail(subject, "boite-smtp@corp.test"))
    ok = wait_archived(args.api, token, subject)
    print(f"[SMTP] {'OK ✅ archivé et cherchable' if ok else 'ÉCHEC ❌ non archivé'} ({subject})")
    return ok


def test_via_source(protocol: str, args, token: str) -> bool:
    subject = f"ingest-{protocol}-{int(time.time())}"
    mailbox = f"boite-{protocol}@collect.test"
    port = args.imap_port if protocol == "imap" else args.pop_port
    print(f"[{protocol.upper()}] dépôt du mail dans {mailbox} via le serveur de test "
          f"({args.deposit_host}:{args.deposit_port})…")
    try:
        smtp_send(args.deposit_host, args.deposit_port, make_mail(subject, mailbox), starttls=False)
    except Exception as e:  # noqa: BLE001
        print(f"[{protocol.upper()}] ÉCHEC ❌ dépôt impossible ({e}). Serveur de test démarré ?")
        return False

    print(f"[{protocol.upper()}] création de la source ({args.mailbox_host}:{port})…")
    created = _api(args.api, "/fetch-sources", "POST", token, {
        "name": f"test-{protocol}",
        "protocol": protocol,
        "host": args.mailbox_host,
        "port": port,
        "username": mailbox,
        "password": "test",
        "use_ssl": False,
        "folder": "INBOX",
        "delete_after": protocol == "pop3",
    })
    sid = created.get("id")
    if sid is None:
        print(f"[{protocol.upper()}] ÉCHEC ❌ création source : {created}")
        return False

    try:
        print(f"[{protocol.upper()}] relève manuelle…")
        run = _api(args.api, f"/fetch-sources/{sid}/run", "POST", token)
        print(f"[{protocol.upper()}] relève : {run}")
        ok = wait_archived(args.api, token, subject)
        print(f"[{protocol.upper()}] {'OK ✅ archivé et cherchable' if ok else 'ÉCHEC ❌ non archivé'} ({subject})")
        return ok
    finally:
        _api(args.api, f"/fetch-sources/{sid}", "DELETE", token)  # nettoyage


def main() -> None:
    ap = argparse.ArgumentParser(description="Test d'injection SMTP / IMAP / POP3")
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="admin")
    ap.add_argument("--gateway-host", default="localhost")
    ap.add_argument("--gateway-port", type=int, default=2525)
    # Serveur mail de test (dépôt + relève)
    ap.add_argument("--deposit-host", default="localhost", help="SMTP du serveur de test (dépôt)")
    ap.add_argument("--deposit-port", type=int, default=3025)
    ap.add_argument("--mailbox-host", default="greenmail", help="hôte IMAP/POP vu par l'API (réseau Docker)")
    ap.add_argument("--imap-port", type=int, default=3143)
    ap.add_argument("--pop-port", type=int, default=3110)
    ap.add_argument("--only", choices=["smtp", "imap", "pop3"], help="ne tester qu'un protocole")
    ap.add_argument("--skip", choices=["smtp", "imap", "pop3"], action="append", default=[])
    args = ap.parse_args()

    token = login(args.api, args.admin_user, args.admin_pass)

    protocols = ["smtp", "imap", "pop3"]
    if args.only:
        protocols = [args.only]
    protocols = [p for p in protocols if p not in args.skip]

    results = {}
    for p in protocols:
        results[p] = test_smtp(args, token) if p == "smtp" else test_via_source(p, args, token)
        print()

    print("===== RÉSULTATS =====")
    for p, ok in results.items():
        print(f"  {p.upper():5} : {'RÉUSSI ✅' if ok else 'ÉCHEC ❌'}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
