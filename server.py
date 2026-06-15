"""Naija Solar — bespoke custom frontend served over FastAPI (the off-brand surface).

This is a fully hand-built web app (web/index.html + web/app.js + web/shell.css) served by FastAPI.
All the heavy lifting is reused from app.py: the deterministic sizing engine, every SVG/HTML
generator, the Three.js scene, and the model clients. app.py is imported with BUILDSMALL_NO_GRADIO=1
so its (now unused) Gradio Blocks is never built. The Gradio app remains a one-line fallback.

  uvicorn server:app --host 0.0.0.0 --port 7860     # local + Docker entrypoint
"""
import os
os.environ.setdefault("BUILDSMALL_NO_GRADIO", "1")

import base64
import hashlib
import hmac
import pathlib
import secrets as _secrets
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if not (HERE / "buildsmall").exists():
    sys.path.insert(0, str(HERE.parents[1] / "_platform"))

import app as core  # noqa: E402  — reuse all logic/generators (Gradio build is skipped)
import store  # noqa: E402  — durable accounts + sizing history (Modal Dict)
import strings  # noqa: E402  — all UI text in 5 languages + seed testimonials

from fastapi import FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

app = FastAPI(title="Naija Solar")
WEB = HERE / "web"

# ── sessions (HMAC-signed cookies; never stores a password, only a signed email) ──────────────
# Keys live in the data dir (persisted on /data, never in a public repo or a shared-org secret),
# so they survive restarts when persistent storage is on, and are private to this app.
DATA_DIR = os.environ.get("BUILDSMALL_DATA_DIR", "user_data")


def _persisted(name, gen):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        p = os.path.join(DATA_DIR, name)
        if os.path.exists(p):
            return open(p).read().strip()
        v = gen()
        open(p, "w").write(v)
        return v
    except Exception:
        return gen()


SECRET = (os.environ.get("SECRET_KEY") or _persisted(".session_key", lambda: _secrets.token_hex(32))).encode("utf-8")
ADMIN_KEY = os.environ.get("ADMIN_KEY") or _persisted(".admin_key", lambda: "naija-" + _secrets.token_hex(8))
ON_SPACE = bool(os.environ.get("SPACE_ID"))
COOKIE, TTL = "ns_session", 30 * 24 * 3600
print("[naija] storage data dir: %s | admin key (for /api/admin/users?key=...): %s" % (DATA_DIR, ADMIN_KEY))

# One-time wipe of testimonials accumulated during development and testing, so the home-page
# carousel starts clean (only the curated seeds, then genuine user ratings). Runs once; the
# marker in the persistent data dir keeps it from repeating.
try:
    _mark = pathlib.Path(DATA_DIR) / ".testi_reset_v1"
    if not _mark.exists():
        store.STORE.reset_testimonials()
        _mark.write_text("done")
        print("[naija] cleared development testimonials (one-time)")
except Exception as _e:
    print("[naija] testimonial reset skipped:", _e)


def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _ub64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(email):
    payload = ("%s|%d" % (email, int(time.time()) + TTL)).encode("utf-8")
    return _b64(payload) + "." + _b64(hmac.new(SECRET, payload, hashlib.sha256).digest())


def read_token(tok):
    try:
        p, s = (tok or "").split(".")
        payload = _ub64(p)
        if not hmac.compare_digest(_ub64(s), hmac.new(SECRET, payload, hashlib.sha256).digest()):
            return None
        email, exp = payload.decode().rsplit("|", 1)
        return email if int(exp) > time.time() else None
    except Exception:
        return None


def current_user(request):
    email = read_token(request.cookies.get(COOKIE))
    return store.STORE.get(email) if email else None


def _set_cookie(resp, email):
    resp.set_cookie(COOKIE, make_token(email), max_age=TTL, httponly=True, samesite="lax", secure=ON_SPACE, path="/")


def _rec(sel, r, state, lang):
    return {"appliances": sel, "panels": r["panel"]["count"], "kva": r["inverter"]["kva"],
            "kwh": r["daily_kwh"], "cost": r["batteries"]["durable"]["total"], "state": state, "lang": lang,
            "label": ", ".join("%d %s" % (v, k.split(" (")[0]) for k, v in list(sel.items())[:4])}


def _save_if_user(request, sel, r, state, lang, bundle):
    u = current_user(request)
    if u:
        store.STORE.add_sizing(u["email"], _rec(sel, r, state, lang))
        bundle["saved"] = True
        bundle["userCount"] = store.STORE.get(u["email"])["count"]   # distinct from the global count pill


