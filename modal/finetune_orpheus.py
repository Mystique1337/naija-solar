"""Modal — fine-tune Soro-Orpheus (Orpheus-3B) for Yorùbá / Hausa / Igbo / Nigerian Pidgin TTS.

A faithful port of `Soro_Orpheus_Multilingual_Finetune.ipynb` to Modal. It:
  1. builds a SNAC-tokenised multilingual dataset (streamed from the HF catalog, cached on a Volume),
  2. LoRA-fine-tunes Hypa-Orpheus with Unsloth (one adapter for all four languages),
  3. saves the adapter + an auto-stamped model card, and pushes it to the Hub.

Usage:
  # cheap end-to-end smoke test (tiny caps, 1 epoch, ~minutes) — validates the whole pipeline + push:
  modal run modal/finetune_orpheus.py --smoke

  # full run (hours on A100; pushes to <your-hf>/orpheus-naija4-v1). --detach so it survives your laptop:
  modal run --detach modal/finetune_orpheus.py

  # tune knobs without editing the file, e.g. commercial-only data on an L4 with 4-bit:
  FT_GPU=L4 modal run --detach modal/finetune_orpheus.py --commercial-only --load-4bit

Requirements:
  * Modal secret `huggingface` with a WRITE-scoped HF_TOKEN (for the push, and for gated corpora).
  * For the gated NaijaVoices / Common Voice corpora, accept their terms on the HF page with that
    account — or pass --commercial-only to skip them (smaller, but a shippable, commercial licence).

The base model the notebook referenced (Hypa_Orpheus-3b-0.1-ft-unsloth-merged_16bit) was removed,
so this defaults to the current `hypaai/hypaai_orpheus_v5` (same Orpheus-3B, adapted to yor/hau/ibo).
"""
import os

import modal

GPU = os.environ.get("FT_GPU", "A100-40GB")   # A100 → bf16 LoRA (best); set FT_GPU=L4 for 4-bit QLoRA

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "build-essential")
    # Unsloth pulls a CUDA torch + xformers + bitsandbytes + peft + trl. Then the audio/data extras.
    .pip_install("unsloth")
    .pip_install("snac", "soundfile", "soxr", "librosa", "tqdm", "pandas",
                 "datasets>=3.4.1,<4.0.0", "huggingface_hub[hf_transfer]", "hf_transfer")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

app = modal.App("soro-orpheus-finetune")
hf_cache = modal.Volume.from_name("buildsmall-hf-cache", create_if_missing=True)   # shared model/data cache
work = modal.Volume.from_name("orpheus-finetune", create_if_missing=True)          # tokenised set + adapter

# Defaults mirror the notebook's Control Panel. Override per-run via the `cfg` dict / CLI flags.
DEFAULTS = dict(
    langs=["yor", "hau", "ibo", "pcm"],
    commercial_only=False, allow_sharealike=True, allow_unverified=False, allow_paid=False,
    include_asr_sources=True,
    smoke_test=False, per_source_cap=3000,
    min_clip_sec=0.7, max_clip_sec=15.0, max_seq_length=2048,
    model_name="hypaai/hypaai_orpheus_v5", load_in_4bit=False,
    lora_r=64, lora_alpha=64, lora_dropout=0.0,
    num_epochs=2, learning_rate=2e-4, batch_size=1, grad_accum=4, warmup_ratio=0.03,
    version="v1", add_bibletts=True, bibletts_hausa_hf=True, bibletts_yoruba_openslr=False,
    bibletts_cap=4000, push_model=True, force_rebuild=False,
    name="sorotts",   # HF repo name for the pushed model: <hf-user>/sorotts (or sorotts-smoke)
)


@app.function(image=image, gpu=GPU, timeout=24 * 60 * 60, retries=0,
              volumes={"/root/.cache/huggingface": hf_cache, "/work": work},
              secrets=[modal.Secret.from_name("huggingface")])
