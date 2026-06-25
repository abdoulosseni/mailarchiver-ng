#!/usr/bin/env python3
"""Suite de non-régression E2E de MailArchiver-NG.

Exerce TOUTES les fonctionnalités contre la stack en marche (docker compose up) :
ingestion SMTP, archivage, crypto/intégrité, déduplication, idempotence,
partitionnement PostgreSQL, recherche + pagination search_after, RBAC/périmètre,
restauration (SMTP & IMAP), DLQ, rétention, sécurité (verrouillage), métriques,
santé, import EML, paramètres, temps réel (SSE).

Usage :
    .venv/bin/python tests/nonreg.py [--base URL] [--quick] [--keep]

  --quick : saute les tests perturbateurs (DLQ coupe MinIO, rétention, restore
            via GreenMail, SSE). Idéal pour une vérification rapide.
  --keep  : ne supprime pas les ressources créées (debug).

Sortie : rapport PASS/FAIL par test + résumé. Code de sortie 1 si un test échoue
(intégrable en CI). Prérequis : stack démarrée, `docker compose` disponible.
"""
from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from email.message import EmailMessage

# ── Configuration (surchargée par les arguments) ───────────────────
BASE = "http://localhost:8080"
SMTP_HOST, SMTP_PORT = "localhost", 2525
OS_URL = "http://localhost:9200"
GREENMAIL_IMAP = ("localhost", 3143)
RUN = str(int(time.time()))  # identifiant de run (isole les ressources créées)
# Marqueur de sujet SANS underscore : l'analyseur standard d'OpenSearch ne
# découpe pas sur « _ » (=> un seul token), donc on utilise un token propre,
# séparé par des espaces, pour des recherches plein-texte fiables.
MARKER = f"nonreg{RUN}"

C_GREEN, C_RED, C_YEL, C_RST = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


