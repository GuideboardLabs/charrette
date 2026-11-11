#!/usr/bin/env python3
"""Manage the board session's key pool. Keys never go through chat or a shell history.

    python3 scripts/keys.py list
    python3 scripts/keys.py add nvidia --base-url https://integrate.api.nvidia.com/v1 \
        --model meta/llama-3.3-70b-instruct --rpm 40
    python3 scripts/keys.py test nvidia
    python3 scripts/keys.py remove nvidia

`add` reads the key from a hidden prompt (or stdin with --stdin), writes
~/.hermes/charrette/keys.json at 0600, and prints only a masked form.

Known good endpoints for free or rate-limited tiers:
    nvidia     https://integrate.api.nvidia.com/v1          (per-minute limits, many open models)
    groq       https://api.groq.com/openai/v1
    openrouter https://openrouter.ai/api/v1                 (free model ids end in :free)
    cerebras   https://api.cerebras.ai/v1
    ollama     https://ollama.com/v1
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "plugins"))
from charrette.board import keys as keypool  # noqa: E402


def mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return secret[:3] + "…" + secret[-4:]


def cmd_list(_args) -> int:
    entries = keypool.load_entries()
    if not entries:
        print("no keys in", keypool.KEYS_PATH)
        print("falling back to Hermes providers:", [e["name"] for e in keypool.hermes_entries()] or "none")
        return 0
    print(keypool.KEYS_PATH, "(mode 0600)" if oct(keypool.KEYS_PATH.stat().st_mode)[-3:] == "600" else "")
    for e in entries:
        stream = "stream" if e.get("stream", True) else "json  "
        print(f"  {e['name']:<12} {e['base_url']:<42} model={(e['models'] or ['(hermes default)'])[0]:<34}"
              f" rpm={e['rpm'] or '-':<4} p={e.get('priority', 60):<3} {stream} key={mask(e['api_key'])}")
    return 0


def cmd_add(args) -> int:
    entries = keypool.load_entries()
    if any(e["name"] == args.name for e in entries):
        print(f"{args.name} already exists; remove it first to replace the key")
        return 1
    if args.stdin:
        secret = sys.stdin.readline().strip()
    else:
        secret = getpass.getpass(f"paste the {args.name} API key (hidden): ").strip()
    if len(secret) < 12:
        print("that does not look like a key")
        return 1
    entry = {"name": args.name, "base_url": args.base_url.rstrip("/"), "api_key": secret,
             "models": [args.model] if args.model else [], "rpm": int(args.rpm or 0),
             "stream": not args.no_stream, "priority": args.priority}
    entries.append(entry)
    path = keypool.write_entries(entries)
    print(f"wrote {path}  {args.name} key={mask(secret)} model={args.model or '(hermes default)'}")
    return 0


def cmd_remove(args) -> int:
    entries = [e for e in keypool.load_entries() if e["name"] != args.name]
    path = keypool.write_entries(entries)
    print(f"removed {args.name}; {len(entries)} left in {path}")
    return 0


def cmd_test(args) -> int:
    pool = keypool.KeyPool([e for e in keypool.load_entries() if e["name"] == args.name])
    if not pool.entries:
        print("no such key")
        return 1
    entry = pool.entries[0]
    model = (entry["models"] or ["deepseek-v4.1-flash"])[0]
    body = {"model": model, "messages": [{"role": "user", "content": "reply with the single word: ok"}],
            "max_tokens": 8}
    req = urllib.request.Request(entry["base_url"] + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {entry['api_key']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.load(resp)
        text = (out["choices"][0]["message"].get("content") or "").strip()
        print(f"{args.name} ok · model={model} · said {text[:40]!r}")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"{args.name} HTTP {exc.code}: {exc.read(200).decode('utf-8', 'replace')}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"{args.name} {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="board session key pool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--base-url", default="https://integrate.api.nvidia.com/v1")
    add.add_argument("--model", default="")
    add.add_argument("--rpm", type=int, default=0)
    add.add_argument("--stdin", action="store_true", help="read the key from stdin instead of a prompt")
    add.add_argument("--priority", type=int, default=60,
                     help="lower runs first; the host provider sits at 40, extra keys default 60")
    add.add_argument("--no-stream", action="store_true",
                     help="ask for one JSON body per turn (some providers leak tool calls as XML while streaming)")
    add.set_defaults(fn=cmd_add)
    rm = sub.add_parser("remove")
    rm.add_argument("name")
    rm.set_defaults(fn=cmd_remove)
    test = sub.add_parser("test")
    test.add_argument("name")
    test.set_defaults(fn=cmd_test)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