def finetune(cfg: dict | None = None):
    import hashlib  # noqa: F401  (kept for parity with the notebook's speaker hashing)
    import json
    import re

    import numpy as np
    import torch
    from snac import SNAC
    from unsloth import FastLanguageModel

    C = {**DEFAULTS, **(cfg or {})}
    LANGS = C["langs"]
    LANG_TITLE = {"yor": "Yoruba", "hau": "Hausa", "ibo": "Igbo", "pcm": "Pidgin"}
    VOICE_PREFIX = {"yor": "Yor", "hau": "Hau", "ibo": "Ibo", "pcm": "Naija"}
    MAX_SEQ_LENGTH = C["max_seq_length"]
    MIN_CLIP_SEC, MAX_CLIP_SEC = C["min_clip_sec"], C["max_clip_sec"]
    PRESERVE_SPEAKER_SOURCES = {"pidgin_npc"}
    MAX_SPK_PER_SOURCE = 12
    VERSION = "smoke" if C["smoke_test"] else C["version"]
    NAME = C["name"]
    repo_name = f"{NAME}-smoke" if C["smoke_test"] else NAME   # HF repo: <hf-user>/sorotts
    OUT_DIR = f"/work/{repo_name}"
    CACHE_DIR = f"/work/tok-cache-{'smoke' if C['smoke_test'] else 'full'}"

    PER_SOURCE_CAP = C["per_source_cap"]
    CAP_BY_KEY = {"fleurs_r": 5000, "hypa_fleurs": 5000, "waxal_tts": 5000, "iroyinspeech": 5000,
                  "openslr86": 5000, "pidgin_npc": 100000, "naijavoices": 6000, "common_voice": 3000,
                  "fleurs": 4000, "waxal_asr": 4000, "afrodigits": 1500, "naija_cv_aggr": 3000}
    if C["smoke_test"]:
        PER_SOURCE_CAP = 40
        CAP_BY_KEY = {k: 40 for k in CAP_BY_KEY}
    TTS_QUALITY_RANK = {"restored": 0, "studio": 1, "read": 2, "natural": 3, "aggregated": 4, "telephone": 5}

    # ── HF auth ──────────────────────────────────────────────────────────────────
    from huggingface_hub import HfApi, login, whoami
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        login(token)
    hf_user = None
    try:
        hf_user = whoami()["name"]
        print("HF user:", hf_user)
    except Exception as e:
        print("HF auth note:", type(e).__name__, "— gated data + push need a WRITE HF_TOKEN in the `huggingface` secret")

    # ── registry (the notebook's self-contained fallback) + Pidgin extension ─────
    from dataclasses import dataclass
    from enum import Enum
    from typing import Dict, Optional, Tuple

    class License(Enum):
        CC0 = "CC0-1.0"; CC_BY = "CC-BY-4.0"; APACHE2 = "Apache-2.0"; MIT = "MIT"
        CC_BY_SA = "CC-BY-SA-4.0"; CC_BY_NC_SA = "CC-BY-NC-SA-4.0"; CC_BY_NC = "CC-BY-NC-4.0"
        LDC = "LDC (paid)"; UNKNOWN = "UNKNOWN"
    PERMISSIVE = {License.CC0, License.CC_BY, License.APACHE2, License.MIT}
    SHAREALIKE = {License.CC_BY_SA}
    NONCOMMERCIAL = {License.CC_BY_NC, License.CC_BY_NC_SA}

    class Task(Enum):
        ASR = "asr"; TTS = "tts"; BOTH = "asr+tts"

    @dataclass
    class DatasetSpec:
        key: str; name: str; repo: Optional[str]; configs: Dict[str, Optional[str]]
        text_keys: Tuple[str, ...]; audio_key: str; license: "License"; task: "Task"
        langs: Tuple[str, ...]; quality: str; hours: str
        gated: bool = False; manual: bool = False; aux_english: bool = False
        sr: Optional[int] = None; notes: str = ""
        @property
        def permissive(self): return self.license in PERMISSIVE
        @property
        def sharealike(self): return self.license in SHAREALIKE
        @property
        def noncommercial(self): return self.license in NONCOMMERCIAL
        def tier(self):
            if self.permissive: return "PERMISSIVE"
            if self.sharealike: return "SHARE-ALIKE"
            if self.noncommercial: return "NON-COMMERCIAL"
            return "RESTRICTED"

    # Curated to sources with configs/columns verified live on the HF datasets-server (June 2026).
    CATALOG = [
        # Pidgin — the new language (cols: sentence, filename, audio)
        DatasetSpec("pidgin_npc", "Nigerian Pidgin v1.0", "asr-nigerian-pidgin/nigerian-pidgin-1.0",
                    {"pcm": "default"}, ("sentence",), "audio", License.CC_BY, Task.BOTH,
                    ("pcm",), "read", "~4-5h", sr=16000, notes="speaker id in filename"),
        # WAXAL clean single-speaker TTS (cols: text, audio; configs yor_tts/hau_tts/ibo_tts)
        DatasetSpec("waxal_tts", "WAXAL TTS", "google/WaxalNLP",
                    {"yor": "yor_tts", "hau": "hau_tts", "ibo": "ibo_tts"}, ("text",), "audio",
                    License.CC_BY, Task.TTS, ("yor", "hau", "ibo"), "studio", "single-spk"),
        # FLEURS read speech (configs yo_ng/ha_ng/ig_ng verified)
        DatasetSpec("fleurs", "FLEURS", "google/fleurs",
                    {"yor": "yo_ng", "hau": "ha_ng", "ibo": "ig_ng"}, ("transcription", "raw_transcription"),
                    "audio", License.CC_BY, Task.ASR, ("yor", "hau", "ibo"), "read", "~12h/lang"),
        # NaijaVoices — large gated corpus (terms accepted; configs <lang>-batch-0..2)
        DatasetSpec("naijavoices", "NaijaVoices", "naijavoices/naijavoices-dataset",
                    {"yor": "yoruba-batch-0", "hau": "hausa-batch-0", "ibo": "igbo-batch-0"},
                    ("transcript", "sentence", "text", "normalized_text"), "audio", License.CC_BY_NC_SA, Task.BOTH,
                    ("yor", "hau", "ibo"), "natural", "~600h/lang", gated=True),
    ]

    def usable(s):
        if s.license == License.LDC and not C["allow_paid"]: return False
        if s.license == License.UNKNOWN and not C["allow_unverified"]: return False
        if C["commercial_only"]:
            if s.noncommercial: return False
            if s.sharealike and not C["allow_sharealike"]: return False
        return True

    def sources_for(lang, task):
        out = []
        for s in CATALOG:
            if s.aux_english: continue
            if lang not in s.langs: continue
            if not (s.task == task or s.task == Task.BOTH): continue
            if not usable(s): continue
            out.append(s)
        return out

    def effective_license(specs):
        specs = list(specs)
        if any(s.noncommercial for s in specs): return "cc-by-nc-sa-4.0"
        if any(s.sharealike for s in specs): return "cc-by-sa-4.0"
        return "cc-by-4.0"

    # ── load base + SNAC ─────────────────────────────────────────────────────────
    # hypaai_orpheus_v5 ships its full merged weights AND its own LoRA adapter files. Download the
    # full model but EXCLUDE the adapter, so Unsloth loads a clean base we can attach a fresh,
    # trainable LoRA to (same tokenizer/vocab, so the cached tokenised set stays valid).
    print(f"Loading {C['model_name']} as a clean base (its own adapter excluded) …")
    from huggingface_hub import snapshot_download
    local = snapshot_download(C["model_name"], ignore_patterns=["adapter_*"])
    for _f in ("adapter_config.json", "adapter_model.safetensors", "adapter_model.bin"):
        _p = os.path.join(local, _f)
        if os.path.exists(_p):
            os.remove(_p)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=local, max_seq_length=MAX_SEQ_LENGTH, dtype=None, load_in_4bit=C["load_in_4bit"])
    snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda").eval()
    SNAC_DEV = next(snac_model.parameters()).device   # SNAC has no .device attribute
    SOH, EOT, EOH, SOAI, EOAI, SOS, EOS, PAD, AUDIO_OFFSET = (
        128259, 128009, 128260, 128261, 128262, 128257, 128258, 128263, 128266)

    import soxr   # tiny, fast, high-quality resampler — no torch dependency, so no ABI clash with Unsloth

    def tokenise_audio(waveform, sr):
        arr = np.asarray(waveform, dtype=np.float32)
        if arr.ndim > 1: arr = arr.mean(axis=1)
        if sr != 24000:
            arr = soxr.resample(arr, sr, 24000)
        wav = torch.from_numpy(np.ascontiguousarray(arr, dtype=np.float32)).unsqueeze(0).unsqueeze(0).to(SNAC_DEV)
        with torch.inference_mode():
            codes = snac_model.encode(wav)
        flat = []
        for i in range(codes[0].shape[1]):
            flat += [codes[0][0][i].item() + AUDIO_OFFSET,
                     codes[1][0][2 * i].item() + AUDIO_OFFSET + 4096,
                     codes[2][0][4 * i].item() + AUDIO_OFFSET + 2 * 4096,
                     codes[2][0][4 * i + 1].item() + AUDIO_OFFSET + 3 * 4096,
                     codes[1][0][2 * i + 1].item() + AUDIO_OFFSET + 4 * 4096,
                     codes[2][0][4 * i + 2].item() + AUDIO_OFFSET + 5 * 4096,
                     codes[2][0][4 * i + 3].item() + AUDIO_OFFSET + 6 * 4096]
        return flat

    def build_sequence(voice, text, code_ids):
        text_ids = tokenizer.encode(f"{voice}: {text}", add_special_tokens=True)
        text_ids.append(EOT)
        return [SOH] + text_ids + [EOH] + [SOAI, SOS] + code_ids + [EOS, EOAI]

    # ── build (or reuse) the SNAC-tokenised dataset ──────────────────────────────
    from datasets import Dataset, load_dataset, load_from_disk
    from tqdm.auto import tqdm

    summary = []
    ft_ds = None
    if os.path.isdir(CACHE_DIR) and not C["force_rebuild"]:
        try:
            ft_ds = load_from_disk(CACHE_DIR)
            print(f"Reusing cached tokenised set: {len(ft_ds)} clips at {CACHE_DIR}")
            MODEL_LICENSE = "cc-by-sa-4.0" if C["commercial_only"] else "cc-by-nc-sa-4.0"
        except Exception as e:
            print("cache invalid -> rebuilding:", type(e).__name__)
            ft_ds = None
    if ft_ds is None:
        def resolve_sources(lang):
            seen, out = set(), []
            tasks = [Task.TTS] + ([Task.ASR] if C["include_asr_sources"] else [])
            for tk in tasks:
                for s in sources_for(lang, tk):
                    if s.key in seen or s.repo is None or s.manual: continue
                    if s.configs.get(lang) is None: continue
                    seen.add(s.key); out.append(s)
            out.sort(key=lambda s: TTS_QUALITY_RANK.get(s.quality, 9))
            return out

        _voice_map, _voice_n = {}, {}

        def _speaker_key(ex):
            fn = ex.get("filename") or ex.get("path") or ""
            m = re.search(r"_pcm_([0-9a-zA-Z]+)_", str(fn))
            if m: return m.group(1)
            for k in ("speaker_id", "speaker", "client_id"):
                if ex.get(k) not in (None, ""): return str(ex[k])
            return None

        def voice_for(lang, spec, ex, src_idx):
            prefix = VOICE_PREFIX[lang]
            if spec.key in PRESERVE_SPEAKER_SOURCES:
                sk = _speaker_key(ex)
                if sk is not None:
                    mk = (prefix, sk)
                    if mk not in _voice_map:
                        idx = _voice_n.get(prefix, 0)
                        use = min(idx, MAX_SPK_PER_SOURCE - 1)
                        _voice_map[mk] = f"{prefix}{chr(65 + use)}"
                        _voice_n[prefix] = idx + 1
                    return _voice_map[mk]
            return f"{prefix}{src_idx}"

        def _text_of(ex, keys):
            return next((ex[k] for k in keys if k in ex and ex[k]), None)

        from datasets import Audio

        def encode_source(lang, spec, src_idx, cap):
            # Everything (load + iterate) is guarded, so a single bad/gated/script source is skipped,
            # never crashing the build. trust_remote_code=True enables script datasets (FLEURS, WAXAL);
            # cast_column(Audio(24000)) forces a uniform decode + resample so every source yields arrays.
            rows, secs, voices, n = [], 0.0, set(), 0
            seen = no_data = bad_dur = enc_fail = 0
            first_err = [""]
            keys = spec.text_keys or ("text", "sentence", "transcript", "raw_transcription", "transcription")
            try:
                ds = load_dataset(spec.repo, spec.configs[lang], split="train",
                                  streaming=True, trust_remote_code=True)
                try:
                    ds = ds.cast_column(spec.audio_key, Audio(sampling_rate=24000))
                except Exception:
                    pass
                for ex in tqdm(ds, desc=f"    {spec.key}", leave=False):
                    if n >= cap:
                        break
                    seen += 1
                    try:
                        text = _text_of(ex, keys)
                        audio = ex.get(spec.audio_key) if isinstance(ex.get(spec.audio_key), dict) else ex.get("audio")
                        if not text or not isinstance(audio, dict) or audio.get("array") is None:
                            no_data += 1
                            continue
                        arr, sr = audio["array"], audio["sampling_rate"]
                        dur = len(arr) / sr
                        if dur < MIN_CLIP_SEC or dur > MAX_CLIP_SEC:
                            bad_dur += 1
                            continue
                        seq = build_sequence(voice_for(lang, spec, ex, src_idx), text, tokenise_audio(arr, sr))
                        if len(seq) > MAX_SEQ_LENGTH:
                            continue
                        rows.append({"input_ids": seq, "attention_mask": [1] * len(seq), "labels": seq})
                        secs += dur; voices.add(voice_for(lang, spec, ex, src_idx)); n += 1
                    except Exception as ee:                       # a single corrupt clip is skipped, not the source
                        enc_fail += 1
                        if not first_err[0]:
                            first_err[0] = f"{type(ee).__name__}: {str(ee)[:90]}"
                        continue
            except Exception as e:
                hint = " (accept its terms + token access)" if spec.gated else ""
                print(f"      ! {spec.key}: skipped ({type(e).__name__}: {str(e)[:70]}){hint}")
            if not rows and seen:
                print(f"      (diag {spec.key}: seen={seen} no_audio/text={no_data} bad_dur={bad_dur} "
                      f"enc_fail={enc_fail} firsterr={first_err[0]})")
            return rows, secs, voices

        all_rows = []
        for lang in LANGS:
            srcs = resolve_sources(lang)
            print(f"\n=== {LANG_TITLE[lang]} ({lang}) — {len(srcs)} source(s): {[s.key for s in srcs]} ===")
            for i, spec in enumerate(srcs, start=1):
                cap = CAP_BY_KEY.get(spec.key, PER_SOURCE_CAP)
                rows, secs, voices = encode_source(lang, spec, i, cap)
                all_rows.extend(rows)
                summary.append({"lang": LANG_TITLE[lang], "source": spec.key, "clips": len(rows),
                                "minutes": round(secs / 60, 1), "voices": ",".join(sorted(voices)),
                                "tier": spec.tier(), "license": spec.license.value})
                print(f"  · {spec.key:<13} -> {len(rows)} clips, {secs / 60:.1f} min, voices {sorted(voices)}")

        # BibleTTS booster (Hausa via HF streaming mirror; Yoruba optional big download)
        if C["add_bibletts"]:
            def _encode_bible(voice, rec_iter, cap, tag):
                rows, secs, n = [], 0.0, 0
                for text, arr, sr in tqdm(rec_iter, desc=f"    {tag}", leave=False):
                    if n >= cap: break
                    if not text: continue
                    try:
                        dur = len(arr) / sr
                    except Exception:
                        continue
                    if dur < MIN_CLIP_SEC or dur > MAX_CLIP_SEC: continue
                    try:
                        seq = build_sequence(voice, str(text).strip(), tokenise_audio(arr, sr))
                    except Exception:
                        continue
                    if len(seq) > MAX_SEQ_LENGTH: continue
                    rows.append({"input_ids": seq, "attention_mask": [1] * len(seq), "labels": seq})
                    secs += dur; n += 1
                return rows, secs
            if C["bibletts_hausa_hf"] and "hau" in LANGS:
                try:
                    import io
                    import soundfile as sf
                    from datasets import Audio
                    hds = load_dataset("vpetukhov/bible_tts_hausa", split="train", streaming=True, trust_remote_code=True)
                    try:
                        hds = hds.cast_column("audio", Audio())
                    except Exception:
                        pass

                    def _iter_hau():
                        for ex in hds:
                            a = ex.get("audio")
                            if not isinstance(a, dict): continue
                            if a.get("array") is not None:
                                yield ex.get("sentence"), a["array"], a["sampling_rate"]
                            elif a.get("bytes"):
                                arr, sr = sf.read(io.BytesIO(a["bytes"]))
                                if getattr(arr, "ndim", 1) > 1: arr = arr.mean(axis=1)
                                yield ex.get("sentence"), arr, sr
                    r, s = _encode_bible("HauBible", _iter_hau(), C["bibletts_cap"], "bibletts_hau")
                    all_rows += r
                    summary.append({"lang": "Hausa", "source": "bibletts", "clips": len(r),
                                    "minutes": round(s / 60, 1), "voices": "HauBible",
                                    "tier": "SHARE-ALIKE", "license": "cc-by-sa-4.0"})
                    print(f"  Hausa BibleTTS: {len(r)} clips -> voice HauBible")
                except Exception as e:
                    print(f"  ! Hausa BibleTTS failed: {type(e).__name__}: {e}")

        if not all_rows:
            raise RuntimeError("0 training clips collected — see the per-source diagnostics above "
                               "(gated terms / configs / encoding). Nothing was cached.")
        ft_ds = Dataset.from_list(all_rows).shuffle(seed=1234)
        MODEL_LICENSE = effective_license([s for lang in LANGS for s in resolve_sources(lang)])
        if any(r["license"] == "cc-by-sa-4.0" for r in summary) and MODEL_LICENSE == "cc-by-4.0":
            MODEL_LICENSE = "cc-by-sa-4.0"
        import shutil
        shutil.rmtree(CACHE_DIR, ignore_errors=True)   # clear any stale/partial cache first
        os.makedirs(CACHE_DIR, exist_ok=True)
        ft_ds.save_to_disk(CACHE_DIR)
        work.commit()
        print(f"\nTOTAL {len(ft_ds)} clips | licence {MODEL_LICENSE} | cached -> {CACHE_DIR}")

    if len(ft_ds) == 0:
        raise RuntimeError("0 training clips — check HF login / gated terms / network.")

    # ── attach LoRA + train ──────────────────────────────────────────────────────
    from transformers import Trainer, TrainingArguments
    try:
        FastLanguageModel.for_training(model)
    except Exception:
        pass
    model = FastLanguageModel.get_peft_model(
        model, r=C["lora_r"], lora_alpha=C["lora_alpha"], lora_dropout=C["lora_dropout"], bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=1234)

    class OrpheusCollator:
        def __init__(self, pad_id): self.pad_id = pad_id
        def __call__(self, feats):
            m = max(len(f["input_ids"]) for f in feats)
            ids, attn, lab = [], [], []
            for f in feats:
                x = list(f["input_ids"]); p = m - len(x)
                ids.append(x + [self.pad_id] * p)
                attn.append([1] * len(x) + [0] * p)
                lab.append(x + [-100] * p)
            return {"input_ids": torch.tensor(ids), "attention_mask": torch.tensor(attn), "labels": torch.tensor(lab)}

    bf16 = torch.cuda.is_bf16_supported()
    args = TrainingArguments(
        output_dir="/work/ckpt", per_device_train_batch_size=C["batch_size"],
        gradient_accumulation_steps=C["grad_accum"], warmup_ratio=C["warmup_ratio"],
        num_train_epochs=1 if C["smoke_test"] else C["num_epochs"], learning_rate=C["learning_rate"],
        logging_steps=25, save_steps=500, save_total_limit=2, bf16=bf16, fp16=not bf16,
        optim="adamw_8bit", seed=1234, report_to="none", remove_unused_columns=False)
    Trainer(model=model, args=args, train_dataset=ft_ds, data_collator=OrpheusCollator(PAD)).train()
    print("Training complete.")

    # ── save adapter + auto-stamped card, push to the Hub ────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    model.save_pretrained(OUT_DIR)
    tokenizer.save_pretrained(OUT_DIR)
    legend = {}
    for r in summary:
        for v in (r["voices"].split(",") if r["voices"] else []):
            if v: legend.setdefault(v, set()).add(r["source"])
    legend_lines = "\n".join(f"- **{v}** — {', '.join(sorted(s))}" for v, s in sorted(legend.items())) or "- (none)"
    card = f"""---
license: {MODEL_LICENSE}
language: [yo, ha, ig, pcm]
base_model: {C['model_name']}
tags: [tts, orpheus, snac, yoruba, hausa, igbo, nigerian-pidgin, unsloth, lora]
---

# SoroTTS ({VERSION})

LoRA fine-tune of **Hypa-Orpheus** (Orpheus-3B + SNAC) covering **Yorùbá, Hausa, Igbo and
Nigerian Pidgin** in one adapter. Trained on Modal.

- Base: `{C['model_name']}`
- Training clips: {len(ft_ds)} | Effective licence: **{MODEL_LICENSE}**

## Voices
{legend_lines}
"""
    with open(os.path.join(OUT_DIR, "README.md"), "w") as f:
        f.write(card)
    work.commit()

    pushed = None
    if C["push_model"] and hf_user:
        try:
            pushed = f"{hf_user}/{repo_name}"
            api = HfApi()
            api.create_repo(pushed, repo_type="model", exist_ok=True)
            api.upload_folder(folder_path=OUT_DIR, repo_id=pushed, repo_type="model",
                              commit_message=f"Soro-Orpheus Naija-4 {VERSION} ({len(ft_ds)} clips)")
            print("Pushed ->", pushed)
        except Exception as e:                      # the adapter is safe on the Volume regardless
            print("Push FAILED — is the `huggingface` secret a WRITE token?", type(e).__name__, str(e)[:140])
            pushed = None

    return {"clips": len(ft_ds), "licence": MODEL_LICENSE, "pushed": pushed,
            "voices": sorted(legend.keys()), "out_dir": OUT_DIR}


@app.local_entrypoint()
def main(smoke: bool = False, commercial_only: bool = False, load_4bit: bool = False,
         epochs: int = 0, version: str = ""):
    """Launch a fine-tune run. Examples:
        modal run modal/finetune_orpheus.py --smoke
        modal run --detach modal/finetune_orpheus.py
        FT_GPU=L4 modal run --detach modal/finetune_orpheus.py --commercial-only --load-4bit
    """
    cfg = {"smoke_test": smoke, "commercial_only": commercial_only, "load_in_4bit": load_4bit}
    if epochs:
        cfg["num_epochs"] = epochs
    if version:
        cfg["version"] = version
    print("Launching on GPU", GPU, "with", cfg)
    out = finetune.remote(cfg)
    print("DONE:", out)
