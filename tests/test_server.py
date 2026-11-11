from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from charrette.board.store import Store, add_stroke
from charrette.web.server import BoardServer, SharedBoard, ensure_running


class BoardAddressContractTests(unittest.TestCase):
    """A board is drawn on the agent's machine and read from the human's."""

    def test_public_base_is_what_the_url_says(self):
        import os
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"CHARRETTE_PUBLIC_BASE": "https://box.tail.ts.net/board/",
                         "CHARRETTE_WEB_TOKEN": "pinned-token"}):
            server = BoardServer(store=Store(Path(tmp)), port=0)
            try:
                self.assertEqual(server.url, "https://box.tail.ts.net/board/?t=pinned-token",
                                 "the printed URL must be the one a remote browser can load, "
                                 "not the loopback the server happens to bind")
            finally:
                server.stop()

    def test_no_public_base_still_prints_a_loadable_loopback_url(self):
        import os
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHARRETTE_PUBLIC_BASE", None)
            server = BoardServer(store=Store(Path(tmp)), port=0)
            try:
                self.assertTrue(server.url.startswith(f"http://127.0.0.1:{server.port}/?t="),
                                server.url)
            finally:
                server.stop()

    def test_a_board_already_on_the_pinned_port_is_reused_not_duplicated(self):
        import os
        import socket
        import unittest.mock as mock

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"CHARRETTE_PORT": str(port), "CHARRETTE_WEB_TOKEN": "pinned-token",
                         "CHARRETTE_PUBLIC_BASE": "https://box.tail.ts.net/board"}):
            import charrette.web.server as web
            web._LIVE.clear()
            live = BoardServer(store=Store(Path(tmp)), port=port).start()
            try:
                borrowed = ensure_running(store=Store(Path(tmp) / "other"))
                assert isinstance(borrowed, SharedBoard), type(borrowed)
                self.assertEqual(borrowed.port, port)
                self.assertEqual(borrowed.url, live.url,
                                 "both faces must hand the human the same address")
                self.assertTrue(hasattr(borrowed, "owned"))
                self.assertFalse(borrowed.owned)
            finally:
                live.stop()
                web._LIVE.clear()


class StoreSurvivesATidiedDataDirTests(unittest.TestCase):
    def test_a_write_recreates_a_data_dir_that_was_deleted_under_it(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "chalk"
            store = Store(root)
            store.ensure_board("b1", "board 1")

            shutil.rmtree(root, ignore_errors=True)
            try:
                store.write("b1", add_stroke([[0, 0], [10, 10]]))
            except FileNotFoundError as exc:  # the bug: every later write fails
                self.fail(f"a deleted data dir must not kill the store: {exc}")
            self.assertTrue((root / "boards" / "b1.json").exists(),
                            "the store rebuilds its own directory instead of failing")


class BoardServerContractTests(unittest.TestCase):
    def test_stop_route_is_authorised_additive_and_reports_inactive_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = BoardServer(store=Store(Path(tmp)), port=0).start()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{server.port}/api/session/stop",
                    data=json.dumps({"board": "b1"}).encode(),
                    headers={"X-Charrette-Token": server.token, "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=2) as response:
                    payload = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(payload, {"ok": True, "active": False})
            finally:
                server.stop()

    def test_replay_route_serves_generated_pages_only_to_the_token_holder(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            (Path(tmp) / "replays").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "replays" / "b1-sc_1.html").write_text("<!doctype html><title>replay</title>")
            server = BoardServer(store=store, port=0).start()
            base = f"http://127.0.0.1:{server.port}"
            try:
                with urllib.request.urlopen(f"{base}/replay/b1-sc_1.html?t={server.token}", timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
                    self.assertIn(b"replay", response.read())

                for path, expected in ((f"/replay/b1-sc_1.html", 401),
                                       (f"/replay/..%2Fboards%2Fb1.json?t={server.token}", 400),
                                       (f"/replay/missing.html?t={server.token}", 404)):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(f"{base}{path}", timeout=2)
                    self.assertEqual(caught.exception.code, expected)
                    caught.exception.close()
            finally:
                server.stop()

    def test_replay_route_returns_a_timeline_in_creation_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            first = store.write("b1", add_stroke([[0, 0], [60, 0]]))["_last"]
            second = store.write("b1", add_stroke([[0, 40], [60, 40]]))["_last"]
            server = BoardServer(store=store, port=0).start()
            base = f"http://127.0.0.1:{server.port}"
            try:
                with urllib.request.urlopen(f"{base}/api/replay?board=b1&t={server.token}", timeout=2) as response:
                    all_of = json.load(response)
                self.assertEqual([s["id"] for s in all_of["segments"]], [first, second])
                self.assertGreater(all_of["total"], 0)
                self.assertEqual(all_of["segments"][0]["t0"], 0)

                with urllib.request.urlopen(f"{base}/api/replay?board=b1&ids={second}&t={server.token}", timeout=2) as response:
                    one = json.load(response)
                self.assertEqual([s["id"] for s in one["segments"]], [second],
                                 "an explicit id list is a sequence, not a filter over everything")
                self.assertEqual(one["segments"][0]["kind"], "stroke")
            finally:
                server.stop()

    def test_director_channel_queues_a_cue_and_hands_it_back_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (home / ".hermes" / "charrette").mkdir(parents=True)
            store = Store(Path(tmp) / "boards")
            server = BoardServer(store=store, port=0).start()
            base = f"http://127.0.0.1:{server.port}"
            import unittest.mock as mock
            try:
                with mock.patch("pathlib.Path.home", return_value=home):
                    req = urllib.request.Request(
                        f"{base}/api/control?t={server.token}",
                        data=json.dumps({"cmd": "replay", "ids": ["a1"], "speed": 2}).encode(),
                        headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=2) as response:
                        pushed = json.load(response)
                    self.assertEqual(pushed["seq"], 1)

                    with urllib.request.urlopen(f"{base}/api/control?since=0&t={server.token}", timeout=2) as response:
                        queue = json.load(response)
                    self.assertEqual(len(queue["items"]), 1)
                    self.assertEqual(queue["items"][0]["cmd"], "replay")
                    self.assertEqual(queue["items"][0]["speed"], 2)

                    with urllib.request.urlopen(f"{base}/api/control?since=1&t={server.token}", timeout=2) as response:
                        empty = json.load(response)
                    self.assertEqual(empty["items"], [],
                                     "a cue is handed to the page once, not replayed on every tick")
            finally:
                server.stop()

if __name__ == "__main__":
    unittest.main()
