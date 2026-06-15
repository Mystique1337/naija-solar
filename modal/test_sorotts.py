"""Modal — quick functional test of the fine-tuned SoroTTS adapter (no web function, so no deploy limit).

Loads the Orpheus base + the Shinzmann/sorotts LoRA adapter + SNAC, synthesises one phrase per
language, and uploads the clips to the model repo under `samples/` so you can listen on the Hub.

  modal run modal/test_sorotts.py
"""
import os

import modal

BASE = os.environ.get("SOROTTS_BASE", "hypaai/hypaai_orpheus_v5")
ADAPTER = os.environ.get("SOROTTS_ADAPTER", "Shinzmann/sorotts")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg")
    .pip_install("unsloth")
    .pip_install("snac", "soundfile", "soxr", "librosa", "peft", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "PYTHONUNBUFFERED": "1"})
)
app = modal.App("sorotts-test")
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)

DEMOS = [
    ("yor", "Yor1", "Ẹ kú àbọ̀ sí Nàìjíríà, orílẹ̀-èdè wa tó kún fún ìbùkún."),
    ("hau", "Hau1", "Sannu da zuwa Najeriya, ƙasarmu mai albarka."),
    ("ibo", "Ibo1", "Nnọọ na Naịjịrịa, obodo anyị nke jupụtara na ngọzi."),
    ("pcm", "NaijaA", "How far na? Wetin dey happen? Make we yarn small about dis our new pidgin voice."),
]


@app.function(image=image, gpu="L4", timeout=1800,
              volumes={"/root/.cache/huggingface": cache},
              secrets=[modal.Secret.from_name("huggingface")])
def test():
    import io

    import numpy as np
    import soundfile as sf
    import torch
    from huggingface_hub import HfApi, login
    from snac import SNAC
    from unsloth import FastLanguageModel

    tok_env = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if tok_env:
        login(tok_env)

    from huggingface_hub import snapshot_download
    local = snapshot_download(BASE, ignore_patterns=["adapter_*"])   # clean base, same as training
    for _f in ("adapter_config.json", "adapter_model.safetensors", "adapter_model.bin"):
        _p = os.path.join(local, _f)
        if os.path.exists(_p):
            os.remove(_p)
    model, tok = FastLanguageModel.from_pretrained(model_name=local, max_seq_length=2048, dtype=None, load_in_4bit=True)
    from peft import PeftModel
    model = PeftModel.from_pretrained(model, ADAPTER)
    FastLanguageModel.for_inference(model)
    snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda").eval()
    SOH, EOT, EOH, SOS, EOS, OFF = 128259, 128009, 128260, 128257, 128258, 128266

    def redistribute(codes):
        l1, l2, l3 = [], [], []
        for i in range((len(codes) + 1) // 7):
            b = 7 * i
            l1.append(codes[b]); l2.append(codes[b + 1] - 4096)
            l3.append(codes[b + 2] - 2 * 4096); l3.append(codes[b + 3] - 3 * 4096)
            l2.append(codes[b + 4] - 4 * 4096)
            l3.append(codes[b + 5] - 5 * 4096); l3.append(codes[b + 6] - 6 * 4096)
        dev = next(snac.parameters()).device
        c = [torch.tensor(x).unsqueeze(0).to(dev) for x in (l1, l2, l3)]
        with torch.inference_mode():
            return snac.decode(c).squeeze().cpu().numpy()

    def gen(text, voice):
        ids = tok(f"{voice}: {text}", return_tensors="pt").input_ids
        ids = torch.cat([torch.tensor([[SOH]]), ids, torch.tensor([[EOT, EOH]])], dim=1).to(model.device)
        out = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids), max_new_tokens=1200,
                             do_sample=True, temperature=0.6, top_p=0.95, repetition_penalty=1.1,
                             eos_token_id=EOS, use_cache=True)[0]
        sos = (out == SOS).nonzero(as_tuple=True)[0]
        out = out[sos[-1].item() + 1:] if len(sos) else out
        out = out[out != EOS]
        n = (out.size(0) // 7) * 7
        return redistribute([t.item() - OFF for t in out[:n]])

    api = HfApi()
    results = []
    for lang, voice, text in DEMOS:
        try:
            wave = gen(text, voice)
            buf = io.BytesIO()
            sf.write(buf, np.asarray(wave, dtype=np.float32), 24000, format="WAV")
            buf.seek(0)
            path = f"samples/{lang}_{voice}.wav"
            api.upload_file(path_or_fileobj=buf, path_in_repo=path, repo_id=ADAPTER, repo_type="model")
            dur = len(wave) / 24000
            results.append({"lang": lang, "voice": voice, "sec": round(dur, 1), "sample": path, "ok": dur > 0.5})
            print(f"  {lang}/{voice}: {dur:.1f}s -> {ADAPTER}/{path}")
        except Exception as e:
            results.append({"lang": lang, "voice": voice, "ok": False, "err": f"{type(e).__name__}: {str(e)[:80]}"})
            print(f"  {lang}/{voice}: FAILED {type(e).__name__}: {e}")
    return results


@app.local_entrypoint()
def main():
    out = test.remote()
    ok = sum(1 for r in out if r.get("ok"))
    print(f"\nSoroTTS test: {ok}/{len(out)} languages synthesised. Samples at "
          f"https://huggingface.co/{ADAPTER}/tree/main/samples")
    for r in out:
        print(" ", r)
