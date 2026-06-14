"""Central configuration for the Build Small platform layer.

Everything is driven by environment variables so the SAME app code runs:
  * locally / on a CPU Space with a deterministic MOCK backend (default),
  * against an OpenAI-compatible endpoint (Modal vLLM, OpenAI, llama.cpp server, Cohere-compat),
  * with in-process transformers, or
  * with in-process llama.cpp (GGUF) for the Off-the-Grid + Llama-Champion badges.

Pick a backend with BUILDSMALL_BACKEND. Nothing else in an app changes.
"""
from __future__ import annotations

import os

# ── Backend selection ────────────────────────────────────────────────────────
# mock | openai | transformers | llamacpp
BACKEND = os.environ.get("BUILDSMALL_BACKEND", "mock").strip().lower()

# OpenAI-compatible endpoint (covers Modal vLLM, OpenAI, local llama.cpp server, etc.)
OPENAI_BASE_URL = os.environ.get("BUILDSMALL_BASE_URL", os.environ.get("OPENAI_BASE_URL", "")).rstrip("/")
OPENAI_API_KEY = os.environ.get("BUILDSMALL_API_KEY", os.environ.get("OPENAI_API_KEY", "EMPTY"))

# Per-capability endpoints — each defaults to the main one, but can point at its own
# Modal app: text→vLLM, vision→MiniCPM-V, ASR→Whisper, TTS→YarnGPT (see _platform/modal/).
VISION_BASE_URL = os.environ.get("BUILDSMALL_VISION_BASE_URL", OPENAI_BASE_URL).rstrip("/")
ASR_BASE_URL = os.environ.get("BUILDSMALL_ASR_BASE_URL", OPENAI_BASE_URL).rstrip("/")
TTS_BASE_URL = os.environ.get("BUILDSMALL_TTS_BASE_URL", OPENAI_BASE_URL).rstrip("/")

# Default model ids per capability. Apps may override per-call.
TEXT_MODEL = os.environ.get("BUILDSMALL_TEXT_MODEL", "gpt-oss-20b")
VISION_MODEL = os.environ.get("BUILDSMALL_VISION_MODEL", "minicpm-v-4.6")
ASR_MODEL = os.environ.get("BUILDSMALL_ASR_MODEL", "whisper-small")
TTS_MODEL = os.environ.get("BUILDSMALL_TTS_MODEL", "yarngpt2")

# Local GGUF path (llamacpp backend) / local HF repo (transformers backend).
GGUF_PATH = os.environ.get("BUILDSMALL_GGUF_PATH", "")
HF_TEXT_REPO = os.environ.get("BUILDSMALL_HF_TEXT_REPO", "Qwen/Qwen3-4B")

# Generation defaults
DEFAULT_TEMPERATURE = float(os.environ.get("BUILDSMALL_TEMPERATURE", "0.6"))
DEFAULT_MAX_TOKENS = int(os.environ.get("BUILDSMALL_MAX_TOKENS", "1024"))

# Trace logging (Sharing is Caring)
TRACE_DATASET_REPO = os.environ.get("BUILDSMALL_TRACE_REPO", "")  # e.g. "your-user/app-traces"
TRACE_LOCAL_DIR = os.environ.get("BUILDSMALL_TRACE_DIR", "./traces")

# App identity (used in headers/footers/traces)
APP_NAME = os.environ.get("BUILDSMALL_APP_NAME", "Build Small app")
HF_ORG = "build-small-hackathon"


def is_mock() -> bool:
    return BACKEND == "mock"


def summary() -> dict:
    """Human-readable view of the active config (safe — no secrets)."""
    return {
        "backend": BACKEND,
        "text_model": TEXT_MODEL,
        "vision_model": VISION_MODEL,
        "asr_model": ASR_MODEL,
        "tts_model": TTS_MODEL,
        "base_url": OPENAI_BASE_URL or "(none)",
        "trace_repo": TRACE_DATASET_REPO or "(local only)",
    }
