"""API REST MailArchiver-NG (FastAPI).

Expose : authentification AD/LDAP, recherche simple/avancée, export EML,
import EML, transfert SMTP, le tout journalisé (audit) et internationalisé.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

import asyncpg
import aio_pika
from sqlalchemy import func, select, text
from sqlalchemy.exc import InterfaceError, OperationalError

from mailarchiver_common.config import get_settings as _common_settings
import structlog

from mailarchiver_common.logging import configure_logging

configure_logging()
log = structlog.get_logger()

from mailarchiver_common.models import Attachment, FetchSource, Message, User, get_sessionmaker
from mailarchiver_common.fetch import imap_append, run_and_publish
from mailarchiver_common.queue import (
    RAW_DEAD_QUEUE,
    connect,
    consume_restore_jobs,
    declare_dead_letter_topology,
    declare_events_exchange,
    publish_raw_mail,
    publish_restore_job,
)
from mailarchiver_common.storage import BlobStore

from . import audit, i18n, smtp_relay
from .auth import AuthenticatedUser, LdapAuthenticator
from .fetch_sources import FetchSourceRepo
from .fixtures import apply_fixtures
from .settings_store import SettingsStore
from .transfer_jobs import TransferJobRepo
from .mail_access import AccessDenied, MailAccess
from .search import SearchService
from .tokens import create_token, decode_token
from .users import UserRepo

app = FastAPI(title="MailArchiver-NG API", version="0.1.0")

_ldap = LdapAuthenticator()
_users = UserRepo()
_fetch_sources = FetchSourceRepo()
_settings_store = SettingsStore()
_transfer_jobs = TransferJobRepo()
_restore_channel = None  # canal RabbitMQ pour publier les jobs de restauration
_search = SearchService()
_blobs = BlobStore()
_mail = MailAccess(_blobs)


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(asyncpg.PostgresError)
@app.exception_handler(ConnectionError)
async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # Base de données indisponible (ex. redémarrage) → 503 plutôt que 500.
    # Couvre aussi les erreurs asyncpg brutes (CannotConnectNowError « starting up »)
    # qui échappent parfois à l'emballage SQLAlchemy.
    return JSONResponse(status_code=503, content={"detail": "service temporairement indisponible (base de données)"})


@app.on_event("startup")
async def _startup() -> None:
    # Crée le schéma si besoin, puis applique les fixtures d'initialisation
    # (dont le compte admin par défaut admin/admin).
    await _users.ensure_schema()
    await apply_fixtures(_users)

    # File durable des jobs de restauration : un consommateur exécute les jobs.
    # Si l'API redémarre en plein job, le message non acquitté est rejoué.
    global _restore_channel
    connection = await connect()
    _restore_channel = await connection.channel(publisher_confirms=True)
    consume_channel = await connection.channel()
    asyncio.create_task(consume_restore_jobs(consume_channel, _handle_restore_command))
    log.info("api_started", restore_consumer="up")


async def _handle_restore_command(body: bytes) -> None:
    """Exécute un job de restauration (re-dérive la destination, sans secret
    transitant par la file)."""
    cmd = json.loads(body)
    job_id, user_id, rmethod, restrict = cmd["job_id"], cmd["user_id"], cmd["method"], cmd["restrict"]
    if rmethod == "imap":
        imap_cfg = await _users.get_restore_imap(user_id)
        if imap_cfg is None:
            await _transfer_jobs.finish(job_id, "error", "destination IMAP supprimée")
            return
        dest = {"method": "imap", "imap": imap_cfg}
    else:
        smtp_cfg = await _settings_store.get_smtp()
        target = next((u for u in await _users.list() if u["id"] == user_id), None)
        if smtp_cfg is None or target is None or not target.get("email"):
            await _transfer_jobs.finish(job_id, "error", "relais SMTP ou adresse indisponible")
            return
        dest = {"method": "smtp", "smtp": smtp_cfg, "recipient": target["email"]}
    log.info("restore_job_start", job_id=job_id, user_id=user_id, method=rmethod)
    await _run_perimeter_transfer(job_id, restrict, dest)
    log.info("restore_job_done", job_id=job_id)

# L'IHM Vue est servie par le service `web` (Nginx). En dev, on autorise le
# CORS depuis l'origine du frontend si elle diffère (CORS_ORIGINS, séparées par ,).
_cors = os.environ.get("CORS_ORIGINS", "")
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ── Modèles de requête ──────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class AdvancedSearchRequest(BaseModel):
    text: str | None = None
    from_: str | None = None
    to: str | None = None
    participant: str | None = None  # expéditeur OU destinataire (from/to/cc)
    subject: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    has_attachment: bool | None = None
    size_min: int | None = None
    size_max: int | None = None
    search_after: list | None = None  # curseur de pagination (valeurs de tri)


class ForwardRequest(BaseModel):
    message_id: int
    recipients: list[str]


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"  # 'admin' | 'user' | 'auditor'
    email: str | None = None
    display_name: str | None = None
    audited_emails: list[str] | None = None  # adresses auditées (rôle 'auditor')


class PasswordChangeRequest(BaseModel):
    password: str


class SelfPasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class ActiveChangeRequest(BaseModel):
    is_active: bool


class AuditedEmailsRequest(BaseModel):
    audited_emails: list[str]


class EmailChangeRequest(BaseModel):
    email: str | None = None


class RestoreImapRequest(BaseModel):
    host: str | None = None  # vide => efface la destination (retour au SMTP)
    port: int | None = None
    username: str | None = None
    password: str | None = None
    ssl: bool = True
    folder: str | None = "INBOX"


class LegalHoldRequest(BaseModel):
    hold: bool


class SettingsUpdateRequest(BaseModel):
    retention_days: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool | None = None
    smtp_from: str | None = None
    # Serveur SMTP entrant (SMTPD)
    smtpd_host: str | None = None
    smtpd_port: int | None = None
    smtpd_require_starttls: bool | None = None
    smtpd_max_message_bytes: int | None = None


class CreateFetchSourceRequest(BaseModel):
    name: str | None = None
    protocol: str  # 'imap' | 'pop3'
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool = True
    folder: str | None = "INBOX"
    interval_minutes: int = 15
    delete_after: bool = False


# ── Dépendance d'authentification (placeholder : à remplacer par JWT/session) ──


async def current_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser:
    """Valide le JWT du header Authorization: Bearer <token>."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="non authentifié")
    user = decode_token(authorization[len("Bearer ") :].strip())
    if user is None:
        raise HTTPException(status_code=401, detail="jeton invalide ou expiré")
    return user


