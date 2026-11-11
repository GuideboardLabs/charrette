"""A chat session attached to a board.

The board is its own piece: it can be opened anywhere the page is served and still be talked to.
This module is the agent half of that. It runs a small tool-calling loop against the model
credentials Hermes already has, with the board's own verbs as the tool surface, so the thing that
answers in the panel draws with the same code the plugin exposes to any other harness.

Why not spawn `hermes chat` per turn: that consumes an active-session lease, and Hermes caps those
at 3 by default, so a board conversation would fail whenever the user has a desktop and a CLI open.
Resolving the provider credentials through Hermes (`hermes_cli.auth`) keeps the same model, the same
key, and the same spend without taking a session slot. The `hermes-cli` backend is kept behind the
same interface for harnesses that want skills and memory, and it says plainly when a slot is full.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import keys as keypool

# Verbs the panel's agent may use. Anything that needs an image pipeline (board_look, board_image)
# stays out until there is a surface that can show its result in the panel.
AGENT_TOOLS = ("board_read", "board_draw", "board_stroke", "board_edit", "board_point", "board_scene")
MAX_ROUNDS = 4
HISTORY_CAP = 40


_POOL = None
_POOL_MTIME = -1.0


def _get_pool():
    """One pool for the process, rebuilt when the keys file changes on disk."""
    global _POOL, _POOL_MTIME
    try:
        mtime = keypool.KEYS_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _POOL is None or mtime != _POOL_MTIME:
        _POOL = keypool.pool(os.environ.get("CHARRETTE_CHAT_PROVIDER") or None)
        _POOL_MTIME = mtime
    return _POOL


def _model_name() -> str:
    try:
        import yaml
        from pathlib import Path
        cfg = yaml.safe_load((Path.home() / ".hermes" / "config.yaml").read_text())
        return str((cfg.get("model") or {}).get("default") or "deepseek-v4.1-flash")
    except Exception:
        return "deepseek-v4.1-flash"


def _board_brief(store, bid: str, view: dict | None = None) -> str:
    doc = store.read(bid)
    objs = doc.get("objects") or {}
    lines = []
    for oid, o in list(objs.items())[-28:]:
        kind = o.get("kind")
        bb = o.get("bbox")
        where = f" bbox={[round(v) for v in bb]}" if bb and len(bb) == 4 else ""
        extra = ""
        if kind == "text":
            extra = f" text={json.dumps(str(o.get('text') or '')[:40])}"
        lines.append(f"- {oid} {kind}{extra}{where}")
    cam = doc.get("camera") or {}
    visible = ""
    if view and view.get("w") and view.get("h") and cam.get("z"):
        z = float(cam.get("z") or 1)
        x, y = float(cam.get("x") or 0), float(cam.get("y") or 0)
        visible = (f"\nvisible area right now: x {round(x)} to {round(x + float(view['w']) / z)}, "
                   f"y {round(y)} to {round(y + float(view['h']) / z)} (board units, y down)")
    return (
        f"board {bid}: rev {doc.get('rev')}, {len(objs)} objects, camera x={cam.get('x', 0)} "
        f"y={cam.get('y', 0)} z={cam.get('z', 1)}\n"
        + ("\n".join(lines) if lines else "(empty board)")
        + visible
    )


SYSTEM = """You are the agent working on a shared whiteboard beside the chat, called Charrette.

The board is the message; your reply is its caption. Draw with the board_* tools, then say in one or
two short sentences what you did. Never describe a drawing you did not make. Coordinates are board
units with y down; the camera tells you what the person is looking at. Keep new marks inside the
area they can see unless they ask otherwise.

The verbs, with their real argument names (guessing costs you a round trip):
- board_stroke {points: [[x, y], ...], ink, width, label} is the workhorse. Anything freehand is a
  stroke. A circle is 28 points around a centre, closed on the first point:
  [cx + r*cos(t), cy + r*sin(t)] for t = 0, 2pi/28, ... A label is a short caption drawn beside it.