class Runner:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n{C_YEL}── {title} ──{C_RST}")

    def check(self, name: str, ok: bool, info: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  {C_GREEN}✓{C_RST} {name}")
        else:
            self.failed += 1
            self.failures.append(name)
            print(f"  {C_RED}✗ {name}{C_RST}  {info}")
        return ok

    def summary(self) -> int:
        total = self.passed + self.failed
        color = C_GREEN if self.failed == 0 else C_RED
        print(f"\n{color}══ {self.passed}/{total} tests OK, {self.failed} échec(s) ══{C_RST}")
        if self.failures:
            print("Échecs : " + ", ".join(self.failures))
        return 1 if self.failed else 0


# ── Helpers HTTP / infra ───────────────────────────────────────────
class Resp:
    def __init__(self, status: int, data, headers: dict):
        self.status, self.data, self.headers = status, data, headers


def api(method: str, path: str, token: str | None = None, body=None) -> Resp:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            ctype = r.headers.get("content-type", "")
            payload = json.loads(raw) if raw and "application/json" in ctype else raw
            return Resp(r.status, payload, dict(r.headers))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return Resp(e.code, json.loads(raw), dict(e.headers))
        except Exception:
            return Resp(e.code, raw, dict(e.headers))


def login(username: str, password: str) -> Resp:
    return api("POST", "/auth/login", body={"username": username, "password": password})


def smtp_send(msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
        s.starttls(context=ctx)
        s.send_message(msg)


def mk_mail(frm: str, to: str, subject: str, body: str, msgid: str, attachment: bytes | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    m["Message-ID"] = msgid
    m["Date"] = "Sun, 21 Jun 2026 09:00:00 +0000"
    m.set_content(body)
    if attachment is not None:
        m.add_attachment(attachment, maintype="application", subtype="octet-stream", filename="shared.bin")
    return m


def compose(*args: str, check: bool = False) -> str:
    out = subprocess.run(["docker", "compose", *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(out.stderr)
    return (out.stdout or "").strip()


def psql(sql: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "mailarchiver", "-d", "mailarchiver", "-tAc", sql).strip()


def queue_count(queue: str) -> int:
    out = compose("exec", "-T", "rabbitmq", "rabbitmqctl", "list_queues", "name", "messages")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == queue:
            return int(parts[1])
    return 0


def wait_queue_empty(timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if queue_count("raw_mail") == 0:
            time.sleep(2)  # laisse l'indexation OpenSearch se terminer
            return True
        time.sleep(2)
    return False


def search(token: str, filters: dict, size: int = 50, search_after=None) -> dict:
    body = dict(filters)
    if search_after:
        body["search_after"] = search_after
    return api("POST", f"/search/advanced?size={size}", token=token, body=body).data


# ── Tests ──────────────────────────────────────────────────────────
def t_health_metrics(r: Runner, admin: str) -> None:
    r.section("Santé & métriques")
    r.check("GET /health = 200", api("GET", "/health").status == 200)
    hc = api("GET", "/health/components", token=admin)
    comps = {c["name"]: c["status"] for c in (hc.data.get("components", []) if isinstance(hc.data, dict) else [])}
    r.check("santé globale = ok", isinstance(hc.data, dict) and hc.data.get("status") == "ok", str(comps))
    for name in ("postgres", "rabbitmq", "opensearch", "minio", "smtp_gateway"):
        r.check(f"composant {name} = ok", comps.get(name) == "ok")
    r.check("GET /metrics/throughput = 200", api("GET", "/metrics/throughput", token=admin).status == 200)
    r.check("scrape Prometheus public bloqué (403)", api("GET", "/metrics/prometheus").status == 403)
    metrics = compose("exec", "-T", "api", "python", "-c",
                      "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/metrics/prometheus').read().decode())")
    r.check("métriques Prometheus exposées en interne", "mailarchiver_raw_mail_backlog" in metrics)


def t_auth_security(r: Runner) -> list[int]:
    r.section("Authentification & sécurité")
    created: list[int] = []
    admin = login("admin", "admin")
    r.check("login admin = 200 + token", admin.status == 200 and "token" in (admin.data or {}))
    r.check("login mauvais mot de passe = 401", login("admin", "WRONG").status == 401)
    tok = admin.data["token"]

    # Verrouillage de compte après N échecs
    u = f"nonreg_lock_{RUN}"
    cr = api("POST", "/users", token=tok, body={"username": u, "password": "good-pw", "role": "user", "email": f"lock_{RUN}@t"})
    if cr.status in (200, 201):
        uid = next((x["id"] for x in api("GET", "/users", token=tok).data["users"] if x["username"] == u), None)
        if uid:
            created.append(uid)
        codes = [login(u, "bad").status for _ in range(5)]
        r.check("5 échecs renvoient 401", all(c == 401 for c in codes), str(codes))
        r.check("bon mot de passe après 5 échecs = 423 (verrouillé)", login(u, "good-pw").status == 423)
        psql(f"UPDATE users SET locked_until=NULL, failed_logins=0 WHERE username='{u}'")
        r.check("déverrouillage admin → connexion 200", login(u, "good-pw").status == 200)

    # RBAC : un non-admin n'accède pas aux endpoints admin
    pw = login(u, "good-pw")
    if pw.status == 200:
        utok = pw.data["token"]
        r.check("non-admin GET /users = 403", api("GET", "/users", token=utok).status == 403)
        r.check("non-admin GET /stats = 403", api("GET", "/stats", token=utok).status == 403)
    return created


def inject_dataset(r: Runner) -> None:
    r.section("Ingestion SMTP (jeu de données de test)")
    # Contenu UNIQUE par run (sinon la dédup globale par sha256 accumule le
    # ref_count entre exécutions successives).
    shared = (f"NONREG-{RUN}-".encode() * 300)[:4096]
    # 12 mails étiquetés ; #0 et #1 partagent une PJ (dédup).
    for i in range(12):
        att = shared if i in (0, 1) else None
        smtp_send(mk_mail(f"s{i}_{RUN}@nonreg.test", f"r{i}_{RUN}@nonreg.test",
                          f"{MARKER} mail {i:02d}", f"corps {i}", f"<nr-{RUN}-{i}@t>", att))
    # Doublon EXACT du mail #0 (idempotence)
    smtp_send(mk_mail(f"s0_{RUN}@nonreg.test", f"r0_{RUN}@nonreg.test",
                      f"{MARKER} mail 00", "corps 0", f"<nr-{RUN}-0@t>", shared))
    r.check("file vidée (mails traités)", wait_queue_empty())


def t_archival_search(r: Runner, admin: str) -> None:
    r.section("Archivage & recherche")
    res = search(admin, {"subject": MARKER}, size=50)
    r.check("12 mails archivés et trouvés (doublon dédupliqué)", res.get("total") == 12,
            f"total={res.get('total')}")
    res_from = search(admin, {"from_": f"s3_{RUN}@nonreg.test"})
    r.check("recherche par expéditeur", res_from.get("total") == 1, f"total={res_from.get('total')}")
    res_att = search(admin, {"subject": MARKER, "has_attachment": True})
    r.check("filtre pièce jointe (2 mails)", res_att.get("total") == 2, f"total={res_att.get('total')}")


def t_idempotency_dedup(r: Runner) -> None:
    r.section("Idempotence & déduplication")
    n_subj = int(psql(f"SELECT count(*) FROM messages WHERE subject='{MARKER} mail 00'") or 0)
    r.check("doublon exact archivé une seule fois", n_subj == 1, f"count={n_subj}")
    ref = psql("SELECT ref_count FROM attachments WHERE filename='shared.bin' ORDER BY id DESC LIMIT 1")
    r.check("PJ partagée : ref_count = 2", ref == "2", f"ref_count={ref}")
    links = psql("SELECT count(*) FROM message_attachments")
    r.check("liens d'association créés (sans FK vers messages)", int(links or 0) >= 2, f"links={links}")


def t_partitioning(r: Runner) -> None:
    r.section("Partitionnement PostgreSQL")
    kind = psql("SELECT relkind FROM pg_class WHERE relname='messages'")
    r.check("table messages partitionnée", kind == "p", f"relkind={kind}")
    parts = psql("SELECT count(*) FROM pg_inherits WHERE inhparent='messages'::regclass")
    r.check("partitions créées (≥ default + mois courant)", int(parts or 0) >= 2, f"parts={parts}")
    cur = psql("SELECT count(*) FROM messages WHERE date_trunc('month',signed_at)=date_trunc('month',now())")
    r.check("mails routés dans la partition du mois courant", int(cur or 0) >= 12, f"cur={cur}")
    msgs = int(psql("SELECT count(*) FROM messages") or 0)
    dedup = int(psql("SELECT count(*) FROM message_dedup") or 0)
    r.check("message_dedup cohérent avec messages", msgs == dedup, f"messages={msgs} dedup={dedup}")


def t_integrity_view(r: Runner, admin: str) -> None:
    r.section("Crypto / intégrité & consultation")
    res = search(admin, {"subject": MARKER}, size=1)
    if not r.check("message trouvé pour consultation", res.get("results"), str(res.get("total"))):
        return
    mid = res["results"][0]["id"]
    view = api("GET", f"/messages/{mid}", token=admin)
    r.check("GET /messages/{id} = 200 (vue parsée)", view.status == 200 and isinstance(view.data, dict))
    eml = api("GET", f"/messages/{mid}/eml", token=admin)
    integrity = eml.headers.get("X-Archive-Integrity") or eml.headers.get("x-archive-integrity")
    r.check("export EML = 200", eml.status == 200)
    r.check("signature vérifiée (X-Archive-Integrity: valid)", integrity == "valid", f"integrity={integrity}")


def t_pagination(r: Runner, admin: str) -> None:
    r.section("Pagination search_after")
    p1 = search(admin, {"subject": MARKER}, size=5)
    cur = p1.get("next_search_after")
    r.check("page 1 : 5 résultats + curseur", len(p1.get("results", [])) == 5 and cur, str(cur))
    p2 = search(admin, {"subject": MARKER}, size=5, search_after=cur)
    ids1 = {x["id"] for x in p1.get("results", [])}
    ids2 = {x["id"] for x in p2.get("results", [])}
    r.check("page 2 distincte (aucun chevauchement)", ids2 and not (ids1 & ids2), f"overlap={ids1 & ids2}")


def t_rbac_scoping(r: Runner, admin: str) -> list[int]:
    r.section("RBAC & périmètre")
    created: list[int] = []
    # Utilisateur : périmètre = sa propre adresse (= 1 mail, l'expéditeur s0).
    uname = f"nonreg_user_{RUN}"
    api("POST", "/users", token=admin, body={"username": uname, "password": "pw", "role": "user", "email": f"s0_{RUN}@nonreg.test"})
    # Auditeur : périmètre = 2 adresses auditées (= mails #1 et #2).
    aname = f"nonreg_audit_{RUN}"
    api("POST", "/users", token=admin, body={"username": aname, "password": "pw", "role": "auditor",
        "email": f"audit_{RUN}@nonreg.test", "audited_emails": [f"s1_{RUN}@nonreg.test", f"s2_{RUN}@nonreg.test"]})
    users = {x["username"]: x["id"] for x in api("GET", "/users", token=admin).data["users"]}
    created += [users[n] for n in (uname, aname) if n in users]

    ut = login(uname, "pw")
    if ut.status == 200:
        res = search(ut.data["token"], {})
        froms = {x.get("from_addr") for x in res.get("results", [])}
        r.check("utilisateur : périmètre limité à sa propre adresse",
                res.get("total") == 1 and froms <= {f"s0_{RUN}@nonreg.test"}, f"total={res.get('total')} froms={froms}")
    at = login(aname, "pw")
    if at.status == 200:
        res = search(at.data["token"], {})
        froms = {x.get("from_addr") for x in res.get("results", [])}
        r.check("auditeur : périmètre limité aux adresses auditées (2 mails)",
                res.get("total") == 2 and froms <= {f"s1_{RUN}@nonreg.test", f"s2_{RUN}@nonreg.test"},
                f"total={res.get('total')} froms={froms}")
    return created


def t_import_eml(r: Runner, admin: str) -> None:
    r.section("Import EML")
    marker = f"nonregimp{RUN}"
    eml = mk_mail(f"import_{RUN}@nonreg.test", "x@t", marker, "importé", f"<imp-{RUN}@t>").as_bytes()
    # L'endpoint attend files: list[UploadFile] → multipart/form-data, champ « files ».
    boundary = "----nonreg" + RUN
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="files"; filename="import.eml"\r\n',
        b"Content-Type: message/rfc822\r\n\r\n", eml, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(BASE + "/import/eml", data=body, method="POST")
    req.add_header("Authorization", "Bearer " + admin)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ok = resp.status in (200, 201, 202)
    except urllib.error.HTTPError as e:
        ok = e.code in (200, 201, 202)
    wait_queue_empty()
    res = search(admin, {"subject": marker})
    r.check("mail importé via EML archivé", ok and res.get("total") == 1, f"http_ok={ok} total={res.get('total')}")


def t_settings(r: Runner, admin: str) -> None:
    r.section("Paramètres")
    before = api("GET", "/settings", token=admin).data
    api("PATCH", "/settings", token=admin, body={"retention_days": 400})
    after = api("GET", "/settings", token=admin).data
    r.check("modification de la rétention persistée", str(after.get("retention_days")) == "400",
            f"got={after.get('retention_days')}")
    # Restaure la valeur initiale.
    api("PATCH", "/settings", token=admin, body={"retention_days": before.get("retention_days", 365)})


def t_sse(r: Runner, admin: str) -> None:
    r.section("Temps réel (SSE)")
    received: list[str] = []

    def listen():
        try:
            req = urllib.request.Request(BASE + f"/events/stream?token={admin}")
            with urllib.request.urlopen(req, timeout=25) as resp:
                deadline = time.time() + 20
                while time.time() < deadline:
                    line = resp.readline()
                    if not line:
                        break
                    if line.startswith(b"data:") and RUN.encode() in line:
                        received.append(line.decode())
                        return
        except Exception:
            pass

    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(2)
    smtp_send(mk_mail(f"sse_{RUN}@nonreg.test", "x@t", f"nonreg_sse_{RUN}", "live", f"<sse-{RUN}@t>"))
    th.join(timeout=22)
    r.check("événement SSE reçu en temps réel", bool(received))
    wait_queue_empty()


def _greenmail_up() -> bool:
    net = compose("ps", "--format", "{{.Name}}")
    network = "mailarchiver-ng_default"
    subprocess.run(["docker", "rm", "-f", "greenmail"], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", "greenmail", "--network", network,
                    "-p", "3025:3025", "-p", "3143:3143",
                    "-e", "GREENMAIL_OPTS=-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.auth.disabled",
                    "greenmail/standalone:2.1.0"], capture_output=True)
    for _ in range(15):
        try:
            import imaplib
            imaplib.IMAP4(*GREENMAIL_IMAP).logout()
            return True
        except Exception:
            time.sleep(2)
    return False


def _greenmail_count(mailbox: str) -> int:
    import imaplib
    try:
        m = imaplib.IMAP4(*GREENMAIL_IMAP)
        m.login(mailbox, "x")
        m.select("INBOX")
        _, d = m.search(None, "ALL")
        n = len(d[0].split())
        m.logout()
        return n
    except Exception:
        return -1


def t_restore(r: Runner, admin: str) -> None:
    r.section("Restauration (SMTP & IMAP via GreenMail)")
    if not _greenmail_up():
        r.check("GreenMail démarré", False, "image indisponible — test sauté")
        return
    r.check("GreenMail démarré", True)
    mailbox = f"s0_{RUN}@nonreg.test"  # périmètre = 1 mail (l'utilisateur nonreg_user)
    uid = next((x["id"] for x in api("GET", "/users", token=admin).data["users"]
                if x["username"] == f"nonreg_user_{RUN}"), None)
    if uid is None:
        r.check("utilisateur de test présent", False)
        return

    # Restauration SMTP : relais → GreenMail
    api("PATCH", "/settings", token=admin, body={"smtp_host": "greenmail", "smtp_port": 3025,
        "smtp_starttls": False, "smtp_from": "archiver@nonreg"})
    job = api("POST", f"/users/{uid}/transfer-perimeter?method=smtp", token=admin)
    r.check("job de restauration SMTP créé (202)", job.status == 202 and "job_id" in (job.data or {}))
    _wait_job_done(job.data.get("job_id"), admin)
    r.check("restauration SMTP : mail déposé dans GreenMail", _greenmail_count(mailbox) >= 1,
            f"recus={_greenmail_count(mailbox)}")

    # Restauration IMAP (APPEND) vers une autre boîte
    api("PATCH", f"/users/{uid}/restore-imap", token=admin, body={"host": "greenmail", "port": 3143,
        "username": f"imapbox_{RUN}@nonreg.test", "password": "x", "ssl": False, "folder": "INBOX"})
    job2 = api("POST", f"/users/{uid}/transfer-perimeter?method=imap", token=admin)
    r.check("job de restauration IMAP créé (202)", job2.status == 202)
    _wait_job_done(job2.data.get("job_id"), admin)
    r.check("restauration IMAP : mail déposé via APPEND", _greenmail_count(f"imapbox_{RUN}@nonreg.test") >= 1,
            f"recus={_greenmail_count(f'imapbox_{RUN}@nonreg.test')}")
    api("PATCH", "/settings", token=admin, body={"smtp_host": ""})


def _wait_job_done(job_id, admin: str, timeout: int = 40) -> None:
    if not job_id:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = api("GET", "/transfer-jobs", token=admin).data.get("jobs", [])
        j = next((x for x in jobs if x["id"] == job_id), None)
        if j and j["status"] in ("done", "error"):
            return
        time.sleep(2)


def t_dlq(r: Runner, admin: str) -> None:
    r.section("Quarantaine (DLQ)")
    compose("stop", "minio")
    smtp_send(mk_mail(f"poison_{RUN}@t", "x@t", f"nonregpoison{RUN}", "poison", f"<poison-{RUN}@t>"))
    deadline = time.time() + 60
    while time.time() < deadline and queue_count("raw_mail_dead") == 0:
        time.sleep(3)
    dlq = api("GET", "/dlq", token=admin).data
    r.check("mail empoisonné mis en quarantaine", isinstance(dlq, dict) and dlq.get("count", 0) >= 1,
            f"count={dlq.get('count') if isinstance(dlq, dict) else dlq}")
    compose("start", "minio")
    time.sleep(8)
    rep = api("POST", "/dlq/replay", token=admin).data
    r.check("rejeu de la DLQ", isinstance(rep, dict) and rep.get("replayed", 0) >= 1, str(rep))
    wait_queue_empty()
    res = search(admin, {"subject": f"nonregpoison{RUN}"})
    r.check("mail rejoué puis archivé", res.get("total") == 1, f"total={res.get('total')}")


def t_retention(r: Runner, admin: str) -> None:
    r.section("Rétention (purge)")
    # Antidater 1 mail de test → éligible à la purge (rétention 365 j).
    hash_purge = psql(f"SELECT archive_hash FROM messages WHERE subject='{MARKER} mail 11' LIMIT 1")
    psql(f"UPDATE messages SET signed_at = now() - interval '2 years' WHERE subject='{MARKER} mail 11'")
    before = int(psql("SELECT count(*) FROM messages") or 0)
    compose("exec", "-T", "retention-worker", "python", "-c",
            "import asyncio;from retention_worker.main import run_retention;asyncio.run(run_retention())")
    after = int(psql("SELECT count(*) FROM messages") or 0)
    r.check("message expiré purgé (PK composite)", after == before - 1, f"{before}->{after}")
    remain = psql(f"SELECT count(*) FROM message_dedup WHERE archive_hash='{hash_purge}'")
    r.check("ligne message_dedup du purgé nettoyée", remain == "0", f"reste={remain}")


def cleanup(admin: str, user_ids: list[int]) -> None:
    for uid in user_ids:
        api("DELETE", f"/users/{uid}", token=admin)
    api("PATCH", "/settings", token=admin, body={"smtp_host": ""})
    subprocess.run(["docker", "rm", "-f", "greenmail"], capture_output=True)


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--quick", action="store_true", help="saute DLQ/rétention/restore/SSE")
    ap.add_argument("--keep", action="store_true", help="ne pas supprimer les ressources créées")
    args = ap.parse_args()
    BASE = args.base

    print(f"MailArchiver-NG — non-régression (run {RUN}) sur {BASE}")
    admin_resp = login("admin", "admin")
    if admin_resp.status != 200:
        print(f"{C_RED}Impossible de se connecter en admin — stack démarrée ?{C_RST}")
        return 2
    admin = admin_resp.data["token"]

    r = Runner()
    created: list[int] = []
    try:
        t_health_metrics(r, admin)
        created += t_auth_security(r)
        inject_dataset(r)
        t_archival_search(r, admin)
        t_idempotency_dedup(r)
        t_partitioning(r)
        t_integrity_view(r, admin)
        t_pagination(r, admin)
        created += t_rbac_scoping(r, admin)
        t_import_eml(r, admin)
        t_settings(r, admin)
        if not args.quick:
            t_restore(r, admin)
            t_sse(r, admin)
            t_dlq(r, admin)
            t_retention(r, admin)
    finally:
        if not args.keep:
            cleanup(admin, created)
    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
