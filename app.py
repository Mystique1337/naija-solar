"""Solar, a voice-first solar advisor with an illustrated Nigerian home.

Pick your language (shown in its own script), then speak or type your appliances. It
detects what you said, fills the appliances, sizes over the real catalog for your
location, draws your home with the solar panels and your appliances arranged in the
rooms, and speaks the result back in your language. Sizing is exact Python.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import pathlib
import re
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import buildsmall  # noqa
except ModuleNotFoundError:
    sys.path.insert(0, str(HERE.parents[1] / "_platform"))

import gradio as gr

import catalog  # noqa
import data
import dataset
import engine
import locations
from buildsmall import asr, config, llm, sessions, tts, vision

# ── user tracking: count + feedback + optional email ──────────────────────────
# Appends a local JSONL. On a Space, set HF_TOKEN + EVENTS_DATASET and a CommitScheduler
# mirrors it to a private Dataset every few minutes so usage survives restarts. Pull the
# full data privately from that Dataset; only aggregate counts are ever shown in-app.
_DATA_DIR = pathlib.Path(os.environ.get("BUILDSMALL_DATA_DIR", str(HERE / "user_data")))
_EVENTS = _DATA_DIR / "events.jsonl"
_TRACK_LOCK = threading.Lock()
_TRACK_INIT = False


def _init_tracking():
    global _TRACK_INIT
    if _TRACK_INIT:
        return
    _TRACK_INIT = True
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        repo, token = os.environ.get("EVENTS_DATASET"), os.environ.get("HF_TOKEN")
        if repo and token:
            from huggingface_hub import CommitScheduler
            CommitScheduler(repo_id=repo, repo_type="dataset", folder_path=str(_DATA_DIR),
                            path_in_repo="data", every=5, token=token, private=True)
    except Exception:
        pass


def track_event(etype, data=None):
    try:
        rec = {"type": etype, "data": data or {}, "ts": int(time.time())}
        with _TRACK_LOCK, open(_EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_stats():
    n = {"sizings": 0, "emails": 0, "feedback": 0}
    m = {"sizing": "sizings", "email": "emails", "feedback": "feedback"}
    try:
        with open(_EVENTS, encoding="utf-8") as f:
            for line in f:
                try:
                    ty = json.loads(line).get("type")
                except Exception:
                    continue
                k = m.get(ty)
                if k:
                    n[k] += 1
    except FileNotFoundError:
        pass
    return n


def count_html():
    try:
        n = get_stats()["sizings"]
    except Exception:
        n = 0
    if not n:
        return ""
    return ('<div class="ucount"><span class="udot"></span><b>%s</b> '
            'system%s sized with Naija Solar</div>') % (format(n, ","), "" if n == 1 else "s")


def submit_feedback(rating, comment):
    track_event("feedback", {"rating": rating, "comment": (comment or "").strip()[:400]})
    return '<div class="okmsg">🙏 Thank you — your feedback helps make Naija Solar better.</div>'


def submit_email(email):
    e = (email or "").strip()
    if not e:
        return ""
    if "@" not in e or "." not in e.split("@")[-1] or len(e) > 120 or " " in e:
        return '<div class="warnmsg">That email doesn\'t look right — please check it, or leave it blank.</div>'
    track_event("email", {"email": e})
    return '<div class="okmsg">✅ You\'re on the list — only useful solar updates, no spam.</div>'


UI = {
    "en": {"title": "Power your home with the sun", "sub": "Say or type your appliances. I size it, draw your home, and tell you out loud.",
           "type": "or type:  1 fridge, 2 fans, 6 bulbs", "fine": "Estimates from current Nigerian prices. Confirm with a licensed installer.",
           "empty": "Your home appears here"},
    "pcm": {"title": "Power your house with sun", "sub": "Talk or type your appliances. I go size am, draw your house, and yarn am for you.",
            "type": "or type:  1 fridge, 2 fans, 6 bulbs", "fine": "Na estimate from current Naija price. Confirm with one licensed installer.",
            "empty": "Your house go show here"},
    "yo": {"title": "Fi oòrùn pèsè iná ilé rẹ", "sub": "Sọ tàbí kọ àwọn ohun-èlò rẹ. Màá ṣe ìwọ̀n, yá ilé rẹ, kí n sọ ọ́ sókè.",
           "type": "tàbí kọ:  1 fridge, 2 fan, 6 bulb", "fine": "Ìfojúsùn láti owó ọjà Nàìjíríà. Jẹ́rìí pẹ̀lú òṣìṣẹ́ tó ní ìwé-àṣẹ.",
           "empty": "Ilé rẹ yóò hàn níbí"},
    "ha": {"title": "Bayar da wutar gidanka da rana", "sub": "Faɗa ko rubuta kayan aikinka. Zan auna, zana gidanka, in faɗa maka.",
           "type": "ko rubuta:  1 fridge, 2 fans, 6 bulbs", "fine": "Kiyasi daga farashin Najeriya na yanzu. Tabbatar da kwararren mai shigarwa.",
           "empty": "Gidanka zai bayyana nan"},
    "ig": {"title": "Jiri anyanwụ nye ụlọ gị ọkụ", "sub": "Kwuo ma ọ bụ pịnye ngwa gị. M ga-atụle, sere ụlọ gị, kwuo ya n'olu.",
           "type": "ma ọ bụ pịnye:  1 fridge, 2 fans, 6 bulbs", "fine": "Atụmatụ site na ọnụahịa Naịjirịa ugbu a. Kwado ya na onye ọrụ nwere ikike.",
           "empty": "Ụlọ gị ga-apụta ebe a"},
}
LANG_NAME = {"en": "English", "pcm": "Pidgin", "yo": "Yorùbá", "ha": "Hausa", "ig": "Igbo"}
# Interface labels that also switch with the language (modes + action buttons).
LABELS = {
    "en": {"mv": "Speak your appliances", "mt": "Or type them", "mp": "Or snap / upload photos", "bs": "Size it", "bd": "Detect appliances"},
    "pcm": {"mv": "Talk your appliances", "mt": "Or type am", "mp": "Or snap / upload photo", "bs": "Size am", "bd": "Detect appliances"},
    "yo": {"mv": "Sọ àwọn ohun-èlò rẹ", "mt": "Tàbí kọ wọ́n", "mp": "Tàbí ya fọ́tò", "bs": "Ṣe ìwọ̀n", "bd": "Wá ohun-èlò"},
    "ha": {"mv": "Faɗa kayan aikinka", "mt": "Ko rubuta su", "mp": "Ko ɗauki hoto", "bs": "Auna shi", "bd": "Nemo kayan aiki"},
    "ig": {"mv": "Kwuo ngwa gị", "mt": "Ma ọ bụ pịnye ha", "mp": "Ma ọ bụ sere foto", "bs": "Tụọ ya", "bd": "Chọta ngwa"},
}
# Scope guardrail: shown when the input is not about appliances or solar.
GUARD = {
    "en": "I only size solar systems from home appliances, so I do not know that and I am not trained for it. Try: one fridge, two fans, six bulbs.",
    "pcm": "Na only your appliances I sabi use size solar, so I no know that one and dem no train me for am. Try: one fridge, two fans, six bulbs.",
    "yo": "Awon ohun-elo ile nikan ni mo le fi se isiro oorun, nitori naa emi ko mo nkan yen, won ko si ko mi fun un. Gbiyanju: firiji kan, fan meji, bulb mefa.",
    "ha": "Kayan aikin gida kawai nake iya auna hasken rana da su, don haka ban san wannan ba kuma ba a horar da ni a kai ba. Gwada: firiji daya, fanka biyu, kwararan fitila shida.",
    "ig": "Naani ngwa ulo ka m ji atule anyanwu, ya mere amaghi m nke ahu na-azughi m maka ya. Nwaa: otu friji, fan abuo, baolb isii.",
}
EMOJI = {"Fridge (small)": "🧊", "Chest freezer": "🧊", "Ceiling fan": "🌀", "Standing fan": "🌀",
         'TV (32–43" LED)': "📺", "TV (large)": "📺", "Decoder (DStv/GOtv)": "📡", "Sound system": "🔊",
         "LED bulb": "💡", "Energy-saver bulb": "💡", "Security light": "💡", "Laptop": "💻", "Desktop PC": "🖥️",
         "Phone charger": "📱", "Wifi router": "📶", "Air conditioner (1HP)": "❄️", "Air conditioner (1.5HP)": "❄️",
         "Air conditioner (2HP)": "❄️", "Water pump (0.5HP)": "🚰", "Water pump (1HP)": "🚰", "Electric iron": "👔",
         "Microwave": "🍲", "Blender": "🥤", "Electric kettle": "☕", "Washing machine": "🧺", "Water heater": "♨️"}

# ── appliance language parsing (deterministic) ─────────────────────────────────
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
       "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
KW = [("ceiling fan", "Ceiling fan"), ("standing fan", "Standing fan"), ("security light", "Security light"),
      ("air condition", "Air conditioner (1HP)"), ("washing machine", "Washing machine"),
      ("water heater", "Water heater"), ("water pump", "Water pump (1HP)"), ("pumping machine", "Water pump (1HP)"),
      ("sound system", "Sound system"), ("deep freezer", "Chest freezer"), ("chest freezer", "Chest freezer"),
      ("phone charger", "Phone charger"), ("flat screen", 'TV (32–43" LED)'), ("television", 'TV (32–43" LED)'),
      ("decoder", "Decoder (DStv/GOtv)"), ("dstv", "Decoder (DStv/GOtv)"), ("gotv", "Decoder (DStv/GOtv)"),
      ("microwave", "Microwave"), ("blender", "Blender"), ("kettle", "Electric kettle"),
      ("pressing iron", "Electric iron"), ("electric iron", "Electric iron"), ("desktop", "Desktop PC"),
      ("computer", "Desktop PC"), ("laptop", "Laptop"), ("router", "Wifi router"), ("wifi", "Wifi router"),
      ("freezer", "Chest freezer"), ("fridge", "Fridge (small)"), ("refrigerator", "Fridge (small)"),
      ("ac", "Air conditioner (1HP)"), ("pump", "Water pump (1HP)"), ("iron", "Electric iron"),
      ("fan", "Standing fan"), ("tv", 'TV (32–43" LED)'), ("bulb", "LED bulb"), ("light", "LED bulb"),
      ("lamp", "LED bulb"), ("phone", "Phone charger"), ("charger", "Phone charger"),
      ("aircon", "Air conditioner (1HP)"), ("washer", "Washing machine"), ("telly", 'TV (32–43" LED)'),
      ("tele", 'TV (32–43" LED)'), ("freeze", "Chest freezer")]
KW2 = [(k, a) for k, a in KW if " " in k]
KW1 = [(k, a) for k, a in KW if " " not in k]
_KW1_MAP = dict(KW1)
_FUZZ_KEYS = [k for k, _ in KW1 if len(k) >= 5]   # long keys only — safe to fuzzy-match


def _base(t):
    return t[:-1] if (t.endswith("s") and len(t) > 2) else t


def _fuzz(t):
    """Closest appliance keyword for a misspelled token (e.g. 'frige' -> fridge, 'televison' -> TV)."""
    if len(t) < 5:
        return None
    m = difflib.get_close_matches(t, _FUZZ_KEYS, n=1, cutoff=0.82)
    return _KW1_MAP[m[0]] if m else None


def _m1(t):
    for k, a in KW1:
        if t == k or _base(t) == k:
            return a
    return _fuzz(_base(t))


def _m2(a, b):
    for k, ap in KW2:
        w0, w1 = k.split(" ", 1)
        if a == w0 and (b == w1 or _base(b) == w1 or
                        (len(w1) >= 5 and difflib.SequenceMatcher(None, _base(b), w1).ratio() >= 0.82)):
            return ap
    return None


def parse_appliances(text):
    toks = re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
    sel, pending, i = {}, None, 0
    while i < len(toks):
        tok = toks[i]
        if tok.isdigit():
            pending = (int(tok), i); i += 1; continue
        if tok in NUM:
            pending = (NUM[tok], i); i += 1; continue
        appl, adv = None, 1
        if i + 1 < len(toks):
            appl = _m2(tok, toks[i + 1])
            if appl:
                adv = 2
        appl = appl or _m1(tok)
        if appl:
            qty = pending[0] if (pending and i - pending[1] <= 3) else 1
            sel[appl] = max(sel.get(appl, 0), qty)
            pending = None; i += adv; continue
        i += 1
    return sel


def extract(text):
    sel = parse_appliances(text)
    if sel:
        return sel
    try:
        out = llm.json(f"List home appliances and integer quantities from: '{text}'. Use ONLY names from "
                       f"this list: {list(data.APPLIANCES)}. Reply a JSON object name to count.")
        if isinstance(out, dict):
            return {k: int(v) for k, v in out.items() if k in data.APPLIANCES and str(v).strip().lstrip('-').isdigit()}
    except Exception:
        pass
    return sel


def detect_language(text):
    t = (text or "").lower()
    for code, marks in (("yo", ("ẹ", "ọ", "ṣ", "bawo", "jọwọ", "ilé", "mo ní")),
                        ("ig", ("kedu", "biko", "achọrọ", "ụlọ", "enwere")),
                        ("ha", ("ina", "kana", "yaya", "gida", "muna", "ina so")),
                        ("pcm", ("abeg", "wetin", " dey ", " na ", "wahala", "oga", "i get"))):
        if any(m in t for m in marks):
            return code
    return "en"


def _cost_phrase(n):
    """A speech-friendly amount: 1,924,000 -> '1.9 million', 950,000 -> '950 thousand'.
    Clearer to read and to hear than a long string of digits with commas."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + " million"
    if n >= 1000:
        return f"{round(n / 1000)} thousand"
    return str(n)


def _template(r, lang):
    p, kva = r["panel"]["count"], r["inverter"]["kva"]
    b, kwh = r["batteries"]["durable"]["count"], r["daily_kwh"]
    cost = _cost_phrase(r["batteries"]["durable"]["total"])
    pw, bw = ("panel" if p == 1 else "panels"), ("battery" if b == 1 else "batteries")
    T = {
        "en": f"You use about {kwh} kilowatt hours a day. I recommend {p} solar {pw}, a {kva} kVA inverter, and {b} {bw}. The system costs about {cost} naira. Please confirm with a licensed installer.",
        "pcm": f"You dey use about {kwh} kilowatt every day. You go need {p} solar {pw}, one {kva} kVA inverter, and {b} {bw}. E go cost around {cost} naira. Make you confirm with a licensed installer.",
        "yo": f"O ń lo tó {kwh} kilowatt lójúmọ́. Mo dábàá panel oòrùn {p}, inverter {kva} kVA kan, àti bátìrì {b}. Ètò náà yóò ná nǹkan bíi náírà {cost}. Jọ̀wọ́ bèèrè lọ́wọ́ onímọ̀ tó ní ìwé àṣẹ.",
        "ha": f"Kana amfani da kusan {kwh} kilowatt a kullum. Ina ba da shawarar panel hasken rana {p}, inverter {kva} kVA ɗaya, da batir {b}. Tsarin zai kai kusan naira {cost}. Don Allah ka tabbatar da ƙwararren mai shigarwa.",
        "ig": f"Ị na-eji ihe dịka {kwh} kilowatt kwa ụbọchị. Ana m atụ aro panel anyanwụ {p}, otu inverter {kva} kVA, na batrị {b}. Usoro a ga-efu ihe dịka naịra {cost}. Biko kwado ya na onye ọrụ nwere ikike.",
    }
    return T.get(lang, T["en"])


