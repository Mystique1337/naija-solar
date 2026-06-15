"""Modal — exercise the DEPLOYED buildsmall-tts endpoint for all 5 languages.

Runs server-side (so the Bearer key stays in the `buildsmall-api` secret), POSTs a real
4-sentence plan per language to the live `/v1/audio/speech`, and reports wall time + audio
duration. Use it to confirm the batched SoroTTS path speaks the whole plan and is fast.

  modal run modal/test_tts_endpoint.py
"""
import os
import time

import modal

image = modal.Image.debian_slim(python_version="3.11").pip_install("requests", "soundfile", "numpy")
app = modal.App("buildsmall-tts-probe", image=image)

URL = "https://chidi-ashinze--buildsmall-tts-tts-web.modal.run/v1/audio/speech"

# the actual narration the app sends (already run through _for_tts: kVA -> "k V A")
PLANS = {
    "en": "You use about 4.2 kilowatt hours a day. I recommend 6 solar panels, a 3.5 k V A inverter, and 4 batteries. The system costs about 1.2 million naira. Please confirm with a licensed installer.",
    "pcm": "You dey use about 4.2 kilowatt every day. You go need 6 solar panels, one 3.5 k V A inverter, and 4 batteries. E go cost around 1.2 million naira. Make you confirm with a licensed installer.",
    "yo": "O ń lo tó 4.2 kilowatt lójúmọ́. Mo dábàá panel oòrùn 6, inverter 3.5 k V A kan, àti bátìrì 4. Ètò náà yóò ná nǹkan bíi náírà 1.2 million. Jọ̀wọ́ bèèrè lọ́wọ́ onímọ̀ tó ní ìwé àṣẹ.",
    "ha": "Kana amfani da kusan 4.2 kilowatt a kullum. Ina ba da shawarar panel hasken rana 6, inverter 3.5 k V A ɗaya, da batir 4. Tsarin zai kai kusan naira 1.2 million. Don Allah ka tabbatar da ƙwararren mai shigarwa.",
    "ig": "Ị na-eji ihe dịka 4.2 kilowatt kwa ụbọchị. Ana m atụ aro panel anyanwụ 6, otu inverter 3.5 k V A, na batrị 4. Usoro a ga-efu ihe dịka naịra 1.2 million. Biko kwado ya na onye ọrụ nwere ikike.",
}


@app.function(secrets=[modal.Secret.from_name("buildsmall-api")], timeout=900)
def probe():
    import io

    import requests
    import soundfile as sf
    key = os.environ.get("ENDPOINT_API_KEY", "")
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    print(f"key set: {bool(key)}\n")
    for lang, text in PLANS.items():
        t0 = time.time()
        try:
            r = requests.post(URL, json={"input": text, "voice": lang, "model": "sorotts"}, headers=hdr, timeout=600)
            dt = time.time() - t0
            if r.status_code != 200:
                print(f"{lang}: HTTP {r.status_code}  {r.text[:120]}  ({dt:.1f}s)")
                continue
            wav, sr = sf.read(io.BytesIO(r.content))
            dur = len(wav) / sr
            print(f"{lang}: OK  audio={dur:4.1f}s  sr={sr}  bytes={len(r.content):>7}  wall={dt:5.1f}s")
        except Exception as e:
            print(f"{lang}: ERROR {type(e).__name__} {str(e)[:120]}  ({time.time()-t0:.1f}s)")


@app.local_entrypoint()
def main():
    probe.remote()
