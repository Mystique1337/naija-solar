"""Modal — vision endpoint using OpenBMB MiniCPM-V-2 (3.4B) to read appliances from a photo.

MiniCPM-V-2 (openbmb/MiniCPM-V-2, 3.43B) is small enough for the Tiny Titan badge (every model
≤4B) while still being an OpenBMB MiniCPM model (Best MiniCPM Build). Unlike MiniCPM-V-2.6 it
needs NO flash_attn, and its chat() takes the image as a separate argument.

    modal deploy _platform/modal/serving_minicpm.py
    export BUILDSMALL_VISION_BASE_URL=https://<printed-url>/v1

Deploys into the SAME `buildsmall-vision` app slot (same URL the app already points at), so
nothing else changes. To trade Tiny Titan for more accuracy, set VISION_MODEL=openbmb/MiniCPM-V-2_6
(8B) — but that needs flash_attn + transformers==4.40 (see git history) and forfeits Tiny Titan.

OpenAI vision compatible: POST /v1/chat/completions with an image_url part; the model returns a
short comma list of appliances which the app parses into a selection.
"""
import os

import modal

MODEL = os.environ.get("VISION_MODEL", "openbmb/MiniCPM-V-2")   # 3.43B → ≤4B (Tiny Titan) + MiniCPM (OpenBMB)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.1.2", "torchvision==0.16.2", "transformers==4.36.0", "accelerate",
                 "sentencepiece", "timm==0.9.10", "numpy<2", "pillow",
                 "fastapi[standard]", "huggingface_hub[hf_transfer]")
    .run_commands("pip install 'peft==0.10.0'")   # MiniCPM-V-2's modeling code imports peft; own layer guarantees it
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
app = modal.App(os.environ.get("VISION_APP", "buildsmall-vision"))
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)
# huggingface for model pulls; buildsmall-api carries ENDPOINT_API_KEY so the endpoint is gated like the others.
SECRETS = [modal.Secret.from_name("huggingface"), modal.Secret.from_name("buildsmall-api")]

PROMPT = ("List every household electrical appliance you can see in this image, with a count for each. "
          "Use simple names like fridge, ceiling fan, standing fan, TV, air conditioner, light bulb, "
          "freezer, laptop, microwave, washing machine, water pump, decoder. Reply as a short comma list, "
          'for example: "1 fridge, 2 ceiling fans, 1 TV". If none, say none.')


@app.cls(image=image, gpu="L4", scaledown_window=300,
         min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),
         max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),   # cap GPUs/app (workspace has a 10-GPU limit)
         volumes={"/root/.cache/huggingface": cache}, secrets=SECRETS)
class Vision:
    @modal.enter()
    def load(self):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        # float32 avoids a dtype clash in MiniCPM-V-2's resampler (a float32 pos-embed vs a bf16
        # model); 3.43B in fp32 (~14GB) fits the L4, and photos are an occasional, optional input.
        self.model = AutoModel.from_pretrained(
            MODEL, trust_remote_code=True, torch_dtype=torch.float32).eval().to("cuda")
        self.tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)

    def _describe(self, img, prompt):
        msgs = [{"role": "user", "content": prompt}]
        with self.torch.no_grad():
            res = self.model.chat(image=img, msgs=msgs, context=None, tokenizer=self.tok, sampling=False)
        if isinstance(res, tuple):   # MiniCPM-V-2 returns (answer, context, _)
            res = res[0]
        return (res or "").strip()

    @modal.asgi_app()
    def web(self):
        import base64
        import io

        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from PIL import Image

        api = FastAPI(redirect_slashes=False)

        @api.middleware("http")
        async def _auth(request: Request, call_next):
            key = os.environ.get("ENDPOINT_API_KEY", "")   # from the buildsmall-api secret, at runtime
            if key and request.headers.get("authorization") != f"Bearer {key}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

        @api.post("/v1/chat/completions")
        @api.post("/chat/completions")
        async def chat(body: dict):
            prompt, img = PROMPT, None
            for msg in body.get("messages", []):
                c = msg.get("content")
                if isinstance(c, list):
                    for part in c:
                        if part.get("type") == "text" and part.get("text"):
                            prompt = part["text"]
                        elif part.get("type") == "image_url":
                            url = part["image_url"]["url"]
                            if url.startswith("data:"):
                                img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
                elif isinstance(c, str):
                    prompt = c
            out = "(no image)" if img is None else self._describe(img, prompt)
            return {"choices": [{"message": {"role": "assistant", "content": out}}]}

        return api
