"""Gestion des comptes locaux : amorçage admin, authentification, CRUD.

- Mots de passe hachés avec bcrypt (jamais stockés en clair).
- Rôles : 'admin' (gestion comptes + tout) et 'user' (recherche/consultation).
- Amorçage : au premier démarrage, si aucun compte n'existe, on crée l'admin
  par défaut depuis ADMIN_USERNAME / ADMIN_PASSWORD (défaut admin / admin).
"""

from __future__ import annotations

import datetime as dt
import os

import bcrypt
from sqlalchemy import func, select

from mailarchiver_common import crypto
from mailarchiver_common.models import Base, User, ensure_schema as _ensure_schema, get_sessionmaker

from .auth import AuthenticatedUser

VALID_ROLES = {"admin", "user", "auditor"}

# Compte administrateur principal (amorcé par les fixtures) : non supprimable.
PROTECTED_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def _restore_imap_public(cfg: dict | None) -> dict | None:
    """Vue publique de la destination IMAP (sans mot de passe)."""
    if not cfg:
        return None
    return {
        "host": cfg.get("host"),
        "port": cfg.get("port"),
        "username": cfg.get("username"),
        "ssl": cfg.get("ssl", True),
        "folder": cfg.get("folder", "INBOX"),
        "password_set": bool(cfg.get("password_enc")),
    }


def _to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "role": u.role,
        "display_name": u.display_name,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "audited_emails": u.audited_emails or [],
        "restore_imap": _restore_imap_public(u.restore_imap),
        # Compte administrateur principal : non supprimable (UI + API).
        "protected": u.username == PROTECTED_ADMIN_USERNAME,
    }