- board_draw {kind: "diagram", spec: {dir: "LR"|"TB", nodes: [{key, label}], edges: [{from, to, label}]},
  place: {near: "<object id>"} or {at: [x, y]}} for boxes and arrows.
- board_draw {kind: "table", rows, cols, cells: [[...]]} and
  board_draw {kind: "chart", chart: "bar"|"line", values: [...], labels: [...]}.
- board_draw {kind: "shape", over: "<object id>", shape: "poly"|"ellipse"|"rect", fill: "#D92D20"}
  fills or outlines a shape that already exists. It cannot create a new ellipse on empty canvas.
- board_edit {op: "erase"|"relabel"|"restyle"|"relayout", target: "<object id>", text} for your own work.
- board_scene {text, refs: [ids]} records the work as a replay and gives you a caption line.

Ink colours are exactly: red #D92D20, blue #1570EF, green #12805C, black #111318, purple #7A5AF8.

Rules that matter:
- Read the board before changing it, and never restyle or erase the person's own ink.
- Agent marks belong in the agent layer; the person's stroke stays on top and visibly theirs.
- Prefer the smallest mark that answers the request. One idea per reply.
- Never erase and redraw your own work in the same turn to chase a detail: draw once, then say what you
  drew and what you would change if asked. A redraw loop costs the person minutes.
- If a request is ambiguous, make the reasonable mark and ask the one question that would change it.
- You are inside a chat panel attached to the board, so the person can already see it. Never emit chat
  directives such as ::preview{file=...}, never link to a replay page, and never describe the board as
  if it were somewhere else. Your words are the caption; the board is right there.

Current board:
__BRIEF__
"""



def _normalise_args(name: str, args: dict) -> dict:
    """Models disagree about point shape: [[x, y]] or [{"x": .., "y": ..}] or `pts`.

    Normalising here rather than in each verb means any key can serve the panel.
    """
    if not isinstance(args, dict):
        return {}
    out = dict(args)
    if "points" not in out and "pts" in out:
        out["points"] = out.pop("pts")
    pts = out.get("points")
    if isinstance(pts, list):
        fixed = []
        for p in pts:
            try:
                if isinstance(p, dict) and "x" in p and "y" in p:
                    fixed.append([float(p["x"]), float(p["y"])])
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    fixed.append([float(p[0]), float(p[1])])
            except (TypeError, ValueError):
                continue          # drop junk rather than raising inside the verb
        out["points"] = fixed
    for key, cast in (("width", int), ("if_rev", int), ("rows", int), ("cols", int)):
        if key in out:
            try:
                out[key] = cast(out[key])
            except (TypeError, ValueError):
                out.pop(key)
    return out

def _tool_schema(schemas: list[dict]) -> list[dict]:
    out = []
    for s in schemas:
        if s.get("name") not in AGENT_TOOLS:
            continue
        params = dict(s.get("parameters") or {})
        # the panel is always about one board: drop the choice so the model cannot wander
        props = dict(params.get("properties") or {})
        props.pop("board", None)
        # a bare `array` lets the model invent a point shape; say [[x, y], ...] outright
        if "points" in props and isinstance(props["points"], dict):
            props["points"] = {
                "type": "array",
                "description": "board coordinates as [[x, y], [x, y], ...]; y is down",
                "items": {"type": "array", "items": {"type": "number"}, "minItems": 2},
            }
        params["properties"] = props
        params["required"] = [r for r in (params.get("required") or []) if r != "board"]
        out.append({"type": "function",
                    "function": {"name": s["name"], "description": s.get("description", ""),
                                 "parameters": params}})
    return out


def _post_json(base_url: str, key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps({**payload, "stream": False}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def _post_stream(base_url: str, key: str, payload: dict):
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
    )
    return urllib.request.urlopen(req, timeout=180)


class _Cancelled(Exception):
    """Internal control flow for a run the person explicitly stopped."""


class _RunState:
    """Cancellation state shared by the HTTP thread and stop-route thread."""

    def __init__(self):
        self.event = threading.Event()
        self._guard = threading.Lock()
        self._response = None

    def attach(self, response) -> bool:
        with self._guard:
            if self.event.is_set():
                _close_later(response)
                return False
            self._response = response
            return True

    def detach(self, response) -> None:
        with self._guard:
            if self._response is response:
                self._response = None

    def cancel(self) -> None:
        self.event.set()
        with self._guard:
            response = self._response
            self._response = None
        if response is not None:
            _close_later(response)


def _close_later(response) -> None:
    """Close without letting a provider's I/O lock block the stop route."""
    def close():
        try:
            response.close()
        except Exception:
            pass

    threading.Thread(target=close, daemon=True, name="charrette-provider-close").start()