async def require_admin(user: AuthenticatedUser = Depends(current_user)) -> AuthenticatedUser:
    """Réserve l'accès aux administrateurs (gestion des comptes)."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="accès réservé aux administrateurs")
    return user


def locale_of(request: Request, accept_language: str | None) -> str:
    return i18n.negotiate(accept_language, request.query_params.get("locale"))


def _scope_addresses(user: AuthenticatedUser) -> list[str] | None:
    """Périmètre d'adresses visibles par le compte.

    - admin    -> None  (aucune restriction : voit tout)
    - auditor  -> liste des adresses auditées
    - user     -> sa propre adresse (liste à 1)
    Une liste VIDE signifie « ne voit rien » (jamais None, qui = accès total)."""
    if user.is_admin:
        return None
    if user.role == "auditor":
        return [a.lower() for a in (user.audited_emails or [])]
    return [user.email.lower()] if user.email else []


def _event_visible(event: dict, user: AuthenticatedUser) -> bool:
    """Un admin voit tout ; sinon, le mail doit impliquer une adresse du périmètre."""
    scope = _scope_addresses(user)
    if scope is None:
        return True
    if not scope:
        return False
    parties = {(event.get("from_addr") or "").lower()}
    parties.update(a.lower() for a in (event.get("to_addrs") or []))
    parties.update(a.lower() for a in (event.get("cc_addrs") or []))
    return any(a in parties for a in scope)


# ── Routes ──────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Healthcheck des composants (réservé admin, vrais checks) ────────


async def _check_postgres() -> None:
    async with get_sessionmaker()() as session:
        await session.execute(text("SELECT 1"))


async def _check_rabbitmq() -> None:
    conn = await aio_pika.connect(_common_settings().amqp_url)
    await conn.close()


async def _check_smtp_gateway() -> None:
    port = (await _settings_store.get_all())["smtpd"]["port"]
    reader, writer = await asyncio.open_connection("smtp-gateway", port)
    writer.close()
    await writer.wait_closed()


async def _timed(name: str, label: str, coro) -> dict:
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(coro, timeout=4)
        return {"name": name, "label": label, "status": "ok",
                "latency_ms": round((time.monotonic() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "label": label, "status": "down", "detail": str(exc)[:200]}


async def _rabbit_mgmt(method: str, path: str, body: dict | None = None):
    """Appel générique à l'API de gestion RabbitMQ (port 15672)."""
    import base64
    import urllib.request
    from urllib.parse import urlparse

    p = urlparse(_common_settings().amqp_url)
    url = f"http://{p.hostname}:15672{path}"
    auth = base64.b64encode(f"{p.username}:{p.password}".encode()).decode()

    def _do():
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Basic " + auth)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    return await asyncio.to_thread(_do)


