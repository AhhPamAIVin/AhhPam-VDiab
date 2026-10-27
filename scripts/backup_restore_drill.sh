#!/usr/bin/env bash
# Backup/restore drill (task 6.3): back up the source, restore into EMPTY
# Postgres + Qdrant containers, compare every table's row count and every
# collection's point count, and report the measured times. Nothing is written
# to the source. Results go into docs/BACKUP_RESTORE.md ("Drill log").
#
#   PG_CONTAINER=vdiab-db-1 QDRANT_URL=http://localhost:6333 scripts/backup_restore_drill.sh
#
# Source: the same variables as backup_db.sh (default: the compose `db`
# container and the local Qdrant). Needs docker, openssl, curl.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
export PG_CONTAINER="${PG_CONTAINER:-vdiab-db-1}"
export DB_USER="${DB_USER:-postgres}" DB_NAME="${DB_NAME:-diab}"
SOURCE_QDRANT="${QDRANT_URL:-http://localhost:6333}"
DRILL_PG=vdiab-drill-pg
DRILL_QDRANT=vdiab-drill-qdrant
DRILL_QDRANT_PORT="${DRILL_QDRANT_PORT:-6343}"
work="$(mktemp -d)"
export BACKUP_PASSPHRASE="drill-$(openssl rand -hex 16)"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

cleanup() {
  if [[ "${KEEP:-}" != "1" ]]; then
    docker rm -f "$DRILL_PG" "$DRILL_QDRANT" >/dev/null 2>&1 || true
    rm -rf "$work"
  fi
}
trap cleanup EXIT

row_counts() {  # container -> "table count" lines, every public base table
  docker exec "$1" psql -U "$DB_USER" -d "$DB_NAME" -Atc "
    select table_name || ' ' || (xpath('/row/c/text()', query_to_xml(
      format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text
    from information_schema.tables
    where table_schema = 'public' and table_type = 'BASE TABLE' order by table_name"
}

point_counts() {  # qdrant url -> "collection points" lines
  local url="$1"
  for c in $(curl -fsS "$url/collections" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | sort); do
    echo "$c $(curl -fsS -X POST "$url/collections/$c/points/count" -H 'Content-Type: application/json' \
      -d '{"exact":true}' | grep -o '"count":[0-9]*' | cut -d: -f2)"
  done
}

log "starting empty Postgres and Qdrant"
docker rm -f "$DRILL_PG" "$DRILL_QDRANT" >/dev/null 2>&1 || true
docker run -d --name "$DRILL_PG" -e POSTGRES_PASSWORD=drill -e POSTGRES_DB="$DB_NAME" postgres:16-alpine >/dev/null
docker run -d --name "$DRILL_QDRANT" -p "$DRILL_QDRANT_PORT:6333" qdrant/qdrant:v1.20.0 >/dev/null
until docker exec "$DRILL_PG" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do sleep 1; done
until curl -fsS "http://localhost:$DRILL_QDRANT_PORT/readyz" >/dev/null 2>&1; do sleep 1; done
sleep 2  # the postgres image restarts once after initdb

log "source snapshot of row counts"
row_counts "$PG_CONTAINER" >"$work/source-rows.txt"
point_counts "$SOURCE_QDRANT" >"$work/source-points.txt"

backup_started=$(date +%s)
run_dir=$(BACKUP_DIR="$work/backups" QDRANT_URL="$SOURCE_QDRANT" "$here/backup_db.sh")
backup_seconds=$(( $(date +%s) - backup_started ))
backup_size=$(du -sh "$run_dir" | cut -f1)

restore_started=$(date +%s)
PG_CONTAINER="$DRILL_PG" QDRANT_URL="http://localhost:$DRILL_QDRANT_PORT" "$here/restore_db.sh" "$run_dir"
restore_seconds=$(( $(date +%s) - restore_started ))

row_counts "$DRILL_PG" >"$work/restored-rows.txt"
point_counts "http://localhost:$DRILL_QDRANT_PORT" >"$work/restored-points.txt"

status=0
if diff -u "$work/source-rows.txt" "$work/restored-rows.txt"; then
  rows_result="OK — $(wc -l <"$work/source-rows.txt") tables, $(awk '{s+=$2} END {print s}' "$work/source-rows.txt") rows identical"
else
  rows_result="MISMATCH"; status=1
fi
if diff -u "$work/source-points.txt" "$work/restored-points.txt"; then
  points_result="OK — $(wc -l <"$work/source-points.txt") collections, $(awk '{s+=$2} END {print s+0}' "$work/source-points.txt") points identical"
else
  points_result="MISMATCH"; status=1
fi

audit_result="audit_events not in this database"
if grep -q '^audit_events ' "$work/source-rows.txt"; then
  if docker exec "$DRILL_PG" psql -U "$DB_USER" -d "$DB_NAME" -c "DELETE FROM audit_events" >/dev/null 2>&1; then
    audit_result="FAIL — DELETE on audit_events allowed after restore"; status=1
  else
    audit_result="OK — still append-only after restore"
  fi
fi

cat <<EOF

=== Backup/restore drill $(date -u +%Y-%m-%dT%H:%M:%SZ) ===
commit           $(git -C "$here/.." rev-parse --short HEAD 2>/dev/null || echo unknown)
backup           ${backup_seconds}s, ${backup_size} encrypted
restore (RTO)    ${restore_seconds}s into empty Postgres + Qdrant
postgres rows    ${rows_result}
qdrant points    ${points_result}
audit trigger    ${audit_result}
EOF
exit $status
