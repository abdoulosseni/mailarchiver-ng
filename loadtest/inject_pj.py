"""Envoi de mails AVEC pièces jointes (test de l'archivage des PJ + dédup).

Chaque mail porte 2 pièces jointes :
- logo.png : IDENTIQUE pour tous les mails  -> doit être dédupliquée (1 seul blob,
  ref_count = nombre de mails).
- doc-<i>.pdf : UNIQUE par mail             -> 1 blob par mail.

Usage : python inject_pj.py --total 1000 --conns 10
"""

from __future__ import annotations

import argparse
import ssl
import threading
import time
from email.message import EmailMessage

import smtplib

SHARED_PJ = b"%PNG\r\n logo partage commun a tous les mails " + b"\x00" * 3000


def make(i: int) -> EmailMessage:
    m = EmailMessage()
    m["From"] = f"sender{i % 50}@pj.test"
    m["To"] = f"rcpt{i % 50}@pj.test"
    m["Subject"] = f"Mail avec PJ {i}"
    m["Message-ID"] = f"<pj-{i}-{time.time_ns()}@pj.test>"
    m["Date"] = "Sat, 20 Jun 2026 19:00:00 +0000"
    m.set_content(f"Corps du mail {i} avec deux pieces jointes.")
    m.add_attachment(SHARED_PJ, maintype="image", subtype="png", filename="logo.png")
    unique = (f"document unique du mail numero {i} - ".encode()) * 40
    m.add_attachment(unique, maintype="application", subtype="pdf", filename=f"doc-{i}.pdf")
    return m


def worker(cid: int, n: int, host: str, port: int, errors: list) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        s = smtplib.SMTP(host, port, timeout=60)
        s.starttls(context=ctx)
        for j in range(n):
            s.send_message(make(cid * 1_000_000 + j))
        s.quit()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"conn{cid}: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=1000)
    ap.add_argument("--conns", type=int, default=10)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2525)
    a = ap.parse_args()

    per = a.total // a.conns
    total = per * a.conns
    errors: list = []
    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(c, per, a.host, a.port, errors)) for c in range(a.conns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0
    print(f"INJECTED={total} (avec 2 PJ chacun) time={dt:.1f}s rate={total / dt:.0f} msg/s errors={len(errors)}")
    for e in errors[:5]:
        print("  ERR", e)


if __name__ == "__main__":
    main()
