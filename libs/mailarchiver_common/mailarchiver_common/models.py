"""Modèle de données relationnel (PostgreSQL via SQLAlchemy 2.0 async).

Déduplication des pièces jointes : une PJ = une ligne `Attachment` (clé = sha256),
référencée par N messages via `message_attachments`. `ref_count` permet le
garbage-collection lors de la purge de rétention.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import get_settings


class Base(DeclarativeBase):
    pass


# `messages` est partitionnée (RANGE sur signed_at) → sa PK est composite
# (id, signed_at) et aucune FK simple ne peut la cibler. La table d'association
# ne porte donc PAS de FK vers messages (intégrité gérée par l'ORM/applicatif) ;
# elle conserve la FK vers attachments. Les jointures de relation sont explicites.
message_attachments = Table(
    "message_attachments",
    Base.metadata,
    Column("message_id", BigInteger, primary_key=True),
    Column("attachment_id", ForeignKey("attachments.id", ondelete="RESTRICT"), primary_key=True),
)


class Message(Base):
    """Table **partitionnée par mois** (RANGE sur signed_at). La clé de partition
    doit faire partie de la PK → PK composite (id, signed_at). L'unicité globale
    d'`archive_hash` (idempotence) est portée par la table non partitionnée
    `MessageDedup` (une contrainte UNIQUE sur une table partitionnée devrait
    inclure la clé de partition, ce qui ne garantirait pas l'unicité globale).

    Le DDL réel est créé par `ensure_schema()` (PARTITION BY RANGE) ; `create_all`
    ne crée PAS cette table. Garder les colonnes ci-dessous synchronisées."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(998), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    from_addr: Mapped[str] = mapped_column(String(998), index=True)
    to_addrs: Mapped[list] = mapped_column(JSONB, default=list)
    cc_addrs: Mapped[list] = mapped_column(JSONB, default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    # Référence vers le blob du corps (sealed) dans le BlobStore
    body_sha256: Mapped[str] = mapped_column(String(64))

    # Intégrité / inviolabilité (unicité globale via MessageDedup)
    archive_hash: Mapped[str] = mapped_column(String(64), index=True)
    signature: Mapped[str] = mapped_column(Text)

    # Rétention
    retention_class: Mapped[str] = mapped_column(String(64), default="default", index=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    legal_hold: Mapped[bool] = mapped_column(default=False)

    # Jointures explicites : message_attachments n'a pas de FK vers messages.
    attachments: Mapped[list["Attachment"]] = relationship(
        secondary=message_attachments,
        primaryjoin="Message.id == message_attachments.c.message_id",
        secondaryjoin="Attachment.id == message_attachments.c.attachment_id",
        back_populates="messages",
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    ref_count: Mapped[int] = mapped_column(Integer, default=0)

    messages: Mapped[list[Message]] = relationship(
        secondary=message_attachments,
        primaryjoin="Attachment.id == message_attachments.c.attachment_id",
        secondaryjoin="Message.id == message_attachments.c.message_id",
        back_populates="attachments",
    )


class MessageDedup(Base):
    """Index d'idempotence global (non partitionné) : garantit l'unicité
    d'`archive_hash` à travers toutes les partitions de `messages`. Un mail
    rejoué retrouve ici (message_id, signed_at) pour ré-indexation."""

    __tablename__ = "message_dedup"

    archive_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    signed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class User(Base):
    """Compte local. role ∈ {admin, user}. Mot de passe haché (bcrypt).

    L'authentification LDAP/AD reste possible en parallèle ; cette table gère
    les comptes locaux (dont l'admin amorcé par défaut).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # 'admin' | 'user' | 'auditor'
    # Adresse e-mail : sert à filtrer les mails accessibles à un utilisateur
    # (mails envoyés depuis cette adresse ou la mentionnant en to/cc).
    email: Mapped[str | None] = mapped_column(String(320), index=True, nullable=True)
    # Pour les comptes 'auditor' : liste des adresses dont l'auditeur peut lire
    # les mails (définie par l'admin à la création).
    audited_emails: Mapped[list] = mapped_column(JSONB, default=list)
    # Destination IMAP de restauration (façon imapsync) : {host, port, username,
    # password_enc, ssl, folder}. Mot de passe chiffré. Null = restauration SMTP.
    restore_imap: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    # Durcissement : verrouillage après trop d'échecs de connexion.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TransferJob(Base):
    """Job asynchrone de transfert du périmètre d'un auditeur via SMTP."""

    __tablename__ = "transfer_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auditor_username: Mapped[str] = mapped_column(String(255))
    recipient: Mapped[str] = mapped_column(String(320))
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    """Paramètres globaux de l'application (clé/valeur), modifiables par l'admin."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class FetchSource(Base):
    """Source de collecte IMAP/POP3 relevée périodiquement pour archivage.

    Le mot de passe est chiffré au repos (AES-256 via la clé maître)."""

    __tablename__ = "fetch_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    protocol: Mapped[str] = mapped_column(String(8))  # 'imap' | 'pop3'
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(320))
    password_enc: Mapped[str] = mapped_column(Text)  # chiffré (clé maître)
    use_ssl: Mapped[bool] = mapped_column(default=True)
    folder: Mapped[str] = mapped_column(String(255), default="INBOX")  # IMAP
    interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    delete_after: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_run: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_count: Mapped[int] = mapped_column(Integer, default=0)


class AuditEvent(Base):
    """Journal append-only des événements système et actions utilisateur."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(255), index=True)  # utilisateur ou "system"
    action: Mapped[str] = mapped_column(String(64), index=True)  # login, search, view, export, forward, purge
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


# ── Initialisation du schéma (avec `messages` partitionnée) ─────────

# `messages` + `message_attachments` sont créées en SQL brut (partitionnement /
# pas de FK), les autres tables via create_all. Chaque énoncé séparément (asyncpg
# n'exécute qu'une commande par appel).
_DDL_MESSAGES = [
    """
    CREATE TABLE IF NOT EXISTS messages (
        id              BIGINT       GENERATED BY DEFAULT AS IDENTITY,
        signed_at       TIMESTAMPTZ  NOT NULL,
        message_id      VARCHAR(998) NOT NULL,
        date            TIMESTAMPTZ  NOT NULL,
        from_addr       VARCHAR(998) NOT NULL,
        to_addrs        JSONB        NOT NULL DEFAULT '[]',
        cc_addrs        JSONB        NOT NULL DEFAULT '[]',
        subject         TEXT         NOT NULL DEFAULT '',
        size_bytes      BIGINT       NOT NULL DEFAULT 0,
        body_sha256     VARCHAR(64)  NOT NULL,
        archive_hash    VARCHAR(64)  NOT NULL,
        signature       TEXT         NOT NULL,
        retention_class VARCHAR(64)  NOT NULL DEFAULT 'default',
        expires_at      TIMESTAMPTZ,
        legal_hold      BOOLEAN      NOT NULL DEFAULT false,
        PRIMARY KEY (id, signed_at)
    ) PARTITION BY RANGE (signed_at)
    """,
    "CREATE INDEX IF NOT EXISTS ix_messages_message_id ON messages (message_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_date ON messages (date)",
    "CREATE INDEX IF NOT EXISTS ix_messages_from_addr ON messages (from_addr)",
    "CREATE INDEX IF NOT EXISTS ix_messages_archive_hash ON messages (archive_hash)",
    "CREATE INDEX IF NOT EXISTS ix_messages_retention_class ON messages (retention_class)",
    "CREATE INDEX IF NOT EXISTS ix_messages_expires_at ON messages (expires_at)",
    # Filet de sécurité : toute date hors partition connue atterrit ici.
    "CREATE TABLE IF NOT EXISTS messages_default PARTITION OF messages DEFAULT",
    """
    CREATE TABLE IF NOT EXISTS message_attachments (
        message_id    BIGINT NOT NULL,
        attachment_id BIGINT NOT NULL REFERENCES attachments(id) ON DELETE RESTRICT,
        PRIMARY KEY (message_id, attachment_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_msgatt_message ON message_attachments (message_id)",
    # Création des partitions mensuelles à la demande (mois courant + à l'avance).
    """
    CREATE OR REPLACE FUNCTION ensure_message_partition(p_month date)
    RETURNS void LANGUAGE plpgsql AS $fn$
    DECLARE
        s date := date_trunc('month', p_month)::date;
        e date := (date_trunc('month', p_month) + interval '1 month')::date;
        part text := format('messages_%s', to_char(date_trunc('month', p_month), 'YYYY_MM'));
    BEGIN
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF messages FOR VALUES FROM (%L) TO (%L)',
            part, s, e);
    END $fn$
    """,
]


async def ensure_schema(engine) -> None:
    """Crée le schéma de façon idempotente, `messages` étant partitionnée.

    Sérialisé par un verrou consultatif Postgres (plusieurs services démarrent en
    parallèle). create_all pour les tables simples ; SQL brut pour les tables
    partitionnées/sans FK ; partitions du mois courant + 2 mois d'avance."""
    skip = {"messages", "message_attachments"}
    simple_tables = [t for t in Base.metadata.sorted_tables if t.name not in skip]
    async with engine.begin() as conn:
        await conn.exec_driver_sql("SELECT pg_advisory_xact_lock(7272741)")
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=simple_tables))
        for stmt in _DDL_MESSAGES:
            await conn.exec_driver_sql(stmt)
        for offset in ("0", "1", "2"):
            await conn.execute(
                text(f"SELECT ensure_message_partition((now() + interval '{offset} month')::date)")
            )

