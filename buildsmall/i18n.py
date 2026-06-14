"""Lightweight i18n for Nigerian languages.

English + Nigerian Pidgin are provided in full. Hausa / Yoruba / Igbo carry
greetings and fall back to English for sensitive disclaimers — get those reviewed
by a native speaker before relying on them (see DATA_NEEDS.md).
"""
from __future__ import annotations

LANGS = [("English", "en"), ("Pidgin", "pcm"), ("Hausa", "ha"), ("Yorùbá", "yo"), ("Igbo", "ig")]
LANG_CODES = [c for _, c in LANGS]

_STR = {
    "greeting": {
        "en": "Welcome", "pcm": "You welcome", "ha": "Barka da zuwa",
        "yo": "Ẹ káàbọ̀", "ig": "Nnọọ",
    },
    "verify_authority": {
        "en": "Always verify with the official source before acting.",
        "pcm": "Always confirm with the correct office before you act on am.",
    },
    "not_medical": {
        "en": "This reads and explains information only. For medical advice, see a pharmacist or doctor.",
        "pcm": "Dis one na to read and explain only. For health matter, go see pharmacist or doctor.",
    },
    "not_legal": {
        "en": "This formats your own information; it is not legal advice.",
        "pcm": "Dis one dey arrange your own gist; e no be legal advice.",
    },
    "not_detection": {
        "en": "A small model cannot detect danger or verify identity. The human decides.",
        "pcm": "Small model no fit detect danger or confirm person. Na human dey decide.",
    },
    "estimate_only": {
        "en": "Figures are estimates based on what you entered.",
        "pcm": "Dis figures na estimate based on wetin you put.",
    },
    "btn_generate": {"en": "Generate", "pcm": "Generate am", "ha": "Ƙirƙira", "yo": "Ṣẹ̀dá", "ig": "Mepụta"},
    "btn_clear": {"en": "Clear", "pcm": "Clear am", "ha": "Share", "yo": "Nù ú", "ig": "Hichaa"},
    "btn_save": {"en": "Save", "pcm": "Save am", "ha": "Ajiye", "yo": "Fi pamọ́", "ig": "Chekwaa"},
    "btn_play": {"en": "Play", "pcm": "Play am", "ha": "Kunna", "yo": "Tẹ̀ ẹ́", "ig": "Kpọọ"},
}


def t(key, lang="en"):
    entry = _STR.get(key, {})
    return entry.get(lang) or entry.get("en") or key
