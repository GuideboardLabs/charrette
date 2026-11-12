"""A chat session attached to a board.

The board is its own piece: it can be opened anywhere the page is served and still be talked to.
This module is the agent half. A turn goes to the Hermes session the plugin is loaded into
(``ctx.llm``), so it uses the user's own active model, whatever it is and however it is
authenticated. No provider is named, no key is read, and no HTTP request is built here.

The model answers with a PLAN, not with native tool calls: ``{"say": ..., "ops": [...]}``. This plugin
executes the ops against the store. That shape is deliberate. Native tool calling is a per-provider
feature, so a client that speaks it works on some providers and quietly degrades on others; asking any
model for JSON works everywhere, including small local ones. One code path, every provider.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid

# Verbs the panel's agent may use. Anything that needs an image pipeline (board_look, board_image)
# stays out until there is a surface that can show its result in the panel.
AGENT_TOOLS = ("board_read", "board_draw", "board_stroke", "board_edit", "board_point", "board_scene")
MAX_ROUNDS = 4
HISTORY_CAP = 40

OPS_CONTRACT = """Answer for a whiteboard, not for a chat window.

Return ONE JSON object and nothing else:
{"say": "<what you tell the user, two sentences at most>", "ops": [{"tool": "<verb>", "args": {...}}]}

Rules:
- "ops" may be empty when the answer is only words.
- Use only these verbs, with only the arguments their schema names.
- A turn may hold several ops; they run in order, and each result comes back to you before your next turn.
- If an op is rejected you will be told why. Fix the args or say what you could not do; never invent success.
- No markdown, no code fences, no prose outside the JSON."""

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



class _Cancelled(Exception):
    """The reader pressed stop."""


class _RunState:
    """One live turn. Cancelling closes nothing but a flag: there is no socket to hang up."""

    def __init__(self):
        self.cancelled = False
        self.started = time.time()

    def cancel(self) -> None:
        self.cancelled = True


def _check(run: _RunState) -> None:
    if run.cancelled:
        raise _Cancelled()


class BoardChat:
    """One conversation per board, streamed as server-sent events."""

    def __init__(self, store, schemas: list[dict], handlers: dict, llm=None):
        self.store = store
        self.schemas = _tool_schema(schemas)
        self.handlers = handlers
        self.llm = llm
        self.provider = ""     # filled from the first turn: the session decides, not a config read
        self.model = ""
        self._locks: dict[str, threading.Lock] = {}
        self._runs: dict[str, _RunState] = {}
        self._guard = threading.Lock()

    # ---------------------------------------------------------------- state
    def _lock(self, bid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(bid, threading.Lock())

    def info(self) -> dict:
        """What the panel shows above the composer. The model comes from Hermes, so it is asked lazily."""
        return {"backend": "hermes", "provider": self.provider or "hermes",
                "model": self.model or "the session's model",
                "ready": self.llm is not None, "source": "hermes session",
                "keys": [], "tools": list(AGENT_TOOLS)}

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

    # ---------------------------------------------------------------- the model
    def _ask(self, convo: list[dict]) -> dict:
        """One turn on the Hermes session. Returns the parsed plan."""
        result = self.llm.complete(convo, temperature=0.4, max_tokens=1400, timeout=180)
        self.provider = getattr(result, "provider", "") or self.provider
        self.model = getattr(result, "model", "") or self.model
        return _parse_plan(result.text)

    # ---------------------------------------------------------------- run
    def stream(self, bid: str, text: str, view: dict | None = None):
        """Yield SSE frames. Ops execute against the store as they are planned."""
        lock = self._lock(bid)
        if not lock.acquire(blocking=False):
            yield _sse({"type": "error", "error": "this board is already answering a message"})
            return
        run = _RunState()
        run_start_rev = self.store.read(bid).get("rev") or 0
        with self._guard:
            self._runs[bid] = run
        try:
            if self.llm is None:
                yield _sse({"type": "error",
                            "error": "the board answers through Hermes; this process has no model seam"})
                return
            self._append(bid, {"role": "user", "text": text, "at": _now()})
            convo = [{"role": "system", "content": SYSTEM.replace("__BRIEF__", _board_brief(self.store, bid, view))},
                     {"role": "system", "content": OPS_CONTRACT}]
            for m in self.history(bid)[-16:]:
                if m.get("role") in ("user", "assistant") and m.get("text"):
                    convo.append({"role": m["role"], "content": m["text"]})
            yield _sse({"type": "start", "model": self.model or "the session's model",
                        "provider": self.provider or "hermes", "session": self._session_id(bid)})

            said = []
            announced_route = False
            for _round in range(MAX_ROUNDS + 1):
                _check(run)
                plan = self._ask(convo)
                if self.provider and not announced_route:
                    announced_route = True
                    yield _sse({"type": "route", "name": self.provider, "model": self.model})
                # a second round often restates what the first one already said; keep the sentence
                # once, or the panel shows the same line growing a beard
                line = str(plan.get("say") or "").strip()
                if line and (not said or line != said[-1]):
                    said.append(line)
                    yield _sse({"type": "text", "delta": line})
                ops = [op for op in (plan.get("ops") or []) if isinstance(op, dict)]
                if not ops:
                    break
                convo.append({"role": "assistant", "content": json.dumps({"say": plan.get("say") or "",
                                                                          "ops": ops})[:4000]})
                results = []
                for op in ops:
                    _check(run)
                    name = str(op.get("tool") or op.get("name") or "")
                    args = op.get("args") if isinstance(op.get("args"), dict) else {}
                    args.pop("board", None)
                    try:
                        args = _normalise_args(name, args)
                        args["board"] = bid
                        handler = self.handlers.get(name)
                        if not handler:
                            result = {"err": "E_TOOL", "message": f"no such verb: {name}"}
                        else:
                            result = handler(args)
                    except Exception as exc:  # noqa: BLE001 — a bad op must not end the turn
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
                    results.append({"tool": name, "result": brief})
                convo.append({"role": "user",
                              "content": "results: " + json.dumps(results)[:4000]})

            answer = "\n\n".join(said).strip()
            # which objects this run touched: anything whose rev moved past the rev the run started
            # at, in the order it was first written
            doc_now = self.store.read(bid)
            touched = [(oid, o) for oid, o in (doc_now.get("objects") or {}).items()
                       if (o.get("rev") or 0) > run_start_rev]
            touched.sort(key=lambda kv: (kv[1].get("created") or 0, kv[1].get("rev") or 0))
            # a run that drew but said nothing still has a body of work to replay, so it still gets a
            # message: the replay chip is how the reader sees what it did
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


def _parse_plan(text: str) -> dict:
    """Take the model's JSON plan, tolerating fences and a sentence wrapped around it."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        got = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            # no JSON at all: treat the whole reply as the sentence, so the turn is not lost
            return {"say": raw[:1200], "ops": []}
        try:
            got = json.loads(raw[start:end + 1])
        except ValueError:
            return {"say": raw[:1200], "ops": []}
    if not isinstance(got, dict):
        return {"say": raw[:1200], "ops": []}
    say = got.get("say") or got.get("text") or ""
    ops = got.get("ops")
    if ops is None:
        ops = got.get("calls") or []
    if not isinstance(ops, list):
        ops = []
    return {"say": str(say), "ops": ops}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sse(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj) + "\n\n").encode()
