"""Deterministic, domain-aware MOCK backend.

The goal is not random lorem-ipsum — it is plausible, Nigerian-flavoured output so
every app is fully demoable (and screenshot-able) with no weights and no GPU. Each
app passes a `task=` hint; handlers below reflect the user's input back so the UI
feels responsive. Swap to a real model by setting BUILDSMALL_BACKEND=openai|llamacpp.
"""
from __future__ import annotations

import json
import math
import os
import struct
import wave


# ── message helpers ───────────────────────────────────────────────────────────
def _last_user(msgs):
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):  # vision-style content
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return c or ""
    return ""


def _system(msgs):
    return " ".join(m.get("content", "") for m in msgs if m.get("role") == "system" and isinstance(m.get("content"), str))


# ── task handlers ─────────────────────────────────────────────────────────────
def _tutor_explain(u, **k):
    return (
        f"**Worked solution**\n\n"
        f"Let's break down the question step by step.\n\n"
        f"1. **Identify what's asked.** Read it again: \"{u[:160]}\". Underline the key quantity.\n"
        f"2. **Recall the rule.** For this topic, the relevant formula/principle is applied directly.\n"
        f"3. **Work it through.** Substitute the values and simplify carefully, line by line.\n"
        f"4. **Check.** Does the answer make sense in size and units?\n\n"
        f"**Answer:** the option that follows from step 3.\n\n"
        f"_Tip: this explanation can err — always cross-check with your textbook._"
    )


def _quiz_gen(u, n=5, **k):
    qs = [{
        "question": f"Sample question {i+1} on: {u[:60] or 'the selected topic'}?",
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "answer": "Option A",
        "explanation": "Option A is correct because it follows directly from the curated source material.",
    } for i in range(int(n))]
    return json.dumps({"questions": qs}, ensure_ascii=False)


def _meal_plan(u, **k):
    return (
        "## This week's plan (₦ budget-aware)\n\n"
        "Built on affordable staples and balanced for protein, energy and vegetables.\n\n"
        "| Day | Meal | Est. cost |\n|---|---|---|\n"
        "| Mon | Beans & garri, palm oil, pepper | ₦900 |\n"
        "| Tue | Jollof rice (small chicken share) | ₦1,400 |\n"
        "| Wed | Yam porridge with ugwu | ₦1,100 |\n"
        "| Thu | Beans & plantain | ₦1,000 |\n"
        "| Fri | Eba & egusi (smoked fish) | ₦1,300 |\n\n"
        "**Market list:** beans 2 cups, garri 1 paint, rice 1 derica, ugwu 1 bunch, palm oil, pepper, smoked fish.\n\n"
        "**Stretch tips:** cook beans in bulk; reuse stew base; buy pepper in bulk and freeze.\n\n"
        "_Estimates only — your local prices decide the final figure._"
    )


def _grade(u, max_score=20, **k):
    max_score = int(max_score or 20)
    score = max(0, round(max_score * 0.7))
    return json.dumps({
        "score": score,
        "max": max_score,
        "feedback": "Clear introduction and a relevant example. Strengthen the second argument with evidence, and watch subject-verb agreement in paragraph 3.",
        "strengths": ["Clear thesis", "Good use of an example"],
        "improvements": ["Weak second argument", "Two grammar slips", "Conclusion is rushed"],
    }, ensure_ascii=False)


def _letter(u, **k):
    return (
        "[Your Name]\n[Your Address]\n[Date]\n\n"
        "The Honourable Representative,\n[Office / Ward]\n\n"
        "Dear Sir/Ma,\n\n"
        f"RE: {u[:80] or 'A community concern requiring your attention'}\n\n"
        "I write as a constituent to respectfully bring to your attention the matter above, "
        "which affects residents of our ward directly. We would be grateful for your intervention "
        "and a timeline for action.\n\n"
        "Thank you for your service.\n\nYours faithfully,\n[Your Name]\n[Phone]"
    )


