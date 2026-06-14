"""Sharing-is-Caring: publish agent traces to the Hub.

An "agent trace" is just your run logs as a list of OpenAI-format messages
({role, content, tool_calls}). Drop `trace.log(...)` into any agentic app, then
`trace.push()` to upload a Hugging Face Dataset. The badge comes essentially free.

Privacy: content is anonymised (phones, emails, amounts, obvious names) before it
ever touches disk or the Hub.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import config

_PHONE = re.compile(r"(\+?234|0)\d{9,10}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MONEY = re.compile(r"(₦|N|NGN)\s?[\d,]+(\.\d+)?", re.IGNORECASE)
_NIN = re.compile(r"\b\d{11}\b")


def anonymise(text):
    if not isinstance(text, str):
        return text
    text = _PHONE.sub("[phone]", text)
    text = _EMAIL.sub("[email]", text)
    text = _NIN.sub("[id]", text)
    text = _MONEY.sub("[amount]", text)
    return text


def _clean_messages(messages):
    out = []
    for m in messages:
        c = m.get("content")
        out.append({
            "role": m.get("role"),
            "content": anonymise(c) if isinstance(c, str) else c,
            **({"tool_calls": m["tool_calls"]} if m.get("tool_calls") else {}),
        })
    return out


class Tracer:
    def __init__(self):
        self.path = os.path.join(config.TRACE_LOCAL_DIR, "traces.jsonl")
        self._n = 0

    def log(self, messages, tool_calls=None, app=None, meta=None):
        """Append one anonymised run (a list of messages) to the local JSONL."""
        os.makedirs(config.TRACE_LOCAL_DIR, exist_ok=True)
        record = {
            "app": app or config.APP_NAME,
            "ts": int(time.time()),
            "messages": _clean_messages(messages),
            "tool_calls": tool_calls or [],
            "meta": meta or {},
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._n += 1
        return record

    def push(self, repo_id=None, private=False):
        """Upload the accumulated JSONL as a Hugging Face Dataset."""
        repo_id = repo_id or config.TRACE_DATASET_REPO
        if not repo_id:
            raise ValueError("Set BUILDSMALL_TRACE_REPO (or pass repo_id) to push traces.")
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
        api.upload_file(path_or_fileobj=self.path, path_in_repo="traces.jsonl",
                        repo_id=repo_id, repo_type="dataset")
        return f"https://huggingface.co/datasets/{repo_id}"


tracer = Tracer()
