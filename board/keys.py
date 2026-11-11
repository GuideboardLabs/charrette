"""A pool of model keys for the board session.

Free and rate-limited keys are the practical way to run a chat panel on someone else's board: no
session leases, no single provider to depend on. This module owns the pool, the rate budget and the
cooldown bookkeeping. The file lives outside the repo and outside the vault, at 0600, because it
holds secrets.

Shape of ~/.hermes/charrette/keys.json:

    {"keys": [
      {"name": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1",
       "api_key": "...", "models": ["meta/llama-3.3-70b-instruct"], "rpm": 40}
    ]}

`rpm` is the provider's requests-per-minute for that key; the pool skips a key that is over budget
and puts one in cooldown after a 429 or a 5xx, so a burst degrades to the next key instead of failing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

KEYS_PATH = Path.home() / ".hermes" / "charrette" / "keys.json"
COOLDOWN_SECONDS = 45
WINDOW_SECONDS = 60.0


def load_entries() -> list[dict]:
    try:
        raw = json.loads(KEYS_PATH.read_text())
    except (OSError, ValueError):
        return []
    items = raw.get("keys") if isinstance(raw, dict) else raw
    out = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("api_key") or not item.get("base_url"):
            continue
        if item.get("enabled") is False:
            continue
        models = item.get("models") or []
        out.append({
            "name": str(item.get("name") or "key"),
            "base_url": str(item["base_url"]).rstrip("/"),
            "api_key": str(item["api_key"]),
            "models": [str(m) for m in models] if isinstance(models, list) else [str(models)],
            "rpm": int(item.get("rpm") or 0),
            "stream": item.get("stream", True) is not False,
            "priority": int(item.get("priority", 60)),
            "source": "keys.json",
        })
    return out


def hermes_entries(provider: str | None = None) -> list[dict]:
    """Providers Hermes already has credentials for, so a fresh install works with no keys file."""
    names = [provider] if provider else ["ollama-cloud"]
    out = []
    try:
        from hermes_cli.auth import resolve_api_key_provider_credentials
    except Exception:
        return out
    for name in names:
        try:
            cred = resolve_api_key_provider_credentials(name)
        except Exception:
            continue
        if not cred.get("api_key"):
            continue
        out.append({"name": name, "base_url": (cred.get("base_url") or "").rstrip("/"),
                    "api_key": cred["api_key"], "models": [], "rpm": 0,
                    "stream": True, "priority": 40,
                    "source": cred.get("source") or "hermes auth"})
    return out


class KeyPool:
    """Ordered candidates with a per-minute budget and a cooldown after a failure."""

    def __init__(self, entries: list[dict]):
        self.entries = entries
        self._hits: dict[str, list[float]] = {}
        self._cool: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self._cursor = 0

    # ------------------------------------------------------------ reporting
    def state(self, name: str) -> dict:
        now = time.time()
        used = [t for t in self._hits.get(name, []) if now - t < WINDOW_SECONDS]
        self._hits[name] = used
        return {"name": name,
                "ready": self._cool.get(name, 0) < now,
                "cooling_for": max(0, int(self._cool.get(name, 0) - now)),
                "used_last_minute": len(used),
                "last_error": self._errors.get(name, "")}

    def describe(self) -> list[dict]:
        out = []
        for e in self.entries:
            row = self.state(e["name"])
            row.update({"base_url": e["base_url"], "rpm": e["rpm"],
                        "model": (e["models"] or [""])[0], "source": e["source"]})
            out.append(row)
        return out

    def ready(self) -> bool:
        return any(self.state(e["name"])["ready"] for e in self.entries)

    # ------------------------------------------------------------ selection
    def candidates(self) -> list[dict]:
        """Ready entries in rotation order, never more than a handful per attempt."""
        now = time.time()
        live = []
        for i in range(len(self.entries)):
            e = self.entries[(self._cursor + i) % len(self.entries)]
            if self._cool.get(e["name"], 0) > now:
                continue
            hits = [t for t in self._hits.get(e["name"], []) if now - t < WINDOW_SECONDS]
            if e["rpm"] and len(hits) >= e["rpm"]:
                self._errors[e["name"]] = f"rpm budget {e['rpm']}/min reached"
                continue
            live.append(e)
        if live:
            self._cursor = (self._cursor + 1) % len(self.entries)
        return live[:3]

    def note_hit(self, name: str) -> None:
        self._hits.setdefault(name, []).append(time.time())

    def note_failure(self, name: str, error: str) -> None:
        self._errors[name] = error[:160]
        self._cool[name] = time.time() + COOLDOWN_SECONDS

    def note_success(self, name: str) -> None:
        self._errors.pop(name, None)
        self._cool.pop(name, None)


def pool(provider: str | None = None) -> KeyPool:
    """Extra keys first, then whatever Hermes already has: a bad key degrades, it does not disable."""
    entries = load_entries()
    seen = {e["name"] for e in entries}
    for extra in hermes_entries(provider):
        if extra["name"] not in seen:
            extra.setdefault("stream", True)
            extra.setdefault("priority", 40)
            entries.append(extra)
            seen.add(extra["name"])
    # lower priority first: the host's own provider defaults ahead of an extra key, because a
    # shared free tier answered a drawing turn in 49 s against 5 s. Set priority 10 on a key to lead.
    entries.sort(key=lambda e: (e.get("priority", 60), e["name"]))
    return KeyPool(entries)


def write_entries(entries: list[dict]) -> Path:
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"keys": [
        {"name": e["name"], "base_url": e["base_url"], "api_key": e["api_key"],
         "models": e.get("models") or [], "rpm": int(e.get("rpm") or 0),
         "stream": e.get("stream", True) is not False,
         "priority": int(e.get("priority", 60))}
        for e in entries]}
    KEYS_PATH.write_text(json.dumps(payload, indent=1) + "\n")
    os.chmod(KEYS_PATH, 0o600)
    return KEYS_PATH
