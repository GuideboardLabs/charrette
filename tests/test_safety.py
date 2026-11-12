"""The three boundaries: a name that becomes a path, a URL that becomes a request, a path that
becomes a read. Each one is checked here with the value an attacker would actually send."""

from __future__ import annotations

import base64
import http.server
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from board import safety
from board.store import Store, apply_objects

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class SrcTests(unittest.TestCase):
    def test_accepts_only_what_save_produces(self):
        self.assertTrue(safety.valid_src("0123456789abcdef.png"))
        self.assertTrue(safety.valid_src("fedcba9876543210.webp"))
        for bad in ("../../etc/passwd", "/etc/passwd", "0123456789ABCDEF.png", "abc.png",
                    "0123456789abcdef.gif", "0123456789abcdef.png/../../x", "", None, 7,
                    "http://evil/x.png", "0123456789abcdef.png\x00"):
            self.assertFalse(safety.valid_src(bad), bad)

    def test_image_path_stays_under_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            self.assertEqual(safety.image_path(root, "0123456789abcdef.png"),
                             root / "images" / "0123456789abcdef.png")
            self.assertIsNone(safety.image_path(root, "../store.json"))
            self.assertIsNone(safety.image_path(root, "/etc/passwd"))

    def test_a_write_cannot_introduce_a_foreign_src(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.ensure_board("b1", "one")
            mutate = apply_objects(upserts=[{"id": "img1", "kind": "image", "src": "../../etc/passwd"},
                                            {"id": "img2", "kind": "image", "src": "0123456789abcdef.png"}])
            store.write("b1", mutate)
            objs = store.read("b1").get("objects") or {}
            self.assertNotIn("img1", objs, "a traversal name was stored")
            self.assertEqual(objs["img2"]["src"], "0123456789abcdef.png")


class UrlTests(unittest.TestCase):
    def test_refuses_every_internal_target(self):
        for url in ("http://127.0.0.1/x.png", "http://localhost/x.png",
                    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                    "http://[::1]/x.png", "http://10.0.0.5/x.png", "http://192.168.1.1/x.png",
                    "http://172.16.0.1/x.png", "http://0.0.0.0/x.png", "http://[fd00::1]/x.png",
                    "file:///etc/passwd", "ftp://example.com/x.png", "gopher://127.0.0.1:6379/_x"):
            self.assertFalse(safety.safe_url(url), url)

    def test_allows_a_public_literal(self):
        self.assertTrue(safety.safe_url("https://93.184.216.34/x.png"))

    def test_fetch_checks_every_hop(self):
        """A public host that redirects to the metadata service must be refused, not followed."""
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append(self.path)
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                    self.end_headers()
                else:  # pragma: no cover - the hop that must not happen
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"secret")

            def log_message(self, format, *args):  # noqa: A002 - the base class's name
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        try:
            allowed = f"http://127.0.0.1:{port}/start"
            with patch.object(safety, "safe_url", lambda u: u == allowed):
                with self.assertRaises(ValueError) as ctx:
                    safety.fetch(allowed, timeout=5)
            self.assertIn("refused", str(ctx.exception))
            self.assertEqual(seen, ["/start"], "the redirect target was requested")
        finally:
            srv.shutdown()


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "imports").mkdir()
        (self.root / "elsewhere").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_an_image_from_the_import_dir(self):
        f = self.root / "imports" / "pic.png"
        f.write_bytes(PNG)
        blob, mime = safety.read_import(str(f), self.root)
        self.assertEqual(blob, PNG)
        self.assertEqual(mime, "image/png")

    def test_refuses_a_file_outside_it(self):
        f = self.root / "elsewhere" / "pic.png"
        f.write_bytes(PNG)
        with self.assertRaises(ValueError):
            safety.read_import(str(f), self.root)

    def test_refuses_a_dot_directory(self):
        hidden = self.root / "imports" / ".ssh"
        hidden.mkdir()
        f = hidden / "id_rsa.png"
        f.write_bytes(PNG)
        with self.assertRaises(ValueError):
            safety.read_import(str(f), self.root)

    def test_refuses_a_traversal_out_of_it(self):
        f = self.root / "elsewhere" / "pic.png"
        f.write_bytes(PNG)
        with self.assertRaises(ValueError):
            safety.read_import(str(self.root / "imports" / ".." / "elsewhere" / "pic.png"), self.root)

    def test_refuses_something_that_is_not_an_image(self):
        f = self.root / "imports" / "notapicture.png"
        f.write_text("this is a text file wearing a png name")
        with self.assertRaises(ValueError):
            safety.read_import(str(f), self.root)

    def test_refuses_the_hermes_home_even_when_it_is_the_import_dir(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "secrets").mkdir()
            f = Path(home) / "secrets" / "pic.png"
            f.write_bytes(PNG)
            with patch.dict(os.environ, {"CHARRETTE_IMPORT_DIR": str(Path(home) / "secrets")}):
                with patch.object(safety, "hermes_home", lambda: Path(home)):
                    with self.assertRaises(ValueError) as ctx:
                        safety.read_import(str(f), self.root)
            self.assertIn("Hermes home", str(ctx.exception))


class RootTests(unittest.TestCase):
    def test_the_data_root_follows_the_profile(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HERMES_HOME": home}, clear=False):
                os.environ.pop("CHARRETTE_DATA_DIR", None)
                with patch.object(safety, "hermes_home", lambda: Path(home)):
                    from board import store as store_mod

                    self.assertEqual(store_mod.default_root(), Path(home) / "charrette")

    def test_a_secret_comes_from_the_profile_store_first(self):
        with patch.dict(os.environ, {"CHARRETTE_IMAGE_KEY": "from-env"}, clear=False):
            self.assertEqual(safety.secret("CHARRETTE_IMAGE_KEY"), "from-env")
        self.assertEqual(safety.secret("CHARRETTE_IMAGE_KEY_DEFINITELY_UNSET"), "")


if __name__ == "__main__":
    unittest.main()
