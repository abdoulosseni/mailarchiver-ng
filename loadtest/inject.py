"""Injecteur de charge SMTP pour MailArchiver-NG.

Envoie N mails UNIQUES via plusieurs connexions SMTP persistantes (STARTTLS
négocié une fois par connexion, puis réutilisée pour tous les messages).
Affiche le débit d'acceptation SMTP. La mesure du débit de TRAITEMENT
(archivage) se fait côté appelant en observant le compteur de messages en base.

Usage : python inject.py --total 6000 --conns 24 [--host localhost --port 2525]
"""

from __future__ import annotations

import argparse
import ssl
import threading
import time
from email.message import EmailMessage

import smtplib


def make_msg(i: int) -> EmailMessage:
    m = EmailMessage()
    m["From"] = f"sender{i % 100}@load.test"
    m["To"] = f"rcpt{i % 100}@load.test"
    m["Subject"] = f"Load test message {i}"
    # Message-ID + sujet uniques => archive_hash unique => pas de déduplication.
    m["Message-ID"] = f"<load-{i}-{time.time_ns()}@load.test>"
    m["Date"] = "Sat, 20 Jun 2026 18:00:00 +0000"
    m.set_content(f"Corps du message de charge numero {i}. " * 8)
    return m


_sent = [0] * 4096  # compteur par connexion (sans verrou : indices disjoints)


def worker(conn_id, count, host, port, errors, interval=0.0, deadline=None):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        s = smtplib.SMTP(host, port, timeout=60)
        s.starttls(context=ctx)
        i = 0
        next_t = time.time()
        while True:
            if deadline is not None:
                if time.time() >= deadline:
                    break
                next_t += interval
                delay = next_t - time.time()
                if delay > 0:
                    time.sleep(delay)
            elif i >= count:
                break
            s.send_message(make_msg(conn_id * 1_000_000 + i))
            i += 1
            _sent[conn_id] = i
        s.quit()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"conn{conn_id}: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=6000)
    ap.add_argument("--conns", type=int, default=24)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--rate", type=int, default=0, help="débit cible msg/s (mode soutenu)")
    ap.add_argument("--duration", type=int, default=0, help="durée en s (mode soutenu)")
    a = ap.parse_args()

    errors: list = []
    t0 = time.time()

    if a.rate and a.duration:
        # Mode soutenu : injecter à débit constant pendant `duration`.
        interval = a.conns / a.rate  # intervalle entre envois, par connexion
        deadline = t0 + a.duration
        threads = [
            threading.Thread(target=worker, args=(c, 0, a.host, a.port, errors, interval, deadline))
            for c in range(a.conns)
        ]
    else:
        per = a.total // a.conns
        threads = [
            threading.Thread(target=worker, args=(c, per, a.host, a.port, errors)) for c in range(a.conns)
        ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0
    total = sum(_sent[: a.conns])

    print(f"INJECTED={total} conns={a.conns} time={dt:.2f}s rate={total / dt:.0f} msg/s errors={len(errors)}")
    for e in errors[:5]:
        print("  ERR", e)


if __name__ == "__main__":
    main()