def _check(run: _RunState) -> None:
    if run.event.is_set():
        raise _Cancelled


def _open_cancellable(run: _RunState, opener):
    """Open a provider off-thread so cancellation is not hostage to urlopen()."""
    out = queue.Queue(maxsize=1)

    def work():
        try:
            response = opener()
            if not isinstance(response, dict) and not run.attach(response):
                return
            out.put(("ok", response))
        except BaseException as exc:  # transport errors are returned to the owning thread
            out.put(("error", exc))

    threading.Thread(target=work, daemon=True, name="charrette-provider-open").start()
    while True:
        _check(run)
        try:
            kind, value = out.get(timeout=0.05)
        except queue.Empty:
            continue
        if kind == "error":
            raise value
        return value


def _response_chunks(run: _RunState, response):
    """Read a provider off-thread and expose cancellable, bounded waits."""
    out = queue.Queue()

    def read():
        try:
            with response as opened:
                for raw in opened:
                    out.put(("data", raw))
        except BaseException as exc:
            out.put(("error", exc))
        finally:
            run.detach(response)
            out.put(("done", None))

    threading.Thread(target=read, daemon=True, name="charrette-provider-read").start()
    while True:
        _check(run)
        try:
            kind, value = out.get(timeout=0.05)
        except queue.Empty:
            continue
        _check(run)
        if kind == "data":
            yield value
        elif kind == "error":
            raise value
        else:
            return


