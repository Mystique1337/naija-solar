"""Modal — diagnose why the live Yoruba narration "sounds off".

Generates several Yoruba variants with the SAME model/serving logic, reports per-variant code
health (leaked control tokens, clamped SNAC codes -> tells noise/instability apart from content),
and uploads each clip to the model repo under samples/diag/ so you can A/B listen on the Hub:

  https://huggingface.co/Shinzmann/sorotts/tree/main/samples/diag

  modal run modal/diagnose_yoruba.py
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
app = modal.App("sorotts-diagnose")
cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)

# The exact live narration (4 sentences, code-switched: digits + English terms), and a clean
# pure-Yoruba control known to sound good, plus a "numbers + units spoken as Yoruba words" rewrite.
NARRATION = ("O ń lo tó 2.66 kilowatt lójúmọ́. Mo dábàá panel oòrùn 2, inverter 1.5 kVA kan, "
             "àti bátìrì 1. Ètò náà yóò ná nǹkan bíi náírà 1.9 million. "
             "Jọ̀wọ́ bèèrè lọ́wọ́ onímọ̀ tó ní ìwé àṣẹ.")
CLEAN = "Ẹ kú àbọ̀ sí Nàìjíríà, orílẹ̀-èdè wa tó kún fún ìbùkún."
# same plan, but numbers as Yoruba words and English tech terms softened/naturalised
NATURAL = ("Iná tí o ń lò tó ìwọ̀n mẹ́ta lójoojúmọ́. Mo dábàá pánẹ́ẹ̀lì oòrùn méjì, "
           "ẹ̀rọ̀ amúná ọ̀kan, àti bátìrì ọ̀kan. Ètò náà yóò ná tó mílíọ̀nù náírà méjì. "
           "Jọ̀wọ́ bi ọ̀mọ̀wé tó ní ìwé àṣẹ léèrè.")


@app.function(image=image, gpu="H200", timeout=1800,
              volumes={"/root/.cache/huggingface": cache},
              secrets=[modal.Secret.from_name("huggingface")])
def diagnose():
    import io
    import re

    import numpy as np
    import soundfile as sf
    import torch
    from huggingface_hub import HfApi, login, snapshot_download
    from peft import PeftModel
    from snac import SNAC
    from unsloth import FastLanguageModel

    tok_env = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if tok_env:
        login(tok_env)
    local = snapshot_download(BASE, ignore_patterns=["adapter_*"])
    for _f in ("adapter_config.json", "adapter_model.safetensors", "adapter_model.bin"):
        _p = os.path.join(local, _f)
        if os.path.exists(_p):
            os.remove(_p)
    model, tok = FastLanguageModel.from_pretrained(model_name=local, max_seq_length=2048, dtype=None, load_in_4bit=False)
    model = PeftModel.from_pretrained(model, ADAPTER)
    FastLanguageModel.for_inference(model)
    snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda").eval()
    dev = next(snac.parameters()).device
    SOH, EOT, EOH, SOAI, EOAI, SOS, EOS, PAD, OFF = (
        128259, 128009, 128260, 128261, 128262, 128257, 128258, 128263, 128266)

    def decode(codes):
        clamped = [0]
        def cl(v):
            if v < 0 or v > 4095:
                clamped[0] += 1
            return 0 if v < 0 else (4095 if v > 4095 else v)
        l1, l2, l3 = [], [], []
        for i in range((len(codes) + 1) // 7):
            b = 7 * i
            l1.append(cl(codes[b])); l2.append(cl(codes[b + 1] - 4096))
            l3.append(cl(codes[b + 2] - 2 * 4096)); l3.append(cl(codes[b + 3] - 3 * 4096))
            l2.append(cl(codes[b + 4] - 4 * 4096))
            l3.append(cl(codes[b + 5] - 5 * 4096)); l3.append(cl(codes[b + 6] - 6 * 4096))
        c = [torch.tensor(x).unsqueeze(0).to(dev) for x in (l1, l2, l3)]
        with torch.inference_mode():
            return snac.decode(c).squeeze().cpu().numpy(), clamped[0]

    def gen_chunk(text, voice, temp, max_new):
        ids = tok(f"{voice}: {text}", return_tensors="pt").input_ids
        ids = torch.cat([torch.tensor([[SOH]]), ids, torch.tensor([[EOT, EOH]])], dim=1).to(model.device)
        out = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids), max_new_tokens=max_new,
                             do_sample=True, temperature=temp, top_p=0.95, repetition_penalty=1.1,
                             eos_token_id=[EOS, EOAI], use_cache=True)[0]
        sos = (out == SOS).nonzero(as_tuple=True)[0]
        seq = out[sos[-1].item() + 1:] if len(sos) else out
        raw = [t.item() for t in seq]
        leaked = sum(1 for t in raw if t < OFF)          # control tokens sitting inside the code stream
        codes = [t - OFF for t in raw if t >= OFF]
        n = (len(codes) // 7) * 7
        if n < 7:
            return None, leaked, 0, 0.0
        wave, clamped = decode(codes[:n])
        return np.asarray(wave, dtype=np.float32), leaked, clamped, n

    def trim(w, thresh=0.012, hop=1024, pad=1536):
        if w is None or w.size < hop * 2:
            return w
        m = (w.size // hop) * hop
        rms = np.sqrt((w[:m].reshape(-1, hop) ** 2).mean(axis=1))
        loud = np.where(rms > thresh)[0]
        if loud.size == 0:
            return w[:hop]
        return w[max(0, int(loud[0]) * hop - pad): min(w.size, (int(loud[-1]) + 1) * hop + pad)]

    def synth(text, voice, temp=0.55, max_new=672, split=True):
        chunks = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()] if split else [text]
        sil = np.zeros(int(0.14 * 24000), dtype=np.float32)
        parts, tot_leak, tot_clamp, tot_frames = [], 0, 0, 0
        for ch in chunks[:4]:
            w, lk, cm, fr = gen_chunk(ch[:300], voice, temp, max_new)
            tot_leak += lk; tot_clamp += cm; tot_frames += fr
            w = trim(w)
            if w is not None and len(w) > 240:
                parts.append(w)
        if not parts:
            return None, tot_leak, tot_clamp, tot_frames
        out = []
        for i, w in enumerate(parts):
            out.append(w)
            if i < len(parts) - 1:
                out.append(sil)
        return np.concatenate(out), tot_leak, tot_clamp, tot_frames

    variants = [
        ("A_current_t055", NARRATION, dict(temp=0.55, split=True)),
        ("B_current_t040", NARRATION, dict(temp=0.40, split=True)),
        ("C_clean_baseline", CLEAN, dict(temp=0.55, split=True)),
        ("D_nosplit_t050", NARRATION, dict(temp=0.50, split=False, max_new=1400)),
        ("E_natural_words", NATURAL, dict(temp=0.50, split=True)),
    ]
    api = HfApi()
    report = []
    for name, text, kw in variants:
        try:
            wave, leak, clamp, frames = synth(text, "Yor1", **kw)
            if wave is None:
                report.append({"v": name, "ok": False}); print(f"{name}: NONE"); continue
            buf = io.BytesIO(); sf.write(buf, wave, 24000, format="WAV"); buf.seek(0)
            path = f"samples/diag/yo_{name}.wav"
            api.upload_file(path_or_fileobj=buf, path_in_repo=path, repo_id=ADAPTER, repo_type="model")
            dur = len(wave) / 24000
            clamp_pct = (100.0 * clamp / max(1, frames * 7))
            report.append({"v": name, "sec": round(dur, 1), "leaked": leak, "clamp": clamp,
                           "clamp_pct": round(clamp_pct, 2)})
            print(f"{name}: {dur:4.1f}s  leaked={leak}  clamped={clamp} ({clamp_pct:.2f}% of codes)  -> {path}")
        except Exception as e:
            report.append({"v": name, "ok": False, "err": f"{type(e).__name__}: {str(e)[:90]}"})
            print(f"{name}: FAILED {type(e).__name__}: {e}")
    return report


@app.local_entrypoint()
def main():
    rep = diagnose.remote()
    print("\n=== Yoruba diagnosis ===")
    for r in rep:
        print(" ", r)
    print(f"\nListen: https://huggingface.co/{ADAPTER}/tree/main/samples/diag")
