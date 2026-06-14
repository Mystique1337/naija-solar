"""Shared UI system — a polished Nigerian theme + reusable building blocks.

Every app wraps its content in `ui.shell(...)`, which renders a gradient hero, an
optional disclaimer banner, a merit-badge sash, and a live ≤32B parameter-budget
proof — so even the default-Gradio apps look intentional. Apps chasing the
Off-Brand badge graduate to the gr_server custom frontends in `buildsmall/gr_server`.

    with ui.shell(title="Light Diary", emoji="🔌",
                  badges=["off_grid", "off_brand", "field_notes"],
                  models=["qwen3-4b"],
                  disclaimer=i18n.t("estimate_only")) as demo:
        ...gradio components...
    demo.launch()
"""
from __future__ import annotations

import contextlib
import html

import gradio as gr

from . import i18n, params

# Merit badges: key → (emoji, label)
BADGES = {
    "off_grid": ("🔌", "Off the Grid"),
    "well_tuned": ("🎯", "Well-Tuned"),
    "off_brand": ("🎨", "Off-Brand"),
    "llama": ("🦙", "Llama Champion"),
    "sharing": ("📡", "Sharing is Caring"),
    "field_notes": ("📓", "Field Notes"),
}

THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.green,
    secondary_hue=gr.themes.colors.amber,
    neutral_hue=gr.themes.colors.stone,
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "monospace"],
).set(
    body_background_fill="*neutral_50",
    block_radius="16px",
    button_large_radius="12px",
    button_primary_background_fill="linear-gradient(135deg, #00813f, #006d3b)",
    button_primary_background_fill_hover="linear-gradient(135deg, #009c4c, #00813f)",
    button_primary_text_color="white",
)

CSS = """
:root { --bs-green:#00813f; --bs-green-d:#00351c; --bs-amber:#b45309; }
.gradio-container { max-width: 1080px !important; margin: 0 auto !important; }

.bs-hero {
  background: linear-gradient(135deg, var(--bs-green) 0%, var(--bs-green-d) 100%);
  color: #fff; border-radius: 20px; padding: 26px 28px; margin: 6px 0 14px;
  box-shadow: 0 12px 30px rgba(0,53,28,.28); position: relative; overflow: hidden;
}
.bs-hero::after{ content:""; position:absolute; right:-40px; top:-40px; width:200px; height:200px;
  background: radial-gradient(circle, rgba(255,255,255,.14), transparent 70%); }
.bs-hero .bs-row{ display:flex; align-items:center; gap:18px; position:relative; z-index:1; }
.bs-emoji{ font-size:34px; line-height:1; background:rgba(255,255,255,.16); width:64px; height:64px;
  border-radius:18px; display:flex; align-items:center; justify-content:center; flex:0 0 auto; }
.bs-hero h1{ margin:0; font-size:1.7rem; font-weight:800; letter-spacing:-.01em; }
.bs-hero p{ margin:.3rem 0 0; opacity:.92; font-size:1rem; }
.bs-tag{ display:inline-block; margin-top:10px; font-size:.74rem; font-weight:600; letter-spacing:.04em;
  text-transform:uppercase; background:rgba(255,255,255,.18); padding:4px 10px; border-radius:999px; }

.bs-disclaimer{ background:#fffbeb; border:1px solid #fde68a; color:#7c2d12; border-left:5px solid var(--bs-amber);
  border-radius:12px; padding:11px 14px; margin:0 0 14px; font-size:.92rem; }

.bs-sash{ display:flex; flex-wrap:wrap; gap:8px; margin:18px 0 4px; }
.bs-badge{ display:inline-flex; align-items:center; gap:6px; background:#ecfdf3; color:#065f46;
  border:1px solid #a7f3d0; padding:6px 12px; border-radius:999px; font-size:.84rem; font-weight:600; }
.bs-param{ font-family:var(--font-mono, monospace); font-size:.84rem; margin-top:10px; padding:8px 12px;
  border-radius:10px; background:#f0fdf4; border:1px dashed #86efac; color:#14532d; }
.bs-param.over{ background:#fef2f2; border-color:#fca5a5; color:#991b1b; }
.bs-foot{ margin-top:16px; padding-top:12px; border-top:1px solid #e7e5e4; color:#78716c; font-size:.82rem; }
.bs-foot b{ color:#44403c; }
.bs-backend{ margin-top:8px; padding:7px 12px; border-radius:10px; font-size:.84rem; font-weight:600; }
.bs-backend.live{ background:#ecfdf5; color:#065f46; border:1px solid #6ee7b7; }
.bs-backend.mock{ background:#fef2f2; color:#991b1b; border:1px solid #fca5a5; }
"""


def _esc(s):
    return html.escape(str(s or ""))


