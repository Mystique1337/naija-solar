"""Durable user accounts + per-user sizing history for Naija Solar.

Backed by Modal Dict (immediately consistent, survives Space restarts and rebuilds with zero loss,
no extra web function). Falls back to a local JSON file when Modal is not configured, so local dev
and the mock backend still work. Passwords are salted + pbkdf2-hashed; only hashes are stored.
"""
import hashlib
import json
import os
import pathlib
import threading
import time

_LOCK = threading.Lock()
_ITER = 200_000
MAX_HISTORY = 60


def _hash_pw(password, salt=None):
    salt = salt or os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt), _ITER).hex()
    return "pbkdf2$%d$%s$%s" % (_ITER, salt, h)


def _check_pw(password, stored):
    try:
        _, it, salt, h = stored.split("$")
        return hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt), int(it)).hex() == h
    except Exception:
        return False


class _LocalDict:
    """Minimal persistent dict fallback (single JSON file) for local dev / no-Modal."""
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._d = {}
        if self.path.exists():
            try:
                self._d = json.loads(self.path.read_text())
            except Exception:
                self._d = {}

    def _flush(self):
        tmp = str(self.path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._d, f)
        os.replace(tmp, self.path)

    def get(self, k, default=None):
        return self._d.get(k, default)

    def put(self, k, v):
        self._d[k] = v
        self._flush()

    def contains(self, k):
        return k in self._d

    def keys(self):
        return list(self._d.keys())


class Store:
    def __init__(self):
        self.backend = "local"
        try:
            if os.environ.get("MODAL_TOKEN_ID") or os.environ.get("MODAL_TOKEN_SECRET") or pathlib.Path(
                    os.path.expanduser("~/.modal.toml")).exists():
                import modal
                self.users = modal.Dict.from_name("naija-users", create_if_missing=True)
                self.sizings = modal.Dict.from_name("naija-sizings", create_if_missing=True)
                self.testi = modal.Dict.from_name("naija-testimonials", create_if_missing=True)
                self.backend = "modal"
        except Exception:
            self.backend = "local"
        if self.backend == "local":
            d = os.environ.get("BUILDSMALL_DATA_DIR", "user_data")
            self.users = _LocalDict(os.path.join(d, "users.json"))
            self.sizings = _LocalDict(os.path.join(d, "sizings.json"))
            self.testi = _LocalDict(os.path.join(d, "testimonials.json"))

    # ── dict helpers (Modal Dict and _LocalDict share get/put/contains/keys) ──
    def _get(self, store, k, default=None):
        try:
            return store.get(k, default)
        except Exception:
            return default

    def _put(self, store, k, v):
        try:
            store.put(k, v)
        except Exception:
            store[k] = v

    # ── accounts ──────────────────────────────────────────────────────────────
    def create_user(self, email, password, name):
        email = (email or "").strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            return None, "Please enter a valid email."
        if len(password or "") < 6:
            return None, "Password must be at least 6 characters."
        with _LOCK:
            if self._get(self.users, email):
                return None, "An account with that email already exists. Try signing in."
            user = {"email": email, "name": (name or email.split("@")[0]).strip()[:60],
                    "pw": _hash_pw(password), "created": int(time.time())}
            self._put(self.users, email, user)
        return self.public(user), None

    def login(self, email, password):
        email = (email or "").strip().lower()
        user = self._get(self.users, email)
        if not user or not _check_pw(password, user.get("pw", "")):
            return None, "Wrong email or password."
        return self.public(user), None

    def get(self, email):
        u = self._get(self.users, (email or "").strip().lower())
        return self.public(u) if u else None

    def public(self, u):
        if not u:
            return None
        return {"email": u["email"], "name": u.get("name", ""), "created": u.get("created", 0),
                "count": len(self._get(self.sizings, u["email"], []) or [])}

    # ── sizing history ────────────────────────────────────────────────────────
    def add_sizing(self, email, rec):
        email = (email or "").strip().lower()
        if not email or not self._get(self.users, email):
            return False
        with _LOCK:
            hist = self._get(self.sizings, email, []) or []
            rec = dict(rec)
            rec["ts"] = int(time.time())
            hist.append(rec)
            self._put(self.sizings, email, hist[-MAX_HISTORY:])
        return True

    def list_sizings(self, email):
        return list(reversed(self._get(self.sizings, (email or "").strip().lower(), []) or []))

    # ── testimonials (ratings + comments, from anyone) ────────────────────────
    def add_testimonial(self, rec):
        try:
            with _LOCK:
                lst = self._get(self.testi, "all", []) or []
                rec = dict(rec)
                rec["ts"] = int(time.time())
                lst.append(rec)
                self._put(self.testi, "all", lst[-150:])
            return True
        except Exception:
            return False

    def list_testimonials(self, limit=40):
        return list(reversed(self._get(self.testi, "all", []) or []))[:limit]

    def reset_testimonials(self):
        """Wipe stored testimonials (used to clear development/test entries). Seeds are separate."""
        try:
            with _LOCK:
                self._put(self.testi, "all", [])
            return True
        except Exception:
            return False

    # ── admin / tracking ──────────────────────────────────────────────────────
    def admin_overview(self):
        try:
            emails = list(self.users.keys())
        except Exception:
            emails = []
        users = []
        total = 0
        for e in emails:
            u = self._get(self.users, e)
            if not u:
                continue
            n = len(self._get(self.sizings, e, []) or [])
            total += n
            users.append({"email": e, "name": u.get("name", ""), "created": u.get("created", 0), "sizings": n})
        users.sort(key=lambda x: x["created"], reverse=True)
        return {"backend": self.backend, "users": len(users), "total_sizings": total, "list": users}


STORE = Store()
