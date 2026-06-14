# Naija Solar — bespoke custom frontend (FastAPI + uvicorn) on Hugging Face Spaces (docker SDK).
FROM python:3.11-slim

# ffmpeg is a safety net for any server-side audio handling; curl for the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# /data is where HF persistent storage mounts (accounts + history persist there when it's enabled;
# ephemeral otherwise). chmod so the non-root Spaces user can write.
RUN mkdir -p /data && chmod 777 /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Reuse app.py's logic only; never build its (unused) Gradio Blocks. Writable cache for HF Spaces.
ENV BUILDSMALL_NO_GRADIO=1 \
    HF_HOME=/tmp/hf \
    BUILDSMALL_DATA_DIR=/data \
    PORT=7860
EXPOSE 7860

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
