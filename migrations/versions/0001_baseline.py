"""baseline

Le schéma initial — dont la table **messages partitionnée** (PARTITION BY RANGE)
et `message_dedup` — est créé par `mailarchiver_common.models.ensure_schema()` au
premier démarrage (le partitionnement n'est pas exprimable via les métadonnées
SQLAlchemy / l'autogenerate Alembic).

Procédure sur une base déjà initialisée par l'application :
    alembic -c migrations/alembic.ini stamp 0001_baseline
Les évolutions ultérieures sont gérées par migrations incrémentales
(cf. 0002_security_columns pour un modèle).

Revision ID: 0001_baseline
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Baseline : aucun DDL ici (établi par ensure_schema). Voir le docstring.
    pass


def downgrade() -> None:
    pass
