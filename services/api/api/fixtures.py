"""Données d'initialisation (fixtures) appliquées au démarrage du système.

Idempotent : appliquer les fixtures plusieurs fois ne crée pas de doublons.
C'est ici qu'on ajoute toute donnée de bootstrap future (classes de rétention
par défaut, rôles, etc.).

Fixture actuelle : **compte administrateur par défaut**, créé automatiquement
si aucun compte n'existe encore (identifiants ADMIN_USERNAME / ADMIN_PASSWORD,
par défaut admin / admin).
"""

from __future__ import annotations

import os

import structlog

from .users import UserRepo

log = structlog.get_logger()


async def apply_fixtures(users: UserRepo) -> None:
    await _seed_default_admin(users)


async def _seed_default_admin(users: UserRepo) -> None:
    if await users.count() > 0:
        return  # des comptes existent déjà : ne rien amorcer

    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin")
    await users.create(username=username, password=password, role="admin", display_name="Administrateur")
    log.info("fixture_admin_created", username=username)
