"""Generators for the 'Show, Don't Tell' deliverables.

Every submission needs a 60-90s demo video and a social post; Field Notes earns a
badge. These produce ready-to-edit scaffolds from a small metadata dict so you are
never staring at a blank page on filming day.
"""
from __future__ import annotations

TAGS = "#BuildSmallHackathon #Gradio #HuggingFace"


def demo_script(name, problem_stat, person, aha, outcome, url="", seconds="60-90s"):
    """Four-beat vertical-video script (hook → real use → the aha → outcome)."""
    return f"""# Demo script — {name}  ({seconds}, vertical, captions ON)

BEAT 1 · HOOK (0-3s)
  On-screen text + voice: "{problem_stat}"

BEAT 2 · REAL USE (3-35s)  [Track A: this is the judged moment]
  Show: {person} using the app for a genuine task, end to end.
  B-roll opener: 5s of their real context (the stall / staff room / compound).

BEAT 3 · THE AHA (35-55s)
  Full-screen: {aha}
  Hold on their reaction. Capture one candid quote.

BEAT 4 · OUTCOME + CTA (55-{seconds.split('-')[-1]})
  Voice: "{outcome}"
  On-screen: the Space URL → {url or '[your Space URL]'}

NOTES: good light · phone mic close · quiet room · record the screen separately for crisp close-ups.
"""


def social_post(name, hook, url="", extra_tags=""):
    return f"""{hook}

Built for the Build Small Hackathon — small model (≤32B), runs on a laptop, solves a real Naija problem.

Try it 👉 {url or '[your Space URL]'}

{TAGS} {extra_tags}""".strip()


def field_notes(name, problem_stat, person, why_small, stack, modal_use, what_broke="", evidence="", ethics="", next_steps=""):
    return f"""# Field Notes — {name}

## The problem
{problem_stat}

## Who it's for
{person}

## Why a small model (and what it honestly cannot do)
{why_small}

## The build
- **Model stack:** {stack}
- **Modal / llama.cpp / gr.Server choices:** {modal_use}

## What worked, what broke
{what_broke or '_(fill in — judges love honest debugging stories)_'}

## Did the person actually use it?
{evidence or '_(the quote + what task they completed)_'}

## Ethics & limits
{ethics or '_(stated plainly)_'}

## What's next + running Modal-credit cost tally
{next_steps or '_(numbers make this credible)_'}
"""
