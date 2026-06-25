#!/usr/bin/env bash
# Mesure le débit de TRAITEMENT (archivage) de MailArchiver-NG.
# Usage : measure.sh <total> <conns>
set -euo pipefail
cd "$(dirname "$0")/.."

TOTAL=${1:-6000}
CONNS=${2:-24}
PY=.venv/bin/python

count() { docker compose exec -T postgres psql -U mailarchiver -d mailarchiver -tA -c "SELECT count(*) FROM messages;" | tr -d '[:space:]'; }

BASE=$(count)
TARGET=$((BASE + TOTAL))
echo "baseline=$BASE  cible=$TARGET  (workers=$(docker compose ps --format '{{.Service}}' | grep -c archiver-worker))"

T0=$($PY -c 'import time;print(time.time())')
$PY loadtest/inject.py --total "$TOTAL" --conns "$CONNS"
TINJ=$($PY -c 'import time;print(time.time())')

# Attendre la vidange complète de la file (archivage en base)
while :; do
  C=$(count)
  NOW=$($PY -c 'import time;print(time.time())')
  [ "$C" -ge "$TARGET" ] && break
  # garde-fou : abandon après 120 s
  if $PY -c "import sys;sys.exit(0 if ($NOW-$T0)>120 else 1)"; then echo "TIMEOUT à $C/$TARGET"; break; fi
  sleep 0.5
done
TDONE=$($PY -c 'import time;print(time.time())')
DONE=$(count)

$PY - "$TOTAL" "$T0" "$TINJ" "$TDONE" "$BASE" "$DONE" <<'PY'
import sys
total,t0,tinj,tdone,base,done=float(sys.argv[1]),float(sys.argv[2]),float(sys.argv[3]),float(sys.argv[4]),int(sys.argv[5]),int(sys.argv[6])
archived=done-base
inj=tinj-t0; proc=tdone-t0
print(f"\n--- RÉSULTATS ---")
print(f"injection SMTP : {total/inj:.0f} msg/s ({inj:.1f}s)")
print(f"archivés       : {archived}")
print(f"traitement E2E : {archived/proc:.0f} msg/s ({proc:.1f}s du 1er envoi au dernier archivé)")
PY