# ── helpers ───────────────────────────────────────────────────────────────────
def _sess():
    return core.get_session(None)


def _size(appliances, state="Lagos", geolat=""):
    sel = {str(k): int(v) for k, v in (appliances or {}).items() if int(v) > 0}
    r = core.engine.size(sel, core._psh(state, geolat))
    return sel, r


def _bundle(sel, r, lang="en"):
    """Everything the frontend needs to paint a full result, reusing the existing generators."""
    return {
        "ok": True,
        "sel": sel,
        "house": core._house_data(r, sel),
        "narration": core.narrate(r, lang),       # the written explanation, shown instantly
        "summary": core.set_summary(r)[0],        # also records the 'sizing' event + count
        "tiles": core.stat_tiles(r),
        "cards": core.rec_cards(r),
        "chips": core.chips_html(sel),
        "system": core.system_view(r),
        "twod": core.home_2d_svg(r),
        "breakdown": core.load_breakdown_html(sel),
        "vendors": core.vendors_html(),
        "count": core.count_html(),
        "panels": r["panel"]["count"],
        "kva": r["inverter"]["kva"],
        "kwh": r["daily_kwh"],
        "cost": r["batteries"]["durable"]["total"],
    }


# ── API ───────────────────────────────────────────────────────────────────────
_LANGS = ["en", "pcm", "yo", "ha", "ig"]


@app.get("/api/config")
def config():
    return {
        "ui": core.UI,
        "langName": core.LANG_NAME,
        "states": list(core.locations.STATES),
        "guideTitle": core.GUIDE_TITLE,
        "guard": core.GUARD,
        "count": core.count_html(),
        "appliances": list(core.data.APPLIANCES),
        "logo": core.logo_html(),
        "hero": {L: core.hero_html(L) for L in _LANGS},
        "steps": {L: core.steps_html(L) for L in _LANGS},
        "strings": strings.STRINGS,
    }


@app.post("/api/size")
async def api_size(payload: dict, request: Request):
    text = (payload or {}).get("text", "")
    lang = (payload or {}).get("lang", "en")
    sel = core.extract(text)
    if not sel:
        msg = core.GUARD.get(lang, core.GUARD["en"]) if (text or "").strip() else ""
        return {"ok": False, "msg": msg}
    state = payload.get("state", "Lagos")
    _, r = _size(sel, state, payload.get("geolat", ""))
    b = _bundle(sel, r, lang)
    _save_if_user(request, sel, r, state, lang, b)
    return b


@app.post("/api/recalc")
async def api_recalc(payload: dict, request: Request):
    sel = {k: int(v) for k, v in (payload.get("appliances") or {}).items() if int(v) > 0}
    if not sel:
        return {"ok": False, "msg": "Add an appliance first."}
    state = payload.get("state", "Lagos")
    lang = payload.get("lang", "en")
    _, r = _size(sel, state, payload.get("geolat", ""))
    b = _bundle(sel, r, lang)
    _save_if_user(request, sel, r, state, lang, b)
    return b


@app.post("/api/asr")
async def api_asr(file: UploadFile = File(...)):
    data = await file.read()
    p = tempfile.mktemp(suffix=".wav")
    with open(p, "wb") as f:
        f.write(data)
    try:
        text = core.asr.transcribe(p)
    except Exception:
        text = ""
    return {"text": text or ""}


@app.post("/api/vision")
async def api_vision(request: Request, files: list[UploadFile] = File(...), state: str = Form("Lagos"),
                     lang: str = Form("en"), geolat: str = Form("")):
    seen = {}
    for f in (files or [])[:5]:
        data = await f.read()
        p = tempfile.mktemp(suffix=".png")
        with open(p, "wb") as fp:
            fp.write(data)
        try:
            desc = core.vision.describe(p, "List every household electrical appliance you can see, with "
                                           "counts. Use simple names like fridge, fan, bulb, TV, AC, freezer, laptop.")
            for k, v in core.extract(desc).items():
                seen[k] = seen.get(k, 0) + v
        except Exception:
            pass
    if not seen:
        return {"ok": False, "msg": "Could not spot appliances. Try a clearer photo or type them."}
    _, r = _size(seen, state, geolat)
    b = _bundle(seen, r, lang)
    _save_if_user(request, seen, r, state, lang, b)
    return b


_WARM_TS = [0.0]          # last time we fired a warm-up
_WARM_FILES = {}          # tiny throwaway inputs for ASR/vision, made once


