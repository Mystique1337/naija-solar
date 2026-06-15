"""Modal — TTS endpoint for Naija Solar, now powered by SoroTTS (fine-tuned Orpheus-3B).

This REPLACES the previous Meta MMS-TTS implementation in the same `buildsmall-tts` app (same
OpenAI `/v1/audio/speech` interface, same Bearer auth gate, same URL), so the app's narration
keeps working unchanged — it just speaks with the fine-tuned Soro voices now.

It loads the Orpheus base (with its own adapter excluded) + the Shinzmann/sorotts LoRA + SNAC,
maps the app's language codes to learned voices, and synthesises 24 kHz speech. Long plans are
split into sentences (Orpheus has a ~15s / 2048-token budget per call) and stitched together.

  modal deploy modal/serving_tts.py
  → BUILDSMALL_TTS_BASE_URL stays https://chidi-ashinze--buildsmall-tts-tts-web.modal.run/v1
"""
import os

import modal

BASE = os.environ.get("SOROTTS_BASE", "hypaai/hypaai_orpheus_v5")
ADAPTER = os.environ.get("SOROTTS_ADAPTER", "Shinzmann/sorotts")
TTS_GPU = os.environ.get("TTS_GPU", "A100-40GB")   # Orpheus is autoregressive; A100 ~2-3x faster than L4

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg")
    .pip_install("unsloth")
    .pip_install("snac", "soundfile", "soxr", "librosa", "peft",
                 "fastapi[standard]", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "PYTHONUNBUFFERED": "1"})
)
app = modal.App("buildsmall-tts")
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)

# the app calls TTS with voice = language code. Map each to the cleanest learned Soro voice;
# English uses a base Orpheus/Hypa voice (Soro fine-tuned the Nigerian languages).
VOICE = {"yo": "Yor1", "yor": "Yor1", "ha": "Hau1", "hau": "Hau1", "ig": "Ibo1", "ibo": "Ibo1",
         "pcm": "NaijaA", "naija": "NaijaA", "en": "Eniola"}


@app.cls(image=image, gpu=TTS_GPU, scaledown_window=600,
         min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),  # set 1 to keep warm (no cold start)
         max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),
         volumes={"/root/.cache/huggingface": cache},
         secrets=[modal.Secret.from_name("buildsmall-api"), modal.Secret.from_name("huggingface")])
class TTS:
    @modal.enter()
    def load(self):
        import torch
        from huggingface_hub import login, snapshot_download
        from snac import SNAC
        from unsloth import FastLanguageModel
        tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if tok:
            login(tok)
        self.torch = torch
        # load the base WITHOUT its own adapter (same as training), then attach the Soro adapter
        local = snapshot_download(BASE, ignore_patterns=["adapter_*"])
        for _f in ("adapter_config.json", "adapter_model.safetensors", "adapter_model.bin"):
            _p = os.path.join(local, _f)
            if os.path.exists(_p):
                os.remove(_p)
        # bf16 (not 4-bit) generates notably faster on A100; the 3B model fits easily.
        self.model, self.tok = FastLanguageModel.from_pretrained(
            model_name=local, max_seq_length=2048, dtype=None, load_in_4bit=False)
        try:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, ADAPTER)
            print("loaded Soro adapter:", ADAPTER)
        except Exception as e:
            print("Soro adapter not loaded (using base):", type(e).__name__, str(e)[:90])
        FastLanguageModel.for_inference(self.model)
        self.snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda").eval()
        self.snac_dev = next(self.snac.parameters()).device
        self.SOH, self.EOT, self.EOH, self.SOS, self.EOS, self.OFF = 128259, 128009, 128260, 128257, 128258, 128266

    def _decode(self, codes):
        import torch
        l1, l2, l3 = [], [], []
        for i in range((len(codes) + 1) // 7):
            b = 7 * i
            l1.append(codes[b]); l2.append(codes[b + 1] - 4096)
            l3.append(codes[b + 2] - 2 * 4096); l3.append(codes[b + 3] - 3 * 4096)
            l2.append(codes[b + 4] - 4 * 4096)
            l3.append(codes[b + 5] - 5 * 4096); l3.append(codes[b + 6] - 6 * 4096)
        c = [torch.tensor(x).unsqueeze(0).to(self.snac_dev) for x in (l1, l2, l3)]
        with torch.inference_mode():
            return self.snac.decode(c).squeeze().cpu().numpy()

    def _gen_one(self, text, voice, max_new_tokens=1200):
        torch = self.torch
        ids = self.tok(f"{voice}: {text}", return_tensors="pt").input_ids
        ids = torch.cat([torch.tensor([[self.SOH]]), ids, torch.tensor([[self.EOT, self.EOH]])], dim=1).to(self.model.device)
        out = self.model.generate(input_ids=ids, attention_mask=torch.ones_like(ids), max_new_tokens=max_new_tokens,
                                  do_sample=True, temperature=0.55, top_p=0.95, repetition_penalty=1.1,
                                  eos_token_id=self.EOS, use_cache=True)[0]
        sos = (out == self.SOS).nonzero(as_tuple=True)[0]
        out = out[sos[-1].item() + 1:] if len(sos) else out
        out = out[out != self.EOS]
        n = (out.size(0) // 7) * 7
        return self._decode([t.item() - self.OFF for t in out[:n]])

    def speak(self, text, voice):
        import re

        import numpy as np
        chunks = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()] or [text]
        sil = np.zeros(int(0.16 * 24000), dtype=np.float32)
        waves = []
        # Orpheus is autoregressive (~15-25s/sentence), so speak the 2 key sentences (usage +
        # recommendation); the full plan, incl. cost + disclaimer, is shown in writing in the app.
        for ch in chunks[:2]:
            try:
                w = self._gen_one(ch[:300], voice)
                if w is not None and len(w) > 240:
                    waves.append(np.asarray(w, dtype=np.float32))
            except Exception as e:
                print("chunk failed:", type(e).__name__, str(e)[:60])
        if not waves:
            return None
        out = []
        for i, w in enumerate(waves):
            out.append(w)
            if i < len(waves) - 1:
                out.append(sil)
        return np.concatenate(out)

    @modal.asgi_app()
    def web(self):
        import io

        import numpy as np
        import soundfile as sf
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, Response
        from pydantic import BaseModel

        api = FastAPI()

        @api.middleware("http")
        async def _auth(request: Request, call_next):
            key = os.environ.get("ENDPOINT_API_KEY", "")
            if key and request.headers.get("authorization") != f"Bearer {key}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

        class Req(BaseModel):
            input: str
            voice: str = "yo"
            model: str = "sorotts"

        @api.post("/v1/audio/speech")
        @api.post("/audio/speech")
        def speak(r: Req):
            voice = VOICE.get((r.voice or "").lower(), r.voice if r.voice[:1].isupper() else VOICE["yo"])
            wave = self.speak(r.input[:1200], voice)
            if wave is None:
                return JSONResponse({"error": "synthesis_failed"}, status_code=503)
            buf = io.BytesIO()
            sf.write(buf, np.asarray(wave, dtype=np.float32), 24000, format="WAV")
            return Response(buf.getvalue(), media_type="audio/wav")

        return api
