#!/usr/bin/env bash
# Restore a backup made by scripts/backup_db.sh (task 6.3) — docs/BACKUP_RESTORE.md.
#
#   BACKUP_PASSPHRASE=... DB_HOST=... DB_NAME=... scripts/restore_db.sh backups/20261027T130000Z
#
# 1. verifies SHA256SUMS;
# 2. decrypts postgres.dump.enc and pg_restores it into $DB_NAME (--clean: the
#    restored tables replace existing ones). Refuses a database that already
#    has tables unless RESTORE_INTO_NON_EMPTY=yes — restoring over production
#    by mistake must take a deliberate second step;
# 3. uploads each qdrant-<collection>.snapshot.enc to $QDRANT_URL when set.
#
# Same connection variables as backup_db.sh (DB_*, PG_CONTAINER, PG_IMAGE,
# QDRANT_URL, QDRANT_API_KEY). The backend recreates any missing table/column
# on its next start (db.create_db_and_tables), so restoring an older dump into
# a newer code version is fine.
set -euo pipefail

run_dir="${1:?usage: restore_db.sh <backup run directory>}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE must be set}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-diab}"
DB_SSLMODE="${DB_SSLMODE:-prefer}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
export BACKUP_PASSPHRASE

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

decrypt() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$1"; }

pg_tool() {
  local tool="$1"
  shift
  if [[ -n "${PG_CONTAINER:-}" ]]; then
    docker exec -i -e PGPASSWORD="${DB_PASSWORD:-}" "$PG_CONTAINER" "$tool" -U "$DB_USER" -d "$DB_NAME" "$@"
  elif command -v "$tool" >/dev/null 2>&1; then
    PGPASSWORD="${DB_PASSWORD:-}" PGSSLMODE="$DB_SSLMODE" \
      "$tool" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"
  else
    docker run --rm -i --add-host host.docker.internal:host-gateway \
      -e PGPASSWORD="${DB_PASSWORD:-}" -e PGSSLMODE="$DB_SSLMODE" "$PG_IMAGE" \
      "$tool" -h "$( [[ $DB_HOST == localhost || $DB_HOST == 127.0.0.1 ]] && echo host.docker.internal || echo "$DB_HOST")" \
      -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"
  fi
}

qdrant() {
  curl -fsS ${QDRANT_API_KEY:+-H "api-key: $QDRANT_API_KEY"} "$@"
}

started=$(date +%s)
log "verifying checksums in $run_dir"
(cd "$run_dir" && sha256sum --check --quiet SHA256SUMS)

tables=$(pg_tool psql -Atc "select count(*) from information_schema.tables where table_schema = 'public'")
if (( tables > 0 )) && [[ "${RESTORE_INTO_NON_EMPTY:-}" != "yes" ]]; then
  log "$DB_NAME already has $tables tables — set RESTORE_INTO_NON_EMPTY=yes to restore over it"
  exit 1
fi

log "postgres: restoring into $DB_NAME@$DB_HOST"
# --single-transaction: all or nothing. --exit-on-error: a broken dump stops
# here instead of leaving a half-restored database.
decrypt "$run_dir/postgres.dump.enc" \
  | pg_tool pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --exit-on-error
log "postgres restored ($(( $(date +%s) - started ))s)"

if [[ -n "${QDRANT_URL:-}" ]]; then
  shopt -s nullglob
  for file in "$run_dir"/qdrant-*.snapshot.enc; do
    collection="$(basename "$file" .snapshot.enc)"
    collection="${collection#qdrant-}"
    log "qdrant: restoring $collection"
    tmp="$(mktemp)"
    decrypt "$file" >"$tmp"
    qdrant -X POST "$QDRANT_URL/collections/$collection/snapshots/upload?priority=snapshot&wait=true" \
      -F "snapshot=@$tmp" >/dev/null
    rm -f "$tmp"
  done
fi
log "restore finished in $(( $(date +%s) - started ))s"
