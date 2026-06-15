"""Modal — ONE web function serving TWO small text models from a single slot.

Qwen3-1.7B and MiniCPM5-1B share one Modal web function (the `buildsmall-vllm` app, same `serve`
URL), so no extra web function is used (the workspace has an 8-web-function cap). The OpenAI
`/v1/chat/completions` endpoint routes by the requested `model`:

  - any "qwen"     -> Qwen/Qwen3-1.7B       (kept for the duplicate Space, unchanged)
  - any "minicpm"  -> openbmb/MiniCPM5-1B   (the main app's text; an OpenBMB MiniCPM model, < 4B)

Both are under the 4B "Tiny Titan" line, both self-hosted, both scale to zero. Deploying this REPLACES
the previous single-model vLLM server in the same slot, at the same URL, so nothing downstream changes
except that the endpoint now also answers to MiniCPM.

  modal deploy modal/serving_text_dual.py   # -> https://<workspace>--buildsmall-vllm-serve.modal.run/v1
"""
import os
import threading

import modal

QWEN = os.environ.get("QWEN_MODEL", "Qwen/Qwen3-1.7B")
MINICPM = os.environ.get("MINICPM_MODEL", "openbmb/MiniCPM5-1B")
GPU = os.environ.get("GPU", "L4")   # both models are ~1-1.7B; ~5-6 GB bf16 fits an L4 (24 GB) easily

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch", "transformers>=4.44", "accelerate", "sentencepiece",
                 "fastapi[standard]", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "PYTHONUNBUFFERED": "1"})
)
app = modal.App(os.environ.get("VLLM_APP", "buildsmall-vllm"))
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)
SECRETS = [modal.Secret.from_name("buildsmall-api"), modal.Secret.from_name("huggingface")]

_M = {}                       # lazily loaded {key: (model, tokenizer, model_id)}
_LOAD_LOCK = threading.Lock()
_GEN_LOCK = threading.Lock()  # serialise generation so concurrent requests do not clash on the GPU


def _get(key):
    if key in _M:
        return _M[key]
    with _LOAD_LOCK:
        if key in _M:
            return _M[key]
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        mid = MINICPM if key == "minicpm" else QWEN
        print("loading", key, "->", mid, flush=True)
        tok = AutoTokenizer.from_pretrained(mid, trust_remote_code=True)
        mdl = AutoModelForCausalLM.from_pretrained(
            mid, trust_remote_code=True, torch_dtype=torch.bfloat16).eval().to("cuda")
        _M[key] = (mdl, tok, mid)
        return _M[key]


@app.function(image=image, gpu=GPU, timeout=24 * 60 * 60, scaledown_window=300,
              min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),
              max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),
              volumes={"/root/.cache/huggingface": cache}, secrets=SECRETS)
@modal.concurrent(max_inputs=4)
@modal.asgi_app()
def serve():
    import torch
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    api = FastAPI()

    @api.middleware("http")
    async def _auth(request: Request, call_next):
        key = os.environ.get("ENDPOINT_API_KEY", "") or os.environ.get("VLLM_API_KEY", "")
        if key and request.headers.get("authorization") != f"Bearer {key}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    def _key(name):
        return "minicpm" if "minicpm" in (name or "").lower() else "qwen"

    import re

    def _gen(key, messages, max_tokens, temperature):
        mdl, tok, _ = _get(key)
        msgs, tkw = list(messages), {}
        if key == "qwen":
            tkw = {"enable_thinking": False}                       # Qwen3 template toggle -> direct answer
        elif not any(m.get("role") == "system" for m in msgs):     # MiniCPM has no toggle -> nudge it to answer directly
            msgs = [{"role": "system", "content": "You are a concise assistant. Answer directly and briefly. Do not show your reasoning."}] + msgs
        try:
            enc = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt", return_dict=True, **tkw)
        except TypeError:
            enc = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt", return_dict=True)
        input_ids = enc["input_ids"].to(mdl.device)
        attn = enc.get("attention_mask")
        attn = attn.to(mdl.device) if attn is not None else None
        in_len = input_ids.shape[1]
        floor = 320 if key == "qwen" else 700                      # MiniCPM reasons; give room to finish + answer
        with _GEN_LOCK, torch.inference_mode():
            out = mdl.generate(input_ids=input_ids, attention_mask=attn,
                               max_new_tokens=max(int(max_tokens), floor),
                               do_sample=temperature > 0, temperature=max(float(temperature), 0.01),
                               top_p=0.95, repetition_penalty=1.05,
                               pad_token_id=(tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id))
        txt = tok.decode(out[0][in_len:], skip_special_tokens=True)
        if "</think>" in txt:
            txt = txt.split("</think>")[-1]                        # keep only the post-reasoning answer
        txt = re.sub(r"<think>.*", "", txt, flags=re.DOTALL)       # drop any leftover/unclosed reasoning
        return txt.strip()

    @api.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": QWEN, "object": "model"}, {"id": MINICPM, "object": "model"}]}

    @api.post("/v1/chat/completions")
    @api.post("/chat/completions")
    def chat(body: dict):                       # sync -> FastAPI runs it in a threadpool
        key = _key(body.get("model"))
        msgs = body.get("messages") or [{"role": "user", "content": str(body.get("prompt", ""))}]
        mt = int(body.get("max_tokens") or 256)
        temp = body.get("temperature")
        temp = 0.7 if temp is None else float(temp)
        try:
            txt = _gen(key, msgs, mt, temp)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print("GEN ERROR:\n" + tb, flush=True)
            return JSONResponse({"error": f"{type(e).__name__}: {str(e)[:200]}",
                                 "where": [l.strip() for l in tb.splitlines() if 'serving_text_dual' in l or 'line' in l][-3:]},
                                status_code=500)
        return {"object": "chat.completion", "model": _M[key][2],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": txt}, "finish_reason": "stop"}]}

    return api