async def _rabbit_queue(queue: str) -> dict:
    return await _rabbit_mgmt("GET", f"/api/queues/%2F/{queue}")


def _peek_eml(payload: str) -> tuple[str, str]:
    """Extrait (sujet, expéditeur) d'un .eml brut pour l'aperçu DLQ."""
    from email import message_from_string

    try:
        m = message_from_string(payload)
        return (m["Subject"] or "(sans sujet)", m["From"] or "")
    except Exception:  # noqa: BLE001
        return ("(illisible)", "")


# ── Métriques Prometheus (scrape interne, non exposé via l'ingress) ──


@app.get("/metrics/prometheus")
async def metrics_prometheus() -> PlainTextResponse:
    """Exposition Prometheus (texte). Destiné au scraping interne (api:8000) ;
    bloqué côté ingress public. Cf. deploy/monitoring/."""
    lines: list[str] = []

    def gauge(name: str, value, help_: str) -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    try:
        raw = await _rabbit_queue(_common_settings().raw_mail_queue) or {}
    except Exception:  # noqa: BLE001
        raw = {}
    try:
        dead = await _rabbit_queue(RAW_DEAD_QUEUE) or {}
    except Exception:  # noqa: BLE001
        dead = {}
    gauge("mailarchiver_raw_mail_backlog", raw.get("messages", 0), "Mails en attente d'archivage")
    gauge("mailarchiver_raw_mail_consumers", raw.get("consumers", 0), "Workers consommateurs actifs")
    gauge("mailarchiver_dlq_messages", dead.get("messages", 0), "Mails en quarantaine (DLQ)")

    est = 0
    try:
        async with get_sessionmaker()() as session:
            est = await session.scalar(
                text("SELECT reltuples::bigint FROM pg_class WHERE relname = 'messages'")
            ) or 0
    except Exception:  # noqa: BLE001
        est = 0
    gauge("mailarchiver_messages_estimate", int(est), "Nombre estimé de messages archivés")
    return PlainTextResponse("\n".join(lines) + "\n")


# ── File de quarantaine (DLQ) — réservé admin ──────────────────────


