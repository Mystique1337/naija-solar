"""Modal — TTS endpoint (OpenAI /audio/speech compatible), real multilingual voices.

Uses Meta MMS-TTS (VITS) — standard, reliable, with real Yorùbá / Hausa / Igbo /
English voices. `voice` = language code (en/pcm/yo/ha/ig). Returns audio/wav bytes,
which the platform's TTSClient saves directly.

  modal deploy _platform/modal/serving_tts.py
  → export BUILDSMALL_TTS_BASE_URL=https://<printed-url>/v1
"""
import os

import modal

LANG_MODEL = {
    "en": "facebook/mms-tts-eng", "pcm": "facebook/mms-tts-eng",
    "yo": "facebook/mms-tts-yor", "ha": "facebook/mms-tts-hau", "ig": "facebook/mms-tts-ibo",
}
ENDPOINT_KEY = os.environ.get("ENDPOINT_API_KEY", "")  # when set, require Authorization: Bearer <key>
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("transformers", "torch", "scipy", "numpy",
                 "fastapi[standard]", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
app = modal.App("buildsmall-tts")
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)


@app.cls(image=image, gpu="T4", scaledown_window=300,
         min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),  # set 1 to keep warm (real-time)
         volumes={"/root/.cache/huggingface": cache},
         secrets=[modal.Secret.from_name("buildsmall-api")])
class TTS:
    @modal.enter()
    def load(self):
        from transformers import AutoTokenizer, VitsModel
        import torch
        self._Vits, self._Tok, self._torch = VitsModel, AutoTokenizer, torch
        self.models, self.toks = {}, {}

    def _get(self, lang):
        repo = LANG_MODEL.get(lang, "facebook/mms-tts-eng")
        if repo not in self.models:
            self.models[repo] = self._Vits.from_pretrained(repo).to("cuda")
            self.toks[repo] = self._Tok.from_pretrained(repo)
        return self.models[repo], self.toks[repo]

    @modal.asgi_app()
    def web(self):
        import io
        import numpy as np
        import scipy.io.wavfile as wavfile
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, Response
        from pydantic import BaseModel

        api = FastAPI()

        @api.middleware("http")
        async def _auth(request: Request, call_next):
            key = os.environ.get("ENDPOINT_API_KEY", "")   # from the buildsmall-api secret, at runtime
            if key and request.headers.get("authorization") != f"Bearer {key}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

        class Req(BaseModel):
            input: str
            voice: str = "yo"
            model: str = "mms-tts"

        @api.post("/v1/audio/speech")
        @api.post("/audio/speech")
        def speak(r: Req):
            model, tok = self._get(r.voice)
            inp = tok(r.input[:600], return_tensors="pt").to("cuda")
            with self._torch.no_grad():
                wave = model(**inp).waveform[0].cpu().numpy()
            buf = io.BytesIO()
            wavfile.write(buf, model.config.sampling_rate, (wave * 32767).astype(np.int16))
            return Response(buf.getvalue(), media_type="audio/wav")

        return api
