"""Modal — verify the dual-model text endpoint answers as BOTH Qwen and MiniCPM.

  modal run modal/test_text_endpoint.py
"""
import os
import time

import modal

image = modal.Image.debian_slim(python_version="3.11").pip_install("requests")
app = modal.App("buildsmall-text-probe", image=image)
URL = "https://chidi-ashinze--buildsmall-vllm-serve.modal.run/v1/chat/completions"


@app.function(secrets=[modal.Secret.from_name("buildsmall-api")], timeout=900)
def probe():
    import requests
    key = os.environ.get("ENDPOINT_API_KEY", "") or os.environ.get("VLLM_API_KEY", "")
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    print("key set:", bool(key))
    for model in ["Qwen/Qwen3-1.7B", "openbmb/MiniCPM5-1B"]:
        t0 = time.time()
        try:
            r = requests.post(URL, headers=hdr, timeout=600, json={
                "model": model,
                "messages": [{"role": "user", "content": "In one short sentence, why is solar good for a Nigerian home?"}],
                "max_tokens": 80, "temperature": 0.6})
            dt = time.time() - t0
            if r.status_code != 200:
                print(f"{model}: HTTP {r.status_code} {r.text[:140]} ({dt:.0f}s)")
                continue
            j = r.json()
            served = j.get("model")
            txt = j["choices"][0]["message"]["content"].strip().replace("\n", " ")
            print(f"{model}: OK served={served} ({dt:.0f}s)\n   -> {txt[:160]}")
        except Exception as e:
            print(f"{model}: ERR {type(e).__name__} {str(e)[:120]}")


@app.local_entrypoint()
def main():
    probe.remote()
