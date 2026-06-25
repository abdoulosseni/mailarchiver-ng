#!/usr/bin/env bash
# Restauration depuis une sauvegarde produite par backup.sh.
# Usage :  ./deploy/backup/restore.sh <repertoire_de_sauvegarde>
#   <repertoire> contient postgres.dump et minio/ (cf. backup.sh).
# ⚠️ Écrase les données existantes. Tester d'abord sur un environnement isolé.
set -euo pipefail

SRC="${1:?usage: restore.sh <repertoire_de_sauvegarde>}"
PG_USER="${POSTGRES_USER:-mailarchiver}"
PG_DB="${POSTGRES_DB:-mailarchiver}"
S3_BUCKET="${S3_BUCKET:-archives}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-minioadmin}"
S3_SECRET_KEY="${S3_SECRET_KEY:-change-me}"
NETWORK="${COMPOSE_NETWORK:-mailarchiver-ng_default}"

echo "[1/2] PostgreSQL ← $SRC/postgres.dump"
docker compose exec -T postgres pg_restore -U "$PG_USER" -d "$PG_DB" --clean --if-exists < "$SRC/postgres.dump"

echo "[2/2] MinIO ($S3_BUCKET) ← $SRC/minio/"
if [ -d "$SRC/minio" ]; then
  docker run --rm --network "$NETWORK" -v "$SRC/minio:/restore:ro" --entrypoint sh minio/mc -c \
    "mc alias set dst http://minio:9000 $S3_ACCESS_KEY $S3_SECRET_KEY >/dev/null && \
     mc mirror --overwrite /restore dst/$S3_BUCKET"
fi

echo "OK. Vérifier l'intégrité : ouvrir un mail (X-Archive-Integrity: valid)."

# Test de restauration à blanc (drill) — ne touche pas la base de prod :
#   docker compose exec -T postgres psql -U mailarchiver -d postgres -c "CREATE DATABASE drill"
#   docker compose exec -T postgres pg_restore -U mailarchiver -d drill < postgres.dump
#   docker compose exec -T postgres psql -U mailarchiver -d drill -tAc 'SELECT count(*) FROM messages'
#   docker compose exec -T postgres psql -U mailarchiver -d postgres -c "DROP DATABASE drill"
