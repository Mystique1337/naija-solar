"""Build Small — shared platform layer (Nigeria full-slate campaign).

Import what you need; heavy deps (gradio, torch, llama_cpp) load lazily so a bare
`import buildsmall` stays cheap.

    from buildsmall import llm, vision, asr, tts   # model clients
    from buildsmall import trace                    # Sharing-is-Caring logger
    from buildsmall import i18n, ui, params, demo_kit, config

Switch backends with the BUILDSMALL_BACKEND env var (default "mock").
"""
from __future__ import annotations

__version__ = "0.1.0"

_SINGLETONS = {}


def _build_clients():
    from .model_client import TextClient, VisionClient, ASRClient, TTSClient
    _SINGLETONS["llm"] = TextClient()
    _SINGLETONS["vision"] = VisionClient()
    _SINGLETONS["asr"] = ASRClient()
    _SINGLETONS["tts"] = TTSClient()


def __getattr__(name):  # PEP 562 lazy module attributes
    if name in ("llm", "vision", "asr", "tts"):
        if name not in _SINGLETONS:
            _build_clients()
        return _SINGLETONS[name]
    if name == "trace":
        from .trace_logger import tracer
        return tracer
    if name in ("sessions", "session"):
        import importlib
        return importlib.import_module(".session", __name__)
    if name in ("i18n", "ui", "params", "demo_kit", "config", "model_client"):
        import importlib
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module 'buildsmall' has no attribute '{name}'")


__all__ = ["llm", "vision", "asr", "tts", "trace", "sessions", "i18n", "ui", "params", "demo_kit", "config"]