def _warm_assets():
    if not _WARM_FILES:
        import wave
        wp = tempfile.mktemp(suffix=".wav")
        try:
            with wave.open(wp, "w") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 3200)   # 0.2s of silence
        except Exception:
            wp = None
        ip = tempfile.mktemp(suffix=".png")
        try:
            from PIL import Image
            Image.new("RGB", (32, 32), (210, 210, 210)).save(ip)
        except Exception:
            ip = None
        _WARM_FILES["wav"], _WARM_FILES["img"] = wp, ip
    return _WARM_FILES


@app.post("/api/warm")
async def api_warm():
    """Wake EVERY model (text, speech, voice, vision) in the background when a visitor arrives, so
    the first real request is fast. The models scale to zero when idle, so this is throttled and
    fires again whenever someone returns after a lull. Returns warming=true when it actually fired."""
    now = time.time()
    if now - _WARM_TS[0] < 60:
        return {"ok": True, "warming": False}
    _WARM_TS[0] = now
    a = _warm_assets()
    jobs = [lambda: core.llm.complete("Hi"),
            lambda: core.tts.speak("Ready.", lang="en", out_path=tempfile.mktemp(suffix=".wav"))]
    if a.get("wav"):
        jobs.append(lambda: core.asr.transcribe(a["wav"]))
    if a.get("img"):
        jobs.append(lambda: core.vision.describe(a["img"], "List appliances."))

    def _run(fn):
        try:
            fn()
        except Exception:
            pass
    for fn in jobs:
        threading.Thread(target=_run, args=(fn,), daemon=True).start()
    return {"ok": True, "warming": True}


@app.post("/api/narration")
async def api_narration(payload: dict):
    """The written explanation, the exact words the voice will read. Instant (no model)."""
    sel = {k: int(v) for k, v in (payload.get("appliances") or {}).items() if int(v) > 0}
    if not sel:
        return {"text": ""}
    _, r = _size(sel, payload.get("state", "Lagos"), payload.get("geolat", ""))
    return {"text": core.narrate(r, payload.get("lang", "en"))}


@app.post("/api/narrate")
async def api_narrate(payload: dict):
    sel = {k: int(v) for k, v in (payload.get("appliances") or {}).items() if int(v) > 0}
    if not sel:
        return JSONResponse({"ok": False}, status_code=400)
    _, r = _size(sel, payload.get("state", "Lagos"), payload.get("geolat", ""))
    audio, _ = core.speak(r, payload.get("lang", "en"), _sess())
    if audio and os.path.exists(audio):
        return FileResponse(audio, media_type="audio/wav")
    return JSONResponse({"ok": False}, status_code=503)


@app.post("/api/ask")
async def api_ask(payload: dict):
    sel = {k: int(v) for k, v in (payload.get("appliances") or {}).items() if int(v) > 0}
    if not sel:
        return {"answer": "Size your appliances first, then I can answer questions about your plan."}
    _, r = _size(sel, payload.get("state", "Lagos"), payload.get("geolat", ""))
    ans, _ = core.ask(payload.get("question", ""), r, payload.get("lang", "en"), _sess())
    return {"answer": ans}


@app.post("/api/feedback")
async def api_feedback(payload: dict, request: Request):
    rating = payload.get("rating", "up")          # "up" / "down" (thumbs)
    stars = payload.get("stars")                  # 1..5 (rating prompt), optional
    comment = (payload.get("comment") or "").strip()
    u = current_user(request)
    name = ((u["name"] if u else (payload.get("name") or "")) or "").strip() or "A Naija Solar user"
    lang = payload.get("lang", "en")
    core.track_event("feedback", {"rating": rating, "stars": stars, "comment": comment[:300], "lang": lang})
    positive = (rating == "up") or (isinstance(stars, (int, float)) and stars >= 4)
    if comment and positive and len(comment) >= 6:   # surfaces on the home-page carousel
        store.STORE.add_testimonial({"name": name[:40], "text": comment[:240], "rating": int(stars or 5),
                                     "lang": lang if lang in _LANGS else "en"})
    return {"ok": True, "html": core.submit_feedback(rating if rating in ("up", "down") else "up", comment)}


@app.get("/api/testimonials")
def api_testimonials():
    real = store.STORE.list_testimonials(60)
    items, seen = [], set()
    for t in real:
        txt = (t.get("text") or "").strip()
        if not txt:
            continue
        k = ((t.get("name") or "").strip().lower(), txt.lower())
        if k in seen:                                # drop duplicates
            continue
        seen.add(k)
        items.append({"name": t.get("name", "A user"), "text": txt,
                      "rating": t.get("rating", 5), "lang": t.get("lang", "en")})
    seeds = [{"name": s["name"], "text": s["text"], "rating": s["rating"], "lang": s.get("lang", "en")}
             for s in strings.SEED_TESTIMONIALS]
    return {"items": (items + seeds)[:24], "realCount": len(items)}