@app.get("/dlq")
async def dlq_list(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    # Compteur en temps réel (le `messages` de l'API de gestion est échantillonné).
    connection = await connect()
    try:
        channel = await connection.channel()
        # passive : lecture seule (la file quorum est déclarée par les workers ;
        # re-déclarer sans ses arguments lèverait PRECONDITION_FAILED).
        q = await channel.declare_queue(RAW_DEAD_QUEUE, durable=True, passive=True)
        count = q.declaration_result.message_count
    finally:
        await connection.close()
    preview: list[dict] = []
    if count:
        # Lecture non destructive (ack_requeue_true = on remet en file).
        msgs = await _rabbit_mgmt(
            "POST", f"/api/queues/%2F/{RAW_DEAD_QUEUE}/get",
            {"count": 20, "ackmode": "ack_requeue_true", "encoding": "auto", "truncate": 100000},
        )
        for m in msgs or []:
            subj, frm = _peek_eml(m.get("payload", ""))
            preview.append({"subject": subj, "from": frm, "bytes": m.get("payload_bytes", 0)})
    return {"count": count, "preview": preview}


@app.post("/dlq/replay")
async def dlq_replay(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    """Re-injecte les mails en quarantaine dans la file d'archivage."""
    connection = await connect()
    moved = 0
    try:
        channel = await connection.channel()
        await declare_dead_letter_topology(channel)  # déclare la file quorum
        dead = await channel.declare_queue(RAW_DEAD_QUEUE, durable=True, passive=True)
        while moved < 10000:
            msg = await dead.get(no_ack=False, fail=False)
            if msg is None:
                break
            await publish_raw_mail(channel, msg.body)
            await msg.ack()
            moved += 1
    finally:
        await connection.close()
    await audit.record(admin.username, "dlq_replay", detail={"replayed": moved})
    return {"replayed": moved}


@app.post("/dlq/purge")
async def dlq_purge(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    await _rabbit_mgmt("DELETE", f"/api/queues/%2F/{RAW_DEAD_QUEUE}/contents")
    await audit.record(admin.username, "dlq_purge")
    return {"purged": True}


@app.get("/metrics/throughput")
async def metrics_throughput(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    """Taux d'injection (publication) et de traitement (acquittement) en msg/s,
    backlog et file de quarantaine — issus des statistiques RabbitMQ."""
    try:
        main_q = await _rabbit_queue(_common_settings().raw_mail_queue)
        ms = main_q.get("message_stats", {})
        injection = ms.get("publish_details", {}).get("rate", 0.0)
        processing = ms.get("ack_details", {}).get("rate", 0.0)
        backlog = main_q.get("messages", 0)
        consumers = main_q.get("consumers", 0)
        try:
            dead = (await _rabbit_queue("raw_mail_dead")).get("messages", 0)
        except Exception:  # noqa: BLE001
            dead = 0
        return {
            "available": True,
            "injection_rate": round(injection, 1),
            "processing_rate": round(processing, 1),
            "backlog": backlog,
            "dead_letter": dead,
            "consumers": consumers,
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "detail": str(exc)[:200]}


@app.get("/health/components")
async def health_components(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    checks = await asyncio.gather(
        _timed("postgres", "PostgreSQL (métadonnées)", _check_postgres()),
        _timed("rabbitmq", "RabbitMQ (file de messages)", _check_rabbitmq()),
        _timed("opensearch", "OpenSearch (recherche)", _search.ping()),
        _timed("minio", "MinIO / S3 (stockage objet)", _blobs.ping()),
        _timed("smtp_gateway", "Passerelle SMTP entrante", _check_smtp_gateway()),
    )
    overall = "ok" if all(c["status"] == "ok" for c in checks) else "degraded"
    return {"status": overall, "components": checks}


@app.get("/events/stream")
async def events_stream(token: str, request: Request) -> StreamingResponse:
    """Flux SSE des mails archivés en temps réel.

    Auth via ?token=<jwt> (EventSource ne permet pas d'en-tête Authorization).
    Filtrage par rôle : un utilisateur ne reçoit que les mails le concernant.
    """
    user = decode_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="jeton invalide ou expiré")

    async def generator():
        connection = await connect()
        local: asyncio.Queue = asyncio.Queue()

        async def on_message(message) -> None:
            async with message.process():
                await local.put(message.body)

        try:
            channel = await connection.channel()
            exchange = await declare_events_exchange(channel)
            queue = await channel.declare_queue(exclusive=True, auto_delete=True)
            await queue.bind(exchange)
            await queue.consume(on_message)

            yield ": connecté\n\n"  # ouvre le flux
            while True:
                if await request.is_disconnected():
                    break
                try:
                    body = await asyncio.wait_for(local.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # heartbeat anti-timeout
                    continue
                event = json.loads(body)
                if _event_visible(event, user):
                    yield f"data: {json.dumps(event)}\n\n"
        finally:
            await connection.close()

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/auth/login")
async def login(req: LoginRequest, request: Request, accept_language: str | None = Header(default=None)) -> dict:
    loc = locale_of(request, accept_language)
    # 1. Compte local avec verrouillage anti-bruteforce ; 2. LDAP/AD en repli.
    max_attempts = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
    lockout_minutes = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))
    user, status = await _users.authenticate_with_lockout(req.username, req.password, max_attempts, lockout_minutes)
    if status == "locked":
        await audit.record(req.username, "login_locked", source_ip=request.client.host if request.client else None)
        raise HTTPException(status_code=423, detail="compte temporairement verrouillé (trop de tentatives)")
    if user is None:
        user = _ldap.authenticate(req.username, req.password)
    if user is None:
        await audit.record(req.username, "login_failed", source_ip=request.client.host if request.client else None)
        raise HTTPException(status_code=401, detail=i18n.t("auth.failed", loc))
    await audit.record(user.username, "login", source_ip=request.client.host if request.client else None)
    return {
        "username": user.username,
        "role": user.role,
        "is_admin": user.is_admin,
        "token": create_token(user),
    }


@app.post("/auth/change-password")
async def change_own_password(
    req: SelfPasswordChangeRequest, user: AuthenticatedUser = Depends(current_user)
) -> dict:
    """L'utilisateur connecté modifie son propre mot de passe (ancien requis)."""
    try:
        await _users.change_own_password(user.username, req.old_password, req.new_password)
    except KeyError:
        raise HTTPException(status_code=404, detail="compte introuvable")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await audit.record(user.username, "self_password_change")
    return {"updated": True}


# ── Gestion des comptes locaux (réservée admin) ────────────────────


@app.get("/users")
async def list_users(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    return {"users": await _users.list()}


@app.post("/users", status_code=201)
async def create_user(req: CreateUserRequest, admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    try:
        created = await _users.create(
            req.username, req.password, req.role, req.display_name or "", req.email, req.audited_emails
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await audit.record(admin.username, "user_create", target=req.username, detail={"role": req.role})
    return created


@app.patch("/users/{user_id}/password")
async def change_password(
    user_id: int, req: PasswordChangeRequest, admin: AuthenticatedUser = Depends(require_admin)
) -> dict:
    try:
        username = await _users.set_password(user_id, req.password)
    except KeyError:
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await audit.record(admin.username, "user_password_change", target=username)
    return {"updated": user_id}


async def _run_perimeter_transfer(job_id: int, restrict: list[str], dest: dict) -> None:
    """Tâche de fond : pagine le périmètre et restaure chaque mail.

    dest['method'] == 'imap' : dépôt direct dans une boîte IMAP (APPEND, façon
    imapsync) ; sinon envoi via le relais SMTP vers l'adresse du compte."""
    sent, page, cursor = 0, 200, None
    try:
        while True:
            data = await _search.advanced({}, size=page, restrict_addrs=restrict, search_after=cursor)
            results = data["results"]
            if not results:
                break
            raws = []
            for r in results:
                raw, _ = await _mail.get_eml(int(r["id"]), scope=None)  # admin : pas de restriction
                raws.append(raw)
            if dest["method"] == "imap":
                await asyncio.to_thread(imap_append, dest["imap"], raws)
            else:
                for raw in raws:
                    await smtp_relay.send_raw(dest["smtp"], raw, [dest["recipient"]])
            sent += len(raws)
            await _transfer_jobs.set_progress(job_id, sent)
            cursor = data["next_search_after"]
            if cursor is None or len(results) < page:
                break
        await _transfer_jobs.finish(job_id, "done", None)
    except Exception as exc:  # noqa: BLE001
        await _transfer_jobs.finish(job_id, "error", str(exc))


@app.post("/users/{user_id}/transfer-perimeter", status_code=202)
async def transfer_perimeter(
    user_id: int, method: str = "auto", admin: AuthenticatedUser = Depends(require_admin)
) -> dict:
    """Lance EN ARRIÈRE-PLAN le transfert du périmètre de l'auditeur vers son adresse."""
    users = await _users.list()
    target = next((u for u in users if u["id"] == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    if target["role"] not in ("auditor", "user"):
        raise HTTPException(status_code=400, detail="la restauration n'est pas disponible pour ce type de compte")
    if not target.get("email"):
        raise HTTPException(status_code=400, detail="le compte n'a pas d'adresse e-mail")
    # Périmètre : auditeur = ses adresses auditées ; utilisateur = sa propre adresse.
    if target["role"] == "auditor":
        restrict = [a.lower() for a in (target.get("audited_emails") or [])]
    else:
        restrict = [target["email"].lower()]
    if not restrict:
        raise HTTPException(status_code=400, detail="périmètre vide")

    # Méthode : 'imap' (dépôt IMAP), 'smtp' (relais), ou 'auto' (IMAP si configuré).
    imap_cfg = await _users.get_restore_imap(user_id)
    use_imap = method == "imap" or (method == "auto" and imap_cfg)
    if use_imap:
        if imap_cfg is None:
            raise HTTPException(status_code=400, detail="aucune destination IMAP configurée sur ce compte")
        dest = {"method": "imap", "imap": imap_cfg}
        recipient = f"IMAP {imap_cfg['host']}/{imap_cfg.get('folder', 'INBOX')}"
    else:
        smtp_cfg = await _settings_store.get_smtp()
        if smtp_cfg is None:
            raise HTTPException(status_code=400, detail="relais SMTP non configuré (voir Paramètres)")
        dest = {"method": "smtp", "smtp": smtp_cfg, "recipient": target["email"]}
        recipient = target["email"]

    total = (await _search.advanced({}, size=0, restrict_addrs=restrict))["total"]
    job_id = await _transfer_jobs.create(target["username"], recipient, total)

    # Publie le job dans la file durable (exécuté par le consommateur ; survit au
    # redémarrage de l'API). Aucun secret ne transite : la destination est
    # re-dérivée à l'exécution à partir de user_id + méthode.
    await publish_restore_job(
        _restore_channel,
        json.dumps({"job_id": job_id, "user_id": user_id, "method": dest["method"], "restrict": restrict}).encode(),
    )

    await audit.record(admin.username, "perimeter_transfer", target=target["username"],
                       detail={"recipient": recipient, "method": dest["method"], "total": total, "job_id": job_id})
    return {"job_id": job_id, "total": total, "status": "running", "recipient": recipient, "method": dest["method"]}


@app.get("/transfer-jobs")
async def list_transfer_jobs(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    return {"jobs": await _transfer_jobs.list()}


@app.patch("/users/{user_id}/restore-imap")
async def set_restore_imap(
    user_id: int, req: RestoreImapRequest, admin: AuthenticatedUser = Depends(require_admin)
) -> dict:
    """Définit la destination IMAP de restauration du compte (host vide = efface)."""
    try:
        username = await _users.set_restore_imap(user_id, req.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    await audit.record(admin.username, "restore_imap_set", target=username,
                       detail={"host": req.host, "folder": req.folder})
    return {"updated": user_id}


@app.patch("/messages/{message_id}/legal-hold")
async def set_legal_hold(
    message_id: int, req: LegalHoldRequest, admin: AuthenticatedUser = Depends(require_admin)
) -> dict:
    """Conservation légale : exclut le message de la purge et pose/retire un
    legal hold WORM sur le blob (immuabilité indéfinie tant qu'il est actif)."""
    async with get_sessionmaker()() as session:
        msg = await session.scalar(select(Message).where(Message.id == message_id))
        if msg is None:
            raise HTTPException(status_code=404, detail="message introuvable")
        msg.legal_hold = req.hold
        body_sha = msg.body_sha256
        await session.commit()
    await _blobs.set_legal_hold(body_sha, req.hold)
    await audit.record(admin.username, "legal_hold", target=str(message_id), detail={"hold": req.hold})
    return {"id": message_id, "legal_hold": req.hold}


@app.patch("/users/{user_id}/email")
async def change_user_email(
    user_id: int, req: EmailChangeRequest, admin: AuthenticatedUser = Depends(require_admin)
) -> dict:
    try:
        username = await _users.set_email(user_id, req.email)
    except KeyError:
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await audit.record(admin.username, "user_email_change", target=username, detail={"email": req.email})
    return {"updated": user_id}


@app.patch("/users/{user_id}/audited-emails")
async def set_audited_emails(
    user_id: int, req: AuditedEmailsRequest, admin: AuthenticatedUser = Depends(require_admin)
) -> dict:
    try:
        username = await _users.set_audited_emails(user_id, req.audited_emails)
    except KeyError:
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await audit.record(admin.username, "user_audited_emails_change", target=username,
                       detail={"count": len(req.audited_emails)})
    return {"updated": user_id}


@app.patch("/users/{user_id}/active")
async def set_user_active(
    user_id: int, req: ActiveChangeRequest, admin: AuthenticatedUser = Depends(require_admin)
) -> dict:
    users = await _users.list()
    target = next((u for u in users if u["id"] == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    if not req.is_active:
        # Empêche le verrouillage du système.
        if target.get("protected"):
            raise HTTPException(status_code=400, detail="le compte administrateur principal ne peut pas être désactivé")
        if target["role"] == "admin" and await _users.count_admins() <= 1:
            raise HTTPException(status_code=400, detail="impossible de désactiver le dernier administrateur actif")
    username = await _users.set_active(user_id, req.is_active)
    await audit.record(admin.username, "user_activate" if req.is_active else "user_deactivate", target=username)
    return {"updated": user_id, "is_active": req.is_active}


@app.delete("/users/{user_id}")
async def delete_user(user_id: int, admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    # Empêche de supprimer le dernier administrateur (verrouillage du système).
    users = await _users.list()
    target = next((u for u in users if u["id"] == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    if target.get("protected"):
        raise HTTPException(status_code=400, detail="le compte administrateur principal ne peut pas être supprimé")
    if target["role"] == "admin" and await _users.count_admins() <= 1:
        raise HTTPException(status_code=400, detail="impossible de supprimer le dernier administrateur")
    await _users.delete(user_id)
    await audit.record(admin.username, "user_delete", target=target["username"])
    return {"deleted": user_id}


# ── Statistiques d'archivage (réservé admin) ───────────────────────


@app.get("/stats")
async def get_stats(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    async with get_sessionmaker()() as session:
        m = (
            await session.execute(
                select(
                    func.count(Message.id),
                    func.coalesce(func.sum(Message.size_bytes), 0),
                    func.min(Message.date),
                    func.max(Message.date),
                    func.min(Message.signed_at),
                    func.max(Message.signed_at),
                )
            )
        ).one()
        a = (
            await session.execute(
                select(
                    func.count(Attachment.id),
                    func.coalesce(func.sum(Attachment.ref_count), 0),
                    func.coalesce(func.sum(Attachment.size_bytes), 0),
                    func.coalesce(func.sum(Attachment.size_bytes * (Attachment.ref_count - 1)), 0),
                )
            )
        ).one()
        users = await session.scalar(select(func.count(User.id)))
        sources = await session.scalar(select(func.count(FetchSource.id)))

    return {
        "messages": {
            "count": m[0],
            "total_size_bytes": int(m[1]),
            "date_oldest": m[2].isoformat() if m[2] else None,
            "date_newest": m[3].isoformat() if m[3] else None,
            "archived_oldest": m[4].isoformat() if m[4] else None,
            "archived_newest": m[5].isoformat() if m[5] else None,
        },
        "attachments": {
            "stored": a[0],  # PJ distinctes (dédupliquées)
            "references": int(a[1]),  # nombre total de liens mail↔PJ
            "stored_size_bytes": int(a[2]),
            "dedup_saved_bytes": int(a[3]),  # espace économisé par la dédup
        },
        "accounts": users,
        "fetch_sources": sources,
        "indexed": await _search.count_all(),
    }


# ── Paramètres globaux (réservé admin) ─────────────────────────────


@app.get("/settings")
async def get_app_settings(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    return await _settings_store.get_all()


@app.patch("/settings")
async def update_app_settings(req: SettingsUpdateRequest, admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    if req.retention_days is not None and req.retention_days < 0:
        raise HTTPException(status_code=400, detail="la durée de conservation doit être ≥ 0 (0 = illimité)")
    await _settings_store.update(req.model_dump(exclude_none=True))
    await audit.record(admin.username, "settings_update")
    return await _settings_store.get_all()


# ── Sources de collecte IMAP/POP3 (réservé admin) ──────────────────


@app.get("/fetch-sources")
async def list_fetch_sources(admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    return {"sources": await _fetch_sources.list()}


@app.post("/fetch-sources", status_code=201)
async def create_fetch_source(req: CreateFetchSourceRequest, admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    try:
        created = await _fetch_sources.create(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await audit.record(admin.username, "fetch_source_create", target=created["name"])
    return created


@app.delete("/fetch-sources/{source_id}")
async def delete_fetch_source(source_id: int, admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    try:
        await _fetch_sources.delete(source_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="source introuvable")
    await audit.record(admin.username, "fetch_source_delete", target=str(source_id))
    return {"deleted": source_id}


@app.post("/fetch-sources/{source_id}/run")
async def run_fetch_source(source_id: int, admin: AuthenticatedUser = Depends(require_admin)) -> dict:
    """Relève immédiatement la source et réinjecte les mails dans l'archivage."""
    got = await _fetch_sources.get_with_password(source_id)
    if got is None:
        raise HTTPException(status_code=404, detail="source introuvable")
    src, password = got
    connection = await connect()
    try:
        channel = await connection.channel(publisher_confirms=True)
        count = await run_and_publish(channel, src, password)
        status = f"ok: {count} mail(s) relevé(s)"
        await _fetch_sources.update_status(source_id, count, status)
        await audit.record(admin.username, "fetch_source_run", target=src["name"], detail={"count": count})
        return {"fetched": count, "status": status}
    except Exception as exc:  # noqa: BLE001
        await _fetch_sources.update_status(source_id, 0, f"erreur: {exc}")
        raise HTTPException(status_code=502, detail=f"échec de la collecte : {exc}")
    finally:
        await connection.close()


@app.post("/search/advanced")
async def search_advanced(
    req: AdvancedSearchRequest,
    user: AuthenticatedUser = Depends(current_user),
    size: int = 50,
) -> dict:
    filters = req.model_dump(exclude_none=True)
    search_after = filters.pop("search_after", None)
    if "from_" in filters:
        filters["from"] = filters.pop("from_")
    data = await _search.advanced(
        filters, size=size, restrict_addrs=_scope_addresses(user), search_after=search_after
    )
    await _enrich_archived_at(data["results"])
    await audit.record(user.username, "search", detail={"mode": "advanced", "filters": filters, "hits": data["total"]})
    return data


async def _enrich_archived_at(results: list[dict]) -> None:
    """Repli pour les mails indexés AVANT que `archived_at` soit indexé : on
    complète depuis PostgreSQL uniquement les résultats qui en manquent. Pour les
    nouveaux mails (archived_at déjà dans l'index), aucune requête PG n'est faite."""
    missing = [r for r in results if not r.get("archived_at")]
    if not missing:
        return
    ids = [int(r["id"]) for r in missing]
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(Message.id, Message.signed_at).where(Message.id.in_(ids)))).all()
    archived = {str(i): (s.isoformat() if s else None) for i, s in rows}
    for r in missing:
        r["archived_at"] = archived.get(str(r["id"]))


@app.get("/messages/{message_id}")
async def view_message(message_id: int, user: AuthenticatedUser = Depends(current_user)) -> dict:
    """Contenu lisible d'un mail pour consultation dans l'interface."""
    try:
        view = await _mail.get_view(message_id, scope=_scope_addresses(user))
    except KeyError:
        raise HTTPException(status_code=404, detail="message introuvable")
    except AccessDenied:
        raise HTTPException(status_code=403, detail="accès non autorisé à ce message")
    await audit.record(user.username, "view", target=str(message_id))
    return view


@app.get("/messages/{message_id}/eml")
async def export_eml(message_id: int, user: AuthenticatedUser = Depends(current_user)) -> Response:
    try:
        raw, integrity_ok = await _mail.get_eml(message_id, scope=_scope_addresses(user))
    except KeyError:
        raise HTTPException(status_code=404, detail="message introuvable")
    except AccessDenied:
        raise HTTPException(status_code=403, detail="accès non autorisé à ce message")
    await audit.record(user.username, "export", target=str(message_id), detail={"integrity_ok": integrity_ok})
    return Response(
        content=raw,
        media_type="message/rfc822",
        headers={
            "Content-Disposition": f'attachment; filename="message-{message_id}.eml"',
            "X-Archive-Integrity": "valid" if integrity_ok else "INVALID",
        },
    )


@app.post("/messages/forward")
async def forward(req: ForwardRequest, request: Request, accept_language: str | None = Header(default=None),
                  user: AuthenticatedUser = Depends(current_user)) -> dict:
    loc = locale_of(request, accept_language)
    smtp_cfg = await _settings_store.get_smtp()
    if smtp_cfg is None:
        raise HTTPException(status_code=400, detail="relais SMTP non configuré (voir Paramètres)")
    try:
        raw, _ = await _mail.get_eml(req.message_id, scope=_scope_addresses(user))
    except KeyError:
        raise HTTPException(status_code=404, detail="message introuvable")
    except AccessDenied:
        raise HTTPException(status_code=403, detail="accès non autorisé à ce message")
    await smtp_relay.send_raw(smtp_cfg, raw, req.recipients)
    await audit.record(user.username, "forward", target=str(req.message_id), detail={"recipients": req.recipients})
    return {"message": i18n.t("forward.sent", loc)}


@app.post("/import/eml")
async def import_eml(files: list[UploadFile], user: AuthenticatedUser = Depends(current_user)) -> dict:
    """Réinjecte des .eml dans le pipeline d'archivage (via la même queue)."""
    connection = await connect()
    channel = await connection.channel(publisher_confirms=True)
    count = 0
    try:
        for f in files:
            await publish_raw_mail(channel, await f.read())
            count += 1
    finally:
        await connection.close()
    await audit.record(user.username, "import", detail={"count": count})
    return {"imported": count}


@app.on_event("shutdown")
async def _shutdown() -> None:
    await _search.close()