# ── speech-friendly narration for the Nigerian languages ──────────────────────
# SoroTTS is a Yorùbá/Hausa/Igbo voice: it reads pure prose beautifully but stumbles on English
# digits and units ("2.66", "1.5 kVA") read mid-sentence, which is what made the voice sound off.
# So for yo/ha/ig the narration spells counts as words, drops the hardest units, and rounds the cost
# to a spoken approximation. The exact figures still show in the result tiles, and because this same
# text is both shown and read, "you hear what you read" still holds. English/Pidgin keep the precise
# wording (their voices read digits fine).
_NUM = {
    "yo": ["", "ọ̀kan", "méjì", "mẹ́ta", "mẹ́rin", "márùn-ún", "mẹ́fà", "méje", "mẹ́jọ", "mẹ́sàn-án", "mẹ́wàá",
           "mọ́kànlá", "méjìlá", "mẹ́tàlá", "mẹ́rìnlá", "mẹ́ẹ̀ẹ́dógún", "mẹ́rìndínlógún", "mẹ́tàdínlógún",
           "méjìdínlógún", "mọ́kàndínlógún", "ogún"],
    "ha": ["", "ɗaya", "biyu", "uku", "huɗu", "biyar", "shida", "bakwai", "takwas", "tara", "goma",
           "goma sha ɗaya", "goma sha biyu", "goma sha uku", "goma sha huɗu", "goma sha biyar",
           "goma sha shida", "goma sha bakwai", "goma sha takwas", "goma sha tara", "ashirin"],
    "ig": ["", "otu", "abụọ", "atọ", "anọ", "ise", "isii", "asaa", "asatọ", "itoolu", "iri",
           "iri na otu", "iri na abụọ", "iri na atọ", "iri na anọ", "iri na ise", "iri na isii",
           "iri na asaa", "iri na asatọ", "iri na itoolu", "iri abụọ"],
}


def _num_words(n, lang):
    t = _NUM.get(lang)
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return t[n] if (t and 1 <= n <= 20) else str(n)


def _cost_words(total, lang):
    """A spoken, mostly-round amount in millions (sub-million numerals are unspeakable in these
    languages). The exact naira figure is shown in the cost card."""
    m = round((total or 0) / 1_000_000)
    if m < 1:
        return {"yo": "ìdajì mílíọ̀nù náírà", "ha": "rabin miliyan naira", "ig": "ọkara nde naịra"}[lang]
    w = _num_words(m, lang)
    return {"yo": f"mílíọ̀nù náírà {w}", "ha": f"naira miliyan {w}", "ig": f"naịra nde {w}"}[lang]


def _template_tts(r, lang):
    """Speech-friendly narration for yo/ha/ig (numbers as words, hard units dropped, cost rounded);
    English/Pidgin fall back to the precise template."""
    if lang not in ("yo", "ha", "ig"):
        return _template(r, lang)
    p, b = r["panel"]["count"], r["batteries"]["durable"]["count"]
    kw = max(1, round(r["daily_kwh"]))
    P, B, KW = _num_words(p, lang), _num_words(b, lang), _num_words(kw, lang)
    C = _cost_words(r["batteries"]["durable"]["total"], lang)
    if lang == "yo":
        return (f"Iná tí o ń lò tó ìwọ̀n {KW} lójoojúmọ́. Mo dábàá pánẹ́ẹ̀lì oòrùn {P}, "
                f"ẹ̀rọ̀ amúná ọ̀kan, àti bátìrì {B}. Ètò náà yóò ná tó {C}. "
                f"Jọ̀wọ́ bèèrè lọ́wọ́ onímọ̀ tó ní ìwé àṣẹ.")
    if lang == "ha":
        return (f"Kana amfani da wuta kusan {KW} a kullum. Ina ba da shawarar panel hasken rana {P}, "
                f"inverter ɗaya, da batir {B}. Tsarin zai kai kusan {C}. "
                f"Don Allah ka tabbatar da ƙwararren mai shigarwa.")
    return (f"Ị na-eji ọkụ dịka {KW} kwa ụbọchị. Ana m atụ aro panel anyanwụ {P}, "
            f"otu inverter, na batrị {B}. Usoro a ga-efu ihe dịka {C}. "
            f"Biko kwado ya na onye ọrụ nwere ikike.")


def _clean(t):
    return re.sub(r"<think>.*?</think>", "", t or "", flags=re.DOTALL).strip()


def narrate(r, lang):
    """The plan in one localized paragraph. It is shown on screen AND read aloud, so the written
    words and the spoken words are always identical. It is a deterministic template, so it is
    instant (no model call before the voice), reusable from cache, and dependable in all five
    languages, which a 1.7B model is not for Yoruba, Hausa or Igbo. For yo/ha/ig the wording is
    speech-friendly (numbers as words, no bare digits/units), which is what the voice reads cleanly;
    the exact figures live in the result tiles."""
    return _template_tts(r, lang)


def _for_tts(text):
    """Light touch so the voice reads the plan clearly: speak 'kVA' as letters, never as a word."""
    return (text or "").replace("kVA", "k V A").replace("kWh", "kilowatt hours")


# ── original illustrated artwork (SVG, comic style, renders as an image) ─────────
def _img(svg, cls):
    return f'<img alt="" class="{cls}" src="data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}"/>'


def _sun(cx, cy, r, col="#ffd23f", out="#e8a200"):
    rl = "".join(f'<line x1="{cx}" y1="{cy - r - 4}" x2="{cx}" y2="{cy - r - 14}" '
                 f'transform="rotate({a} {cx} {cy})"/>' for a in range(0, 360, 45))
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{col}" stroke="{out}" stroke-width="3"/>'
            f'<g stroke="{out}" stroke-width="4" stroke-linecap="round">{rl}</g>')


def _palm(x):
    return (f'<g stroke="#6b4a25" stroke-width="6" stroke-linecap="round" fill="none">'
            f'<path d="M{x} 338 Q {x-6} 290 {x+4} 258"/></g>'
            f'<g fill="#39a35a" stroke="#1f7d3e" stroke-width="2">'
            f'<path d="M{x+4} 256 q -34 -10 -52 6 q 28 -2 52 8 Z"/>'
            f'<path d="M{x+4} 256 q 34 -10 52 6 q -28 -2 -52 8 Z"/>'
            f'<path d="M{x+4} 256 q -16 -34 4 -50 q 6 26 -4 52 Z"/>'
            f'<path d="M{x+4} 256 q 18 -30 40 -30 q -18 14 -40 32 Z"/></g>')


def banner_svg():
    def home(x, w, body, roof="#a8553c"):
        cx = x + w // 2
        return ('<g stroke="#33271c" stroke-width="2" stroke-linejoin="round">'
                '<rect x="%d" y="136" width="%d" height="40" rx="2.5" fill="%s"/>'
                '<polygon points="%d,136 %d,112 %d,136" fill="%s"/>'
                '<rect x="%d" y="117" width="%d" height="14" rx="1.5" fill="#21539e" stroke="#0e2a72" stroke-width="1.3"/>'
                '<line x1="%d" y1="117" x2="%d" y2="131" stroke="#79a3dc" stroke-width="0.8"/>'
                '<rect x="%d" y="154" width="10" height="22" rx="1.5" fill="#6f4426"/>'
                '<rect x="%d" y="144" width="10" height="9" rx="1.5" fill="#ffe08a"/></g>'
                ) % (x, w, body, x - 6, cx, x + w + 6, roof, x + 6, w - 12, cx, cx, cx - 5, x + w - 16)

    def tree(x, s=1.0):
        return ('<g><rect x="%d" y="152" width="6" height="24" rx="2" fill="#7a5630"/>'
                '<circle cx="%d" cy="145" r="%d" fill="#57a85f"/><circle cx="%d" cy="138" r="%d" fill="#6cbf72"/>'
                '<circle cx="%d" cy="149" r="%d" fill="#4e9d57"/></g>'
                ) % (x, x + 3, int(15 * s), x - 6, int(11 * s), x + 11, int(12 * s))
    glow = ('<circle cx="566" cy="60" r="70" fill="#ffe08a" opacity="0.38"/>'
            '<circle cx="566" cy="60" r="46" fill="#ffd25a" opacity="0.45"/>')
    svg = ('<svg viewBox="0 0 680 200" xmlns="http://www.w3.org/2000/svg">'
           '<defs>'
           '<linearGradient id="sky" x1="0" y1="0" x2="0.3" y2="1">'
           '<stop offset="0" stop-color="#ffcf6e"/><stop offset="0.5" stop-color="#ffe3a4"/><stop offset="1" stop-color="#fdeccb"/></linearGradient>'
           '<linearGradient id="hill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#86cf8d"/><stop offset="1" stop-color="#56a661"/></linearGradient>'
           '<linearGradient id="hill2" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#549f5e"/><stop offset="1" stop-color="#3c8649"/></linearGradient>'
           '<linearGradient id="shade" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0b2e1c" stop-opacity="0.74"/>'
           '<stop offset="0.55" stop-color="#0b2e1c" stop-opacity="0.16"/><stop offset="1" stop-color="#0b2e1c" stop-opacity="0"/></linearGradient></defs>'
           '<rect width="680" height="200" rx="24" fill="url(#sky)"/>'
           + glow + _sun(566, 60, 30)
           + '<g fill="#ffffff" opacity="0.66"><ellipse cx="250" cy="42" rx="30" ry="11"/><ellipse cx="276" cy="36" rx="20" ry="9"/></g>'
           + '<path d="M0 152 Q 160 126 340 148 T 680 140 V200 H0 Z" fill="url(#hill)"/>'
           + tree(300) + home(340, 56, "#f0ead8") + home(430, 62, "#e3efe6") + tree(516, 0.9) + home(556, 54, "#f3e3c8")
           + '<path d="M0 178 Q 200 166 430 178 T 680 174 V200 H0 Z" fill="url(#hill2)"/>'
           + '<rect width="680" height="200" rx="24" fill="url(#shade)"/></svg>')
    return base64.b64encode(svg.encode()).decode()


BANNER = banner_svg()