@app.post("/api/email")
async def api_email(payload: dict):
    return {"html": core.submit_email(payload.get("email", ""))}


@app.post("/api/event")
async def api_event(payload: dict):
    core.track_event(payload.get("type", "event"), payload.get("data"))
    return {"ok": True}


@app.get("/api/stats")
def api_stats():
    return core.get_stats()


@app.post("/api/geo")
async def api_geo(payload: dict):
    return {"note": core.geo_note(payload.get("geolat", ""))}


# ── accounts: sign up / in / out, profile, sizing history ────────────────────
@app.post("/api/auth/signup")
async def auth_signup(payload: dict):
    u, err = store.STORE.create_user(payload.get("email"), payload.get("password"), payload.get("name"))
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    core.track_event("signup", {"email": u["email"]})
    resp = JSONResponse({"ok": True, "user": u})
    _set_cookie(resp, u["email"])
    return resp


@app.post("/api/auth/login")
async def auth_login(payload: dict):
    u, err = store.STORE.login(payload.get("email"), payload.get("password"))
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    resp = JSONResponse({"ok": True, "user": u})
    _set_cookie(resp, u["email"])
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request):
    return {"user": current_user(request)}


@app.get("/api/me/sizings")
def my_sizings(request: Request):
    u = current_user(request)
    if not u:
        return {"ok": False, "sizings": []}
    return {"ok": True, "sizings": store.STORE.list_sizings(u["email"])}


@app.post("/api/me/save")
async def my_save(payload: dict, request: Request):
    u = current_user(request)
    if not u:
        return {"ok": False}
    sel = {k: int(v) for k, v in (payload.get("appliances") or {}).items() if int(v) > 0}
    if not sel:
        return {"ok": False}
    _, r = _size(sel, payload.get("state", "Lagos"), payload.get("geolat", ""))
    store.STORE.add_sizing(u["email"], _rec(sel, r, payload.get("state", "Lagos"), payload.get("lang", "en")))
    return {"ok": True, "count": store.STORE.get(u["email"])["count"]}


@app.get("/api/admin/users")
def admin_users(key: str = ""):
    if not key or key != ADMIN_KEY:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return store.STORE.admin_overview()


# ── the bespoke frontend ──────────────────────────────────────────────────────
@app.get("/app.css")
def app_css():
    return Response(core.CSS, media_type="text/css")


_ASSET_V = None


def _asset_version():
    """A short hash of the frontend assets, appended as ?v=… so browsers never serve a stale
    app.js/CSS after a deploy (which can make features look broken). Recomputed each restart."""
    global _ASSET_V
    if _ASSET_V is None:
        h = hashlib.md5()
        for p in (WEB / "app.js", WEB / "shell.css"):
            try:
                h.update(p.read_bytes())
            except Exception:
                pass
        h.update(core.CSS.encode("utf-8"))
        _ASSET_V = h.hexdigest()[:10]
    return _ASSET_V


@app.get("/classic", response_class=HTMLResponse)
def index():
    """The hand-built FastAPI/SPA frontend, kept reachable while the Gradio app is the root interface."""
    shell = (WEB / "index.html").read_text(encoding="utf-8")
    # inject the reused design-system CSS and the Three.js / count-up head block + cache-bust assets
    html = shell.replace("<!--HEAD-->", core.THREE_HEAD).replace("__V__", _asset_version())
    return HTMLResponse(html)


app.mount("/web", StaticFiles(directory=str(WEB)), name="web")

# ── Gradio is the Space's primary interface ──────────────────────────────────
# The Build Small Hackathon requires the interface to be a Gradio app. The Gradio Blocks
# (app.py build(), styled with the same off-brand design system) is mounted at the root, so the
# Space *is* a Gradio app. The hand-built SPA stays at /classic, and all /api routes still serve
# both. Docker SDK is allowed as long as the interface is Gradio, which it now is.
import gradio as gr  # noqa: E402

