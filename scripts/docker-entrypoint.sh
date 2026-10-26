#!/bin/sh
# Runs before the app server starts on every container boot, as root (see
# Dockerfile — no USER directive, this script does the privilege drop
# itself). Two jobs:
#
# 1. Reclaim ownership of the mounted volume. Railway attaches a fresh
#    volume owned by root:root regardless of what the image's build-time
#    chown set up, so the volume (which holds the embedding model cache,
#    HF_HOME=/app/data/hf-cache) is unwritable by appuser until this runs —
#    verified live (back when the vector index lived there too): first boot
#    after attaching the volume failed ingestion until this fix.
#    Idempotent/cheap on every later boot once ownership is already correct.
#
# 2. Ingest the RAG corpus (codebase/models/rag/ingest.py) as appuser, then
#    exec uvicorn as appuser too — the app must not keep running as root
#    past this point. Ingestion is idempotent and cheap when the corpus
#    hasn't changed (metadata-only reads, no re-embedding — see that
#    module's docstring), so running it on every boot keeps the volume in
#    sync without a separate CI/SSH step, and self-heals a fresh/empty
#    mount. Best-effort: RAG is an enrichment for QA chat, not a hard
#    dependency (personal-data questions don't need it), so a failure here
#    (e.g. Qdrant not reachable yet) must not block the app from starting.
#    The first boot on a fresh volume also downloads the embedding model and
#    embeds the whole corpus on CPU — expect several minutes.
set -e

chown -R appuser:appuser /app/data

# Plain (non-login) su, not `su -p`: a login/preserve-environment su also
# preserves HOME=/root from this root shell, which then makes psycopg2/
# libpq look for a TLS client cert under /root/.postgresql/ — appuser can't
# read that directory, so every DB connection at startup failed with
# "could not open certificate file ... Permission denied" (verified live).
# Plain `su appuser -c` already inherits PATH from this shell for a
# non-login invocation while correctly setting HOME to appuser's own home.
exec su appuser -s /bin/sh -c '
  python -m codebase.models.rag.ingest || echo "WARNING: RAG ingestion failed at startup, continuing without it" >&2
  exec uvicorn codebase.backend.main:app --host 0.0.0.0 --port 8100
'
