"""Parameter-count registry and the 32B-cap checker.

The hackathon rule is: TOTAL parameters across all models in a single submission
must be <= 32 billion. Mixture-of-experts models count by TOTAL params (not active).
Use cap_check() in every app's README/startup to prove the math.

Specs verified against the Nigeria Playbook's Model Arsenal (June 2026).
"""
from __future__ import annotations

CAP_B = 32.0  # billion-parameter ceiling

# Billions of TOTAL parameters per model id.
MODELS_B = {
    # OpenAI
    "gpt-oss-20b": 21.0,          # 21B total / 3.6B active MoE — default workhorse
    # OpenBMB / MiniCPM
    "minicpm-v-4.6": 1.3,         # vision (OCR/on-device), Apache-2.0
    "minicpm-v-4.6-thinking": 1.3,
    "minicpm-o-4.5": 9.0,         # full-duplex speech+vision
    "minicpm5-1b": 1.0,
    # Qwen3
    "qwen2.5-0.5b": 0.5,
    "qwen3-0.6b": 0.6,
    "qwen3-1.7b": 1.7,
    "qwen3-4b": 4.0,
    "qwen3-8b": 8.0,
    "qwen3-14b": 14.0,
    "qwen3-30b-a3b": 30.0,
    # Llama
    "llama-3.2-1b": 1.0,
    "llama-3.2-3b": 3.0,
    # Gemma 3
    "gemma-3-1b": 1.0,
    "gemma-3-4b": 4.0,
    "gemma-3-12b": 12.0,
    "gemma-3-27b": 27.0,
    # NVIDIA Nemotron (at/near the ceiling — use ALONE)
    "nemotron-3-nano": 31.6,
    "nemotron-3-nano-omni": 30.0,
    # Cohere (non-commercial)
    "aya-expanse-8b": 8.0,
    "aya-expanse-32b": 32.0,
    # Speech
    "whisper-small": 0.244,
    "yarngpt2": 0.366,
    "yarngpt": 0.366,
    "sabiyarn-125m": 0.125,
    # Image (separate generation step; confirm on Discord whether it counts)
    "flux.1-schnell": 12.0,
    "flux.1-dev": 12.0,
    # ── Sponsor models confirmed at the kickoff livestream (June 2026) ──
    "flux.2-klein-4b": 4.0,        # Black Forest Labs — current sponsor image model (text2img + edit)
    "flux.2-klein-9b": 9.0,
    "mellum-2": 12.0,             # JetBrains — 12B MoE, code-optimized (thinking + instruct)
    "cohere-transcribe": 2.0,     # Cohere — 2B ASR, 14 languages
    "cohere-multilingual-3.3b": 3.3,  # Cohere — 70 langs; `earth` variant = best for African languages
    "minicpm-4.1-8b": 8.0,        # OpenBMB — deeper text reasoning
    "nemotron-3-nano-4b": 4.0,    # NVIDIA — edge model (RTX/Jetson, NPCs/games)
    "nemotron-cascade": 30.0,     # NVIDIA — math/code fine-tune of Nano (verify exact size)
    "nemotron-parse": 0.9,        # NVIDIA — <1B doc extraction (text/tables/markdown/bbox)
    "nemotron-colembed-4b": 4.0,  # NVIDIA — vision-language embeddings
    "nemotron-colembed-8b": 8.0,
    "llama-nemotron-embed-vl-1b": 1.0,
}

TINY_TITAN_B = 4.0  # "genuinely tiny" award threshold


def b(model_id: str) -> float:
    key = model_id.strip().lower()
    if key not in MODELS_B:
        raise KeyError(f"Unknown model '{model_id}'. Add it to params.MODELS_B with its TOTAL params (billions).")
    return MODELS_B[key]


def cap_check(model_ids):
    """Return (total_b, ok, detail_str) for a list of model ids in one submission."""
    total = sum(b(m) for m in model_ids)
    ok = total <= CAP_B + 1e-9
    parts = " + ".join(f"{m} ({b(m):g}B)" for m in model_ids)
    mark = "✓" if ok else "✗ OVER CAP"
    detail = f"{parts} = {total:g}B {mark} (cap {CAP_B:g}B)"
    return total, ok, detail


def is_tiny_titan(model_ids) -> bool:
    return sum(b(m) for m in model_ids) <= TINY_TITAN_B + 1e-9


if __name__ == "__main__":
    # Quick self-check of the playbook's worked examples.
    for stack in (
        ["gpt-oss-20b", "minicpm-v-4.6"],          # Fake-Alert Trainer → 22.3B
        ["whisper-small", "yarngpt2", "qwen3-4b"], # Voice WhatsApp Reader → ~4.6B
        ["nemotron-3-nano-omni"],                   # 30B alone
        ["whisper-small", "gpt-oss-20b"],          # Sermon Transcriber → 21.244B
    ):
        print(cap_check(stack)[2])
