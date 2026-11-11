#!/usr/bin/env python3
"""Run Charrette's dependency-free offline tests despite the historical .sh suffix."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def run_stdin(source: str, label: str, *args: str) -> None:
    print("+", " ".join(args), "<", label, flush=True)
    subprocess.run(args, cwd=ROOT, check=True, input=source, text=True)


def main() -> int:
    run(sys.executable, "tests/count_assertions.py")
    run("node", "--check", "web/chat-ui.mjs")
    run_stdin((ROOT / "web" / "app.js").read_text(), "web/app.js",
              "node", "--input-type=module", "--check", "-")
    run_stdin((ROOT / "desktop" / "pane.js").read_text(), "desktop/pane.js",
              "node", "--input-type=module", "--check", "-")
    run(sys.executable, "scripts/bundle.py")
    run_stdin((ROOT / "desktop" / "plugin.js").read_text(), "desktop/plugin.js",
              "node", "--input-type=module", "--check", "-")
    run("node", "tests/chat_ui_contract.mjs")
    run("node", "tests/replay_engine_contract.mjs")
    run("node", "tests/minimap_contract.mjs")
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v")
    print("offline suite passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
