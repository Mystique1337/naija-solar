"""Modal — serve SoroTTS (Hypa-Orpheus + the fine-tuned `sorotts` LoRA) over OpenAI /audio/speech.

Loads the Orpheus-3B base + the fine-tuned adapter + the SNAC codec, and synthesises 24 kHz
speech in Yorùbá / Hausa / Igbo / Nigerian Pidgin (and English from the base voices). Same
OpenAI-compatible `/v1/audio/speech` shape and Bearer-key auth gate as the other model
endpoints, so the app can switch TTS to it per language with only a config change.

  modal deploy modal/serving_sorotts.py
  → BUILDSMALL_TTS_BASE_URL=https://<printed-url>/v1   (and BUILDSMALL_TTS_MODEL=sorotts)

The adapter `SOROTTS_ADAPTER` (default Shinzmann/sorotts) is produced by finetune_orpheus.py;
this endpoint works as soon as that repo exists on the Hub.
"""
import os

import modal

BASE = os.environ.get("SOROTTS_BASE", "hypaai/hypaai_orpheus_v5")
ADAPTER = os.environ.get("SOROTTS_ADAPTER", "Shinzmann/sorotts")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg")
    .pip_install("unsloth")
    .pip_install("snac", "soundfile", "soxr", "librosa", "peft",
                 "fastapi[standard]", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
app = modal.App("sorotts")
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)

# cleanest learned voice per language (Yor1/Hau1/Ibo1 = cleanest source; NaijaA = first Pidgin
# speaker). English falls back to a base Orpheus/Hypa voice. Override via the request `voice`.
VOICE = {"yo": "Yor1", "yor": "Yor1", "ha": "Hau1", "hau": "Hau1", "ig": "Ibo1", "ibo": "Ibo1",
         "pcm": "NaijaA", "naija": "NaijaA", "en": "Eniola"}


@app.cls(image=image, gpu="L4", scaledown_window=300,
         min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),
         max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),
         volumes={"/root/.cache/huggingface": cache},
         secrets=[modal.Secret.from_name("buildsmall-api"), modal.Secret.from_name("huggingface")])
class SoroTTS:
    @modal.enter()
    def load(self):
        import torch
        from snac import SNAC
        from unsloth import FastLanguageModel
        self.torch = torch
        self.model, self.tok = FastLanguageModel.from_pretrained(
            model_name=BASE, max_seq_length=2048, dtype=None, load_in_4bit=True)
        try:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, ADAPTER)
            print("loaded adapter:", ADAPTER)
        except Exception as e:
            print("adapter not loaded (using base):", type(e).__name__, str(e)[:100])
        FastLanguageModel.for_inference(self.model)
        self.snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda").eval()
        self.SOH, self.EOT, self.EOH, self.SOS, self.EOS, self.AUDIO_OFFSET = (
            128259, 128009, 128260, 128257, 128258, 128266)

    def _redistribute(self, code_list):
        import torch
        l1, l2, l3 = [], [], []
        for i in range((len(code_list) + 1) // 7):
            b = 7 * i
            l1.append(code_list[b])
            l2.append(code_list[b + 1] - 4096)
            l3.append(code_list[b + 2] - 2 * 4096); l3.append(code_list[b + 3] - 3 * 4096)
            l2.append(code_list[b + 4] - 4 * 4096)
            l3.append(code_list[b + 5] - 5 * 4096); l3.append(code_list[b + 6] - 6 * 4096)
        dev = next(self.snac.parameters()).device
        codes = [torch.tensor(l1).unsqueeze(0), torch.tensor(l2).unsqueeze(0), torch.tensor(l3).unsqueeze(0)]
        with torch.inference_mode():
            return self.snac.decode([c.to(dev) for c in codes]).squeeze().cpu().numpy()

    def _generate(self, text, voice, temperature=0.6, top_p=0.95, rep=1.1, max_new_tokens=1200):
        torch = self.torch
        prompt = f"{voice}: {text}" if voice else text
        ids = self.tok(prompt, return_tensors="pt").input_ids
        ids = torch.cat([torch.tensor([[self.SOH]]), ids, torch.tensor([[self.EOT, self.EOH]])], dim=1).to(self.model.device)
        gen = self.model.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                                  max_new_tokens=max_new_tokens, do_sample=True, temperature=temperature,
                                  top_p=top_p, repetition_penalty=rep, eos_token_id=self.EOS, use_cache=True)
        row = gen[0]
        sos = (row == self.SOS).nonzero(as_tuple=True)[0]
        row = row[sos[-1].item() + 1:] if len(sos) else row
        row = row[row != self.EOS]
        n = (row.size(0) // 7) * 7
        return self._redistribute([t.item() - self.AUDIO_OFFSET for t in row[:n]])

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
            voice = VOICE.get(r.voice, r.voice if r.voice and r.voice[0].isupper() else VOICE["yo"])
            wave = self._generate(r.input[:600], voice)
            buf = io.BytesIO()
            sf.write(buf, np.asarray(wave, dtype=np.float32), 24000, format="WAV")
            return Response(buf.getvalue(), media_type="audio/wav")

        return api
