from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from board.replay import build_html

ROOT = Path(__file__).resolve().parent.parent


class RendererNodeContractTests(unittest.TestCase):
    def test_renderer_module_exposes_drawing_and_replay_entry_points(self):
        run = subprocess.run(
            ["node", "tests/renderer_contract.mjs"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("renderer contract passed", run.stdout)

    def test_generated_replay_seek_contract_holds_at_fixed_timestamps(self):
        picked = [
            ("first", {"kind": "stroke", "pts": [[0, 0], [100, 0]], "bbox": [0, 0, 100, 1]}),
            ("second", {"kind": "stroke", "pts": [[0, 20], [100, 20]], "bbox": [0, 20, 100, 1]}),
        ]
        doc = {"schema": "charrette/1", "rev": 1, "layers": [], "objects": dict(picked)}
        with tempfile.TemporaryDirectory() as root:
            html, total = build_html(doc, Path(root), picked, "one", 1.0)
            replay_path = Path(root) / "replay.html"
            replay_path.write_text(html)
            run = subprocess.run(
                ["node", "tests/replay_seek_contract.mjs", str(replay_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertGreater(total, 0)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("replay seek contract passed", run.stdout)


if __name__ == "__main__":
    unittest.main()
