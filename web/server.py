"""Charrette's own web surface.

The plugin ships its own server instead of a tab inside the Hermes dashboard:
a contest plugin has to be useful to anyone who installs it, not only to
someone running the admin UI, and it must not depend on that UI's auth.

Bound to 127.0.0.1 on a free port by default. Every API call needs the
per-session token that the printed URL carries; the page keeps it in memory
and sends it as a header.

The address it PRINTS is a separate question from the address it BINDS. A
board is usually drawn on the agent's machine and watched from another one, so
the URL has to name something the human's browser can reach:

  CHARRETTE_BIND          interface to bind (default 127.0.0.1)
  CHARRETTE_PORT          port to bind; 0 (default) takes a free one
  CHARRETTE_PUBLIC_BASE   address humans load, e.g. https://box.tailnet.ts.net/board
  CHARRETTE_WEB_TOKEN     pin the token so a browser and the agent agree on one URL

With a pinned port and a public base, a board that is already serving on that
port is REUSED rather than duplicated: the agent's tools and the human's tab
then read and write one document.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..board.chat import BoardChat
from ..board.render import render_crop
from ..board.replay import build_timeline, pick_objects
from ..board.store import DEFAULT_BOARD, RevConflict, Store, add_stroke, apply_objects
from ..tools import SCHEMAS, make_handlers

HERE = Path(__file__).resolve().parent
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/chat-ui.mjs": ("chat-ui.mjs", "text/javascript; charset=utf-8"),
          "/minimap.mjs": ("minimap.mjs", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}


class _Handler(BaseHTTPRequestHandler):
    server_version = "charrette/0.1"

    # ---------------------------------------------------------------- helpers
    @property
    def app(self) -> "BoardServer":
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, *args):  # quiet by default
        if self.app.verbose:
            super().log_message(*args)

    def _send(self, code: int, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _stream(self, frames, ctype: str = "text/event-stream; charset=utf-8", on_disconnect=None):
        """Write frames as they arrive. No Content-Length: the client reads until close."""
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for frame in frames:
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            if on_disconnect:
                on_disconnect()
            close = getattr(frames, "close", None)
            if close:
                close()
        self.close_connection = True

    def _json(self, code: int, payload):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _authorised(self, query: dict) -> bool:
        header = self.headers.get("X-Charrette-Token") or ""
        supplied = header or (query.get("t") or [""])[0]
        return bool(supplied) and secrets.compare_digest(str(supplied), self.app.token)

    # -------------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path in STATIC:
            name, ctype = STATIC[path]
            blob = (HERE / name).read_bytes()
            self._send(200, blob, ctype, {"Cache-Control": "no-store"})
            return

        if path == "/font/CharretteHand.woff2":
            blob = (HERE.parent / "renderer" / "fonts" / "CharretteHand.woff2").read_bytes()
            self._send(200, blob, "font/woff2", {"Cache-Control": "max-age=86400"})
            return

        if path == "/charrette.mjs":
            # the shared renderer, served straight from the repo: one engine for
            # this page and the desktop pane, no build step in the browser
            blob = (HERE.parent / "renderer" / "charrette.mjs").read_bytes()
            self._send(200, blob, "text/javascript; charset=utf-8",
                       {"Cache-Control": "no-store"})
            return

        if not self._authorised(query):
            self._json(401, {"error": "E_AUTH", "message": "open the URL this server printed"})
            return

        if path.startswith("/replay/"):
            # generated scene replays live outside the web root; they are board
            # content, so they stay behind the token like every other route
            name = path[len("/replay/"):]
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.html", name):
                self._json(400, {"error": "E_NAME"})
                return
            blob_path = self.app.store.root / "replays" / name
            if not blob_path.exists():
                self._json(404, {"error": "E_MISSING"})
                return
            self._send(200, blob_path.read_bytes(), "text/html; charset=utf-8",
                       {"Cache-Control": "no-store"})
            return

        store = self.app.store
        if path == "/api/control":
            # The director channel. Whoever is driving the board from outside the page (a
            # chat turn, a script, me) leaves a command here; the page picks it up on its
            # next tick and performs it in front of the reader. A queue rather than a
            # socket: the page can be asleep, reloaded or on a phone, and still catch up.
            since = int((query.get("since") or ["0"])[0] or 0)
            state = _control_read()
            self._json(200, {"seq": state["seq"],
                             "items": [it for it in state["items"] if it["seq"] > since]})
            return
        if path == "/api/boards":
            self._json(200, store.index())
            return
        if path == "/api/replay":
            # the same timeline the replay page embeds, handed to the page as data so
            # a message can replay its own work on the board it was drawn on
            bid = (query.get("board") or [DEFAULT_BOARD])[0]
            ids = [i for i in (query.get("ids") or [""])[0].split(",") if i]
            doc = store.read(bid)
            picked = pick_objects(doc, ids=ids or None)
            if not picked:
                self._json(200, {"board": bid, "segments": [], "total": 0})
                return
            segments, total = build_timeline(picked)
            for seg in segments:
                seg["kind"] = (doc.get("objects") or {}).get(seg["id"], {}).get("kind", "stroke")
            self._json(200, {"board": bid, "segments": segments, "total": total,
                             "objects": [i for i, _ in picked]})
            return
        if path == "/api/session":
            bid = (query.get("board") or [DEFAULT_BOARD])[0]
            payload = self.app.chat.info()
            payload.update({"board": bid, "messages": self.app.chat.history(bid),
                            "session": store.read(bid).get("session")})
            self._json(200, payload)
            return
        if path == "/api/board":
            bid = (query.get("board") or [DEFAULT_BOARD])[0]
            since = query.get("since")
            doc = store.read(bid)
            if since and int(doc.get("rev", 0)) <= int(since[0]):
                self._json(200, {"unchanged": True, "rev": doc.get("rev", 0)})
            else:
                self._json(200, doc)
            return
        if path == "/api/img":
            bid = (query.get("board") or [DEFAULT_BOARD])[0]
            oid = (query.get("id") or [""])[0]
            obj = (store.read(bid).get("objects") or {}).get(oid) or {}
            src = obj.get("src")
            if not src:
                self._json(404, {"error": "E_NO_IMAGE"})
                return
            file = store.root / "images" / src
            if not file.is_file():
                self._json(404, {"error": "E_MISSING_FILE"})
                return
            ctype = mimetypes.guess_type(file.name)[0] or "image/png"
            self._send(200, file.read_bytes(), ctype)
            return
        if path == "/api/export":
            # A picture of a region, rendered by this process: no browser, no tab, no display.
            bid = (query.get("board") or [DEFAULT_BOARD])[0]
            doc = store.read(bid)
            box = None
            if all(k in query for k in ("x", "y", "w", "h")):
                try:
                    box = [float(query[k][0]) for k in ("x", "y", "w", "h")]
                except (TypeError, ValueError):
                    self._json(400, {"error": "E_ARGS", "message": "x, y, w, h must be numbers"})
                    return
            try:
                scale = float((query.get("scale") or ["2"])[0])
            except (TypeError, ValueError):
                scale = 2.0
            scale = max(0.25, min(4.0, scale))
            grid = (query.get("grid") or ["1"])[0] not in ("0", "false", "no")
            refs = [r for r in (query.get("ref") or []) if r] or None
            if refs:
                missing = [r for r in refs if r not in (doc.get("objects") or {})]
                if missing:
                    self._json(404, {"error": "E_NO_OBJECT", "object": missing[0]})
                    return
            try:
                png, rect, used = render_crop(doc, bbox=box, ids=refs,
                                              images_dir=store.root / "images",
                                              scale=scale, grid=grid, pad=44.0)
            except Exception as exc:  # a render failure is a 500 with a reason, not a hang
                self._json(500, {"error": "E_RENDER", "message": str(exc)[:200]})
                return
            self._send(200, png, "image/png",
                       {"X-Charrette-Rect": ",".join(f"{v:.1f}" for v in rect),
                        "X-Charrette-Scale": f"{used:.2f}"})
            return
        if path == "/api/assets":
            bid = (query.get("board") or [DEFAULT_BOARD])[0]
            doc = store.read(bid)
            files = []
            for oid, obj in (doc.get("objects") or {}).items():
                if obj.get("kind") != "image" or not obj.get("src"):
                    continue
                files.append({"kind": "image", "id": oid, "file": obj["src"], "label": obj.get("label"),
                              "bbox": obj.get("bbox"),
                              "url": f"/api/img?board={bid}&id={oid}"})
            self._json(200, {"board": bid, "rev": doc.get("rev"), "assets": files,
                             "capture": f"/api/export?board={bid}&scale=2"})
            return
        self._json(404, {"error": "E_ROUTE", "path": path})

    # ------------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if not self._authorised(query):
            self._json(401, {"error": "E_AUTH"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "E_JSON"})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": "E_JSON"})
            return

        store = self.app.store
        bid = body.get("board") or DEFAULT_BOARD
        if not isinstance(bid, str):
            bid = DEFAULT_BOARD          # a null board once created a board called None
        if path == "/api/stroke":
            points = body.get("points") or []
            if len(points) < 2:
                self._json(400, {"error": "E_ARGS", "message": "need >= 2 points"})
                return
            try:
                doc = store.write(
                    bid,
                    add_stroke(points,
                               ink=body.get("ink") or "#111318",
                               width=int(body.get("width") or 3),
                               label=(body.get("label") or "")[:28] or None),
                    if_rev=body.get("if_rev"),
                )
            except RevConflict as exc:
                self._json(409, {"error": "E_REV", "rev": exc.rev})
                return
            self._json(200, {"ok": True, "object": doc.get("_last"), "rev": doc["rev"]})
            return
        if path == "/api/objects":
            try:
                doc = store.write(
                    bid,
                    apply_objects(body.get("upsert") or [], body.get("delete") or []),
                    if_rev=body.get("if_rev"),
                )
            except RevConflict as exc:
                self._json(409, {"error": "E_REV", "rev": exc.rev})
                return
            self._json(200, {"ok": True, "rev": doc["rev"], "last": doc.get("_last")})
            return
        if path == "/api/boards/new":
            self._json(200, store.new_board(body.get("name")))
            return
        if path == "/api/boards/rename":
            bid = body.get("board") or DEFAULT_BOARD
            name = (body.get("name") or "").strip()
            if not name:
                self._json(400, {"error": "E_ARGS", "message": "name is required"})
                return
            self._json(200, store.rename(bid, name[:48]))
            return
        if path == "/api/boards/active":
            self._json(200, store.set_active(body.get("board") or DEFAULT_BOARD))
            return
        if path == "/api/session/send":
            text = (body.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "E_ARGS", "message": "text is required"})
                return
            frames = self.app.chat.stream(bid, text, body.get("view"))
            self._stream(frames, on_disconnect=lambda: self.app.chat.cancel(bid))
            return
        if path == "/api/session/stop":
            active = self.app.chat.cancel(bid)
            self._json(200, {"ok": True, "active": active})
            return
        if path == "/api/session/reset":
            self.app.chat.reset(bid)
            self._json(200, {"ok": True})
            return
        if path == "/api/control":
            cmd = body.get("cmd")
            if not isinstance(cmd, str) or not cmd:
                self._json(400, {"error": "E_CMD"})
                return
            item = {k: v for k, v in body.items() if k in ("cmd", "board", "ids", "speed",
                                                           "caption", "text", "focus")}
            item["cmd"] = cmd
            item["at"] = _now_iso()
            seq = _control_push(item)
            self._json(200, {"ok": True, "seq": seq})
            return
        if path == "/api/camera":
            cam = body.get("camera") or {}
            try:
                store.write(bid, lambda d: {**d, "camera": cam} if isinstance(cam, dict) else d)
            except Exception:
                pass
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "E_ROUTE", "path": path})


# ---------------------------------------------------------------- director channel
CONTROL_LIMIT = 40


def _control_path() -> Path:
    return Path.home() / ".hermes" / "charrette" / "control.json"


def _control_read() -> dict:
    try:
        data = json.loads(_control_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return {"seq": int(data.get("seq") or 0), "items": data["items"]}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return {"seq": 0, "items": []}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _control_push(item: dict) -> int:
    path = _control_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _control_read()
    state["seq"] += 1
    entry = {"seq": state["seq"], **item}
    state["items"] = (state["items"] + [entry])[-CONTROL_LIMIT:]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)
    return state["seq"]


def _env_int(name: str, default: int = 0) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def bind_host() -> str:
    return (os.environ.get("CHARRETTE_BIND") or "127.0.0.1").strip() or "127.0.0.1"


def bind_port() -> int:
    return max(0, _env_int("CHARRETTE_PORT", 0))


def public_base() -> str:
    """Where a HUMAN loads the board, when loopback is not their machine.

    `CHARRETTE_PUBLIC_BASE` is the one thing a remote install has to say out
    loud: a tailnet name behind `tailscale serve`, a LAN name, a tunnel. Unset,
    the URL names the socket we bound, which is right for a same-machine board.
    """
    return (os.environ.get("CHARRETTE_PUBLIC_BASE") or "").strip().rstrip("/")


def web_token() -> str:
    """Pinned when CHARRETTE_WEB_TOKEN is set, random per process otherwise.

    Pinning matters when two faces share one board: the agent's tools and a
    `charrette open` left running have to print the SAME usable URL.
    """
    return os.environ.get("CHARRETTE_WEB_TOKEN") or secrets.token_urlsafe(18)


def _wildcard_host() -> str:
    """A routable name for a wildcard bind. Loopback when there is nothing better."""
    try:
        name = socket.gethostname()
        for info in socket.getaddrinfo(name, None, socket.AF_INET):
            raw = info[4][0]
            addr = raw if isinstance(raw, str) else str(raw)
            if not addr.startswith("127."):
                return addr
    except OSError:
        pass
    return "127.0.0.1"


class BoardServer:
    def __init__(self, store: Store | None = None, port: int | None = None, verbose: bool = False,
                 host: str | None = None):
        self.store = store or Store()
        # one chat per board; it runs the board's own verbs as tools against this store
        self.chat = BoardChat(self.store, SCHEMAS, make_handlers(self.store))
        self.token = web_token()
        self.verbose = verbose
        self.host = (host or bind_host()).strip() or "127.0.0.1"
        want = bind_port() if port is None else port
        try:
            self.httpd = ThreadingHTTPServer((self.host, want), _Handler)
        except OSError:
            if not want:
                raise
            # The pinned port is held by a board that is already serving a
            # human. Binding a second one would print a URL nobody is looking
            # at, so fall back to a free port and say so in the URL we print.
            self.httpd = ThreadingHTTPServer((self.host, 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = self  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> "BoardServer":
        if self._thread and self._thread.is_alive():
            return self
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True,
                                        name="charrette-web")
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        base = public_base()
        if base:
            return f"{base}/?t={self.token}"
        host = _wildcard_host() if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}/?t={self.token}"

    def stop(self) -> None:
        # shutdown() blocks until serve_forever() returns, so never call it on a
        # server that was only constructed (a test, a probe, a failed start).
        if self._thread and self._thread.is_alive():
            self.httpd.shutdown()
        self.httpd.server_close()


def _serving_brand(port: int, token: str, timeout: float = 0.4) -> bool:
    """True when something answers as a charrette board on `port`."""
    import urllib.request

    try:
        url = f"http://127.0.0.1:{port}/api/boards?t={token}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return b"boards" in resp.read(4096)
    except Exception:
        return False


class SharedBoard:
    """A board another process already serves on the pinned port.

    The plugin does not get to start a second server and hand the human a URL
    nobody is drawing on, so when `CHARRETTE_PORT` is already answering it
    borrows that one: same store on disk, same pinned token, same public base.
    """

    def __init__(self, port: int):
        self.port = port
        self.host = bind_host()
        self.token = web_token()
        self.owned = False

    @property
    def url(self) -> str:
        base = public_base()
        if base:
            return f"{base}/?t={self.token}"
        host = _wildcard_host() if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}/?t={self.token}"

    def start(self) -> "SharedBoard":
        return self

    def stop(self) -> None:
        """Not ours to stop."""
        return


_LIVE: dict[int, object] = {}


def ensure_running(store: Store | None = None, port: int | None = None) -> object:
    key = id(store) if store else 0
    srv = _LIVE.get(key)
    if srv is None:
        want = bind_port() if port is None else port
        loopback = bind_host() in ("127.0.0.1", "localhost", "::1")
        if want and loopback and _serving_brand(want, web_token()):
            srv = SharedBoard(want)
            _LIVE[key] = srv
            return srv
        srv = BoardServer(store=store, port=want).start()
        _LIVE[key] = srv
    return srv
