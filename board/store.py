"""Board state: one JSON document per board plus a registry.

Single writer (`plugin_api.py` process), in-process RLock, every write carries
`if_rev` so a stale writer gets a conflict instead of silently clobbering.
`board.json` is the authority; no oplog replay is needed to read state.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

SCHEMA = "charrette/1"
DEFAULT_BOARD = "b1"


def default_root() -> Path:
    """One location both the plugin half and the dashboard half resolve identically.

    `ctx.state.data_dir` differs between the gateway and the dashboard process,
    so it is deliberately NOT used here.
    """
    env = os.environ.get("CHARRETTE_DATA_DIR")
    if env:
        return Path(env).expanduser()
    home = os.environ.get("HERMES_HOME") or "~/.hermes"
    return Path(home).expanduser() / "charrette"


class RevConflict(Exception):
    def __init__(self, rev: int):
        super().__init__(f"rev conflict at {rev}")
        self.rev = rev


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def empty_board() -> dict:
    return {
        "schema": SCHEMA,
        "rev": 0,
        "camera": {"x": 0.0, "y": 0.0, "z": 1.0},
        "layers": [
            {"id": "agent-diagram", "z": 10},
            {"id": "human-ink", "z": 20},
            {"id": "agent-annotation", "z": 30},
        ],
        "objects": {},
    }


class Store:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else default_root()
        self.boards_dir = self.root / "boards"
        self.boards_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.index_path = self.boards_dir / "index.json"

    # ---------------------------------------------------------------- io
    def _read(self, path: Path, default):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _write(self, path: Path, data) -> None:
        # The data dir can vanish under a long-lived process (a cleanup sweep,
        # a moved home). Re-create it rather than failing every later write
        # with FileNotFoundError on the temp file.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # directory fsync is POSIX-only: on Windows O_DIRECTORY does not exist
        # and os.open on a directory raises, so both paths must be tolerated
        if not hasattr(os, "O_DIRECTORY"):
            return
        try:
            dfd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass

    def _board_path(self, bid: str) -> Path:
        if not bid or "/" in bid or ".." in bid:
            raise ValueError(f"bad board id {bid!r}")
        return self.boards_dir / f"{bid}.json"

    # ------------------------------------------------------------ registry
    def index(self) -> dict:
        with self._lock:
            idx = self._read(self.index_path, None)
            if not idx:
                idx = {"active": DEFAULT_BOARD, "boards": []}
                self._write(self.index_path, idx)
            if not idx.get("boards") and idx.get("active") == DEFAULT_BOARD:
                # first touch: materialise the default board so every surface agrees
                idx["boards"].append(
                    {
                        "id": DEFAULT_BOARD,
                        "name": "board 1",
                        "rev": 0,
                        "objects": 0,
                        "created": _now(),
                        "last_activity": None,
                        "camera": {"x": 0.0, "y": 0.0, "z": 1.0},
                    }
                )
                self._write(self.index_path, idx)
            return idx

    def ensure_board(self, bid: str = DEFAULT_BOARD, name: str | None = None) -> dict:
        with self._lock:
            path = self._board_path(bid)
            if not path.exists():
                self._write(path, empty_board())
            idx = self.index()
            if not any(b["id"] == bid for b in idx["boards"]):
                idx["boards"].append(
                    {
                        "id": bid,
                        "name": name or bid,
                        "rev": 0,
                        "objects": 0,
                        "created": _now(),
                        "last_activity": None,
                        "camera": {"x": 0.0, "y": 0.0, "z": 1.0},
                    }
                )
                self._write(self.index_path, idx)
            return idx

    def new_board(self, name: str | None = None) -> dict:
        with self._lock:
            idx = self.index()
            used = {b["id"] for b in idx["boards"]}
            n = len(used) + 1
            while f"b{n}" in used:
                n += 1
            bid = f"b{n}"
            self.ensure_board(bid, name or f"board {n}")
            idx = self.index()
            idx["active"] = bid
            self._write(self.index_path, idx)
            return idx

    def rename(self, bid: str, name: str) -> dict:
        """Rename a board in the registry and in its own document."""
        with self._lock:
            idx = self.ensure_board(bid)
            for board in idx["boards"]:
                if board["id"] == bid:
                    board["name"] = name
            self._write(self.index_path, idx)
            doc = self.read(bid)
            doc["name"] = name
            self._write(self._board_path(bid), doc)
            return idx

    def set_active(self, bid: str) -> dict:
        with self._lock:
            idx = self.ensure_board(bid)
            idx["active"] = bid
            self._write(self.index_path, idx)
            return idx

    def _touch(self, bid: str, doc: dict, actor: str, object_id: str, bbox) -> None:
        idx = self.index()
        for board in idx["boards"]:
            if board["id"] == bid:
                board["rev"] = doc["rev"]
                board["objects"] = len(doc.get("objects", {}))
                board["camera"] = doc.get("camera", board.get("camera"))
                board["last_activity"] = {
                    "at": _now(),
                    "actor": actor,
                    "object_id": object_id,
                    "bbox": bbox,
                }
                break
        idx["active"] = bid
        self._write(self.index_path, idx)

    # --------------------------------------------------------------- board
    def read(self, bid: str = DEFAULT_BOARD) -> dict:
        with self._lock:
            doc = self._read(self._board_path(bid), None)
            if doc is None:
                self.ensure_board(bid)
                doc = self._read(self._board_path(bid), empty_board())
            return doc

    def write(self, bid: str, mutate, if_rev: int | None = None) -> dict:
        """Apply `mutate(doc)` under the lock. Returns the new document."""
        with self._lock:
            doc = self.read(bid)
            if if_rev is not None and int(if_rev) != int(doc["rev"]):
                raise RevConflict(int(doc["rev"]))
            out = mutate(doc)
            if out is not None:
                doc = out
            doc["schema"] = SCHEMA
            doc["rev"] = int(doc.get("rev", 0)) + 1
            last = doc.pop("_last", None)
            self._write(self._board_path(bid), doc)
            if last:
                obj = doc["objects"].get(last, {})
                self._touch(bid, doc, obj.get("actor", "human"), last, obj.get("bbox"))
                doc["_last"] = last  # returned copy only, never persisted
            else:
                self._repair_activity(bid, doc)
            return doc

    def _repair_activity(self, bid: str, doc: dict) -> None:
        """Keep registry metadata current and repair a deleted activity pointer."""
        idx = self.index()
        objs = doc.get("objects") or {}
        changed = False
        for board in idx["boards"]:
            if board["id"] != bid:
                continue
            act = board.get("last_activity") or {}
            if act and act.get("object_id") not in objs:
                survivor = next(reversed(list(objs))) if objs else None
                board["last_activity"] = (
                    {"at": _now(), "actor": objs[survivor].get("actor", "human"),
                     "object_id": survivor, "bbox": objs[survivor].get("bbox")}
                    if survivor else None)
                changed = True
            metadata = {
                "rev": doc["rev"],
                "objects": len(objs),
                "camera": doc.get("camera", board.get("camera")),
            }
            if any(board.get(key) != value for key, value in metadata.items()):
                board.update(metadata)
                changed = True
        if changed:
            self._write(self.index_path, idx)


def add_stroke(points: list, ink: str = "#111318", width: int = 3, label: str | None = None,
               actor: str = "human"):
    """Return a mutate function that appends one stroke.

    `actor` decides the layer: the human's ink sits above the agent's, so an agent stroke recorded as
    human would silently claim the top layer and break the one promise the board makes.
    """

    def apply(doc: dict) -> dict:
        objs = doc.setdefault("objects", {})
        n = 1
        while f"obj_{n}" in objs:
            n += 1
        oid = f"obj_{n}"
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        objs[oid] = {
            "kind": "stroke",
            "actor": "agent" if actor == "agent" else "human",
            "layer": "agent-diagram" if actor == "agent" else "human-ink",
            "z": f"a{len(objs) + 1}",
            "bbox": [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)],
            "pts": points,
            "ink": ink,
            "w": width,
            "label": label,
            # every write stamps rev and created here, the same way apply_objects does:
            # a stroke without them cannot be ordered by a replay or found by a diff
            "rev": int(doc.get("rev", 0)) + 1,
            "created": _now(),
        }
        doc["_last"] = oid
        return doc

    return apply


def apply_objects(upserts=None, deletes=None):
    """Generic object write used by the editor and by every agent tool."""

    def apply(doc: dict) -> dict:
        objs = doc.setdefault("objects", {})
        for oid in deletes or []:
            objs.pop(oid, None)
        for o in upserts or []:
            # `_key` is the object's map key when a client built the upsert from a fetched object and
            # forgot to copy the id: address it rather than dropping the write silently.
            oid = o.get("id") or o.get("_key")
            if not oid:
                continue
            merged = {**objs.get(oid, {}), **{k: v for k, v in o.items() if k not in ("id", "_key")}}
            # The box is a fact about the ink, not an opinion the writer gets to hold.
            # A moved stroke that kept its old box is why hit tests, selection and framing
            # all pointed at where the object used to be.
            pts = merged.get("pts")
            if merged.get("kind") in ("stroke", "highlight") and isinstance(pts, list) and pts:
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                merged["bbox"] = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
            merged["rev"] = doc["rev"] + 1
            if oid not in objs:
                # creation time is what a replay orders by; z and rev can be reused or rewritten
                merged["created"] = _now()
            objs[oid] = merged
            doc["_last"] = oid
        return doc

    return apply


def add_image(src: str | None, bbox, label=None, actor: str = "agent", source_url=None, title=None,
              status: str = "ready"):
    def apply(doc: dict) -> dict:
        objs = doc.setdefault("objects", {})
        n = 1
        while f"img_{n}" in objs:
            n += 1
        oid = f"img_{n}"
        objs[oid] = {
            "kind": "image",
            "actor": actor,
            "layer": "human-ink" if actor == "human" else "agent-diagram",
            "z": f"a{len(objs) + 1}",
            "bbox": bbox,
            "src": src,
            "label": label,
            "source_url": source_url,
            "title": title,
            "status": status,
        }
        doc["_last"] = oid
        return doc

    return apply


def finish_image(oid: str, src: str | None, bbox=None, source_url=None, title=None,
                 status: str = "ready", seconds=None):
    """Resolve a placeholder image in place, keeping its id and z-order."""

    def apply(doc: dict) -> dict:
        obj = (doc.get("objects") or {}).get(oid)
        if obj is None:
            doc["_last"] = None
            return doc
        obj["src"] = src
        obj["status"] = status
        if bbox:
            obj["bbox"] = bbox
        if source_url:
            obj["source_url"] = source_url
        if title:
            obj["title"] = title
        if seconds is not None:
            obj["seconds"] = seconds
        obj["rev"] = doc["rev"] + 1
        doc["_last"] = oid
        return doc

    return apply


def add_point(u: float, v: float, label=None, style: str = "ring", actor: str = "agent"):
    """A vision-derived point, in board coordinates. Anchored, undoable, inspectable."""

    def apply(doc: dict) -> dict:
        objs = doc.setdefault("objects", {})
        n = 1
        while f"an_{n}" in objs:
            n += 1
        oid = f"an_{n}"
        objs[oid] = {
            "kind": "annotation",
            "actor": actor,
            "layer": "agent-annotation",
            "z": f"a{len(objs) + 1}",
            "bbox": [u - 12, v - 12, 24, 24],
            "point": [u, v],
            "style": style,
            "label": label,
        }
        doc["_last"] = oid
        return doc

    return apply


def free_rect(doc: dict, w: float, h: float, near: dict | None = None, gap: float = 48.0) -> list:
    """Place to the right of `near` if given, else right of everything on the board."""
    objs = doc.get("objects") or {}
    if near is None:
        boxes = [o["bbox"] for o in objs.values() if isinstance(o.get("bbox"), list)]
    else:
        boxes = [near["bbox"]] if isinstance(near.get("bbox"), list) else []
    if not boxes:
        return [0.0, 0.0, float(w), float(h)]
    x1 = max(b[0] + b[2] for b in boxes)
    y0 = min(b[1] for b in boxes)
    return [x1 + gap, y0, float(w), float(h)]
