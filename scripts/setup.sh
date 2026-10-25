#!/bin/bash
# Setup script cho VDiab — chạy được trên Linux/macOS và Windows (Git Bash).
#
#   bash scripts/setup.sh            # backend + frontend
#   SKIP_FRONTEND=1 bash scripts/setup.sh

set -e

cd "$(dirname "$0")/.."

echo "=== VDiab Setup ==="

# Python 3.11 khớp với Dockerfile (python:3.11-slim). Trên Windows dùng `py -3.11`.
if command -v py >/dev/null 2>&1 && py -3.11 --version >/dev/null 2>&1; then
    PYTHON="py -3.11"
elif command -v python3.11 >/dev/null 2>&1; then
    PYTHON="python3.11"
else
    PYTHON="python3"
fi
$PYTHON -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'"
echo "Python OK: $($PYTHON --version)"

# Virtual environment
if [ ! -d .venv ]; then
    $PYTHON -m venv .venv
fi
if [ -f .venv/Scripts/activate ]; then
    source .venv/Scripts/activate
else
    source .venv/bin/activate
fi

python -m pip install --upgrade pip
pip install -r requirements.txt

# .env files
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env — please edit OPENAI_API_KEY / JWT_SECRET_KEY / ADMIN_SEED_PASSWORD"
fi
if [ ! -f codebase/frontend/.env.local ]; then
    cp codebase/frontend/.env.example codebase/frontend/.env.local
    echo "Created codebase/frontend/.env.local"
fi

# Data directories
mkdir -p data/chroma

# Frontend
if [ -z "$SKIP_FRONTEND" ] && command -v npm >/dev/null 2>&1; then
    (cd codebase/frontend && npm ci)
fi

cat <<'EOF'
Setup complete! Next:
  make db-up          # Postgres + Qdrant (docker)
  make langfuse-up    # optional: Langfuse tracing UI at http://localhost:3300
  make bootstrap      # schema + admin + demo data + RAG ingest
  make run            # backend at http://localhost:8100
  cd codebase/frontend && npm run dev   # frontend at http://localhost:3000
EOF
