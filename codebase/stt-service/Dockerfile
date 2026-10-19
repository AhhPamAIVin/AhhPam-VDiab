FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

# Qwen2.5-Omni-3B is opt-in (multi-GB torch download) — build with
# --build-arg INSTALL_QWEN=1 to include it in this image.
ARG INSTALL_QWEN=0
COPY requirements-qwen.txt ./
RUN if [ "$INSTALL_QWEN" = "1" ]; then \
      python3.11 -m pip install --no-cache-dir -r requirements-qwen.txt; \
    fi

COPY app ./app
COPY eval ./eval

EXPOSE 8300

CMD ["python3.11", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