# A beautification layer applied ONLY to the Gradio interface (it is passed to the mount, so the SPA
# at /classic never sees it). Learns from the best Build Small Spaces: a cohesive atmospheric backdrop,
# a more cinematic hero, premium depth on the cards, warm accent motion, and no default Gradio chrome.
GRADIO_CSS = """
gradio-app, .gradio-container, body{
  background:radial-gradient(1200px 620px at 50% -14%, #fff1d2 0%, #fffaf0 42%, #edfaf3 100%) fixed !important}
footer{display:none !important}
.gradio-container{padding-bottom:64px !important}
/* cinematic hero */
.banner{border-radius:24px !important; min-height:206px !important; overflow:hidden; position:relative;
  box-shadow:0 24px 64px rgba(16,76,52,.24) !important}
.banner::after{content:""; position:absolute; inset:0; background:linear-gradient(105deg, rgba(6,60,42,.62) 0%, rgba(6,60,42,.18) 52%, rgba(6,60,42,0) 78%); pointer-events:none}
.banner .bwrap{position:relative; z-index:1}
.banner .bwrap h1{font-size:2.5rem !important; line-height:1.08 !important; letter-spacing:-.015em; text-shadow:0 2px 16px rgba(0,0,0,.32) !important}
.banner .bwrap p{font-size:1.06rem !important; opacity:.97; text-shadow:0 1px 10px rgba(0,0,0,.28)}
/* premium depth on the input card */
.miccard{box-shadow:0 18px 52px rgba(120,80,20,.13) !important; border-radius:22px !important}
/* primary buttons: warm glow + subtle lift */
.gobtn{transition:box-shadow .18s, transform .15s, filter .15s !important}
.gobtn:hover{box-shadow:0 13px 32px rgba(255,138,0,.38) !important; transform:translateY(-1px); filter:brightness(1.03)}
/* language pills breathe a little more */
.langrow{gap:8px !important; row-gap:8px !important}
/* footer with the classic-UI / GitHub / SoroTTS links */
.gfoot{text-align:center; margin:26px 0 6px; font-size:.9rem; color:#6b7280}
.gfoot a{color:#1f7a4d; text-decoration:none; font-weight:600}
.gfoot a:hover{text-decoration:underline}
"""

# Warm-up + first-load disclaimer for the Gradio interface (mirrors the SPA). Added only to the
# Gradio head, so the SPA at /classic keeps its own warm logic with no double-fire. It POSTs
# /api/warm (waking text, speech, voice, vision) and shows a dismissible "models are waking" note.
GRADIO_WARM_JS = """
<script>
(function(){
  var s=document.createElement('style'); s.textContent='@keyframes nsspin{to{transform:rotate(360deg)}}'; document.head.appendChild(s);
  function warm(){
    try{ fetch('/api/warm',{method:'POST'}); }catch(e){}
    if(document.getElementById('ns-warm')) return;
    var n=document.createElement('div'); n.id='ns-warm';
    n.style.cssText='position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:99999;max-width:560px;width:calc(100% - 28px);background:#fff7e9;border:1.5px solid #f3d9a8;border-radius:14px;padding:10px 14px;color:#7a4a12;font:600 13px/1.4 Inter,system-ui,sans-serif;box-shadow:0 12px 34px rgba(120,80,20,.20);display:flex;align-items:center;gap:9px';
    n.innerHTML='<span style="flex:none;width:13px;height:13px;border:2px solid #e3b770;border-top-color:transparent;border-radius:50%;display:inline-block;animation:nsspin .8s linear infinite"></span>'
      +'<span>Waking up the AI models. They sleep when idle to stay free, so your first result may take a few extra seconds.</span>'
      +'<button aria-label="Dismiss" style="flex:none;margin-left:auto;background:none;border:none;color:#b07d3a;font-size:19px;line-height:1;cursor:pointer" onclick="this.parentNode.remove()">×</button>';
    document.body.appendChild(n);
    setTimeout(function(){ var e=document.getElementById('ns-warm'); if(e){ e.remove(); } }, 55000);
  }
  if(document.readyState==='loading'){ document.addEventListener('DOMContentLoaded', function(){ setTimeout(warm,700); }); }
  else { setTimeout(warm,700); }
})();
</script>
"""

# In Gradio 6 the styling/head parameters are passed to mount_gradio_app (not the Blocks constructor),
# so the off-brand design system (core.CSS), the theme, and the Three.js / voice / count-up head block
# (core.THREE_HEAD) all apply to the mounted Gradio interface.
app = gr.mount_gradio_app(app, core.build(), path="/", ssr_mode=False,   # client-side render; no Node needed in the Docker image
                          css=core.CSS + GRADIO_CSS, theme=core.THEME, head=core.THREE_HEAD + GRADIO_WARM_JS)