def _summary(u, **k):
    return (
        "**What we know**\n- Key verified points extracted from the input.\n\n"
        "**What we don't know**\n- Items still unconfirmed; treat as rumour until verified.\n\n"
        "**What to do**\n- Calm, concrete next steps.\n\n_Verify with the appropriate authority before acting._"
    )


def _minutes(u, **k):
    return (
        "## Meeting Minutes\n\n"
        "**Attendance:** (as recorded)\n\n"
        "**Decisions**\n1. Decision one — agreed.\n2. Decision two — agreed.\n\n"
        "**Action items**\n| Owner | Task | Deadline |\n|---|---|---|\n| A. Member | Follow up | Next week |\n\n"
        "**Next meeting:** TBD"
    )


def _aunty_note(u, **k):
    occ = u[:60] or "your news"
    return (
        f"Ahhh my dear pikin! So I hear about {occ}. Glory be to God! "
        "You know say I dey pray for you every single day, morning and night. "
        "God wey start am, na Him go finish am, you hear? "
        "But ehn — make you no forget where you come from o. Pride goes before a fall. "
        "Remember: 'monkey wey dey jump too much, one day im go fall.' "
        "Greet your papa, greet your mama, greet that your friend wey no dey greet me. "
        "When you reach, send small thing for your aunty, no be by force, but… you understand. "
        "Okay my dear, kiss kiss, God bless you, byeee!"
    )


def _nollywood(u, **k):
    seed = u[:50] or "an inheritance"
    return (
        f"# BLOOD AND BUTTER: The {seed.title()} Saga\n\n"
        "**Tagline:** _Some secrets refuse to stay buried._\n\n"
        "**Act 1.** A joyful family gathering hides a decades-old betrayal.\n"
        "**Act 2.** The village people strike: a mysterious illness, a missing will, a returning prodigal.\n"
        "**Act 3.** Thunder fire! The truth explodes at the worst possible moment, and one slap echoes for generations.\n\n"
        "**Cast:** The Patriarch · The Scheming In-Law · The Returnee · Mama G.\n\n_To be continued in Part 2…_"
    )


def _haggle(u, **k):
    return ("Ahn ahn! My friend, that price wey you talk, you wan finish me? *suck teeth* "
            "This one na original, no be the fake wey dey roadside. Last last, bring small money make I see your hand. "
            "But no be that your price o — add something, this is Balogun market, not charity!")


def _jollof(u, **k):
    return ("Let the record show: Nigerian jollof is the undisputed heavyweight champion of West Africa. "
            "The smoky base, the long-grain par-boiled rice, the party pedigree — Ghana, with respect, brings a lovely side dish. "
            "I rest my case, your honour. *drops ladle*")


def _proverb_judge(u, **k):
    return ("Hmmm. *adjusts cap* That proverb? It lands… but only halfway. "
            "The aptness is there, the timing is questionable. I score it 6 out of 10. "
            "Next time, let the proverb bite before the silence ends.")


def _coach(u, **k):
    return (
        "**Stay calm. Here is your next safe step:**\n\n"
        "1. Do NOT confirm any names or details to the caller.\n"
        "2. Hang up and call your relative directly on a number you already know.\n"
        "3. Note the caller's number, time, and exactly what was said.\n"
        "4. If anyone is truly in danger, contact the police (112) and trusted family.\n\n"
        "_This is a safe procedure to rehearse — it never decides whether a specific call is real._"
    )


def _fake_alert_tells(u, **k):
    return (
        "**Red-flag training (this is education, NOT a verdict on any real alert):**\n\n"
        "- ✅ Always confirm INSIDE your own bank app or USSD — never trust the SMS or a screenshot.\n"
        "- 🔎 Check: bank logo quality, the balance line, reference-number format, odd grammar/spacing.\n"
        "- ⏱️ Never release goods on a 'pending' or screenshot 'success'.\n\n"
        "_The 30-second habit — verify in your own app — defeats every fake alert._"
    )


