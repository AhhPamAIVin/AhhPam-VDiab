# Plain Python image — phase 1 has no local model weights, so unlike
# codebase/stt-service's CUDA base image, no GPU runtime is needed here.
# A phase-2 GPU-resident engine (F5-TTS-Vietnamese-ViVoice) will need its own
# CUDA-based Dockerfile variant when that work starts.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8400

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8400"]
