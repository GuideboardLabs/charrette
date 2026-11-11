#!/usr/bin/env python3
"""Serve deterministic scratch boards for CDP measurement."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from charrette.board.store import Store, apply_objects
from charrette.web.server import BoardServer


def seed(store: Store) -> None:
    store.ensure_board("b1", "blank critique")
    store.ensure_board("b2", "systems seminar")
    objects = [
        {"id": "title", "kind": "text", "actor": "human", "layer": "human-ink", "z": "a1",
         "x": 80, "y": 70, "size": 28, "text": "What changes the room?", "ink": "#111318",
         "bbox": [80, 70, 300, 42]},
        {"id": "flow", "kind": "stroke", "actor": "human", "layer": "human-ink", "z": "a2",
         "pts": [[90, 170], [160, 125], [235, 180], [320, 120], [405, 174]],
         "ink": "#1570EF", "w": 4, "bbox": [90, 120, 315, 60]},
        {"id": "table", "kind": "table", "actor": "agent", "layer": "agent-diagram", "z": "a3",
         "bbox": [110, 245, 360, 150], "rows": 3, "cols": 3,
         "cells": [["signal", "cost", "owner"], ["shared mark", "low", "room"], ["handoff", "high", "system"]],
         "ink": "#111318", "w": 1},
    ]
    store.write("b2", apply_objects(objects, []))
    store.write("b2", lambda doc: {**doc, "chat": [
        {"role": "user", "text": "Give this a rigorous second pass.", "at": "2026-11-09T15:00:00Z"},
        {"role": "assistant", "text": "The strongest claim is **shared mark**.\n\n```text\nroom -> mark -> decision\n```\n\nThe handoff row needs an owner before it can carry weight. This deliberately long reply checks wrapping, code overflow, rhythm, and the boundary between a caption and a wall of text.", "at": "2026-11-09T15:00:02Z"},
    ]})
    store.set_active("b1")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--ready", required=True)
    args = parser.parse_args()

    store = Store(Path(args.data))
    seed(store)
    server = BoardServer(store=store, port=0).start()
    Path(args.ready).write_text(json.dumps({"url": server.url, "port": server.port, "data": args.data}))

    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    print(server.url, flush=True)
    done.wait()
    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
