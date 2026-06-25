#!/usr/bin/env bash
# Sauvegarde des 3 datastores de MailArchiver-NG.
#   - PostgreSQL  : pg_dump (métadonnées, comptes, audit, paramètres)
#   - MinIO/S3    : miroir du bucket d'archives (corps + PJ chiffrés)
#   - OpenSearch  : snapshot de l'index (reconstructible, mais accélère le PRA)
#
# Usage :  BACKUP_DIR=/srv/backups ./deploy/backup/backup.sh
# À planifier (cron quotidien). Les blobs MinIO sont déjà chiffrés au repos ;
# chiffrer aussi la cible de sauvegarde hors-site (PRA).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/$STAMP"
mkdir -p "$DEST"

PG_USER="${POSTGRES_USER:-mailarchiver}"
PG_DB="${POSTGRES_DB:-mailarchiver}"
S3_BUCKET="${S3_BUCKET:-archives}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-minioadmin}"
S3_SECRET_KEY="${S3_SECRET_KEY:-change-me}"
NETWORK="${COMPOSE_NETWORK:-mailarchiver-ng_default}"

echo "[1/3] PostgreSQL → $DEST/postgres.dump"
docker compose exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$DEST/postgres.dump"

echo "[2/3] MinIO ($S3_BUCKET) → $DEST/minio/"
mkdir -p "$DEST/minio"
# Conteneur mc dédié (l'image minio/minio n'a pas tar) : mirror direct vers l'hôte.
docker run --rm --network "$NETWORK" -v "$DEST/minio:/backup" --entrypoint sh minio/mc -c \
  "mc alias set src http://minio:9000 $S3_ACCESS_KEY $S3_SECRET_KEY >/dev/null && \
   mc mirror --overwrite src/$S3_BUCKET /backup" || echo "  (bucket vide ou MinIO indisponible)"

echo "[3/3] OpenSearch → snapshot (repo 'backup' à enregistrer au préalable)"
echo "  PUT _snapshot/backup/snap-$STAMP?wait_for_completion=true"
echo "  (cf. deploy/backup/README.md — l'index est de toute façon reconstructible"
echo "   depuis les EML stockés en MinIO via réindexation)"

echo "OK → $DEST"
ls -lh "$DEST"
