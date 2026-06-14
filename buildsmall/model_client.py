"""Unified model client with swappable backends.

Apps call llm.chat(...) / vision.ocr(...) / asr.transcribe(...) / tts.speak(...)
and never care which backend is live. Choose one with BUILDSMALL_BACKEND:

  mock          deterministic, domain-aware stubs — runs anywhere, no weights, no GPU
  openai        any OpenAI-compatible endpoint (Modal vLLM, OpenAI, llama.cpp server, Cohere)
  transformers  in-process Hugging Face models (needs torch + the model weights)
  llamacpp      in-process GGUF via llama-cpp-python  → Off-the-Grid + Llama-Champion badges

The mock backend is the default so the whole slate is demoable today.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import wave

from . import config


# ── helpers ──────────────────────────────────────────────────────────────────
def _img_to_data_url(image) -> str:
    from PIL import Image
    if isinstance(image, str):
        img = Image.open(image)
    elif isinstance(image, Image.Image):
        img = image
    else:  # numpy array
        img = Image.fromarray(image)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _normalize_messages(messages_or_prompt, system=None):
    if isinstance(messages_or_prompt, str):
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": messages_or_prompt})
        return msgs
    return messages_or_prompt


def _extract_json(text: str):
    """Best-effort: pull the first JSON object/array out of a model reply."""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


# ── Text ─────────────────────────────────────────────────────────────────────
class TextClient:
    def chat(self, messages, model=None, temperature=None, max_tokens=None, task=None, **kw) -> str:
        model = model or config.TEXT_MODEL
        temperature = config.DEFAULT_TEMPERATURE if temperature is None else temperature
        max_tokens = max_tokens or config.DEFAULT_MAX_TOKENS
        msgs = _normalize_messages(messages)
        backend = config.BACKEND
        if backend == "mock":
            from . import mock_backend
            return mock_backend.chat(msgs, task=task, **kw)
        if backend == "openai":
            return self._openai(msgs, model, temperature, max_tokens, **kw)
        if backend == "llamacpp":
            return self._llamacpp(msgs, temperature, max_tokens, **kw)
        if backend == "transformers":
            return self._transformers(msgs, temperature, max_tokens, **kw)
        raise ValueError(f"Unknown BUILDSMALL_BACKEND={backend!r}")

    def complete(self, prompt, system=None, **kw) -> str:
        return self.chat(_normalize_messages(prompt, system), **kw)

    def json(self, prompt, system=None, **kw):
        """Ask for JSON and parse it (returns dict/list or None)."""
        sys = (system or "") + "\nReply with ONLY valid JSON. No prose, no code fences."
        return _extract_json(self.chat(_normalize_messages(prompt, sys.strip()), **kw))

    # backends -----------------------------------------------------------------
    def _openai(self, msgs, model, temperature, max_tokens, **kw):
        import os
        import requests
        url = f"{config.OPENAI_BASE_URL}/chat/completions"
        body = {"model": model, "messages": msgs, "temperature": temperature, "max_tokens": max_tokens}
        body.update({k: v for k, v in kw.items() if k in ("top_p", "stop", "presence_penalty", "frequency_penalty")})
        to = kw.get("timeout") or int(os.environ.get("BUILDSMALL_TEXT_TIMEOUT", "120"))
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, timeout=to)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _llamacpp(self, msgs, temperature, max_tokens, **kw):
        from llama_cpp import Llama
        if not getattr(self, "_llm", None):
            if not config.GGUF_PATH or not os.path.exists(config.GGUF_PATH):
                raise FileNotFoundError("Set BUILDSMALL_GGUF_PATH to a local .gguf for the llamacpp backend.")
            self._llm = Llama(model_path=config.GGUF_PATH, n_ctx=int(os.environ.get("BUILDSMALL_N_CTX", "8192")), n_gpu_layers=int(os.environ.get("BUILDSMALL_N_GPU_LAYERS", "0")), verbose=False)
        out = self._llm.create_chat_completion(messages=msgs, temperature=temperature, max_tokens=max_tokens)
        return out["choices"][0]["message"]["content"]

    def _transformers(self, msgs, temperature, max_tokens, **kw):
        from transformers import pipeline
        import torch
        if not getattr(self, "_pipe", None):
            self._pipe = pipeline("text-generation", model=config.HF_TEXT_REPO, torch_dtype=torch.bfloat16, device_map="auto")
        out = self._pipe(msgs, max_new_tokens=max_tokens, temperature=max(temperature, 0.01), do_sample=temperature > 0)
        return out[0]["generated_text"][-1]["content"]


# ── Vision (OCR + describe) ───────────────────────────────────────────────────
class VisionClient:
    def ocr(self, image, prompt="Transcribe ALL text in this image exactly as printed. Output the text only.", **kw) -> str:
        return self._run(image, prompt, task="ocr", **kw)

    def describe(self, image, prompt="Describe what is visibly in this image, factually and concisely.", **kw) -> str:
        return self._run(image, prompt, task="describe", **kw)

    def _run(self, image, prompt, task=None, model=None, **kw):
        model = model or config.VISION_MODEL
        backend = config.BACKEND
        if backend == "mock":
            from . import mock_backend
            return mock_backend.vision(prompt, task=task, **kw)
        if backend == "openai":
            import requests
            content = [{"type": "text", "text": prompt},
                       {"type": "image_url", "image_url": {"url": _img_to_data_url(image)}}]
            # VISION_BASE_URL may be a comma-separated list: try each in order and fall back
            # on failure, so e.g. MiniCPM-V (OpenBMB) primary -> Qwen2.5-VL secondary.
            urls = [u.strip() for u in str(config.VISION_BASE_URL or "").split(",") if u.strip()]
            timeout = int(os.environ.get("BUILDSMALL_VISION_TIMEOUT", "300"))
            last = None
            for base in urls:
                try:
                    r = requests.post(f"{base}/chat/completions",
                                      json={"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": config.DEFAULT_MAX_TOKENS},
                                      headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                                      timeout=timeout)
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"]
                except Exception as e:  # noqa: BLE001 — try the next endpoint
                    last = e
            raise last or RuntimeError("no vision endpoint configured (BUILDSMALL_VISION_BASE_URL)")
        if backend in ("transformers", "llamacpp"):
            # MiniCPM-V via transformers (see _platform/llamacpp for a GGUF vision recipe).
            return self._transformers(image, prompt)
        raise ValueError(f"Unknown backend for vision: {backend!r}")

    def _transformers(self, image, prompt):
        from transformers import AutoModel, AutoTokenizer
        from PIL import Image
        import torch
        if not getattr(self, "_model", None):
            repo = os.environ.get("BUILDSMALL_HF_VISION_REPO", "openbmb/MiniCPM-V-4_6")
            self._model = AutoModel.from_pretrained(repo, trust_remote_code=True, torch_dtype=torch.bfloat16).eval()
            self._tok = AutoTokenizer.from_pretrained(repo, trust_remote_code=True)
        img = Image.open(image).convert("RGB") if isinstance(image, str) else image
        return self._model.chat(image=img, msgs=[{"role": "user", "content": prompt}], tokenizer=self._tok)


# ── ASR (speech → text) ───────────────────────────────────────────────────────
class ASRClient:
    def transcribe(self, audio_path, language=None, **kw) -> str:
        backend = config.BACKEND
        if backend == "mock":
            from . import mock_backend
            return mock_backend.asr(audio_path, language=language, **kw)
        if backend == "openai":
            import requests
            with open(audio_path, "rb") as f:
                r = requests.post(f"{config.ASR_BASE_URL}/audio/transcriptions",
                                  files={"file": f}, data={"model": config.ASR_MODEL},
                                  headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, timeout=120)
            r.raise_for_status()
            return r.json().get("text", "")
        # local Whisper (faster-whisper or transformers)
        try:
            from faster_whisper import WhisperModel
            if not getattr(self, "_fw", None):
                self._fw = WhisperModel(os.environ.get("BUILDSMALL_FW_MODEL", "small"))
            segs, _ = self._fw.transcribe(audio_path, language=language)
            return " ".join(s.text for s in segs).strip()
        except ImportError:
            from transformers import pipeline
            if not getattr(self, "_pipe", None):
                self._pipe = pipeline("automatic-speech-recognition", model=os.environ.get("BUILDSMALL_HF_ASR_REPO", "openai/whisper-small"))
            return self._pipe(audio_path)["text"]


# ── TTS (text → speech) ───────────────────────────────────────────────────────
class TTSClient:
    def speak(self, text, lang="pcm", out_path=None, **kw) -> str:
        """Return a path to a .wav file. lang: pcm|en|yo|ha|ig."""
        out_path = out_path or os.path.join(config.TRACE_LOCAL_DIR, "tts_out.wav")
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        backend = config.BACKEND
        if backend == "mock":
            from . import mock_backend
            return mock_backend.tts(text, lang=lang, out_path=out_path)
        if backend == "openai":
            import requests
            r = requests.post(f"{config.TTS_BASE_URL}/audio/speech",
                              json={"model": config.TTS_MODEL, "input": text, "voice": kw.get("voice", lang)},
                              headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, timeout=120)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(r.content)
            return out_path
        # local YarnGPT/transformers TTS hook — fill in per the model card on deploy.
        raise NotImplementedError("Wire a local TTS model here (e.g. YarnGPT2) or use BUILDSMALL_BACKEND=openai.")
