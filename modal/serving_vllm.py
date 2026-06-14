"""Modal — shared vLLM serving endpoint (OpenAI-compatible).

This is the heart of the Modal-Awards story: ONE endpoint serves a small model to
your whole slate. Every app calls it by setting:
    BUILDSMALL_BACKEND=openai
    BUILDSMALL_BASE_URL=https://<your-modal-url>/v1
    BUILDSMALL_API_KEY=<the key you set below>

Deploy it (URL is printed):     modal deploy _platform/modal/serving_vllm.py
Tear down:                      modal app stop buildsmall-vllm

Cost: ~$2.50/A100-80GB-hr; it scales to zero when idle (scaledown_window).
Your $250 credit ≈ 100 A100 hours.
"""
import os
import subprocess

import modal

MODEL = os.environ.get("MODEL", "Qwen/Qwen3-1.7B")        # 2.0B → Tiny Titan (≤4B); swap up for more headroom
GPU = os.environ.get("GPU", "L4")                          # L4 (24GB) is plenty for ≤4B; A100/H100 for 20B+
API_KEY = os.environ.get("VLLM_API_KEY", "change-me-set-a-strong-secret")  # set a real secret in prod
PORT = 8000

# hf_transfer makes weight downloads much faster; HF_TOKEN comes from the secret.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("vllm", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

app = modal.App("buildsmall-vllm")
# Cache weights on a Volume so restarts don't re-download.
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)
# Public models need no token. For GATED models: `modal secret create huggingface HF_TOKEN=...`
# then deploy with USE_HF_SECRET=1.
SECRETS = [modal.Secret.from_name("huggingface")] if os.environ.get("USE_HF_SECRET") else []


@app.function(
    image=image,
    gpu=GPU,
    timeout=24 * 60 * 60,
    scaledown_window=300,              # idle 5 min → scale to zero (stop billing)
    min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),  # set 1 to keep warm (real-time; ongoing cost)
    max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),  # cap GPUs/app (10-GPU workspace limit)
    volumes={"/root/.cache/huggingface": cache},
    secrets=SECRETS,
)
@modal.concurrent(max_inputs=64)       # many requests per GPU container (Modal 1.4 API)
@modal.web_server(port=PORT, startup_timeout=900)
def serve():
    cmd = (
        f"vllm serve {MODEL} --host 0.0.0.0 --port {PORT} "
        f"--api-key {API_KEY} --served-model-name {MODEL}"
    )
    print("starting:", cmd)
    subprocess.Popen(cmd, shell=True)


# Quick local sanity check (no GPU): `modal run _platform/modal/serving_vllm.py`
@app.local_entrypoint()
def main():
    print(f"Will serve {MODEL} on {GPU}. After `modal deploy`, set:")
    print("  BUILDSMALL_BACKEND=openai")
    print("  BUILDSMALL_BASE_URL=<printed web url>/v1")
    print(f"  BUILDSMALL_API_KEY={API_KEY}")
