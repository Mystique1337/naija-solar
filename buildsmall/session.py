"""Session tracking — see every interaction.

A Session records timestamped events (utterances, parsed inputs, model outputs,
recommendations, …) to a JSONL, with an in-app viewer so the builder can watch
sessions live, plus a one-call push to a HF Dataset (Sharing-is-Caring). Content is
anonymised by default (phones, emails, IDs, amounts).

    from buildsmall import sessions
    s = sessions.start("Solar Sizing")
    s.event("appliances_parsed", loads=[...])
    s.interaction(user_text, model_output, lang="yo")
    html = sessions.render_html(10)      # drop into a gr.HTML "Sessions" panel
"""
from __future__ import annotations

import html as _html
import json
import os
import time
import uuid

from . import config
from .trace_logger import anonymise

PATH = os.path.join(config.TRACE_LOCAL_DIR, "sessions.jsonl")


class Session:
    def __init__(self, app=None, anonymize=True):
        self.app = app or config.APP_NAME
        self.id = uuid.uuid4().hex[:12]
        self.started = time.time()
        self.anon = anonymize
        self.count = 0

    def event(self, kind, **data):
        clean = {k: (anonymise(v) if self.anon and isinstance(v, str) else v) for k, v in data.items()}
        rec = {"session": self.id, "app": self.app, "ts": round(time.time(), 3), "kind": kind, "data": clean}
        os.makedirs(config.TRACE_LOCAL_DIR, exist_ok=True)
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.count += 1
        return rec

    def interaction(self, user_input, output, **meta):
        return self.event("interaction", input=str(user_input), output=str(output), **meta)


def start(app=None, **kw):
    return Session(app, **kw)


def recent(n=20):
    """Most-recent sessions, each with its ordered events."""
    if not os.path.exists(PATH):
        return []
    rows = [json.loads(line) for line in open(PATH, encoding="utf-8") if line.strip()]
    by = {}
    for r in rows:
        by.setdefault(r["session"], []).append(r)
    out = []
    for sid, evs in by.items():
        evs.sort(key=lambda e: e["ts"])
        out.append({"id": sid, "app": evs[0]["app"], "start": evs[0]["ts"], "events": evs, "count": len(evs)})
    out.sort(key=lambda s: s["start"], reverse=True)
    return out[:n]


def _fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def render_html(n=10):
    """A clean in-app viewer of recent sessions + their events."""
    ss = recent(n)
    if not ss:
        return '<div class="bs-sess" style="color:#78716c">No sessions yet — interactions appear here as they happen.</div>'
    blocks = []
    for s in ss:
        rows = ""
        for e in s["events"]:
            data = e["data"]
            detail = data.get("output") or data.get("input") or json.dumps(data, ensure_ascii=False)
            rows += (f'<tr><td style="color:#94a3b8;white-space:nowrap">{_fmt(e["ts"])[11:]}</td>'
                     f'<td><b>{_html.escape(e["kind"])}</b></td>'
                     f'<td>{_html.escape(str(detail)[:160])}</td></tr>')
        blocks.append(
            f'<details class="bs-sess-item" style="margin:8px 0;border:1px solid #e5e7eb;border-radius:10px;padding:8px 12px">'
            f'<summary style="cursor:pointer;font-weight:600">🗂️ {_html.escape(s["app"])} · {s["id"]} '
            f'<span style="color:#94a3b8;font-weight:400">· {_fmt(s["start"])} · {s["count"]} events</span></summary>'
            f'<table style="width:100%;font-size:.85rem;border-collapse:collapse;margin-top:6px">{rows}</table></details>')
    return f'<div class="bs-sess">{"".join(blocks)}</div>'


def push(repo_id=None, private=False):
    """Publish sessions.jsonl as a HF Dataset (Sharing-is-Caring)."""
    repo_id = repo_id or config.TRACE_DATASET_REPO
    if not repo_id:
        raise ValueError("Set BUILDSMALL_TRACE_REPO (or pass repo_id) to push sessions.")
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
    api.upload_file(path_or_fileobj=PATH, path_in_repo="sessions.jsonl", repo_id=repo_id, repo_type="dataset")
    return f"https://huggingface.co/datasets/{repo_id}"
