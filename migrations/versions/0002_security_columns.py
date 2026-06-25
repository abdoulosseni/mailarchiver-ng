"""exemple d'incrément : colonnes de verrouillage de compte

Modèle de migration incrémentale (équivalent Alembic du ALTER appliqué à chaud).
À utiliser comme patron pour les évolutions de schéma futures.

Revision ID: 0002_security_columns
Revises: 0001_baseline
"""
import sqlalchemy as sa

from alembic import op

revision = "0002_security_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_logins")
