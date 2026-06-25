# Migrations de schéma (Alembic)

## Stratégie

- **Baseline** : le schéma initial — dont `messages` **partitionnée** (PARTITION
  BY RANGE, non exprimable en métadonnées SQLAlchemy) et `message_dedup` — est
  créé par `mailarchiver_common.models.ensure_schema()` au 1er démarrage.
- **Incréments** : toute évolution ultérieure (ALTER, nouvelle table, index) passe
  par une migration Alembic versionnée (cf. `versions/0002_security_columns.py`
  comme patron). Fini les `ALTER TABLE` appliqués à la main en production.

## Mise en place sur une base existante

```bash
# 1) Marquer la base (déjà initialisée par l'app) au niveau baseline :
DATABASE_URL=postgresql+asyncpg://mailarchiver:***@HOST:5432/mailarchiver \
  alembic -c migrations/alembic.ini stamp 0001_baseline

# 2) Appliquer les incréments :
DATABASE_URL=... alembic -c migrations/alembic.ini upgrade head
```

## Nouvelle migration

```bash
DATABASE_URL=... alembic -c migrations/alembic.ini revision -m "ajout colonne X"
# éditer le fichier généré (upgrade/downgrade), puis :
DATABASE_URL=... alembic -c migrations/alembic.ini upgrade head
```

> L'autogenerate (`--autogenerate`) détecte les écarts sur les tables **non
> partitionnées** ; pour `messages` (partitionnée), écrire le DDL à la main et
> créer les partitions via `ensure_message_partition()` (cf. `deploy/db/`).

## Partitions

Planifier la création des partitions à l'avance (extension `pg_cron`) :
```sql
SELECT cron.schedule('msg-partitions', '0 0 25 * *',
  $$ SELECT ensure_message_partition((now() + interval '2 month')::date) $$);
```