def house_scene(panels, batts, sel, empty=""):
    p = max(0, int(panels)); show = min(p, 12)
    cells = ""
    for i in range(show):
        c, row = i % 4, i // 3
        x, y = 236 + c * 30, 152 + row * 13
        cells += ('<rect x="%d" y="%d" width="27" height="11" rx="1.5" fill="url(#pan)" stroke="#15347a" stroke-width="0.7"/>'
                  '<rect x="%d" y="%d" width="27" height="3" rx="1.5" fill="#cfe0ff" opacity="0.55"/>') % (x, y, x, y)
    label = ('<text x="305" y="186" font-size="12" fill="#15347a" text-anchor="middle" font-weight="700">%d solar panels</text>' % p) if p else ""
    cols, rowsy = [194, 303, 413], [232, 312]
    floors = ('<rect x="142" y="257" width="324" height="13" fill="url(#floor)"/>'
              '<rect x="142" y="338" width="324" height="13" fill="url(#floor)"/>') if sel else ""
    div = ('<g stroke="#e6d4b8" stroke-width="2"><line x1="249" y1="192" x2="249" y2="351"/>'
           '<line x1="358" y1="192" x2="358" y2="351"/><line x1="142" y1="270" x2="466" y2="270"/></g>') if sel else ""
    icons = ""
    for idx, (name, qty) in enumerate(list(sel.items())[:6]):
        cx, cy = cols[idx % 3], rowsy[idx // 3]
        icons += '<text x="%d" y="%d" font-size="33" text-anchor="middle" filter="url(#sh)">%s</text>' % (cx, cy + 10, EMOJI.get(name, "🔌"))
        if qty > 1:
            icons += ('<circle cx="%d" cy="%d" r="11" fill="#ff9500" stroke="#fff" stroke-width="2"/>'
                      '<text x="%d" y="%d" font-size="12" fill="#fff" text-anchor="middle" font-weight="800">%d</text>'
                      ) % (cx + 25, cy - 18, cx + 25, cy - 14, qty)
    extra = '<text x="305" y="372" font-size="11.5" fill="#7f9070" text-anchor="middle">+%d more in the list below</text>' % (len(sel) - 6) if len(sel) > 6 else ""
    ph = '<text x="305" y="276" font-size="15" fill="#b69b78" text-anchor="middle">%s</text>' % empty if not sel else ""
    batt = ""
    for j in range(min(int(batts), 4)):
        batt += '<rect x="%d" y="%d" width="20" height="27" rx="4" fill="url(#batt)" stroke="#157f38" stroke-width="1.5"/>' % (90 + (j % 2) * 23, 322 - (j // 2) * 31)
    svg = ('<svg viewBox="0 0 640 430" xmlns="http://www.w3.org/2000/svg"><defs>'
           '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#bfe6f2"/><stop offset="1" stop-color="#e9f7ee"/></linearGradient>'
           '<linearGradient id="grass" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9ed079"/><stop offset="1" stop-color="#73b657"/></linearGradient>'
           '<linearGradient id="wall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fffdf7"/><stop offset="1" stop-color="#efe4cf"/></linearGradient>'
           '<linearGradient id="rf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d4705a"/><stop offset="1" stop-color="#a8462f"/></linearGradient>'
           '<linearGradient id="pan" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3f74e6"/><stop offset="1" stop-color="#1f47b0"/></linearGradient>'
           '<linearGradient id="floor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d9ba8b"/><stop offset="1" stop-color="#c39a64"/></linearGradient>'
           '<linearGradient id="batt" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#46d96a"/><stop offset="1" stop-color="#2bb551"/></linearGradient>'
           '<radialGradient id="glow"><stop offset="0" stop-color="#ffe89a" stop-opacity="0.85"/><stop offset="1" stop-color="#ffe89a" stop-opacity="0"/></radialGradient>'
           '<filter id="sh" x="-40%" y="-40%" width="180%" height="180%"><feDropShadow dx="0" dy="2" stdDeviation="1.4" flood-opacity="0.22"/></filter>'
           '</defs>'
           '<rect width="640" height="430" rx="22" fill="url(#sky)"/>'
           '<circle cx="556" cy="74" r="60" fill="url(#glow)"/>' + _sun(556, 74, 28) +
           '<g fill="#ffffff" opacity="0.92"><ellipse cx="150" cy="66" rx="42" ry="15"/><ellipse cx="188" cy="58" rx="26" ry="13"/><ellipse cx="118" cy="59" rx="22" ry="11"/></g>'
           '<path d="M0 360 Q 320 338 640 360 V430 H0 Z" fill="url(#grass)"/>'
           '<ellipse cx="305" cy="360" rx="202" ry="15" fill="#0000001c"/>' + _palm(602) +
           '<polygon points="118,190 305,116 492,190" fill="url(#rf)" stroke="#7a3a2c" stroke-width="2.5" stroke-linejoin="round"/>'
           '<polygon points="305,116 492,190 305,190" fill="#000000" opacity="0.10"/>'
           '<line x1="305" y1="116" x2="305" y2="190" stroke="#7a3a2c" stroke-width="1.2" opacity="0.45"/>'
           '<rect x="110" y="188" width="390" height="7" rx="3" fill="#7a3a2c"/>' + cells + label +
           # rooftop water tank on a stand (common in Nigerian homes)
           '<g stroke="#6b7a88" stroke-width="2"><line x1="512" y1="190" x2="512" y2="150"/><line x1="540" y1="190" x2="540" y2="150"/></g>'
           '<rect x="503" y="128" width="46" height="22" rx="3" fill="#d3dde6" stroke="#9fb3c4" stroke-width="1.5"/><ellipse cx="526" cy="128" rx="23" ry="6" fill="#eef4f8"/>'
           # house body (cutaway)
           '<rect x="140" y="190" width="326" height="161" rx="3" fill="url(#wall)" stroke="#c9b793" stroke-width="2.5"/>'
           + floors + div + icons + extra + ph +
           # outdoor AC condenser on the grass
           '<rect x="484" y="330" width="40" height="27" rx="3" fill="#eef1f2" stroke="#9aa6ad" stroke-width="1.5"/><circle cx="504" cy="343" r="9" fill="none" stroke="#9aa6ad" stroke-width="1.5"/>'
           # inverter + batteries (left exterior)
           '<rect x="92" y="276" width="22" height="34" rx="4" fill="#3a3a3c" stroke="#1a1a1a" stroke-width="1.5"/><rect x="98" y="283" width="10" height="4" rx="1" fill="#46d96a"/>'
           + batt + '</svg>')
    return _img(svg, "home")


def chips_html(sel):
    items = "".join(
        '<div class="chip"><span class="ce">%s</span><b>%d</b><span class="cn">%s</span></div>'
        % (EMOJI.get(k, "🔌"), v, k.split(" (")[0]) for k, v in sel.items())
    return '<div class="appbox"><div class="apphd">✅ Your appliances</div><div class="chips">%s</div></div>' % items


def day_chart_svg(r):
    """Signature 24-hour visualization: solar generation vs the home's usage."""
    import math
    W, H, L, Rr, T, B = 600, 248, 46, 14, 30, 30
    cw, ch = W - L - Rr, H - T - B
    array_kw = max(0.4, r["array_w"] / 1000.0)
    daily = max(0.5, r["daily_kwh"])

    def gen(h):
        return array_kw * (math.sin(math.pi * (h - 6) / 13.5) ** 1.5) if 6 < h < 19.5 else 0.0

    def shape(h):
        return 0.3 + 1.0 * math.exp(-((h - 7.5) ** 2) / 5.0) + 1.7 * math.exp(-((h - 20.0) ** 2) / 6.0)
    hrs = [i * 0.5 for i in range(49)]
    sc = daily / max(sum(shape(h) for h in hrs) * 0.5, 0.1)

    def load(h):
        return shape(h) * sc
    ymax = max(array_kw, max(load(h) for h in hrs)) * 1.16
    X = lambda h: L + (h / 24.0) * cw
    Y = lambda v: T + ch - (v / ymax) * ch

    def apath(fn):
        return ("M%.1f,%.1f" % (X(0), Y(0)) + "".join("L%.1f,%.1f" % (X(h), Y(fn(h))) for h in hrs)
                + "L%.1f,%.1fZ" % (X(24), Y(0)))

    def lpath(fn):
        return "M" + "L".join("%.1f,%.1f" % (X(h), Y(fn(h))) for h in hrs)
    grid = ""
    for hh, lab in [(0, "12am"), (6, "6am"), (12, "12pm"), (18, "6pm"), (24, "12am")]:
        grid += ('<line x1="%.0f" y1="%d" x2="%.0f" y2="%d" stroke="#ece0c9" stroke-width="1"/>'
                 '<text x="%.0f" y="%d" font-size="11" fill="#a89a82" text-anchor="middle">%s</text>'
                 % (X(hh), T, X(hh), T + ch, X(hh), H - 9, lab))
    svg = ('<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">' % (W, H)
           + '<defs><linearGradient id="gsun" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#ffb74d" stop-opacity="0.8"/>'
             '<stop offset="1" stop-color="#ffd98a" stop-opacity="0.12"/></linearGradient>'
             '<linearGradient id="gload" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#34d399" stop-opacity="0.4"/>'
             '<stop offset="1" stop-color="#34d399" stop-opacity="0.04"/></linearGradient></defs>'
           + grid
           + '<path d="%s" fill="url(#gsun)"/>' % apath(gen)
           + '<path d="%s" fill="url(#gload)"/>' % apath(load)
           + '<path d="%s" fill="none" stroke="#f4a300" stroke-width="2.6" stroke-linejoin="round"/>' % lpath(gen)
           + '<path d="%s" fill="none" stroke="#059669" stroke-width="2.6" stroke-linejoin="round"/>' % lpath(load)
           + '<circle cx="%.0f" cy="%.0f" r="9" fill="#ffb703" stroke="#fff" stroke-width="2.5"/>' % (X(12.7), Y(gen(12.7)))
           + '</svg>')
    legend = ('<div class="chlegend"><span><i style="background:#f4a300"></i>Solar generation</span>'
              '<span><i style="background:#059669"></i>Your usage</span></div>')
    return '<div class="chcard"><div class="chttl">☀️ Your day: sun vs usage</div>%s%s</div>' % (svg, legend)


def system_view(r):
    return day_chart_svg(r) + energy_flow_svg(r)


def home_2d_svg(r):
    """Clean 2D illustrated home + system view — a labelled companion to the 3D (always renders)."""
    panels, kva = r["panel"]["count"], r["inverter"]["kva"]
    d = r["batteries"]["durable"]
    items = r.get("profile", {}).get("items", [])[:8]
    appl = ""
    for i, it in enumerate(items):
        cx, cy = 84 + (i % 4) * 60, 198 + (i // 4) * 52
        appl += ('<text x="%d" y="%d" font-size="22" text-anchor="middle">%s</text>'
                 '<text x="%d" y="%d" font-size="10.5" text-anchor="middle" fill="#5b5444" font-weight="700">x%d</text>'
                 % (cx, cy, EMOJI.get(it["name"], "\U0001f50c"), cx, cy + 15, it["qty"]))
    pan = "".join('<rect x="%d" y="84" width="34" height="20" rx="2" fill="#1e50c8" stroke="#0e2a72" stroke-width="1.2"/>'
                  % (62 + i * 37) for i in range(min(panels, 6)))
    svg = ('<svg viewBox="0 0 600 320" xmlns="http://www.w3.org/2000/svg">'
           + _sun(536, 52, 26)
           + '<rect x="48" y="102" width="276" height="14" rx="3" fill="#c98a5a" stroke="#8a5a35" stroke-width="1.5"/>' + pan
           + '<rect x="56" y="116" width="262" height="188" rx="7" fill="#ffffff" stroke="#e2d4b5" stroke-width="2"/>'
           + '<text x="70" y="150" font-size="13" font-weight="700" fill="#064e3b" font-family="Space Grotesk,Inter">Your appliances</text>'
           + appl
           + '<rect x="362" y="116" width="194" height="188" rx="14" fill="#f3f9f3" stroke="#cdeadb" stroke-width="1.5"/>'
           + '<text x="459" y="146" font-size="13" font-weight="700" fill="#064e3b" text-anchor="middle" font-family="Space Grotesk,Inter">Power system</text>'
           + '<rect x="390" y="160" width="138" height="42" rx="8" fill="#eef1f5" stroke="#b9c1cc"/>'
           + '<text x="459" y="186" font-size="12.5" text-anchor="middle" fill="#2b2f33">\U0001f50c %s kVA inverter</text>' % kva
           + '<rect x="390" y="214" width="138" height="42" rx="8" fill="#1f7a45"/>'
           + '<text x="459" y="240" font-size="12.5" text-anchor="middle" fill="#ffffff" font-weight="700">\U0001f50b %s kWh backup</text>' % d["backup_kwh"]
           + '<text x="459" y="288" font-size="12.5" text-anchor="middle" fill="#3c5a47" font-weight="700">☀️ %d solar panels</text>' % panels
           + '<g stroke="#f4a300" stroke-width="2.4" stroke-dasharray="5 4" fill="none"><path d="M505 70 Q 420 96 330 100"/></g>'
           + '</svg>')
    return '<div class="chcard"><div class="chttl">\U0001f5bc️ Your home &amp; system (2D)</div>%s</div>' % svg


def energy_flow_svg(r):
    """B: a live engineering view, energy flowing sun to panels to battery to inverter to loads."""
    panels, arrayw, kva = r["panel"]["count"], r["array_w"], r["inverter"]["kva"]
    bk = r["batteries"]["durable"]
    backup, nb, loadw, daily = bk["backup_kwh"], bk["count"], r["peak_w"], r["daily_kwh"]
    rays = "".join('<line x1="44" y1="94" x2="44" y2="86" transform="rotate(%d 44 120)"/>' % a for a in range(0, 360, 45))

    def flow(x1, x2, color):
        dots = "".join('<circle r="3.4" fill="%s"><animateMotion dur="1.8s" repeatCount="indefinite" begin="%.1fs" '
                       'path="M%d 120 L%d 120"/></circle>' % (color, k * 0.6, x1, x2) for k in range(3))
        return '<line x1="%d" y1="120" x2="%d" y2="120" stroke="#2a3a5a" stroke-width="3"/>%s' % (x1, x2, dots)

    def node(x, w, fill, title, big, sub):
        cx = x + w / 2
        return ('<rect x="%d" y="80" width="%d" height="82" rx="14" fill="%s" filter="url(#g)"/>'
                '<text x="%.0f" y="102" font-size="11" fill="#cfe0ff" text-anchor="middle">%s</text>'
                '<text x="%.0f" y="129" font-size="20" fill="#fff" font-weight="800" text-anchor="middle">%s</text>'
                '<text x="%.0f" y="148" font-size="10.5" fill="#9fb3d8" text-anchor="middle">%s</text>'
                ) % (x, w, fill, cx, title, cx, big, cx, sub)

    parts = (flow(66, 150, "#ffd23f") + node(150, 108, "#1f4fd0", "Solar", str(panels), "panels  %sW" % arrayw)
             + flow(258, 312, "#7cf2c0") + node(312, 108, "#1f9d4a", "Battery", str(backup), "kWh  %su" % nb)
             + flow(420, 474, "#c9a3ff") + node(474, 108, "#6b3fd0", "Inverter", str(kva), "kVA")
             + flow(582, 624, "#ffb15a") + node(624, 86, "#b4530a", "Loads", str(loadw), "W  %skWh/d" % daily))
    svg = ('<svg viewBox="0 0 720 250" xmlns="http://www.w3.org/2000/svg">'
           '<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0f1830"/>'
           '<stop offset="1" stop-color="#0a1124"/></linearGradient>'
           '<filter id="g" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="2.5" result="b"/>'
           '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'
           '<rect width="720" height="250" rx="20" fill="url(#bg)"/>'
           '<circle cx="44" cy="120" r="22" fill="#ffd23f" filter="url(#g)"/>'
           '<g stroke="#ffd23f" stroke-width="3" stroke-linecap="round">' + rays + '</g>' + parts + '</svg>')
    return _img(svg, "flow")


def load_breakdown_html(sel):
    rows = sorted(((k, data.APPLIANCES[k][0] * v, v) for k, v in sel.items()), key=lambda x: -x[1])
    mx = max((w for _, w, _ in rows), default=1)
    bars = "".join('<div class="bd"><div class="bn">%s %s x%d</div><div class="bbar"><i style="width:%d%%"></i></div>'
                   '<div class="bw">%d W</div></div>' % (EMOJI.get(k, "🔌"), k.split(" (")[0], v, max(6, round(100 * w / mx)), w)
                   for k, w, v in rows)
    return '<div class="breakdown"><div class="bdh">Load breakdown</div>%s</div>' % bars


def stat_tiles(r):
    d = r["batteries"]["durable"]
    cells = [(f"{r['daily_kwh']}", "kWh / day"), (f"{r['inverter']['kva']} kVA", "system"),
             (f"₦{d['total']:,}", "estimate")]
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="v">{v}</div><div class="l">{l}</div></div>' for v, l in cells) + '</div>'


def _gen_card(r):
    """For small loads, suggest a plug-and-play portable solar generator as an alternative."""
    if r["peak_w"] > 2400 or r["daily_kwh"] > 4:
        return ""
    g = dataset.pick_generator(r["peak_w"], r["daily_kwh"])
    if not g:
        return ""
    return ('<div class="rcard alt"><div class="ic">🧳</div><div><div class="t">Or a portable option: %s %s</div>'
            '<div class="s">%d Wh · %d W · about ₦%s — plug-and-play, add a panel to recharge</div></div></div>') % (
        g["brand"], g["model"], g["wh"], g["w"], format(g["price_ngn"], ","))


def rec_cards(r):
    d = r["batteries"]["durable"]
    rows = [("☀️", f"{r['panel']['count']} solar panels", f"{r['panel']['name']}, about {r['array_w']} W"),
            ("🔌", "Inverter", r["inverter"]["name"]),
            ("🔋", f"{d['count']} battery", f"{d['name']}, {d['backup_kwh']} kWh backup")]
    cards = "".join(
        f'<div class="rcard"><div class="ic">{i}</div><div><div class="t">{t}</div><div class="s">{s}</div></div></div>'
        for i, t, s in rows)
    return '<div class="recs">' + cards + _gen_card(r) + '</div>'


def vendors_html():
    fin = {"PAYG": "Pay small-small", "lease": "Lease monthly", "yes": "Installments"}

    def card(v):
        tag = fin.get(v.get("financing"), "")
        chip = ('<span class="vfin">💳 %s</span>' % tag) if tag else ""
        offers = ", ".join(v.get("offers", [])[:2])
        return ('<a class="vcard" href="%s" target="_blank" rel="noopener">'
                '<div class="vtop"><b>%s</b>%s</div><div class="voff">%s</div>'
                '<div class="vcov">📍 %s</div></a>') % (
            v["website"], v["name"], chip, offers, v.get("coverage", "Nigeria"))
    fv = [v for v in dataset.VENDORS if v.get("financing") in fin]
    dv = [v for v in dataset.VENDORS if v.get("financing") not in fin]
    out = ('<div class="vintro">Vetted Nigerian sellers. The ones marked 💳 let you spread the cost over time, '
           'which makes a system like this far easier to afford. Always confirm current prices and warranty.</div>')
    if fv:
        out += ('<div class="vsec">💳 Pay over time (financing)</div><div class="vendors">%s</div>'
                % "".join(card(v) for v in fv[:8]))
    out += '<div class="vsec">🛒 Buy direct</div><div class="vendors">%s</div>' % "".join(card(v) for v in dv[:8])
    return out


def logo_html():
    return ('<div class="logo"><span class="logobadge"><svg width="34" height="34" viewBox="0 0 48 48" fill="none">'
            '<circle cx="24" cy="24" r="9.5" fill="#ffb703" stroke="#fb8500" stroke-width="2"/>'
            '<g stroke="#fb8500" stroke-width="3" stroke-linecap="round">'
            '<line x1="24" y1="5" x2="24" y2="11"/><line x1="24" y1="37" x2="24" y2="43"/>'
            '<line x1="5" y1="24" x2="11" y2="24"/><line x1="37" y1="24" x2="43" y2="24"/>'
            '<line x1="11" y1="11" x2="15" y2="15"/><line x1="33" y1="33" x2="37" y2="37"/>'
            '<line x1="11" y1="37" x2="15" y2="33"/><line x1="33" y1="15" x2="37" y2="11"/>'
            '</g></svg></span><span class="wm">Naija&nbsp;Solar'
            '<span class="tag">Solar sizing in your language</span></span>'
            '<span class="livepill"><span class="livedot"></span>live</span></div>')


# Localized first-time guide (yo/ha/ig are best-effort and deserve a native review).
GUIDE_TEXT = {
    "en": {"why": "<b>Why Naija Solar?</b> Millions of Nigerian homes and shops face daily blackouts and burn money on petrol and diesel. A real solar consultation costs time and money, so most people guess or overpay. Naija Solar gives you a free first estimate in your own language: say, type, or snap your appliances and it sizes a proper solar system with real Nigerian prices.",
           "steps": [("🗣️", "1. Pick your language", "Tap a language above."),
                     ("🎤", "2. Say, type, or snap your appliances", "Use voice, the text box, or up to 5 photos."),
                     ("🏡", "3. See your home and hear the plan", "Your home, the system, the cost, spoken aloud.")],
           "privacy": "🔒 Private, nothing is saved to an account. I only size solar from appliances, not wiring, medical, financial, or legal advice. Estimates only."},
    "pcm": {"why": "<b>Why Naija Solar?</b> Plenty Naija house and shop dey see blackout every day and dey waste money for fuel and diesel. To call solar person come check fit cost time and money, so most people just dey guess or pay too much. Naija Solar go give you free first estimate for your own language: talk, type, or snap your gadgets and e go size correct solar system with real Naija price.",
            "steps": [("🗣️", "1. Choose your language", "Tap one language for up."),
                      ("🎤", "2. Talk, type, or snap your things", "Use voice, the text box, or up to 5 photo."),
                      ("🏡", "3. See your house, hear the plan", "Your house, the system, the price, e go talk am.")],
            "privacy": "🔒 Private, we no dey save anything. I dey only size solar from your gadgets, no be wiring, medical, money, or law advice. Na estimate."},
    "yo": {"why": "<b>Kí ló dé Naija Solar?</b> Ọ̀pọ̀ ilé àti ṣọ́ọ̀bù ní Nàìjíríà ni iná máa ń kú lójoojúmọ́, wọ́n sì ń ná owó lórí epo. Pípe ọ̀jọ̀gbọ́n solar lè ná owó àti àkókò, torí náà ọ̀pọ̀ ènìyàn máa ń fojú díwọ̀n tàbí san jù. Naija Solar yóò fún ọ ní ìdíwọ̀n àkọ́kọ́ ọ̀fẹ́ ní èdè rẹ: sọ, kọ, tàbí ya àwòrán ohun-èlò rẹ.",
           "steps": [("🗣️", "1. Yan èdè rẹ", "Tẹ èdè kan lókè."),
                     ("🎤", "2. Sọ, kọ, tàbí ya àwòrán", "Lo ohùn, àpótí ọ̀rọ̀, tàbí àwòrán márùn-ún."),
                     ("🏡", "3. Rí ilé rẹ, gbọ́ ètò náà", "Ilé rẹ, ètò náà, iye owó, a ó sọ ọ́.")],
           "privacy": "🔒 Àdáni, kò sí ohun tí a fipamọ́. Mo ń díwọ̀n solar láti inú ohun-èlò nìkan, kì í ṣe ìmọ̀ràn waya, ìṣègùn, owó, tàbí òfin. Ìdíwọ̀n nìkan."},
    "ha": {"why": "<b>Me ya sa Naija Solar?</b> Gidaje da shaguna da yawa a Najeriya na fuskantar yankewar wuta kullum suna kuma kashe kuɗi a kan man fetur da dizal. Kawo masanin solar na iya ɗaukar lokaci da kuɗi, don haka mutane da yawa suna ƙididdiga ko biya fiye da kima. Naija Solar zai ba ka kiyasi na farko kyauta a yarenka: faɗa, rubuta, ko ɗauki hoton kayanka.",
           "steps": [("🗣️", "1. Zaɓi yarenka", "Danna yare a sama."),
                     ("🎤", "2. Faɗa, rubuta, ko ɗauki hoto", "Yi amfani da murya, akwatin rubutu, ko hotuna biyar."),
                     ("🏡", "3. Ga gidanka, ji shirin", "Gidanka, tsarin, farashin, za a faɗa da murya.")],
           "privacy": "🔒 Na sirri, ba a ajiye komai ba. Ina auna solar daga kayan aiki kawai, ba shawarar waya, lafiya, kuɗi, ko doka ba. Kiyasi kawai."},
    "ig": {"why": "<b>Gịnị mere Naija Solar?</b> Ọtụtụ ụlọ na ụlọ ahịa na Naịjirịa na-enwe nkwụsị ọkụ kwa ụbọchị ma na-emefu ego na mmanụ. Ịkpọ ọkachamara solar nwere ike iwe oge na ego, ya mere ọtụtụ mmadụ na-eche echiche ma ọ bụ na-akwụ karịa. Naija Solar ga-enye gị atụmatụ mbụ n'efu n'asụsụ gị: kwuo, dee, ma ọ bụ see foto ngwa gị.",
           "steps": [("🗣️", "1. Họrọ asụsụ gị", "Pịa asụsụ n'elu."),
                     ("🎤", "2. Kwuo, dee, ma ọ bụ see foto", "Jiri olu, igbe ederede, ma ọ bụ foto ise."),
                     ("🏡", "3. Hụ ụlọ gị, nụ atụmatụ", "Ụlọ gị, usoro ahụ, ọnụahịa, a ga-akpọ ya.")],
           "privacy": "🔒 Nzuzo, ọ dịghị ihe echekwara. Ana m atụ solar naanị site na ngwa, ọ bụghị ndụmọdụ waya, ahụike, ego, ma ọ bụ iwu. Naanị atụmatụ."},
}


def steps_html(lang="en"):
    g = GUIDE_TEXT.get(lang, GUIDE_TEXT["en"])
    cards = "".join('<div class="step"><div class="se">%s</div><div><div class="st">%s</div>'
                    '<div class="ss">%s</div></div></div>' % s for s in g["steps"])
    return ('<div class="story">%s</div><div class="steps">%s</div><div class="privacy">%s</div>'
            % (g["why"], cards, g["privacy"]))


GUIDE_TITLE = {"en": "New here? How Naija Solar works", "pcm": "You new here? How Naija Solar dey work",
               "yo": "Tuntun níbí? Bí Naija Solar ṣe ń ṣiṣẹ́", "ha": "Sabo ne? Yadda Naija Solar ke aiki",
               "ig": "Ọ̀hụrụ ebe a? Otú Naija Solar si arụ ọrụ"}


def hero_html(lang):
    u = UI.get(lang, UI["en"])
    return (f'<div class="banner" style="background-image:url(data:image/svg+xml;base64,{BANNER})">'
            f'<div class="bwrap"><h1>{u["title"]}</h1><p>{u["sub"]}</p></div></div>')


# ── handlers ──────────────────────────────────────────────────────────────────
def _sel_to_df(sel):
    return [[k, v] for k, v in sel.items()]


def _df_to_sel(df):
    rows = df.values.tolist() if hasattr(df, "values") else (df or [])
    out = {}
    for row in rows:
        try:
            if str(row[0]).strip() in data.APPLIANCES and int(float(row[1])) > 0:
                out[str(row[0]).strip()] = int(float(row[1]))
        except (ValueError, IndexError, TypeError):
            pass
    return out


def get_session(s):
    return s if s is not None else sessions.start("Solar")


def _psh(state, geolat):
    return locations.psh_from_lat(geolat) if (geolat or "").strip() else locations.psh_for_state(state)


HIDE, SHOW = gr.update(visible=False), gr.update(visible=True)


def run(audio, text, state, geolat, uilang, sess):
    sess = get_session(sess)
    src, outlang = (text or ""), uilang
    if audio:
        try:
            src = asr.transcribe(audio)
            if uilang == "en":            # respect an explicit language choice; auto-detect only from the default
                outlang = detect_language(src)
            sess.event("asr", transcript=src)
        except Exception:
            return gr.update(), gr.update(), gr.update(), "", gr.update(), "Could not hear that. Try again or type.", sess, None, uilang, HIDE, gr.update()
    sel = extract(src)
    sess.event("input", text=src, lang=outlang, found=len(sel))
    if not sel:
        msg = GUARD.get(outlang, GUARD["en"]) if (src or "").strip() else ""
        return gr.update(), gr.update(), gr.update(), "", gr.update(), msg, sess, None, outlang, HIDE, gr.update()
    r = engine.size(sel, _psh(state, geolat))
    sess.event("sized", kwh=r["daily_kwh"], panels=r["panel"]["count"], cost=r["batteries"]["durable"]["total"])
    content = chips_html(sel) + stat_tiles(r) + rec_cards(r)
    return (_house_data(r, sel),
            system_view(r), load_breakdown_html(sel), content, applist_html(_sel_to_df(sel)), "", sess, r, outlang, SHOW, _sel_to_df(sel))


# Persistent on /data so cached audio survives Space restarts (was ./traces, which is wiped on
# every rebuild, so the slow first-generation was paid again after each restart).
_TTS_CACHE = str(_DATA_DIR / "tts_cache")
# Bump this whenever the voice itself changes (e.g. MMS -> SoroTTS). It is part of the cache key,
# so old audio rendered by a previous voice is never reused. Without it, a cached Yoruba clip from
# the old MMS voice would keep playing for the same plan even after the voice was upgraded.
_TTS_VERSION = os.environ.get("TTS_CACHE_VERSION", "sorotts-3")
_TTS_CACHE_KEEP = int(os.environ.get("TTS_CACHE_KEEP", "800"))   # cap clips on disk (LRU eviction)


def _prune_tts_cache(keep=None):
    """Keep the audio cache bounded on the persistent disk: evict the least-recently-used clips."""
    keep = _TTS_CACHE_KEEP if keep is None else keep
    try:
        files = [os.path.join(_TTS_CACHE, f) for f in os.listdir(_TTS_CACHE) if f.endswith(".wav")]
        if len(files) <= keep:
            return
        files.sort(key=os.path.getmtime)                 # oldest (least recently used) first
        for p in files[:len(files) - keep]:
            try:
                os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def speak(r, lang, sess):
    if not r:
        return None, sess
    sess = get_session(sess)
    words = narrate(r, lang)
    # The narration is deterministic, so identical (result + language) reuses the audio.
    os.makedirs(_TTS_CACHE, exist_ok=True)
    key = hashlib.md5(("%s|%s|%s" % (_TTS_VERSION, lang, words)).encode("utf-8")).hexdigest()
    path = os.path.join(_TTS_CACHE, key + ".wav")
    if os.path.exists(path) and os.path.getsize(path) > 1200:
        try:
            os.utime(path, None)                         # mark recently used so it is kept (LRU)
        except OSError:
            pass
        sess.event("walkthrough", lang=lang, cached=True)
        return path, sess
    audio = None
    try:
        audio = tts.speak(_for_tts(words), lang=lang, out_path=path)
        _prune_tts_cache()
    except Exception:
        audio = None
    sess.event("walkthrough", lang=lang, cached=False)
    return audio, sess


def recalc(df, state, geolat, sess):
    sess = get_session(sess)
    sel = _df_to_sel(df)
    if not sel:
        return gr.update(), gr.update(), gr.update(), "", "Add an appliance.", sess, None, HIDE
    r = engine.size(sel, _psh(state, geolat))
    return (_house_data(r, sel),
            system_view(r), load_breakdown_html(sel), chips_html(sel) + stat_tiles(r) + rec_cards(r),
            "", sess, r, SHOW)


def from_photos(files, camera, state, geolat, uilang, sess):
    """Up to 5 uploaded photos or a camera snap: the vision model lists the appliances."""
    sess = get_session(sess)
    paths = []
    if camera:
        paths.append(camera if isinstance(camera, str) else getattr(camera, "name", None))
    for f in (files or []):
        paths.append(f if isinstance(f, str) else getattr(f, "name", None))
    paths = [p for p in paths if p][:5]
    if not paths:
        return gr.update(), gr.update(), gr.update(), "", [], "Open the camera or add a photo of your appliances.", sess, None, uilang, HIDE
    seen = {}
    for path in paths:
        try:
            desc = vision.describe(path, "List every household electrical appliance you can see, with counts. "
                                         "Use simple names like fridge, fan, bulb, TV, AC, freezer, laptop.")
            sess.event("vision", desc=desc[:140])
            for k, v in extract(desc).items():
                seen[k] = seen.get(k, 0) + v
        except Exception:
            pass
    if not seen:
        return gr.update(), gr.update(), gr.update(), "", gr.update(), "Could not spot appliances. Try a clearer photo or type them.", sess, None, uilang, HIDE, gr.update()
    r = engine.size(seen, _psh(state, geolat))
    sess.event("sized_photos", panels=r["panel"]["count"])
    return (_house_data(r, seen),
            system_view(r), load_breakdown_html(seen), chips_html(seen) + stat_tiles(r) + rec_cards(r),
            applist_html(_sel_to_df(seen)), "Detected from your photo. Open 'Adjust appliances' to select or fix.", sess, r, uilang, SHOW, _sel_to_df(seen))


def applist_html(rows):
    """Render the current appliance list as chips (HTML always re-renders reliably)."""
    if not rows:
        return '<div class="adjnote">No appliances yet. Pick one above and tap Add.</div>'
    chips = "".join(
        '<span class="achip">%s %s <b>×%s</b></span>' % (
            EMOJI.get(r[0], "🔌"), str(r[0]).split(" (")[0], r[1]) for r in rows)
    return '<div class="chips applchips">%s</div>' % chips


def add_appliance(name, qty, cur):
    """Add or update one appliance in the state-backed list (the reliable source of truth)."""
    rows = [list(x) for x in (cur or [])]
    if not name:
        return rows, applist_html(rows)
    try:
        q = max(1, min(99, int(float(qty or 1))))
    except (ValueError, TypeError):
        q = 1
    for r in rows:
        if str(r[0]).strip().lower() == str(name).strip().lower():
            r[1] = q
            break
    else:
        rows.append([name, q])
    return rows, applist_html(rows)


def clear_appliances():
    return [], applist_html([])


def set_lang(code):
    u = UI.get(code, UI["en"])
    b = LABELS.get(code, LABELS["en"])
    return (code, hero_html(code), gr.update(placeholder=u["type"]),
            f'<div class="fine">{u["fine"]}</div>',
            '<div class="imode">🎤 %s <span>tap Record, speak, then Stop, it sizes itself</span></div>' % b["mv"],
            '<div class="imode">⌨️ %s</div>' % b["mt"],
            '<div class="imode">📷 %s</div>' % b["mp"],
            gr.update(value=b["bs"]), gr.update(value=b["bd"]), steps_html(code),
            gr.update(label=GUIDE_TITLE.get(code, GUIDE_TITLE["en"])))


GEO_JS = ("() => new Promise(res => { if(!navigator.geolocation){res('');return;} "
          "navigator.geolocation.getCurrentPosition(p => res(''+p.coords.latitude), () => res('')); })")


def geo_note(lat):
    return f"📍 {locations.psh_from_lat(lat)} sun hours" if (lat or "").strip() else ""


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700;800&display=swap');
:root{--cream:#fffbf2;--panel:#ffffff;--ink:#1b1b1b;--muted:#6b7280;--sun:#ff8a00;--amber:#f4a300;--green:#1f7a4d;--green-d:#0c4a30;--border:#eee2cf}
*{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.banner h1,.logo .wm,.tile .v,.vsec,.apphd,.sumbar,h1,h2,h3{font-family:'Space Grotesk','Inter',sans-serif;letter-spacing:-.02em}
.gradio-container{max-width:700px!important;margin:0 auto!important;
 background:linear-gradient(180deg,#fffbf2 0%,#fff6e8 42%,#fdf5ea 100%)!important;
 background-image:linear-gradient(180deg,#fffbf2 0%,#fff6e8 42%,#fdf5ea 100%),url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='104' height='104'%3E%3Cg fill='none' stroke='%23f4a300' stroke-width='1.3' opacity='0.08'%3E%3Crect x='12' y='16' width='34' height='22' rx='2'/%3E%3Cline x1='23' y1='16' x2='23' y2='38'/%3E%3Cline x1='34.5' y1='16' x2='34.5' y2='38'/%3E%3Cline x1='12' y1='27' x2='46' y2='27'/%3E%3C/g%3E%3Ccircle cx='76' cy='72' r='7.5' fill='%23ff8a00' opacity='0.09'/%3E%3Cg stroke='%23ff8a00' stroke-width='1.3' opacity='0.09'%3E%3Cline x1='76' y1='57' x2='76' y2='61'/%3E%3Cline x1='76' y1='83' x2='76' y2='87'/%3E%3Cline x1='61' y1='72' x2='65' y2='72'/%3E%3Cline x1='87' y1='72' x2='91' y2='72'/%3E%3C/g%3E%3C/svg%3E")!important;
 background-size:auto,104px 104px!important}
footer{display:none!important}
.logo{display:flex;align-items:center;justify-content:center;gap:11px;padding-top:16px}
.logobadge{display:inline-flex;align-items:center;justify-content:center;width:46px;height:46px;border-radius:50%;background:radial-gradient(circle at 40% 35%,#fff6dd,#ffe9b0);box-shadow:0 6px 16px rgba(251,133,0,.28),inset 0 0 0 1.5px #ffd98a}
.livepill{display:inline-flex;align-items:center;gap:6px;font-size:.64rem;font-weight:700;color:#0c4a30;background:#eaf7ee;border:1px solid #bfe3cf;border-radius:999px;padding:4px 9px;letter-spacing:.07em;text-transform:uppercase}
.livedot{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:livepulse 2s infinite}
@keyframes livepulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.55)}70%{box-shadow:0 0 0 7px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
.logo .wm{font-weight:800;font-size:1.5rem;color:var(--green-d);letter-spacing:-.02em;line-height:1}
.logo .tag{display:block;font-size:.6rem;font-weight:700;color:#059669;letter-spacing:.1em;text-transform:uppercase;margin-top:3px}
.langrow{justify-content:center;gap:8px;margin-top:12px}.langrow .wrap{justify-content:center;gap:8px;flex-wrap:wrap}
.langrow label{border:1.6px solid #bfe3d0!important;border-radius:999px!important;padding:7px 16px!important;background:#fff!important;font-weight:600!important;color:#15803d!important;transition:transform .16s ease,box-shadow .16s ease,background .16s ease}
.langrow label:hover{border-color:#34d399!important;transform:translateY(-1px);box-shadow:0 4px 12px rgba(5,150,105,.16)}
.langrow label.selected,.langrow label:has(input:checked){background:linear-gradient(135deg,#059669,#047857)!important;color:#fff!important;border-color:#047857!important;box-shadow:0 5px 14px rgba(5,150,105,.32)}
.langlabel{text-align:center;color:#15803d;font-weight:700;font-size:.82rem;letter-spacing:.02em;margin-top:8px;opacity:.85}
.banner{border-radius:28px;background-size:cover;background-position:center;padding:30px 28px;margin-top:12px;min-height:152px;display:flex;align-items:center;
 box-shadow:0 18px 44px rgba(20,90,50,.28);overflow:hidden;position:relative}
.banner .bwrap{max-width:66%;text-shadow:0 2px 12px rgba(0,0,0,.45);position:relative;z-index:2}
.banner h1{margin:0;color:#fff;font-weight:800;font-size:1.85rem;letter-spacing:-.02em;line-height:1.12}
.banner p{margin:10px 0 0;color:#eafff0;font-size:1rem;line-height:1.45;font-weight:500}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0 6px}
.step{display:flex;gap:10px;align-items:flex-start;background:rgba(255,255,255,.78);border:1px solid #eee2cf;border-radius:16px;padding:12px}
.step .se{font-size:20px}.step .st{font-weight:700;color:#064e3b;font-size:.84rem}.step .ss{color:#5b7a64;font-size:.77rem;margin-top:2px}
.privacy{text-align:center;color:#5b7a64;font-size:.8rem;margin:4px 0 2px}
.locrow{justify-content:center;gap:8px;margin-top:6px}.locnote{text-align:center;color:#5b7a64;font-size:.82rem}
.miccard{background:rgba(255,255,255,.92);backdrop-filter:blur(8px);border:1.5px solid #eee2cf;border-radius:24px;
 padding:14px 16px;box-shadow:0 10px 30px rgba(40,110,60,.12);margin-top:12px}
.typein textarea,.typein input{border:none!important;background:#eef6ef!important;border-radius:14px!important;font-size:1rem!important}
.status{text-align:center;color:#5b7a64;font-size:.9rem;min-height:1em;margin:8px 0}
.home{width:100%;border-radius:24px;display:block;box-shadow:0 10px 28px rgba(40,110,60,.16);border:1.5px solid #eee2cf;margin-top:10px}
.chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:16px 0 4px}
.chip{display:flex;align-items:center;gap:6px;background:#fff;border:1.5px solid #eee2cf;border-radius:999px;padding:7px 13px;font-size:.92rem;box-shadow:0 2px 6px rgba(40,110,60,.08)}
.chip .ce{font-size:1.1rem}.chip b{color:#064e3b}.chip .cn{color:#5b7a64}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}
.tile{background:linear-gradient(165deg,#ffffff,#f2fbf6);border:1.5px solid #cdeadb;border-radius:20px;padding:20px 10px;text-align:center;box-shadow:0 8px 20px rgba(40,110,60,.10);position:relative;overflow:hidden}
.tile::before{content:"";position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,#34d399,#059669)}
.tile .v{font-size:1.65rem;font-weight:800;color:#059669;letter-spacing:-.02em}
.tile .l{color:#5b7a64;font-size:.76rem;margin-top:4px;font-weight:600}
.recs{display:grid;gap:10px}
.rcard{background:#fff;border:1.5px solid #eee2cf;border-radius:18px;padding:14px 16px;display:flex;gap:14px;align-items:center;box-shadow:0 4px 12px rgba(40,110,60,.07)}
.rcard .ic{font-size:22px;width:46px;height:46px;border-radius:14px;background:linear-gradient(160deg,#ecfdf5,#d1fae5);display:flex;align-items:center;justify-content:center;flex-shrink:0;box-shadow:inset 0 0 0 1px #bbf0d4}.rcard .t{font-weight:700;color:#064e3b}.rcard .s{color:#5b7a64;font-size:.86rem;margin-top:1px}
.fine{color:#8aa394;font-size:.78rem;text-align:center;margin-top:14px}
.flow{width:100%;border-radius:24px;display:block;box-shadow:0 12px 30px rgba(20,40,30,.28);margin-top:10px}
.flowph{height:170px;border-radius:24px;background:#0f1830;color:#7e8db0;display:flex;align-items:center;justify-content:center;font-size:.95rem;margin-top:10px}
.breakdown{margin-top:14px}.bdh{font-weight:700;color:#064e3b;font-size:.9rem;margin-bottom:8px}
.bd{display:grid;grid-template-columns:1fr 110px 56px;align-items:center;gap:10px;margin:7px 0}
.bn{font-size:.86rem;color:#33402c}.bbar{background:#e3efe3;border-radius:6px;height:9px;overflow:hidden}
.bbar i{display:block;height:100%;background:linear-gradient(90deg,#43a047,#059669);border-radius:6px}
.bw{text-align:right;color:#5b7a64;font-size:.82rem;font-weight:700}
.vendors{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.vcard{display:block;background:#fff;border:1.5px solid #eee2cf;border-radius:16px;padding:12px 14px;text-decoration:none}
.vtop{display:flex;justify-content:space-between;align-items:center;gap:8px}.vtop b{color:#064e3b;font-size:.92rem}
.vfin{background:linear-gradient(135deg,#d1fae5,#a7f3d0);color:#047857;font-size:.66rem;font-weight:700;padding:3px 9px;border-radius:999px;white-space:nowrap;box-shadow:0 1px 4px rgba(5,150,105,.18)}
.vintro{color:#3c5a47;font-size:.84rem;line-height:1.45;margin:2px 2px 12px;background:#f1faf4;border:1px solid #eee2cf;border-radius:14px;padding:11px 14px}
.vsec{font-weight:800;color:#064e3b;font-size:.92rem;margin:16px 2px 9px;letter-spacing:-.01em}
.audiolabel{font-weight:700;color:#064e3b;font-size:.9rem;margin:6px 2px 4px}
/* refine gradio default components for a cohesive, premium feel */
.label-wrap{padding:14px 16px!important;border-radius:16px!important;background:#fff!important;border:1.5px solid #e3eee8!important;box-shadow:0 3px 10px rgba(40,110,60,.05)!important;transition:border-color .16s ease,box-shadow .16s ease}
.label-wrap:hover{border-color:#bfe3d0!important;box-shadow:0 6px 16px rgba(40,110,60,.1)!important}
.label-wrap>span:first-child{font-weight:700!important;color:#064e3b!important;font-size:.96rem!important}
.audio-container{border-radius:16px!important;overflow:hidden}
.gradio-container .gap{gap:12px}
.chcard{background:#fff;border:1.5px solid var(--border);border-radius:20px;padding:16px;box-shadow:0 8px 22px rgba(120,80,20,.07);margin-bottom:12px}
.chttl{font-weight:800;color:#064e3b;font-size:1rem;margin-bottom:8px;font-family:'Space Grotesk','Inter',sans-serif}
.chcard svg{width:100%;height:auto;display:block}
.chlegend{display:flex;gap:18px;justify-content:center;margin-top:10px;font-size:.82rem;color:#5b5444;font-weight:600}
.chlegend span{display:inline-flex;align-items:center;gap:6px}
.chlegend i{width:15px;height:4px;border-radius:2px;display:inline-block}
/* premium voice input: big Gemini-style record button, no device-picker clutter */
.voicein .audio-container{background:linear-gradient(135deg,#f1fbf6,#ffffff)!important;border:1.5px solid #cdeadb!important;border-radius:18px!important;box-shadow:inset 0 1px 4px rgba(40,110,60,.04)}
.voicein button.record{background:linear-gradient(135deg,#10b981,#059669)!important;color:#fff!important;border:none!important;border-radius:999px!important;padding:11px 26px!important;font-weight:700!important;box-shadow:0 6px 18px rgba(5,150,105,.34)!important;font-size:.98rem!important;letter-spacing:.01em;transition:transform .15s,filter .15s}
.voicein button.record:hover{filter:brightness(1.06)!important;transform:translateY(-1px)}
.voicein button.stop-button{background:linear-gradient(135deg,#f87171,#ef4444)!important;color:#fff!important;border-radius:999px!important;border:none!important;box-shadow:0 6px 18px rgba(239,68,68,.32)!important}
.voicein select,.voicein .source-selection{display:none!important}
/* premium photo drop zones (fully custom look) */
.photofile .wrap,.camerabox .wrap,.camerabox .upload-container{border:2px dashed #cdeadb!important;border-radius:18px!important;background:linear-gradient(135deg,#f6fcf9,#ffffff)!important;transition:border-color .18s,background .18s,box-shadow .18s!important}
.photofile .wrap:hover,.camerabox .wrap:hover,.camerabox .upload-container:hover{border-color:#34d399!important;background:#f0fbf5!important;box-shadow:0 8px 18px rgba(5,150,105,.12)!important}
.photofile .center,.camerabox .center,.photofile .wrap span,.camerabox .wrap span{color:#059669!important;font-weight:600}
.photofile svg,.camerabox svg{color:#10b981!important}
/* one-tap example chips */
.exrow{gap:8px!important;flex-wrap:wrap!important}
.exbtn{background:#fff!important;border:1.5px solid #cdeadb!important;border-radius:14px!important;font-weight:600!important;color:#0c4a30!important;box-shadow:0 3px 10px rgba(40,110,60,.06)!important;transition:transform .14s,border-color .14s,box-shadow .14s!important;min-width:0!important}
.exbtn:hover{border-color:#34d399!important;transform:translateY(-1px);box-shadow:0 7px 16px rgba(40,110,60,.14)!important;background:#f3fbf6!important}
/* 3D card: sizing HUD chips + interaction hint */
.house3d{position:relative}
.h3dhud{position:absolute;top:12px;left:12px;right:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;pointer-events:none;z-index:5}
.h3dhud span{background:rgba(255,255,255,.93);border:1px solid #e3eee8;border-radius:999px;padding:5px 13px;font-size:.8rem;font-weight:700;color:#0c4a30;box-shadow:0 3px 10px rgba(40,110,60,.14);font-family:'Space Grotesk','Inter',sans-serif}
.h3dhud .h3dhint{margin-left:auto;background:rgba(6,78,59,.86);color:#fff;font-weight:600;font-family:'Inter',sans-serif;border:none}
.chsub{font-weight:500;color:#8a9a7c;font-size:.78rem;margin-left:8px;font-family:'Inter',sans-serif}
/* user tracking: social-proof count pill + feedback/email */
#ucount .ucount{text-align:center;margin:7px auto 2px;font-size:.82rem;color:#0c4a30;font-weight:600;display:flex;align-items:center;justify-content:center;gap:7px}
#ucount b{font-family:'Space Grotesk','Inter',sans-serif;color:#059669;font-size:.98rem}
.udot{width:7px;height:7px;border-radius:50%;background:#10b981;box-shadow:0 0 0 0 rgba(16,185,129,.5);animation:livepulse 2s infinite;display:inline-block}
.fbbtn{background:#fff!important;border:1.5px solid #cdeadb!important;border-radius:12px!important;font-weight:600!important;color:#0c4a30!important}
.fbbtn:hover{background:#f0fbf5!important;border-color:#34d399!important}
.ehint{color:#5b7a64;font-size:.82rem;margin:12px 2px 4px;line-height:1.4}
.okmsg{background:#eafaf1;border:1px solid #b7ebcf;border-radius:12px;padding:9px 12px;color:#0c4a30;font-weight:600;font-size:.88rem;margin-top:8px}
.warnmsg{background:#fff4e8;border:1px solid #f3d3a8;border-radius:12px;padding:9px 12px;color:#7a4a12;font-weight:600;font-size:.88rem;margin-top:8px}
.voff{color:#5b7a64;font-size:.78rem;margin-top:4px;line-height:1.3}.vcov{color:#9bb0a2;font-size:.72rem;margin-top:4px}
.story{background:#f3f9f3;border:1px solid #eee2cf;border-radius:14px;padding:12px 14px;font-size:.85rem;color:#3c4a3e;line-height:1.5;margin-bottom:12px}.story b{color:#064e3b}
.imode{font-weight:700;color:#064e3b;font-size:.88rem;margin:14px 2px 7px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}.imode span{font-weight:500;color:#8a9a7c;font-size:.74rem}
.gobtn{background:linear-gradient(135deg,#10b981,#059669 55%,#047857)!important;color:#fff!important;border:none!important;font-weight:700!important;border-radius:14px!important;box-shadow:0 6px 18px rgba(5,150,105,.34)!important;letter-spacing:.01em}
.addbar .wrap{border-radius:14px!important}.adjnote{color:#8a9a7c;font-size:.78rem;margin:2px 0 8px}
button,.gobtn,a.vcard,.chip,.langrow label{cursor:pointer}
button:focus-visible,.typein textarea:focus-visible,a:focus-visible,.langrow input:focus-visible+span{outline:3px solid #34d399!important;outline-offset:2px!important;border-radius:8px}
.gobtn{transition:transform .15s ease,box-shadow .15s ease,filter .15s ease}
.gobtn:hover{transform:translateY(-2px);box-shadow:0 12px 26px rgba(5,150,105,.42)!important;filter:brightness(1.06)}
.rcard,.tile,.chip,.vcard,.step{transition:transform .18s ease,box-shadow .18s ease}
.rcard:hover,.vcard:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(5,150,105,.16)}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
.house3d{width:100%;min-height:420px;border-radius:24px;background:radial-gradient(120% 95% at 50% 0%,#eaf6ff,#f1faf4);border:1.5px solid #eee2cf;box-shadow:0 12px 30px rgba(40,110,60,.16);margin-top:10px;overflow:hidden;display:flex;align-items:center;justify-content:center}
.appbox{background:#fff;border:1.5px solid #eee2cf;border-radius:18px;padding:14px 16px;margin:14px 0 6px;box-shadow:0 4px 14px rgba(40,110,60,.08)}
.apphd{font-weight:700;color:#064e3b;font-size:.95rem;margin-bottom:10px}
.status{color:#059669!important;font-weight:600!important;font-size:.95rem!important}
.house3d canvas{display:block;border-radius:24px}
#sumbar{position:fixed;left:0;right:0;bottom:10px;z-index:200;display:flex;justify-content:center;padding:0 12px;pointer-events:none}
.sumbar{pointer-events:none;max-width:640px;width:100%;background:rgba(6,78,59,.97);color:#fff;border-radius:16px;padding:11px 16px;display:flex;flex-wrap:wrap;gap:4px;align-items:center;justify-content:center;font-size:.92rem;box-shadow:0 10px 28px rgba(5,80,50,.42)}
.sumbar b{color:#a7f3d0;font-weight:700}.sumbar span{opacity:.45;margin:0 4px}
.gradio-container{padding-bottom:66px!important}
.rcard.alt{border-color:#a7f3d0;border-style:dashed;background:#f1faf4}
.applchips{margin:8px 0}
.achip{display:inline-flex;align-items:center;gap:5px;background:#ecfdf5;border:1.5px solid #a7f3d0;color:#064e3b;border-radius:999px;padding:6px 12px;margin:3px;font-size:.9rem;font-weight:600}
.achip b{color:#059669}
.photofile,.camerabox{min-height:230px}
.camerabox [data-testid="image"],.camerabox .image-container{border-radius:14px;overflow:hidden}
@keyframes micglow{0%,100%{box-shadow:0 10px 30px rgba(16,185,129,.12)}50%{box-shadow:0 12px 38px rgba(16,185,129,.26)}}
.miccard{animation:micglow 3.4s ease-in-out infinite}
.flow,.home{max-width:100%}.tabs{margin-top:6px}
@media(max-width:540px){
 .banner h1{font-size:1.2rem}.banner p{font-size:.82rem;max-width:100%}.banner .bwrap{max-width:100%}
 .tile .v{font-size:1.15rem}.tile{padding:14px 6px}.bd{grid-template-columns:1fr 60px 44px}
 .gradio-container{max-width:100%!important}
}
@media(max-width:640px){.steps{grid-template-columns:1fr}.vendors{grid-template-columns:1fr}}
"""
THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.emerald, secondary_hue=gr.themes.colors.amber,
    neutral_hue=gr.themes.colors.stone, radius_size=gr.themes.sizes.radius_lg,
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"]).set(
    block_radius="20px", block_shadow="0 8px 24px rgba(120,80,20,.07)",
    button_large_radius="14px", button_small_radius="12px")


def show_thinking():
    return "🔎 Listening and sizing your system..."


def show_photo_thinking():
    return "🖼️ Reading your photos with the vision model..."


def set_summary(r):
    if not r:
        return "", "", count_html()
    track_event("sizing", {"panels": r["panel"]["count"], "kva": r["inverter"]["kva"],
                           "kwh": r["daily_kwh"]})
    d = r["batteries"]["durable"]
    summary = ('<div class="sumbar">☀️ <b>%d panels</b><span>·</span>🔌 <b>%s kVA</b><span>·</span>'
               '🔋 <b>%d battery</b><span>·</span>💰 <b>₦%s</b></div>') % (
        r["panel"]["count"], r["inverter"]["kva"], d["count"], format(d["total"], ","))
    return summary, home_2d_svg(r), count_html()


_QA_CACHE = {}


def ask(question, r, lang, sess):
    """Answer a follow-up question about the sized system (cached so repeats are instant)."""
    sess = get_session(sess)
    q = (question or "").strip()
    if not q:
        return "Ask about your plan, like 'why these panels?' or 'what would an AC add?'", sess
    if not r:
        return "Size your appliances first, then I can answer questions about your plan.", sess
    key = hashlib.md5(("%s|%s|%s" % (q, _house_data(r, None), lang)).encode("utf-8")).hexdigest()
    if key in _QA_CACHE:
        sess.event("qa", q=q[:60], cached=True)
        return _QA_CACHE[key], sess
    d = r["batteries"]["durable"]
    ctx = ("Daily use %s kWh. Plan: %d %s (about %s W array), a %s kVA inverter, %d %s with %s kWh backup, "
           "total about %s naira, at %s peak-sun hours." % (
               r["daily_kwh"], r["panel"]["count"], r["panel"]["name"], r["array_w"], r["inverter"]["kva"],
               d["count"], d["name"], d["backup_kwh"], format(d["total"], ","), r["sun_hours"]))
    if config.is_mock():
        ans = "Connect a model (env/solar.sh) and I will answer using your plan above."
    else:
        # Qwen3-1.7B writes fluent English and readable Pidgin, but garbles free-form Yoruba/Hausa/Igbo.
        # Answer those questions in clear English so the advice is always usable; the spoken plan and the
        # whole interface stay in the chosen language, so the experience is still theirs.
        ans_lang = lang if lang in ("en", "pcm") else "en"
        prompt = (
            "You are a warm, practical Nigerian solar advisor talking to a homeowner about their solar plan.\n"
            "Their plan: %s\n"
            "Answer their question in 2 to 4 short, friendly sentences in %s. Be concrete and encouraging, and "
            "use the plan's numbers when they help. You can help with anything about their plan, the panels, "
            "batteries, the inverter, sunlight and weather, cost and savings, running or adding specific "
            "appliances later, comparing solar with a generator, maintenance, or general home solar and "
            "electricity advice for Nigeria. Only if the question is clearly nothing to do with energy, power, "
            "money or the home, reply in one short friendly line and invite them to ask about their solar setup. "
            "Question: %s /no_think" % (ctx, LANG_NAME.get(ans_lang, "English"), q))
        # The small model very occasionally returns an empty turn (only think-tags, or a truncated
        # generation). Retry once, and never hand back a blank — fall back to a useful plan summary.
        ans = ""
        for _ in range(2):
            try:
                ans = _clean(llm.complete(prompt, max_tokens=230, timeout=190))
            except Exception:
                ans = ""
            if len(ans) >= 8:
                break
        if len(ans) < 8:
            ans = ("Here is the short version: your plan is %d %s (about %s W of panels), a %s kVA inverter and "
                   "%d %s for backup, around %s naira in total. Ask me anything about adding appliances, how long "
                   "it runs, your savings, or where to buy." % (
                       r["panel"]["count"], r["panel"]["name"], r["array_w"], r["inverter"]["kva"],
                       d["count"], d["name"], format(d["total"], ",")))
            sess.event("qa", q=q[:60], cached=False, empty=True)
            return ans, sess
    _QA_CACHE[key] = ans
    sess.event("qa", q=q[:60], cached=False)
    return ans, sess


APPLIANCE_ABBR = {
    "Fridge (small)": "fridge", "Chest freezer": "freezer", "Ceiling fan": "cfan", "Standing fan": "fan",
    'TV (32–43" LED)': "tv", "TV (large)": "tv", "Decoder (DStv/GOtv)": "decoder", "Sound system": "sound",
    "LED bulb": "bulb", "Energy-saver bulb": "bulb", "Security light": "bulb", "Laptop": "laptop", "Desktop PC": "desktop",
    "Air conditioner (1HP)": "ac", "Air conditioner (1.5HP)": "ac", "Air conditioner (2HP)": "ac",
    "Microwave": "micro", "Washing machine": "wash", "Water pump (1HP)": "pump", "Water pump (0.5HP)": "pump",
    "Electric iron": "iron", "Electric kettle": "kettle", "Water heater": "heater", "Phone charger": "phone", "Wifi router": "wifi",
}


def _house_data(r, sel=None):
    appl = ",".join("%s:%d" % (APPLIANCE_ABBR.get(k, "box"), v) for k, v in (sel or {}).items())
    return "%d|%d|%s" % (r["panel"]["count"], r["batteries"]["durable"]["count"], appl)


# Three.js loaded in the page <head> (Gradio doesn't sanitize head), drawing a real 3D
# flat-roof home into a persistent canvas via renderHouse(panels, batteries).
THREE_HEAD = """
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
window.renderHouse = function(panels, batts, appl){
  var el = document.getElementById('house3d');
  if(!el || typeof THREE === 'undefined'){ return; }
  if(window.__ns){ window.__ns.stop = true; } el.innerHTML = '';
  var W = el.clientWidth || 620, H = Math.min(560, Math.max(360, Math.round(W*0.62)));
  var scene = new THREE.Scene();
  var cam = new THREE.PerspectiveCamera(42, W/H, 0.1, 120);
  var rnd = new THREE.WebGLRenderer({antialias:true, alpha:true});
  rnd.setSize(W,H); rnd.setPixelRatio(Math.min(2, window.devicePixelRatio||1));
  rnd.shadowMap.enabled = true; rnd.shadowMap.type = THREE.PCFSoftShadowMap;
  el.appendChild(rnd.domElement);
  el.style.position = 'relative'; rnd.domElement.style.cursor = 'grab';
  var hud = document.createElement('div'); hud.className = 'h3dhud';
  var chips = '';
  if(panels > 0){
    chips += '<span>☀️ ' + panels + ' panel' + (panels>1?'s':'') + '</span>';
    chips += '<span>🔋 ' + (batts||1) + ' batter' + ((batts||1)>1?'ies':'y') + '</span>';
  }
  hud.innerHTML = chips + '<span class="h3dhint">↻ drag to look around</span>';
  el.appendChild(hud);
  function mat(c,r,m,e){ return new THREE.MeshStandardMaterial({color:c, roughness:(r==null?0.8:r), metalness:(m||0), emissive:(e||0x000000)}); }
  function box(w,h,d,material){ var b=new THREE.Mesh(new THREE.BoxGeometry(w,h,d), material); b.castShadow=true; b.receiveShadow=true; return b; }
  function add(o,x,y,z){ o.position.set(x,y,z); house.add(o); return o; }
  function ctex(dw){ var c=document.createElement('canvas'); c.width=128; c.height=128; dw(c.getContext('2d')); var t=new THREE.CanvasTexture(c); t.anisotropy=4; return t; }
  function woodTex(b,d){ return ctex(function(x){ x.fillStyle=b; x.fillRect(0,0,128,128); for(var i=0;i<26;i++){ x.strokeStyle=d; x.globalAlpha=0.06+Math.random()*0.09; x.lineWidth=1+Math.random()*2; x.beginPath(); var y=Math.random()*128; x.moveTo(0,y); x.bezierCurveTo(42,y+5,84,y-5,128,y+2); x.stroke(); } x.globalAlpha=1; }); }
  function fabTex(b){ return ctex(function(x){ x.fillStyle=b; x.fillRect(0,0,128,128); for(var i=0;i<2600;i++){ x.fillStyle='rgba(255,255,255,'+(Math.random()*0.05)+')'; x.fillRect(Math.random()*128,Math.random()*128,1,1);} }); }
  function metalTex(b){ return ctex(function(x){ x.fillStyle=b; x.fillRect(0,0,128,128); for(var i=0;i<70;i++){ x.strokeStyle='rgba(255,255,255,0.05)'; x.beginPath(); var xx=Math.random()*128; x.moveTo(xx,0); x.lineTo(xx,128); x.stroke(); } }); }
  function panelTex(){ var c=document.createElement('canvas'); c.width=256; c.height=170; var x=c.getContext('2d'); x.fillStyle='#0c2566'; x.fillRect(0,0,256,170); var cols=6,rows=4,g=4; var cw=(256-g*(cols+1))/cols, ch=(170-g*(rows+1))/rows; for(var i=0;i<cols;i++){for(var j=0;j<rows;j++){ var gx=g+i*(cw+g), gy=g+j*(ch+g); var gr=x.createLinearGradient(gx,gy,gx+cw,gy+ch); gr.addColorStop(0,'#2a5ce0'); gr.addColorStop(0.5,'#1c47b4'); gr.addColorStop(1,'#123693'); x.fillStyle=gr; x.fillRect(gx,gy,cw,ch); x.strokeStyle='rgba(190,210,255,0.28)'; x.lineWidth=1; x.beginPath(); x.moveTo(gx+cw/3,gy); x.lineTo(gx+cw/3,gy+ch); x.moveTo(gx+2*cw/3,gy); x.lineTo(gx+2*cw/3,gy+ch); x.stroke(); }} var t=new THREE.CanvasTexture(c); t.anisotropy=4; return t; }
  function tmat(tex,r,mtl){ return new THREE.MeshStandardMaterial({map:tex, roughness:(r==null?0.7:r), metalness:(mtl||0)}); }
  var T_WOOD=woodTex('#b98a5c','#7a5530'), T_WOOD2=woodTex('#8a5a35','#5a3a1f'), T_FAB=fabTex('#4f6fa0'), T_METAL=metalTex('#e2e6ea'), T_PANEL=panelTex();
  scene.add(new THREE.HemisphereLight(0xeaf4ff, 0x57503f, 0.55));
  var sun = new THREE.DirectionalLight(0xfff2dc, 1.1); sun.position.set(6,11,7); sun.castShadow=true; sun.shadow.mapSize.set(2048,2048); sun.shadow.bias=-0.0004; sun.shadow.radius=3;
  var scam=sun.shadow.camera; scam.left=-8; scam.right=8; scam.top=8; scam.bottom=-8; scam.near=1; scam.far=44; scene.add(sun);
  var fill = new THREE.DirectionalLight(0xbcd2ff, 0.2); fill.position.set(-6,4,4); scene.add(fill);
  var warm = new THREE.PointLight(0xffcf94, 0.65, 13); warm.position.set(-0.3,2.5,0.1); scene.add(warm);
  var ground = new THREE.Mesh(new THREE.CircleGeometry(8.5,64), mat(0xcfe3cb,1)); ground.rotation.x=-Math.PI/2; ground.receiveShadow=true; scene.add(ground);
  var house = new THREE.Group();
  var fl=box(6.2,0.22,4.2, tmat(T_WOOD,0.7)); fl.position.set(0,0.11,0); house.add(fl);
  var rug=box(2.7,0.04,2.0, mat(0xca6450,0.92)); rug.position.set(-0.3,0.24,0.45); house.add(rug);
  var wall=mat(0xf2ebdd,0.92);
  add(box(6.2,3.0,0.22, wall), 0,1.6,-2.05);
  add(box(0.22,3.0,4.2, wall), -3.05,1.6,0);
  add(box(0.22,3.0,4.2, mat(0xe9e0cf,0.92)), 3.05,1.6,0);
  add(box(1.4,1.0,0.06, mat(0xa6d6ec,0.2,0.3)), 1.4,1.95,-1.94);
  add(box(6.5,0.18,2.5, mat(0xe7d8bf,0.9)), 0,3.12,-0.85);
  var show=Math.min(Math.max(0,panels|0),9);
  var pmat=tmat(T_PANEL,0.32,0.1); pmat.emissive=new THREE.Color(0x16387e); pmat.emissiveIntensity=0.44;
  var fmat=mat(0xbac2d0,0.35,0.7), gmat=mat(0x12317e,0.3);
  var rack=new THREE.Group(); rack.position.set(0,3.24,-0.85); rack.rotation.x=-0.34; house.add(rack);
  for(var i=0;i<show;i++){ var c=i%3,row=Math.floor(i/3);
    var pg=new THREE.Group(); pg.position.set(-1.55+c*1.55,0.05,-0.5+row*0.62);
    pg.add(box(1.4,0.05,0.54,fmat)); var p=box(1.3,0.07,0.46,pmat); p.position.y=0.04; pg.add(p);
    var ln=box(1.3,0.08,0.015,gmat); ln.position.y=0.045; pg.add(ln); rack.add(pg); }
  // --- solar power system on the wall: inverter + battery stack (the kit being sized) ---
  var pwr=new THREE.Group();
  pwr.add(box(0.98,1.74,0.06, mat(0xd2cbbb,0.85)));
  var invG=new THREE.Group(); invG.position.set(0,0.52,0.07);
  invG.add(box(0.64,0.46,0.17, mat(0xeef1f5,0.32,0.4)));
  var disp=box(0.4,0.2,0.02, mat(0x05140d,0.2,0,0x1f9a55)); disp.material.emissiveIntensity=0.95; disp.position.set(0,0.05,0.095); invG.add(disp);
  var li1=box(0.045,0.045,0.02, mat(0x55e08a,0.2,0,0x55e08a)); li1.material.emissiveIntensity=1.4; li1.position.set(-0.24,-0.14,0.095); invG.add(li1);
  var li2=box(0.045,0.045,0.02, mat(0xffb84d,0.2,0,0xffb84d)); li2.material.emissiveIntensity=1.2; li2.position.set(-0.16,-0.14,0.095); invG.add(li2);
  pwr.add(invG);
  var nb=Math.min(Math.max(1,batts|0),3), batMat=mat(0x1f7a45,0.5,0.25);
  for(var bi=0;bi<nb;bi++){ var bat=box(0.72,0.27,0.2, batMat); bat.position.set(0,-0.12-bi*0.33,0.07); pwr.add(bat);
    var bl=box(0.05,0.05,0.02, mat(0x8effc0,0.2,0,0x8effc0)); bl.material.emissiveIntensity=1.3; bl.position.set(0.28,-0.12-bi*0.33,0.18); pwr.add(bl); }
  pwr.position.set(-1.95,1.36,-1.95); house.add(pwr);
  var sofM=tmat(T_FAB,0.92); var sofa=new THREE.Group();
  var s1=box(2.0,0.45,0.95,sofM); s1.position.y=0.45; sofa.add(s1);
  var s2=box(2.0,0.75,0.25,sofM); s2.position.set(0,0.85,-0.36); sofa.add(s2);
  var s3=box(0.25,0.62,0.95,sofM); s3.position.set(-0.9,0.62,0); sofa.add(s3);
  var s4=box(0.25,0.62,0.95,sofM); s4.position.set(0.9,0.62,0); sofa.add(s4);
  sofa.position.set(-0.3,0.22,-1.15); house.add(sofa);
  var person=new THREE.Group();
  var skin=mat(0x9c6b43,0.7), shirt=mat(0xe2703a,0.75), pant=mat(0x2f3b54,0.82), hair=mat(0x241811,0.6), shoe=mat(0x33241a,0.6);
  var pt=box(0.44,0.6,0.3,shirt); pt.position.set(0,1.04,-0.02); person.add(pt);
  var pn=new THREE.Mesh(new THREE.CylinderGeometry(0.07,0.08,0.13,10),skin); pn.position.set(0,1.4,-0.01); person.add(pn);
  var ph=new THREE.Mesh(new THREE.SphereGeometry(0.16,18,16),skin); ph.position.set(0,1.55,0.0); ph.castShadow=true; person.add(ph);
  var phair=new THREE.Mesh(new THREE.SphereGeometry(0.17,16,12,0,Math.PI*2,0,Math.PI*0.55),hair); phair.position.set(0,1.57,-0.01); person.add(phair);
  var thighL=box(0.19,0.17,0.46,pant); thighL.position.set(-0.12,0.7,0.2); person.add(thighL);
  var thighR=box(0.19,0.17,0.46,pant); thighR.position.set(0.12,0.7,0.2); person.add(thighR);
  var shinL=box(0.17,0.46,0.18,pant); shinL.position.set(-0.12,0.42,0.42); person.add(shinL);
  var shinR=box(0.17,0.46,0.18,pant); shinR.position.set(0.12,0.42,0.42); person.add(shinR);
  var footL=box(0.18,0.1,0.26,shoe); footL.position.set(-0.12,0.18,0.53); person.add(footL);
  var footR=box(0.18,0.1,0.26,shoe); footR.position.set(0.12,0.18,0.53); person.add(footR);
  var armL=box(0.13,0.46,0.14,shirt); armL.position.set(-0.3,0.95,0.1); armL.rotation.x=0.5; person.add(armL);
  var armR=box(0.13,0.46,0.14,shirt); armR.position.set(0.3,0.95,0.1); armR.rotation.x=0.5; person.add(armR);
  person.position.set(-0.3,0.2,-1.16); house.add(person);
  var tw=tmat(T_WOOD2,0.7); var table=new THREE.Group(); table.add(box(1.1,0.08,0.6,tw));
  [[-0.48,0.24],[0.48,0.24],[-0.48,-0.24],[0.48,-0.24]].forEach(function(q){var lg=box(0.08,0.42,0.08,tw); lg.position.set(q[0],-0.22,q[1]); table.add(lg);});
  table.position.set(-0.3,0.64,0.15); house.add(table);
  add(box(1.7,0.5,0.42, tmat(T_WOOD2,0.7)), -0.3,0.47,1.62);
  var counts={}; (appl||'').split(',').forEach(function(s){ var kv=s.split(':'); if(kv[0]){ counts[kv[0]]=parseInt(kv[1])||1; } });
  var spin=[];
  function fridge(){ var g=new THREE.Group();
    g.add(box(0.74,1.55,0.64,tmat(T_METAL,0.32,0.4)));
    var split=box(0.76,0.03,0.66,mat(0x9aa0a8,0.5,0.4)); split.position.y=0.14; g.add(split);     // fridge/freezer divide
    var seam=box(0.02,0.74,0.66,mat(0xc2c8d0,0.4)); seam.position.y=0.54; g.add(seam);            // double-door seam
    var h1=box(0.04,0.46,0.06,mat(0x70767d,0.4,0.6)); h1.position.set(-0.3,0.54,0.34); g.add(h1);
    var h2=box(0.04,0.46,0.06,mat(0x70767d,0.4,0.6)); h2.position.set(0.3,0.54,0.34); g.add(h2);
    var h3=box(0.46,0.04,0.06,mat(0x70767d,0.4,0.6)); h3.position.set(0,-0.08,0.34); g.add(h3);     // freezer handle
    g.position.y=0.78; return g; }
  function freezer(){ var g=box(1.0,0.72,0.56,mat(0xeef1f4,0.4,0.25)); g.position.y=0.47; return g; }
  function stdfan(){ var g=new THREE.Group();
    var base=new THREE.Mesh(new THREE.CylinderGeometry(0.16,0.18,0.05,16),mat(0x3a3a3a,0.5,0.4)); base.position.y=0.04; g.add(base);
    var pole=new THREE.Mesh(new THREE.CylinderGeometry(0.035,0.045,0.95,12),mat(0x6a6a6a,0.4,0.5)); pole.position.y=0.5; g.add(pole);
    var fan=new THREE.Group(); fan.position.set(0,1.05,0.05);
    var ring=new THREE.Mesh(new THREE.TorusGeometry(0.3,0.022,8,26),mat(0xb6bec9,0.45,0.5)); fan.add(ring);
    var hub=new THREE.Mesh(new THREE.CylinderGeometry(0.06,0.06,0.08,12),mat(0x4a4a4a,0.4,0.5)); hub.rotation.x=Math.PI/2; fan.add(hub);
    var blades=new THREE.Group();
    for(var k=0;k<5;k++){ var bl=box(0.26,0.006,0.11,mat(0x7fb0e0,0.35,0.2)); bl.position.x=0.14; bl.rotation.x=0.3; var bg=new THREE.Group(); bg.add(bl); bg.rotation.z=k*Math.PI*2/5; blades.add(bg); }
    fan.add(blades); spin.push({o:blades,ax:'z',sp:0.7});
    g.add(fan); return g; }
  function tvset(){ var g=new THREE.Group(); g.add(box(1.35,0.8,0.06,mat(0x0a0a0a,0.2,0.3))); var sc=box(1.22,0.68,0.01,mat(0x16344f,0.2,0,0x16344f)); sc.material.emissiveIntensity=0.55; sc.position.z=0.04; g.add(sc); return g; }
  function acUnit(){ var g=new THREE.Group();
    g.add(box(1.05,0.34,0.28,mat(0xf5f7f9,0.5)));
    for(var k=0;k<3;k++){ var lv=box(0.96,0.022,0.02,mat(0xdadfe5,0.5)); lv.position.set(0,-0.07-k*0.045,0.145); lv.rotation.x=0.55; g.add(lv); }
    var dp=box(0.07,0.035,0.01,mat(0x2ad27a,0.2,0,0x2ad27a)); dp.material.emissiveIntensity=1.1; dp.position.set(0.38,0.07,0.145); g.add(dp);
    return g; }
  function bulbObj(){ var g=new THREE.Group(); var wr=new THREE.Mesh(new THREE.CylinderGeometry(0.012,0.012,0.5,6),mat(0x333,0.6)); wr.position.y=0.25; g.add(wr); var bb=new THREE.Mesh(new THREE.SphereGeometry(0.12,16,14),mat(0xfff0b8,0.2,0,0xffe08a)); bb.material.emissiveIntensity=1.0; g.add(bb); return g; }
  function ceilFan(){ var g=new THREE.Group(); g.add(new THREE.Mesh(new THREE.CylinderGeometry(0.14,0.14,0.12,16),mat(0x555,0.5,0.4))); for(var k=0;k<4;k++){ var bg=new THREE.Group(); var bl=box(0.95,0.025,0.18,mat(0x8a5a35,0.6)); bl.position.x=0.55; bg.add(bl); bg.rotation.y=k*Math.PI/2; g.add(bg);} spin.push({o:g,ax:'y',sp:0.3}); return g; }
  function laptopObj(){ var g=new THREE.Group(); g.add(box(0.42,0.03,0.3,mat(0x33373d,0.4,0.5))); var sc=box(0.42,0.28,0.02,mat(0x101418,0.2,0.4,0x0d2a44)); sc.material.emissiveIntensity=0.4; sc.position.set(0,0.15,-0.14); sc.rotation.x=-0.4; g.add(sc); return g; }
  function microObj(){ var g=new THREE.Group();
    g.add(box(0.58,0.34,0.4,mat(0x2b2f33,0.4,0.4)));
    var win=box(0.34,0.24,0.01,mat(0x0d1014,0.15,0,0x0a1f14)); win.material.emissiveIntensity=0.3; win.position.set(-0.08,0,0.205); g.add(win);
    var pnl=box(0.13,0.28,0.01,mat(0x3a3f44,0.4)); pnl.position.set(0.2,0,0.205); g.add(pnl);
    for(var k=0;k<3;k++){ var bt=box(0.07,0.025,0.01,mat(0x9aa0a8,0.4)); bt.position.set(0.2,0.07-k*0.05,0.21); g.add(bt); }
    return g; }
  function washObj(){ var g=new THREE.Group();
    g.add(box(0.62,0.86,0.6,mat(0xeef1f4,0.4,0.2)));
    var ring=new THREE.Mesh(new THREE.TorusGeometry(0.19,0.025,10,24),mat(0xaeb4bb,0.4,0.5)); ring.position.set(0,-0.06,0.3); g.add(ring);
    var glass=new THREE.Mesh(new THREE.CircleGeometry(0.16,22),mat(0x1a2733,0.15,0.4)); glass.position.set(0,-0.06,0.305); g.add(glass);
    var pnl=box(0.5,0.12,0.02,mat(0x3a3f44,0.4)); pnl.position.set(0,0.32,0.3); g.add(pnl);
    var dial=new THREE.Mesh(new THREE.CylinderGeometry(0.04,0.04,0.02,12),mat(0xdddddd,0.4)); dial.rotation.x=Math.PI/2; dial.position.set(0.15,0.32,0.31); g.add(dial);
    g.position.y=0.46; return g; }
  function soundObj(){ var g=new THREE.Group(); [-0.32,0.32].forEach(function(x){var s=box(0.24,0.72,0.24,mat(0x222,0.5)); s.position.set(x,0.36,0); g.add(s);}); return g; }
  function decoderObj(){ var g=box(0.5,0.1,0.32,mat(0x1c1c1c,0.4,0.3)); var led=box(0.06,0.02,0.02,mat(0x44d27a,0.3,0,0x44d27a)); led.material.emissiveIntensity=1; led.position.set(0.18,0,0.17); g.add(led); return g; }
  function genObj(){ return box(0.42,0.42,0.42,mat(0xc2c2c2,0.6)); }
  function plantObj(){ var g=new THREE.Group();
    var pot=new THREE.Mesh(new THREE.CylinderGeometry(0.14,0.1,0.22,12),mat(0xc06a3a,0.7)); pot.position.y=0.11; g.add(pot);
    var lf=mat(0x3f8f4a,0.8);
    for(var k=0;k<6;k++){ var leaf=new THREE.Mesh(new THREE.SphereGeometry(0.13,8,6),lf); leaf.position.set((k%3-1)*0.1,0.34+Math.floor(k/3)*0.15,(k%2-0.5)*0.12); leaf.scale.set(0.7,1.9,0.7); leaf.rotation.z=(k-3)*0.18; leaf.castShadow=true; g.add(leaf); }
    return g; }
  function pictureObj(){ var g=new THREE.Group();
    g.add(box(0.52,0.38,0.03,mat(0x6a4a2e,0.6)));
    var im=box(0.44,0.3,0.01,mat(0x86c0e0,0.4,0,0x2a5a7a)); im.material.emissiveIntensity=0.16; im.position.z=0.02; g.add(im);
    return g; }
  function placeType(type){ var c=counts[type]; if(!c){return;}
    if(type==='fridge'){ add(fridge(),2.45,0,-1.1); }
    else if(type==='freezer'){ add(freezer(),2.4,0,0.95); }
    else if(type==='tv'){ add(tvset(),-0.3,1.1,1.62); }
    else if(type==='ac'){ for(var i=0;i<Math.min(c,2);i++){ add(acUnit(),-1.7+i*1.3,2.55,-1.92); } }
    else if(type==='fan'){ var sp=[[-2.45,1.3],[2.45,1.4]]; for(var i=0;i<Math.min(c,2);i++){ add(stdfan(),sp[i][0],0,sp[i][1]); } }
    else if(type==='cfan'){ for(var i=0;i<Math.min(c,2);i++){ add(ceilFan(),-1.1+i*2.0,3.02,-0.7); } }
    else if(type==='bulb'){ var n=Math.min(c,4); for(var i=0;i<n;i++){ add(bulbObj(),-1.9+i*1.25,3.0,0.3); } }
    else if(type==='laptop'){ add(laptopObj(),-0.3,0.69,0.15); }
    else if(type==='micro'){ add(box(0.7,0.84,0.55,tmat(T_WOOD2,0.7)),2.45,0.42,0.3); add(microObj(),2.45,0.96,0.3); }
    else if(type==='wash'){ add(washObj(),-2.45,0,-1.45); }
    else if(type==='sound'){ add(soundObj(),0.95,0,1.55); }
    else if(type==='decoder'){ add(decoderObj(),-0.3,0.78,1.62); }
    else { add(genObj(),2.3,0.2,1.5); }
  }
  ['fridge','freezer','wash','fan','ac','cfan','bulb','tv','decoder','laptop','micro','sound','desktop','pump','iron','kettle','heater','phone','wifi'].forEach(placeType);
  add(plantObj(), -2.6, 0, 1.55);
  var pic=pictureObj(); pic.position.set(-0.55,2.1,-1.97); house.add(pic);
  scene.add(house);
  cam.position.set(2.6,4.4,8.0);
  var ctr = null;
  if(THREE.OrbitControls){
    ctr = new THREE.OrbitControls(cam, rnd.domElement);
    ctr.target.set(0,1.0,-0.4); ctr.enableDamping = true; ctr.dampingFactor = 0.07;
    ctr.enablePan = false; ctr.minDistance = 5; ctr.maxDistance = 12;
    ctr.maxPolarAngle = Math.PI*0.49; ctr.minPolarAngle = Math.PI*0.14;     // never under the floor
    ctr.minAzimuthAngle = -1.15; ctr.maxAzimuthAngle = 1.15;                // stay on the open front (no blank back wall)
    rnd.domElement.addEventListener('pointerdown', function(){ window.__nsHeld = true; rnd.domElement.style.cursor='grabbing'; });
    rnd.domElement.addEventListener('pointerup', function(){ rnd.domElement.style.cursor='grab'; });
    ctr.update();
  } else { cam.lookAt(0,1.0,-0.4); }
  var t0=0, st={stop:false}; window.__ns=st; window.__nsHeld = false;
  (function loop(){ if(st.stop){return;} requestAnimationFrame(loop); t0+=0.016;
    if(!window.__nsHeld){ house.rotation.y = Math.sin(t0*0.22)*0.16; }     // gentle sway until the user grabs it
    if(ctr){ ctr.update(); }
    for(var i=0;i<spin.length;i++){ spin[i].o.rotation[spin[i].ax]+=spin[i].sp; }
    rnd.render(scene,cam); })();
  window.addEventListener('resize', function(){ var w=el.clientWidth||W, h=Math.min(560,Math.max(360,Math.round(w*0.62))); cam.aspect=w/h; cam.updateProjectionMatrix(); rnd.setSize(w,h); });
};
setTimeout(function(){ try{ window.renderHouse(0,0,''); }catch(e){} }, 700);
</script>
<script>
/* Gemini-style voice auto-stop: stop recording after a short silence. Manual Stop still works. */
(function(){
  var REC=false, ac=null, an=null, stream=null, silence=0, started=0, raf=null;
  function findBtn(re){ var bs=document.querySelectorAll('button'); for(var i=0;i<bs.length;i++){ var t=((bs[i].getAttribute('aria-label')||'')+' '+(bs[i].textContent||'')).toLowerCase(); if(re.test(t)) return bs[i]; } return null; }
  function stopRec(){ if(!REC){return;} REC=false; if(raf){cancelAnimationFrame(raf);} try{ stream.getTracks().forEach(function(t){t.stop();}); ac.close(); }catch(e){} var sb=findBtn(/stop/); if(sb){ sb.click(); } }
  function monitor(){ if(!REC||!an){return;} var d=new Uint8Array(an.frequencyBinCount); an.getByteTimeDomainData(d);
    var s=0; for(var i=0;i<d.length;i++){ var v=(d[i]-128)/128; s+=v*v; } var rms=Math.sqrt(s/d.length);
    if(rms<0.02){ silence++; } else { silence=0; }
    if(Date.now()-started>1200 && silence>78){ stopRec(); return; } raf=requestAnimationFrame(monitor); }
  function startVAD(){ if(REC){return;} navigator.mediaDevices.getUserMedia({audio:true}).then(function(s){
    stream=s; ac=new (window.AudioContext||window.webkitAudioContext)(); var src=ac.createMediaStreamSource(s);
    an=ac.createAnalyser(); an.fftSize=512; src.connect(an); REC=true; started=Date.now(); silence=0; monitor(); }).catch(function(){}); }
  document.addEventListener('click', function(e){ var b=e.target&&e.target.closest&&e.target.closest('button'); if(!b){return;}
    var t=((b.getAttribute('aria-label')||'')+' '+(b.textContent||'')).toLowerCase();
    if(/record/.test(t)&&!/stop/.test(t)){ setTimeout(startVAD,350); } else if(/stop/.test(t)){ REC=false; if(raf){cancelAnimationFrame(raf);} } }, true);
})();
</script>
<script>
/* Count-up animation on the result metric numbers (premium motion cue). */
(function(){
  function animate(el){
    if(el.dataset.counted) return;
    var m=(el.textContent||'').trim().match(/^([^0-9]*)([0-9,]+(?:\\.[0-9]+)?)(.*)$/);
    if(!m){ return; } var pre=m[1], target=parseFloat(m[2].replace(/,/g,'')), suf=m[3];
    if(isNaN(target)){ return; } el.dataset.counted='1';
    var isInt=m[2].indexOf('.')<0, dur=850, start=null;
    function fmt(n){ return pre+(isInt?Math.round(n).toLocaleString():n.toFixed(2))+suf; }
    function step(ts){ if(!start)start=ts; var p=Math.min((ts-start)/dur,1), e=1-Math.pow(1-p,3);
      el.textContent=fmt(target*e); if(p<1){requestAnimationFrame(step);} else {el.textContent=fmt(target);} }
    requestAnimationFrame(step);
  }
  var obs=new MutationObserver(function(){ document.querySelectorAll('.tile .v').forEach(animate); });
  try{ obs.observe(document.body,{childList:true,subtree:true}); }catch(e){}
})();
</script>
"""
RENDER_JS = "(s) => { if(s){ var a=(''+s).split('|'); if(window.renderHouse){ window.renderHouse(parseInt(a[0])||0, parseInt(a[1])||0, a[2]||''); } } }"


def build():
    with gr.Blocks(title="Naija Solar", analytics_enabled=False) as demo:   # css/theme/head are applied at launch()/mount in Gradio 6
        sess, result, lang = gr.State(None), gr.State(None), gr.State("en")
        gr.HTML(logo_html())
        usercount = gr.HTML(count_html(), elem_id="ucount")
        summary_bar = gr.HTML(elem_id="sumbar")
        gr.HTML('<div class="langlabel">🌍 CHOOSE YOUR LANGUAGE</div>')
        lang_sel = gr.Radio([("English", "en"), ("Naijá Pidgin", "pcm"), ("Yorùbá", "yo"), ("Hausa", "ha"), ("Ìgbò", "ig")],
                            value="en", show_label=False, elem_classes="langrow")
        hero = gr.HTML(hero_html("en"))
        with gr.Accordion("New here? How Naija Solar works", open=False) as guide_acc:
            guide = gr.HTML(steps_html())
        with gr.Row(elem_classes="locrow"):
            state = gr.Dropdown(locations.STATES, value="Lagos", container=False, scale=2, show_label=False)
            geo_btn = gr.Button("Use my location", size="sm", scale=1)
        geolat = gr.Textbox(visible=False)
        loc_note = gr.Markdown(elem_classes="locnote")
        EXAMPLES = [
            ("🏠 Small home", "1 fridge, 2 standing fans, 6 bulbs, 1 TV"),
            ("🏪 Corner shop", "1 chest freezer, 1 air conditioner, 4 bulbs, 1 TV, 1 decoder"),
            ("👨‍👩‍👧 Family house", "1 fridge, 1 air conditioner, 3 fans, 8 bulbs, 1 TV, 1 washing machine"),
        ]
        with gr.Group(elem_classes="miccard"):
            mode_v = gr.HTML('<div class="imode">🎤 Speak your appliances <span>tap Record, speak, then Stop, it sizes itself</span></div>')
            voice = gr.Audio(sources=["microphone"], type="filepath", show_label=False, elem_classes="voicein")
            mode_t = gr.HTML('<div class="imode">⌨️ Or type them</div>')
            with gr.Row():
                text = gr.Textbox(placeholder=UI["en"]["type"], show_label=False, container=False,
                                  lines=1, scale=4, elem_classes="typein")
                text_btn = gr.Button("Size it", variant="primary", scale=1, min_width=96, elem_classes="gobtn")
            gr.HTML('<div class="imode">✨ New here? Tap an example <span>a full result in one click, no mic or typing</span></div>')
            with gr.Row(elem_classes="exrow"):
                ex_btns = [gr.Button(lbl, elem_classes="exbtn", size="sm") for lbl, _ in EXAMPLES]
            mode_p = gr.HTML('<div class="imode">📷 Or snap / upload photos</div>')
            with gr.Row(equal_height=True):
                photos = gr.File(file_count="multiple", file_types=["image"], scale=1,
                                 label="Upload up to 5 photos", elem_classes="photofile")
                camera = gr.Image(sources=["webcam"], type="filepath", scale=1,
                                  label="Open camera", height=216, elem_classes="camerabox")
            photo_btn = gr.Button("Detect appliances from photo", variant="primary", elem_classes="gobtn")
        status = gr.Markdown("", elem_classes="status")
        with gr.Tabs():
            with gr.Tab("🏠 3D Home"):
                house = gr.HTML('<div class="chcard"><div class="chttl">🏠 Your home in 3D '
                                '<span class="chsub">drag to look around · scroll to zoom</span></div>'
                                '<div id="house3d" class="house3d"></div></div>')
                hdata = gr.Textbox(visible=False)
            with gr.Tab("🖼️ 2D View"):
                home2d = gr.HTML('<div class="flowph">Your home and system will appear here in 2D</div>')
            with gr.Tab("⚡ System"):
                flow = gr.HTML('<div class="flowph">Your energy system will animate here</div>')
                breakdown = gr.HTML()
        with gr.Column(visible=False) as panel:
            gr.HTML('<div class="audiolabel">🔊 Your plan, spoken aloud (tap play to hear it again)</div>')
            walk_audio = gr.Audio(autoplay=True, show_label=False, container=False)
            content = gr.HTML()
            fine = gr.HTML(f'<div class="fine">{UI["en"]["fine"]}</div>')
        with gr.Accordion("Adjust appliances (add or edit manually)", open=False):
            gr.HTML('<div class="adjnote">Pick an appliance and quantity, then tap Add. Add as many as you like, '
                    'then tap Update sizing.</div>')
            with gr.Row():
                picker = gr.Dropdown(list(data.APPLIANCES), multiselect=False, value=None, filterable=True,
                                     label="Appliance", scale=3, elem_classes="addbar")
                qty_in = gr.Number(value=1, minimum=1, maximum=99, step=1, label="Qty", scale=1)
                add_btn = gr.Button("➕ Add", variant="primary", elem_classes="gobtn", scale=1)
            applist = gr.HTML(elem_classes="applist")
            appl_state = gr.State([])
            with gr.Row():
                recalc_btn = gr.Button("⚡ Update sizing", variant="primary", elem_classes="gobtn", scale=3)
                clear_btn = gr.Button("Clear list", scale=1)
        with gr.Accordion("💬 Ask about your system", open=False):
            qa_in = gr.Textbox(placeholder="e.g. Why these panels? What would an AC add? How long does the battery last?",
                               show_label=False, container=False, lines=1, elem_classes="typein")
            qa_btn = gr.Button("Ask", variant="primary", elem_classes="gobtn")
            qa_out = gr.Markdown()
        with gr.Accordion("Where to buy", open=False):
            gr.HTML(vendors_html())
        with gr.Accordion("Activity", open=False):
            sess_btn = gr.Button("Refresh", size="sm")
            sess_html = gr.HTML()
        with gr.Accordion("💛 Was this helpful? Get solar updates", open=False, elem_classes="fbacc"):
            fb_comment = gr.Textbox(placeholder="Any comment to help us improve? (optional)", show_label=False,
                                    lines=2, elem_classes="typein")
            with gr.Row():
                fb_up = gr.Button("👍 Helpful", elem_classes="fbbtn")
                fb_down = gr.Button("👎 Could be better", elem_classes="fbbtn")
            fb_msg = gr.HTML()
            gr.HTML('<div class="ehint">📩 Want solar tips, or your result saved? Add your email — optional, no spam.</div>')
            with gr.Row():
                email_in = gr.Textbox(placeholder="you@email.com", show_label=False, scale=3, elem_classes="typein")
                email_btn = gr.Button("Notify me", elem_classes="gobtn", scale=1)
            email_msg = gr.HTML()

        ins = [voice, text, state, geolat, lang, sess]
        outs = [hdata, flow, breakdown, content, applist, status, sess, result, lang, panel, appl_state]
        spk = (speak, [result, lang, sess], [walk_audio, sess])
        ls_outs = [lang, hero, text, fine, mode_v, mode_t, mode_p, text_btn, photo_btn, guide, guide_acc]
        voice.stop_recording(show_thinking, None, status).then(run, ins, outs).then(set_summary, result, [summary_bar, home2d, usercount]).then(*spk)
        text.submit(show_thinking, None, status).then(run, ins, outs).then(set_summary, result, [summary_bar, home2d, usercount]).then(*spk)
        text_btn.click(show_thinking, None, status).then(run, ins, outs).then(set_summary, result, [summary_bar, home2d, usercount]).then(*spk)
        for _btn, (_lbl, _val) in zip(ex_btns, EXAMPLES):     # one-tap examples: fill, clear mic, then size
            _btn.click(lambda v=_val: (v, None), None, [text, voice]).then(
                show_thinking, None, status).then(run, ins, outs).then(
                set_summary, result, [summary_bar, home2d, usercount]).then(*spk)
        lang_sel.change(set_lang, lang_sel, ls_outs)
        hdata.change(None, hdata, None, js=RENDER_JS)
        geo_btn.click(None, None, geolat, js=GEO_JS)
        geolat.change(geo_note, geolat, loc_note)
        add_btn.click(add_appliance, [picker, qty_in, appl_state], [appl_state, applist])
        clear_btn.click(clear_appliances, None, [appl_state, applist])
        photo_btn.click(show_photo_thinking, None, status).then(
            from_photos, [photos, camera, state, geolat, lang, sess], outs).then(set_summary, result, [summary_bar, home2d, usercount]).then(*spk)
        recalc_btn.click(show_thinking, None, status).then(
            recalc, [appl_state, state, geolat, sess], [hdata, flow, breakdown, content, status, sess, result, panel]).then(set_summary, result, [summary_bar, home2d, usercount]).then(*spk)
        sess_btn.click(lambda: sessions.render_html(12), None, sess_html)
        qa_btn.click(ask, [qa_in, result, lang, sess], [qa_out, sess])
        qa_in.submit(ask, [qa_in, result, lang, sess], [qa_out, sess])
        fb_up.click(lambda c: submit_feedback("up", c), fb_comment, fb_msg)
        fb_down.click(lambda c: submit_feedback("down", c), fb_comment, fb_msg)
        email_btn.click(submit_email, email_in, email_msg)
        demo.load(count_html, None, usercount)
    return demo


_init_tracking()
# The custom FastAPI frontend (server.py) imports this module for its logic/generators only,
# so it sets BUILDSMALL_NO_GRADIO=1 to skip building the (unused) Gradio Blocks.
demo = None if os.environ.get("BUILDSMALL_NO_GRADIO") == "1" else build()

if __name__ == "__main__":
    on_space = bool(os.environ.get("SPACE_ID"))
    demo.launch(css=CSS, theme=THEME, head=THREE_HEAD,
                server_name="0.0.0.0" if on_space else "127.0.0.1",
                server_port=int(os.environ.get("PORT", "7860")))