class UserRepo:
    def __init__(self) -> None:
        self._sm = get_sessionmaker()

    async def ensure_schema(self) -> None:
        """Crée le schéma si absent (messages partitionnée ; verrou consultatif)."""
        await _ensure_schema(self._sm.kw["bind"])

    async def count(self) -> int:
        async with self._sm() as session:
            return await session.scalar(select(func.count()).select_from(User))

    async def authenticate_with_lockout(
        self, username: str, password: str, max_attempts: int, lockout_minutes: int
    ) -> tuple[AuthenticatedUser | None, str]:
        """Auth avec verrouillage : retourne (user, status) où status ∈ ok|bad|locked."""
        now = dt.datetime.now(dt.timezone.utc)
        async with self._sm() as session:
            u = await session.scalar(
                select(User).where(User.username == username, User.is_active.is_(True))
            )
            if u is None:
                return None, "bad"  # ne révèle pas l'existence du compte
            if u.locked_until and u.locked_until > now:
                return None, "locked"
            if verify_password(password, u.password_hash):
                if u.failed_logins or u.locked_until:
                    u.failed_logins = 0
                    u.locked_until = None
                    await session.commit()
                return (
                    AuthenticatedUser(
                        username=u.username,
                        display_name=u.display_name or u.username,
                        role=u.role,
                        is_admin=(u.role == "admin"),
                        email=u.email,
                        audited_emails=u.audited_emails or [],
                    ),
                    "ok",
                )
            u.failed_logins = (u.failed_logins or 0) + 1
            if u.failed_logins >= max_attempts:
                u.locked_until = now + dt.timedelta(minutes=lockout_minutes)
                u.failed_logins = 0
            await session.commit()
            return None, "bad"

    async def authenticate(self, username: str, password: str) -> AuthenticatedUser | None:
        async with self._sm() as session:
            u = await session.scalar(
                select(User).where(User.username == username, User.is_active.is_(True))
            )
            if u is None or not verify_password(password, u.password_hash):
                return None
            return AuthenticatedUser(
                username=u.username,
                display_name=u.display_name or u.username,
                role=u.role,
                is_admin=(u.role == "admin"),
                email=u.email,
                audited_emails=u.audited_emails or [],
            )

    async def list(self) -> list[dict]:
        async with self._sm() as session:
            rows = (await session.scalars(select(User).order_by(User.id))).all()
            return [_to_dict(u) for u in rows]

    async def create(
        self,
        username: str,
        password: str,
        role: str,
        display_name: str = "",
        email: str | None = None,
        audited_emails: list | None = None,
    ) -> dict:
        if role not in VALID_ROLES:
            raise ValueError(f"rôle invalide (attendu : {', '.join(sorted(VALID_ROLES))})")
        if not username or not password:
            raise ValueError("identifiant et mot de passe requis")
        # Normalise les adresses auditées (minuscules, sans doublon ni vide).
        audited = sorted({a.strip().lower() for a in (audited_emails or []) if a.strip()})
        if role == "auditor" and not (email or "").strip():
            raise ValueError("un compte auditeur requiert une adresse e-mail")
        if role == "auditor" and not audited:
            raise ValueError("un compte auditeur requiert au moins une adresse à auditer")
        async with self._sm() as session:
            if await session.scalar(select(User.id).where(User.username == username)):
                raise ValueError("cet identifiant existe déjà")
            u = User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                email=(email or None),
                audited_emails=audited,
                display_name=display_name or username,
                is_active=True,
                created_at=dt.datetime.now(dt.timezone.utc),
            )
            session.add(u)
            await session.commit()
            return _to_dict(u)

    async def set_password(self, user_id: int, password: str) -> str:
        """Change le mot de passe d'un compte. Retourne le username (pour l'audit)."""
        if not password:
            raise ValueError("mot de passe requis")
        async with self._sm() as session:
            u = await session.get(User, user_id)
            if u is None:
                raise KeyError("utilisateur introuvable")
            u.password_hash = hash_password(password)
            await session.commit()
            return u.username

    async def change_own_password(self, username: str, old_password: str, new_password: str) -> None:
        """Self-service : l'utilisateur change son mot de passe en prouvant l'ancien."""
        if not new_password:
            raise ValueError("nouveau mot de passe requis")
        async with self._sm() as session:
            u = await session.scalar(
                select(User).where(User.username == username, User.is_active.is_(True))
            )
            if u is None:
                raise KeyError("compte introuvable")
            if not verify_password(old_password, u.password_hash):
                raise ValueError("ancien mot de passe incorrect")
            u.password_hash = hash_password(new_password)
            await session.commit()

    async def set_email(self, user_id: int, email: str | None) -> str:
        """Met à jour l'adresse e-mail d'un compte. Retourne le username."""
        email = (email or "").strip() or None
        async with self._sm() as session:
            u = await session.get(User, user_id)
            if u is None:
                raise KeyError("utilisateur introuvable")
            if u.role == "auditor" and not email:
                raise ValueError("un compte auditeur requiert une adresse e-mail")
            u.email = email
            await session.commit()
            return u.username

    async def set_restore_imap(self, user_id: int, data: dict | None) -> str:
        """Définit (ou efface si data=None) la destination IMAP de restauration."""
        async with self._sm() as session:
            u = await session.get(User, user_id)
            if u is None:
                raise KeyError("utilisateur introuvable")
            if data is None or not data.get("host"):
                u.restore_imap = None
            else:
                cfg = {
                    "host": data["host"].strip(),
                    "port": int(data.get("port") or (993 if data.get("ssl", True) else 143)),
                    "username": data.get("username", "").strip(),
                    "ssl": bool(data.get("ssl", True)),
                    "folder": (data.get("folder") or "INBOX").strip(),
                }
                # Conserver le mot de passe existant si non fourni.
                existing = u.restore_imap or {}
                if data.get("password"):
                    cfg["password_enc"] = crypto.encrypt_secret(data["password"])
                elif existing.get("password_enc"):
                    cfg["password_enc"] = existing["password_enc"]
                u.restore_imap = cfg
            await session.commit()
            return u.username

    async def get_restore_imap(self, user_id: int) -> dict | None:
        """Destination IMAP avec mot de passe déchiffré (pour la restauration)."""
        async with self._sm() as session:
            u = await session.get(User, user_id)
        if u is None or not u.restore_imap or not u.restore_imap.get("host"):
            return None
        cfg = dict(u.restore_imap)
        cfg["password"] = crypto.decrypt_secret(cfg["password_enc"]) if cfg.get("password_enc") else ""
        return cfg

    async def set_audited_emails(self, user_id: int, audited_emails: list) -> str:
        """Met à jour le périmètre audité d'un compte auditeur. Retourne le username."""
        audited = sorted({a.strip().lower() for a in (audited_emails or []) if a.strip()})
        async with self._sm() as session:
            u = await session.get(User, user_id)
            if u is None:
                raise KeyError("utilisateur introuvable")
            if u.role != "auditor":
                raise ValueError("le périmètre audité ne concerne que les comptes auditeur")
            if not audited:
                raise ValueError("au moins une adresse à auditer requise")
            u.audited_emails = audited
            await session.commit()
            return u.username

    async def set_active(self, user_id: int, active: bool) -> str:
        """Active ou désactive un compte. Retourne le username (pour l'audit)."""
        async with self._sm() as session:
            u = await session.get(User, user_id)
            if u is None:
                raise KeyError("utilisateur introuvable")
            u.is_active = active
            await session.commit()
            return u.username

    async def delete(self, user_id: int) -> None:
        async with self._sm() as session:
            u = await session.get(User, user_id)
            if u is None:
                raise KeyError("utilisateur introuvable")
            await session.delete(u)
            await session.commit()

    async def count_admins(self) -> int:
        async with self._sm() as session:
            return await session.scalar(
                select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
            )
