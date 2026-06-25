#!/usr/bin/env python3
"""Injection de mails RÉALISTES et VOLUMINEUX (test de charge / stockage).

Simule des mails entrants d'Internet (Gmail / Yahoo) avec en-têtes complètes :
chaîne Received, DKIM-Signature, Authentication-Results (spf/dkim/dmarc),
Received-SPF, Message-ID au format du fournisseur. Corps texte volumineux
(par défaut 7 Mo) + une pièce jointe binaire (par défaut 2 Mo).

Usage :
  python loadtest/inject_large.py --total 10000 --conns 8 --body-mb 7 --attach-mb 2
  python loadtest/inject_large.py --total 20            # petit lot de validation
"""

from __future__ import annotations

import argparse
import os
import random
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import smtplib

_sent = [0] * 4096

PROVIDERS = [
    ("gmail.com", "mail-sor-f41.google.com", "209.85.220.41", "20230601"),
    ("yahoo.com", "sonic.gate.mail.ne1.yahoo.com", "98.137.64.150", "s2048"),
    ("outlook.com", "mail-am6eur05.outbound.protection.outlook.com", "40.107.22.66", "selector1"),
]
FIRST = ["Jean", "Marie", "Paul", "Sophie", "Luc", "Emma", "Hugo", "Lea", "Tom", "Eva"]
LAST = ["Martin", "Bernard", "Dubois", "Durand", "Moreau", "Laurent", "Simon", "Michel"]
WORDS = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
         "incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud "
         "exercitation ullamco laboris nisi aliquip ex ea commodo consequat duis aute").split()


def _b64ish(n: int) -> str:
    import base64
    return base64.b64encode(os.urandom(n)).decode()


def _big_text(size_bytes: int) -> str:
    """Texte semi-aléatoire (compressible comme du vrai texte) d'environ size_bytes."""
    chunks = []
    total = 0
    while total < size_bytes:
        line = " ".join(random.choice(WORDS) for _ in range(random.randint(8, 16)))
        chunks.append(line)
        total += len(line) + 1
    return "\n".join(chunks)


def make_mail(i: int, body_bytes: int, attach_bytes: int) -> EmailMessage:
    domain, relay, ip, selector = random.choice(PROVIDERS)
    user = f"{random.choice(FIRST)}.{random.choice(LAST)}{i}".lower()
    sender = f"{user}@{domain}"
    rcpt = f"dest{i % 200}@corp.example"
    date = formatdate(localtime=False)
    msgid = make_msgid(domain=f"mail.{domain}")

    m = EmailMessage()
    # En-têtes « réseau » (ajoutées par les MTA traversés) — ordre : plus récent en haut.
    m["Return-Path"] = f"<{sender}>"
    m["Received"] = (f"from {relay} ({relay} [{ip}]) by mx.archive.example with ESMTPS id {i}xyz "
                     f"(version=TLS1_3 cipher=TLS_AES_256_GCM_SHA384); {date}")
    m["Received"] = f"by {relay} with SMTP id {_b64ish(6)} for <{rcpt}>; {date}"
    m["Authentication-Results"] = (f"mx.archive.example; dkim=pass header.d={domain}; "
                                   f"spf=pass smtp.mailfrom={sender}; dmarc=pass header.from={domain}")
    m["Received-SPF"] = f"pass (mx.archive.example: domain of {sender} designates {ip} as permitted sender)"
    m["DKIM-Signature"] = (f"v=1; a=rsa-sha256; c=relaxed/relaxed; d={domain}; s={selector}; "
                           f"t={int(time.time())}; h=from:to:subject:date:message-id; "
                           f"bh={_b64ish(32)}; b={_b64ish(180)}")
    m["From"] = f'"{random.choice(FIRST)} {random.choice(LAST)}" <{sender}>'
    m["To"] = rcpt
    m["Subject"] = f"[{domain}] Message volumineux de test {i}"
    m["Date"] = date
    m["Message-ID"] = msgid
    m["X-Mailer"] = "MailArchiver-NG load test"

    m.set_content(_big_text(body_bytes))
    m.add_attachment(os.urandom(attach_bytes), maintype="application", subtype="pdf",
                     filename=f"document-{i}.pdf")
    return m


def worker(cid: int, n: int, host: str, port: int, body_bytes: int, attach_bytes: int, errors: list) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        s = smtplib.SMTP(host, port, timeout=120)
        s.starttls(context=ctx)
        base = cid * 1_000_000
        for j in range(n):
            s.send_message(make_mail(base + j, body_bytes, attach_bytes))
            _sent[cid] = j + 1
        s.quit()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"conn{cid}: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=10000)
    ap.add_argument("--conns", type=int, default=8)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--body-mb", type=float, default=7.0)
    ap.add_argument("--attach-mb", type=float, default=2.0)
    a = ap.parse_args()

    body_bytes = int(a.body_mb * 1024 * 1024)
    attach_bytes = int(a.attach_mb * 1024 * 1024)
    per = max(1, a.total // a.conns)
    total = per * a.conns
    errors: list = []

    print(f"Injection de {total} mails (~{a.body_mb} Mo corps + {a.attach_mb} Mo PJ), {a.conns} connexions…")
    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(c, per, a.host, a.port, body_bytes, attach_bytes, errors))
               for c in range(a.conns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0
    sent = sum(_sent[: a.conns])
    mb = sent * (a.body_mb + a.attach_mb)
    print(f"INJECTED={sent} time={dt:.0f}s rate={sent / dt:.1f} msg/s (~{mb / dt:.0f} Mo/s) errors={len(errors)}")
    for e in errors[:5]:
        print("  ERR", e)


if __name__ == "__main__":
    main()