class BoardChat:
    """One conversation per board, streamed as server-sent events."""

    def __init__(self, store, schemas: list[dict], handlers: dict):
        self.store = store
        self.schemas = _tool_schema(schemas)
        self.handlers = handlers
        self._locks: dict[str, threading.Lock] = {}
        self._runs: dict[str, _RunState] = {}
        self._guard = threading.Lock()

    # ---------------------------------------------------------------- state
    def _lock(self, bid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(bid, threading.Lock())

    def info(self) -> dict:
        pool = _get_pool()
        routes = pool.describe()
        first = next((r for r in routes if r["ready"]), routes[0] if routes else {})
        return {"backend": "pool", "provider": first.get("name", "none"),
                "model": first.get("model") or _model_name(),
                "ready": bool(routes) and pool.ready(), "source": first.get("source", "missing"),
                "keys": routes, "tools": list(AGENT_TOOLS)}

    def history(self, bid: str) -> list[dict]:
        doc = self.store.read(bid)
        return list(doc.get("chat") or [])

    def reset(self, bid: str) -> None:
        self.store.write(bid, lambda d: {**d, "chat": [], "session": None})

    def cancel(self, bid: str) -> bool:
        """Stop one active board run without affecting conversations elsewhere."""
        with self._guard:
            run = self._runs.get(bid)
        if run is None:
            return False
        run.cancel()
        return True

    def _append(self, bid: str, msg: dict) -> None:
        def apply(doc):
            chat = list(doc.get("chat") or []) + [msg]
            return {**doc, "chat": chat[-HISTORY_CAP:]}
        self.store.write(bid, apply)

    # ---------------------------------------------------------------- run
    def stream(self, bid: str, text: str, view: dict | None = None):
        """Yield SSE frames. Tool calls execute against the store as they arrive."""
        lock = self._lock(bid)
        if not lock.acquire(blocking=False):
            yield _sse({"type": "error", "error": "this board is already answering a message"})
            return
        run = _RunState()
        run_start_rev = self.store.read(bid).get("rev") or 0
        with self._guard:
            self._runs[bid] = run
        try:
            pool = _get_pool()
            if not pool.entries:
                yield _sse({"type": "error",
                            "error": "no model keys: add one with `python3 scripts/keys.py add <name>`, "
                                     "or run `hermes auth` for ollama-cloud"})
                return
            model = _model_name()
            self._append(bid, {"role": "user", "text": text, "at": _now()})
            messages = [{"role": "system", "content": SYSTEM.replace("__BRIEF__", _board_brief(self.store, bid, view))}]
            for m in self.history(bid)[-16:]:
                if m.get("role") in ("user", "assistant") and m.get("text"):
                    messages.append({"role": m["role"], "content": m["text"]})
            yield _sse({"type": "start", "model": model, "keys": len(pool.entries),
                        "session": self._session_id(bid)})

            said = []
            announced = False
            for round_no in range(MAX_ROUNDS + 1):
                _check(run)
                payload_base = {"messages": messages, "stream": True, "temperature": 0.4}
                if round_no < MAX_ROUNDS:
                    payload_base["tools"] = self.schemas
                calls, chunk_text = {}, []
                # a run walks the pool: a 429 or a bad key costs one route, not the message
                resp = None
                route = None
                failures = []
                for candidate in pool.candidates():
                    _check(run)
                    route_model = (candidate["models"] or [model])[0]
                    try:
                        if candidate.get("stream", True):
                            resp = _open_cancellable(
                                run,
                                lambda candidate=candidate, route_model=route_model: _post_stream(
                                    candidate["base_url"], candidate["api_key"],
                                    {**payload_base, "model": route_model}),
                            )
                        else:
                            # Some OpenAI-compatible layers return tool calls as XML text while
                            # streaming. Ask for one JSON body instead, and parse the message.
                            resp = _open_cancellable(
                                run,
                                lambda candidate=candidate, route_model=route_model: _post_json(
                                    candidate["base_url"], candidate["api_key"],
                                    {**payload_base, "model": route_model}),
                            )
                        route = candidate
                        pool.note_hit(candidate["name"])
                        break
                    except _Cancelled:
                        raise
                    except urllib.error.HTTPError as exc:
                        body = exc.read(400).decode("utf-8", "replace").strip()
                        failures.append(f"{candidate['name']} {exc.code}")
                        pool.note_failure(candidate["name"], f"HTTP {exc.code} {body[:120]}")
                    except Exception as exc:  # noqa: BLE001
                        failures.append(f"{candidate['name']} {type(exc).__name__}")
                        pool.note_failure(candidate["name"], f"{type(exc).__name__}: {exc}")
                if resp is None:
                    yield _sse({"type": "error",
                                "error": "no key answered: " + (", ".join(failures) or "pool empty")})
                    return
                if route and not announced:
                    announced = True
                    yield _sse({"type": "route", "name": route["name"], "model": route_model})

                finish = None
                if isinstance(resp, dict):
                    msg = ((resp.get("choices") or [{}])[0].get("message") or {})
                    if msg.get("content"):
                        chunk_text.append(msg["content"])
                        yield _sse({"type": "text", "delta": msg["content"]})
                    for i, tc in enumerate(msg.get("tool_calls") or []):
                        calls[i] = {"id": tc.get("id") or f"call_{i}",
                                    "name": (tc.get("function") or {}).get("name") or "",
                                    "args": (tc.get("function") or {}).get("arguments") or "{}"}
                    finish = ((resp.get("choices") or [{}])[0].get("finish_reason"))
                if not isinstance(resp, dict):
                    for raw in _response_chunks(run, resp):
                        _check(run)
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            evt = json.loads(data)
                        except ValueError:
                            continue
                        choice = (evt.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            chunk_text.append(delta["content"])
                            yield _sse({"type": "text", "delta": delta["content"]})
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]

                if route:
                    pool.note_success(route["name"])
                text_so_far = "".join(chunk_text)
                if text_so_far:
                    said.append(text_so_far)
                    messages.append({"role": "assistant", "content": text_so_far})
                if not calls:
                    break
                messages.append({"role": "assistant", "content": text_so_far or None,
                                 "tool_calls": [
                                     {"id": s["id"] or f"call_{i}", "type": "function",
                                      "function": {"name": s["name"], "arguments": s["args"] or "{}"}}
                                     for i, s in sorted(calls.items())]})
                for i, slot in sorted(calls.items()):
                    _check(run)
                    name = slot["name"]
                    try:
                        args = json.loads(slot["args"] or "{}")
                    except ValueError:
                        args = {}
                    args.pop("board", None)
                    args = _normalise_args(name, args)
                    args["board"] = bid
                    handler = self.handlers.get(name)
                    if not handler:
                        result = {"err": "E_TOOL", "message": f"no such verb: {name}"}
                    else:
                        try:
                            result = handler(args)
                        except Exception as exc:  # noqa: BLE001
                            result = {"err": "E_TOOL", "message": f"{type(exc).__name__}: {exc}"}
                    # verbs answer with a dict or with a plain sentence; normalise both so the
                    # model and the panel see the same thing
                    if isinstance(result, str):
                        result_obj = {"ok": True, "text": result}
                    elif isinstance(result, dict):
                        result_obj = result
                    else:
                        result_obj = {"ok": True, "text": str(result)}
                    brief = {k: v for k, v in result_obj.items()
                             if k in ("ok", "err", "object", "rev", "bbox", "message")}
                    if "text" in result_obj:
                        brief["text"] = str(result_obj["text"])[:160]
                    yield _sse({"type": "tool", "name": name, "args": args, "result": brief})
                    messages.append({"role": "tool", "tool_call_id": slot["id"] or f"call_{i}",
                                     "content": json.dumps(result_obj)[:4000]})

            answer = "\n\n".join(said).strip()
            # which objects this run touched: anything whose rev moved past the rev
            # the run started at, in the order they were first written
            doc_now = self.store.read(bid)
            touched = [(oid, o) for oid, o in (doc_now.get("objects") or {}).items()
                       if (o.get("rev") or 0) > run_start_rev]
            touched.sort(key=lambda kv: (kv[1].get("created") or 0, kv[1].get("rev") or 0))
            # a run that drew but said nothing still has a body of work to replay, so it
            # still gets a message: the replay chip is how the reader sees what it did
            if answer or touched:
                msg = {"role": "assistant", "text": answer, "at": _now()}
                if touched:
                    msg["refs"] = [oid for oid, _ in touched]
                self._append(bid, msg)
            doc = self.store.read(bid)
            yield _sse({"type": "done", "rev": doc.get("rev"), "text": answer})
        except _Cancelled:
            yield _sse({"type": "stopped"})
        except GeneratorExit:
            run.cancel()
            raise
        finally:
            run.cancel()
            with self._guard:
                if self._runs.get(bid) is run:
                    self._runs.pop(bid, None)
            lock.release()

    def _session_id(self, bid: str) -> str:
        doc = self.store.read(bid)
        sid = doc.get("session")
        if sid:
            return sid
        sid = f"ch_{uuid.uuid4().hex[:12]}"
        self.store.write(bid, lambda d: {**d, "session": sid})
        return sid


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _null_iter:
    def __enter__(self):
        return iter(())

    def __exit__(self, *exc):
        return False


def _sse(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj) + "\n\n").encode()
