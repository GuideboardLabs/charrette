#!/usr/bin/env python3
"""CLI for a scene replay. The generator lives in board/replay.py so the tool and this share one path.

    python3 scripts/make_replay.py <board> [--scene ID] [--objects id,id] [--caption TEXT]
                                   [--speed 1.0] [--out PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "plugins"))
from charrette.board.replay import write_replay  # noqa: E402
from charrette.board.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--scene", default="")
    ap.add_argument("--objects", default="")
    ap.add_argument("--caption", default="")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    ids = [i.strip() for i in args.objects.split(",") if i.strip()] or None
    path, total, n = write_replay(Store(), args.board, ids=ids, scene_id=args.scene,
                                  caption=args.caption, speed=args.speed, out=args.out)
    print(path)
    print("objects", n, "total_ms", total, "bytes", path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
