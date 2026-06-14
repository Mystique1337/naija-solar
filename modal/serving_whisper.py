"""Modal — ASR endpoint (OpenAI /audio/transcriptions compatible).

Serves Whisper (including Nigerian-accent fine-tunes) for every voice app
(lang-whatsapp-reader, faith-sermon-transcriber, lang-proverb-archive, …).

  ASR_MODEL=openai/whisper-small modal deploy _platform/modal/serving_whisper.py
  # Nigerian: ASR_MODEL=dauda-dauda/whisper-yoruba-hausa-igbo  (or your NaijaVoices fine-tune)

Then point apps at it:  export BUILDSMALL_ASR_BASE_URL=https://<printed-url>/v1
"""
import os

import modal

MODEL = os.environ.get("ASR_MODEL", "openai/whisper-small")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("transformers", "torch", "torchaudio", "soundfile", "librosa",
                 "fastapi[standard]", "python-multipart", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
app = modal.App("buildsmall-whisper")
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)


@app.cls(image=image, gpu="T4", scaledown_window=300,
         min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),  # set 1 to keep warm (real-time)
         max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),  # cap GPUs/app (10-GPU workspace limit)
         volumes={"/root/.cache/huggingface": cache})
class ASR:
    @modal.enter()
    def load(self):
        from transformers import pipeline
        import torch
        self.pipe = pipeline("automatic-speech-recognition", model=MODEL,
                             torch_dtype=torch.float16, device="cuda", chunk_length_s=30)

    @modal.asgi_app()
    def web(self):
        import os as _os
        import tempfile
        from fastapi import FastAPI, File, Form, UploadFile

        api = FastAPI()

        @api.post("/v1/audio/transcriptions")
        @api.post("/audio/transcriptions")
        async def transcribe(file: UploadFile = File(...), model: str = Form(default="whisper")):
            data = await file.read()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(data)
                path = f.name
            out = self.pipe(path, return_timestamps=False)
            _os.unlink(path)
            return {"text": out["text"]}

        return api
