#!/usr/bin/env bash
# Hourly encrypted backup of VDiab's data (task 6.3) — docs/BACKUP_RESTORE.md.
#
#   BACKUP_PASSPHRASE=... scripts/backup_db.sh
#
# Writes into $BACKUP_DIR/<UTC timestamp>/:
#   postgres.dump.enc              pg_dump custom format, AES-256 (openssl, PBKDF2)
#   qdrant-<collection>.snapshot.enc   one per Qdrant collection (optional)
#   SHA256SUMS                     checksums of the encrypted files
# keeps the newest $BACKUP_KEEP runs, and copies the run off the server with
# rclone when $BACKUP_REMOTE is set. Meal photos are never stored (Epic 1),
# so there is no file directory to back up.
#
# Environment (defaults in brackets):
#   DB_HOST [localhost] DB_PORT [5432] DB_USER [postgres] DB_PASSWORD DB_NAME [diab] DB_SSLMODE [prefer]
#   BACKUP_PASSPHRASE   required — keep it OUTSIDE the server (password manager); without it the backup is unreadable
#   BACKUP_DIR [./backups]   BACKUP_KEEP [72]   BACKUP_REMOTE   e.g. "s3:vdiab-backups" (an rclone remote:path)
#   PG_CONTAINER        run pg_dump inside this container (docker exec) instead of a local pg_dump
#   PG_IMAGE [postgres:16-alpine]  used through `docker run` when there is neither pg_dump nor PG_CONTAINER;
#                       must be >= the server's major version
#   QDRANT_URL          e.g. http://localhost:6333; unset = skip Qdrant (it can be rebuilt, see the runbook)
#   QDRANT_API_KEY
set -euo pipefail

: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE must be set (see docs/BACKUP_RESTORE.md)}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-diab}"
DB_SSLMODE="${DB_SSLMODE:-prefer}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP="${BACKUP_KEEP:-72}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
export BACKUP_PASSPHRASE

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

encrypt() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE; }

pg_dump_cmd() {
  if [[ -n "${PG_CONTAINER:-}" ]]; then
    docker exec -i -e PGPASSWORD="${DB_PASSWORD:-}" "$PG_CONTAINER" \
      pg_dump -U "$DB_USER" -d "$DB_NAME" "$@"
  elif command -v pg_dump >/dev/null 2>&1; then
    PGPASSWORD="${DB_PASSWORD:-}" PGSSLMODE="$DB_SSLMODE" \
      pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"
  else
    docker run --rm -i --add-host host.docker.internal:host-gateway \
      -e PGPASSWORD="${DB_PASSWORD:-}" -e PGSSLMODE="$DB_SSLMODE" "$PG_IMAGE" \
      pg_dump -h "$( [[ $DB_HOST == localhost || $DB_HOST == 127.0.0.1 ]] && echo host.docker.internal || echo "$DB_HOST")" \
      -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"
  fi
}

qdrant() {
  curl -fsS ${QDRANT_API_KEY:+-H "api-key: $QDRANT_API_KEY"} "$@"
}

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$BACKUP_DIR/$stamp"
mkdir -p "$run_dir"
started=$(date +%s)

log "postgres: dumping $DB_NAME@$DB_HOST"
# Custom format (-Fc): compressed, restorable table by table with pg_restore.
# The output is encrypted as it streams — no plaintext copy touches the disk.
pg_dump_cmd -Fc --no-owner --no-privileges | encrypt >"$run_dir/postgres.dump.enc"
# pipefail above makes a failed pg_dump fail the script; an empty dump is a failure too.
[[ -s "$run_dir/postgres.dump.enc" ]] || { log "postgres dump is empty"; exit 1; }

if [[ -n "${QDRANT_URL:-}" ]]; then
  collections=$(qdrant "$QDRANT_URL/collections" | grep -o '"name":"[^"]*"' | cut -d'"' -f4)
  for collection in $collections; do
    log "qdrant: snapshot of $collection"
    name=$(qdrant -X POST "$QDRANT_URL/collections/$collection/snapshots?wait=true" \
      | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')
    qdrant "$QDRANT_URL/collections/$collection/snapshots/$name" | encrypt >"$run_dir/qdrant-$collection.snapshot.enc"
    # The snapshot also stays on the Qdrant server's disk — remove it there.
    qdrant -X DELETE "$QDRANT_URL/collections/$collection/snapshots/$name" >/dev/null
  done
fi

(cd "$run_dir" && sha256sum -- *.enc >SHA256SUMS)
size=$(du -sh "$run_dir" | cut -f1)
log "backup $stamp written ($size, $(( $(date +%s) - started ))s)"

if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  log "copying to $BACKUP_REMOTE/$stamp"
  rclone copy "$run_dir" "$BACKUP_REMOTE/$stamp"
fi

# Keep the newest $BACKUP_KEEP runs locally (the remote has its own lifecycle rule).
mapfile -t runs < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*Z' | sort)
if (( ${#runs[@]} > BACKUP_KEEP )); then
  for old in "${runs[@]:0:${#runs[@]}-BACKUP_KEEP}"; do
    log "pruning $old"
    rm -rf -- "$old"
  done
fi
echo "$run_dir"
