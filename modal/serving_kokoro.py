"""Modal, high quality TTS. Kokoro-82M for English/Pidgin (natural, fast), F5-TTS for
Yoruba (naijaml/f5-tts-yoruba, far more natural than MMS), MMS for Hausa/Igbo (and as the
Yoruba fallback). OpenAI /audio/speech compatible. voice = lang code (en/pcm/yo/ha/ig).

  modal deploy _platform/modal/serving_kokoro.py
  → export BUILDSMALL_TTS_BASE_URL=https://<printed-url>/v1
"""
import os

import modal

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("espeak-ng", "ffmpeg")
    .pip_install("kokoro>=0.9.2", "misaki[en]", "soundfile", "numpy", "scipy",
                 "transformers", "torch", "torchaudio", "f5-tts",
                 "fastapi[standard]", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
app = modal.App("buildsmall-tts")   # replaces the MMS app slot (plan caps app count); Kokoro + MMS hybrid
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)
KVOICE = {"en": "af_heart", "pcm": "am_michael"}                 # Kokoro English voices
# Meta never released facebook/mms-tts-ibo; use a community VITS Igbo model (drop-in compatible).
MMS = {"yo": "facebook/mms-tts-yor", "ha": "facebook/mms-tts-hau", "ig": "rnjema-unima/mms-tts-ibo-baseline"}
# F5-TTS (335M, natural) per language. Hausa/Igbo from the OpenBible series (CC-BY-SA); they ship
# no reference clip, so all three share the natural Yoruba female reference (F5 is voice-cloning,
# so the OpenBible models still produce correct Hausa/Igbo phonetics in that voice).
F5 = {"yo": ("naijaml/f5-tts-yoruba", "model_150000.pt"),
      "ha": ("multilingual-tts/F5-TTS-OpenBible-Hausa", "model_last.pt"),
      "ig": ("multilingual-tts/F5-TTS-OpenBible-Igbo", "model_last.pt")}
F5_REF = ("naijaml/f5-tts-yoruba", "samples/female_1_news.wav")


@app.cls(image=image, gpu="T4", scaledown_window=300,
         min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),
         max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),   # cap GPUs/app (10-GPU workspace limit)
         volumes={"/root/.cache/huggingface": cache})
class TTS:
    @modal.enter()
    def load(self):
        import torch
        from kokoro import KPipeline
        from transformers import AutoTokenizer, VitsModel
        self.kok = KPipeline(lang_code="a")          # American English G2P
        self.torch, self._Vits, self._Tok = torch, VitsModel, AutoTokenizer
        self.mms_m, self.mms_t = {}, {}
        self._f5, self._f5ref = {}, None

    def _mms(self, repo):
        if repo not in self.mms_m:
            self.mms_m[repo] = self._Vits.from_pretrained(repo).to("cuda")
            self.mms_t[repo] = self._Tok.from_pretrained(repo)
        return self.mms_m[repo], self.mms_t[repo]

    def _f5_load(self, lang):
        """Lazy-load the F5-TTS model for a language (yo/ha/ig). Character-level models, so
        neutralise the pinyin converter BEFORE importing the API (per the model card)."""
        if lang not in self._f5:
            from huggingface_hub import hf_hub_download
            if not getattr(self, "_f5_patched", False):
                import f5_tts.model.utils as f5u
                f5u.convert_char_to_pinyin = lambda texts, polyphone=True: texts
                self._f5_patched = True
            from f5_tts.api import F5TTS
            repo, ckptname = F5[lang]
            ckpt = hf_hub_download(repo, ckptname)
            vocab = hf_hub_download(repo, "vocab.txt")
            if self._f5ref is None:
                self._f5ref = hf_hub_download(F5_REF[0], F5_REF[1])
            self._f5[lang] = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cuda")
        return self._f5[lang], self._f5ref

    @modal.asgi_app()
    def web(self):
        import io

        import numpy as np
        import soundfile as sf
        from fastapi import FastAPI
        from fastapi.responses import Response
        from pydantic import BaseModel

        api = FastAPI()

        class Req(BaseModel):
            input: str
            voice: str = "en"
            model: str = "kokoro"

        @api.post("/v1/audio/speech")
        @api.post("/audio/speech")
        def speak(r: Req):
            wav, sr = None, 24000
            if r.voice in KVOICE:                       # Kokoro for English/Pidgin (natural)
                try:
                    chunks = []
                    for res in self.kok(r.input[:600], voice=KVOICE[r.voice]):
                        a = res.audio if hasattr(res, "audio") else res[2]
                        chunks.append((a.cpu().numpy() if hasattr(a, "cpu") else np.asarray(a)).astype("float32"))
                    if chunks:
                        wav = np.concatenate(chunks)
                except Exception:
                    wav = None
            if wav is None and r.voice in F5:            # F5-TTS for Yo/Ha/Ig (high quality)
                try:
                    f5, ref = self._f5_load(r.voice)
                    out = f5.infer(ref_file=ref, ref_text="", gen_text=r.input[:400], nfe_step=16, speed=1.0)
                    wav, sr = np.asarray(out[0], dtype="float32"), int(out[1])
                except Exception:
                    wav = None
            if wav is None:                             # MMS for Yo/Ha/Ig (Yoruba here = fallback)
                try:
                    model, tok = self._mms(MMS.get(r.voice, "facebook/mms-tts-eng"))
                    inp = tok(r.input[:600], return_tensors="pt").to("cuda")
                    with self.torch.no_grad():
                        wav = model(**inp).waveform[0].cpu().numpy()
                    sr = model.config.sampling_rate
                except Exception:
                    wav = None
            if wav is None:                             # never 500: fall back to English Kokoro
                try:
                    chunks = []
                    for res in self.kok(r.input[:600], voice="af_heart"):
                        a = res.audio if hasattr(res, "audio") else res[2]
                        chunks.append((a.cpu().numpy() if hasattr(a, "cpu") else np.asarray(a)).astype("float32"))
                    if chunks:
                        wav, sr = np.concatenate(chunks), 24000
                except Exception:
                    wav = None
            if wav is None:
                wav, sr = np.zeros(int(24000 * 0.3), dtype="float32"), 24000
            buf = io.BytesIO()
            sf.write(buf, wav, sr, format="WAV")
            return Response(buf.getvalue(), media_type="audio/wav")

        return api