# Per-app personality: each app picks (or defines) a palette so no two look alike.
PALETTES = {
    "naija":    {"g1": "#00813f", "g2": "#00351c"},  # default — Nigeria green
    "market":   {"g1": "#b45309", "g2": "#7c2d12"},  # buka / meal / catalog — terracotta
    "night":    {"g1": "#1e293b", "g2": "#0f172a"},  # power / outage — dark
    "story":    {"g1": "#7c3aed", "g2": "#4c1d95"},  # folktales / kids — purple
    "danfo":    {"g1": "#facc15", "g2": "#ca8a04"},  # transport / street whimsy — bus yellow
    "whatsapp": {"g1": "#128c7e", "g2": "#075e54"},  # aunty bot / wa reader — teal
    "bank":     {"g1": "#2563eb", "g2": "#1e3a8a"},  # fake-alert / fintech — blue
    "civic":    {"g1": "#0e7490", "g2": "#155e75"},  # civic / gov — teal-blue
    "health":   {"g1": "#0d9488", "g2": "#115e59"},  # health / pharmacy — clean teal
    "exam":     {"g1": "#4338ca", "g2": "#312e81"},  # education / tutor — indigo
    "harvest":  {"g1": "#65a30d", "g2": "#3f6212"},  # food / agric — green-gold
    "dusk":     {"g1": "#db2777", "g2": "#831843"},  # culture / nollywood — magenta
}


def hero_html(title, subtitle="", emoji="✨", track=None, palette="naija"):
    p = PALETTES.get(palette, PALETTES["naija"]) if isinstance(palette, str) else palette
    style = f'background:linear-gradient(135deg,{p["g1"]} 0%,{p["g2"]} 100%);'
    tag = f'<span class="bs-tag">{_esc(track)}</span>' if track else ""
    sub = f"<p>{_esc(subtitle)}</p>" if subtitle else ""
    return (f'<div class="bs-hero" style="{style}"><div class="bs-row"><div class="bs-emoji">{emoji}</div>'
            f'<div><h1>{_esc(title)}</h1>{sub}{tag}</div></div></div>')


def disclaimer_html(text):
    return f'<div class="bs-disclaimer">⚠️ {_esc(text)}</div>'


def sash_html(badges):
    chips = "".join(
        f'<span class="bs-badge">{BADGES[b][0]} {BADGES[b][1]}</span>'
        for b in badges if b in BADGES
    )
    return f'<div class="bs-sash">{chips}</div>'


def param_html(model_ids):
    total, ok, detail = params.cap_check(model_ids)
    cls = "bs-param" if ok else "bs-param over"
    return f'<div class="{cls}">🧮 {_esc(detail)}</div>'


def footer_html(note="The model formats, explains and logs — <b>the human decides.</b>"):
    return f'<div class="bs-foot">🏕️ Build Small Hackathon · Nigeria edition &nbsp;·&nbsp; {note}</div>'


def backend_html():
    """A live indicator of which model/backend is actually running — so it's obvious
    when an app is still on the MOCK dev stand-in vs. a real ≤32B model."""
    from . import config
    if config.BACKEND == "mock":
        return ('<div class="bs-backend mock">⚠️ MOCK backend — a dev stand-in, NOT a model. '
                'Set <code>BUILDSMALL_BACKEND</code> to <code>llamacpp</code>/<code>openai</code>/'
                '<code>transformers</code> before submitting.</div>')
    label = {"openai": "served · OpenAI-compatible (Modal/OpenBMB/vLLM)",
             "llamacpp": "local · llama.cpp GGUF", "transformers": "local · transformers"}.get(config.BACKEND, config.BACKEND)
    return f'<div class="bs-backend live">🟢 Live model: <b>{_esc(config.TEXT_MODEL)}</b> &nbsp;·&nbsp; {label}</div>'


def lang_selector(default="en", label="Language / Èdè / Harshe / Asụsụ"):
    return gr.Dropdown(choices=[(n, c) for n, c in i18n.LANGS], value=default, label=label)


@contextlib.contextmanager
def shell(title, subtitle="", emoji="✨", badges=None, models=None, disclaimer=None,
          track=None, palette="naija", extra_css="", theme=None):
    """Context manager that frames an app: hero + disclaimer (top), badge sash +
    param proof + footer (bottom). Add your components inside the `with` block.

    Give each app its own identity with `palette=` (see PALETTES) and `extra_css=`
    for bespoke components. The theme/css are applied later via `ui.launch(demo)`.
    """
    demo = gr.Blocks(title=title, analytics_enabled=False)
    demo._bs_css = CSS + (("\n" + extra_css) if extra_css else "")
    demo._bs_theme = theme or THEME
    with demo:
        gr.HTML(hero_html(title, subtitle, emoji, track, palette))
        if disclaimer:
            gr.HTML(disclaimer_html(disclaimer))
        yield demo
        tail = []
        if badges:
            tail.append(sash_html(badges))
        if models:
            tail.append(param_html(models))
        tail.append(backend_html())
        tail.append(footer_html())
        gr.HTML("\n".join(tail))


def launch(demo, **kwargs):
    """Launch a shell-built (or any) Blocks, applying its stashed theme + CSS.
    On Gradio 6 theme/css belong on launch(); this keeps app code to `ui.launch(demo)`."""
    kwargs.setdefault("theme", getattr(demo, "_bs_theme", THEME))
    css = getattr(demo, "_bs_css", CSS)
    if "css" in kwargs:
        css = css + "\n" + kwargs.pop("css")
    return demo.launch(css=css, **kwargs)
