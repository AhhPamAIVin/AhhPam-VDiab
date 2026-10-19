# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: Production ----
FROM python:3.11-slim

WORKDIR /app

# Copy the virtualenv (with installed packages) from the builder.
# Using a venv under /opt (not `pip install --user`, which lands in /root)
# avoids /root's 700 permissions blocking the non-root user below.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Security: run as non-root user
RUN useradd -m appuser

# Copy application code
COPY . .
RUN chmod +x scripts/docker-entrypoint.sh

# Create data directory with correct ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app /opt/venv

# No USER directive: the container starts as root so
# scripts/docker-entrypoint.sh can chown the volume Railway mounts at
# runtime (always root:root, regardless of this build-time chown — see
# that script), then drops to appuser itself before running the app.

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')" || exit 1

CMD ["scripts/docker-entrypoint.sh"]