def _diacritics(u, **k):
    # toy restoration so the demo shows *something* changed
    repl = {"e": "ẹ", "o": "ọ", "s": "ṣ"}
    out = "".join(repl.get(c, c) if i % 7 == 0 else c for i, c in enumerate(u))
    return out or "Ẹ káàárọ̀ — (paste unmarked Yoruba to see restored tone marks)."


def _stretch_tips(u, **k):
    return (
        "**Make your chop money go far:**\n"
        "- Cook beans in bulk once — e go serve like two days, save gas and money.\n"
        "- Reuse one stew base for rice today and yam tomorrow.\n"
        "- Buy pepper, tomato and onion in bulk, blend am, freeze small-small.\n"
        "- Stretch protein: small fish for many plates, no be one big piece for one plate."
    )


HANDLERS = {
    "tutor_explain": _tutor_explain, "quiz_gen": _quiz_gen, "meal_plan": _meal_plan,
    "stretch_tips": _stretch_tips,
    "grade": _grade, "letter": _letter, "summary": _summary, "minutes": _minutes,
    "aunty_note": _aunty_note, "nollywood": _nollywood, "haggle": _haggle,
    "jollof": _jollof, "proverb_judge": _proverb_judge, "coach": _coach,
    "scam_coach": _coach, "fake_alert_tells": _fake_alert_tells, "diacritics": _diacritics,
}

_KEYWORDS = [
    ("meal", "meal_plan"), ("budget", "meal_plan"), ("grade", "grade"), ("rubric", "grade"),
    ("letter", "letter"), ("petition", "letter"), ("minutes", "minutes"),
    ("scam", "coach"), ("fake alert", "fake_alert_tells"), ("nollywood", "nollywood"),
    ("haggl", "haggle"), ("jollof", "jollof"), ("proverb", "proverb_judge"),
    ("aunty", "aunty_note"), ("diacritic", "diacritics"), ("quiz", "quiz_gen"),
    ("explain", "tutor_explain"), ("summar", "summary"),
]


# ── public API ────────────────────────────────────────────────────────────────
def chat(msgs, task=None, **kw) -> str:
    u = _last_user(msgs)
    sys = _system(msgs).lower()
    if task and task in HANDLERS:
        return HANDLERS[task](u, **kw)
    blob = (sys + " " + u).lower()
    for kw_str, name in _KEYWORDS:
        if kw_str in blob:
            return HANDLERS[name](u, **kw)
    if "json" in sys:
        return json.dumps({"result": u[:200], "note": "mock JSON output"}, ensure_ascii=False)
    # generic, still-useful fallback
    return (
        f"(mock) Here is a helpful, structured response to your request.\n\n"
        f"You said: \"{u[:200]}\"\n\n"
        f"• A small model formats, explains and organises — the human decides.\n"
        f"• Set BUILDSMALL_BACKEND=openai (or llamacpp) to use a real model."
    )


def vision(prompt, task=None, **kw) -> str:
    if task == "ocr":
        return ("PARACETAMOL TABLETS BP 500mg\nDose: As directed by physician\n"
                "NAFDAC Reg No: A4-1234\nMfg: 2025-01  Exp: 2027-12\nBatch: NG-0042")
    return ("A printed product pack on a wooden surface. Visible text includes a brand name, "
            "a dosage line, a NAFDAC number and an expiry date. No people are visible.")


def asr(audio_path, language=None, **kw) -> str:
    base = os.path.basename(str(audio_path or "clip"))
    return f"(mock transcript of {base}) Good afternoon everyone, I want to log today's contribution and the route for the trip."


def tts(text, lang="pcm", out_path="tts_out.wav", **kw) -> str:
    """Write a short, soft tone as a real .wav so the audio component plays."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rate, dur = 16000, min(1.2, 0.25 + len(text) / 400.0)
    n = int(rate * dur)
    with wave.open(out_path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(2500 * math.sin(2 * math.pi * 330 * i / rate) * (1 - i / n))) for i in range(n))
        w.writeframes(frames)
    return out_path
