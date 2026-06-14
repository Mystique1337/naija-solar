"""Modal, vision endpoint using Qwen2.5-VL-3B-Instruct to read appliances from photos.

Non-gated, Apache-2.0 (commercial OK), 3B, native transformers support (no custom code,
no gate, no token needed). OpenAI vision compatible (/v1/chat/completions with an
image_url part); the model lists the appliances and the app parses that into a selection.

  modal deploy _platform/modal/serving_vision.py
  → export BUILDSMALL_VISION_BASE_URL=https://<printed-url>/v1
"""
import os

import modal

MODEL = os.environ.get("VISION_MODEL", "Qwen/Qwen2.5-VL-3B-Instruct")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch", "torchvision", "transformers==4.51.3", "accelerate", "qwen-vl-utils",
                 "pillow", "fastapi[standard]", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
app = modal.App("buildsmall-vision")
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)

PROMPT = ("List every household electrical appliance you can see in this image, with a count for each. "
          "Use simple names like fridge, ceiling fan, standing fan, TV, air conditioner, light bulb, "
          "freezer, laptop, microwave, washing machine, water pump, decoder. Reply as a short comma list, "
          'for example: "1 fridge, 2 ceiling fans, 1 TV". If none, say none.')


@app.cls(image=image, gpu="L4", scaledown_window=300,
         min_containers=int(os.environ.get("MIN_CONTAINERS", "0")),
         max_containers=int(os.environ.get("MAX_CONTAINERS", "1")),   # cap GPUs/app (workspace has a 10-GPU limit)
         volumes={"/root/.cache/huggingface": cache})
class Vision:
    @modal.enter()
    def load(self):
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
        self.torch = torch
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL, torch_dtype=torch.bfloat16, device_map="cuda").eval()
        self.proc = AutoProcessor.from_pretrained(MODEL)

    def _describe(self, img, prompt):
        from qwen_vl_utils import process_vision_info
        messages = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": prompt}]}]
        text = self.proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self.proc(text=[text], images=image_inputs, videos=video_inputs,
                           padding=True, return_tensors="pt").to("cuda")
        with self.torch.no_grad():
            gen = self.model.generate(**inputs, max_new_tokens=256, do_sample=False)
        trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, gen)]
        return self.proc.batch_decode(trimmed, skip_special_tokens=True)[0].strip()

    @modal.asgi_app()
    def web(self):
        import base64
        import io

        from fastapi import FastAPI
        from PIL import Image

        api = FastAPI(redirect_slashes=False)

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
