from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
count = 0
for path in ROOT.glob("test_*.py"):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr.startswith("assert"):
                count += 1
for path in ROOT.glob("*_contract.mjs"):
    count += len(re.findall(r"\bassert\s*\(", path.read_text()))
print(f"assertions declared: {count}")
if count < 30:
    print("need at least 30 explicit assertions", file=sys.stderr)
    raise SystemExit(1)
