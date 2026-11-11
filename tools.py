"""Charrette tool handlers: the agent's half of the board.

Ten verbs, one job each. Every handler returns a short text block the model
reads directly, in one grammar: `ok ...`, `err CODE ...`, or the board digest.
"""

from __future__ import annotations

import json
import io
import re
import shutil
import threading
import time
from pathlib import Path

try:  # Pillow ships with Hermes on 3.14; declared here so older installs get it
    from PIL import Image
except Exception:  # pragma: no cover - degraded mode is tested by import failure
    Image = None

from .board import image as image_mod
from .board.render import render_crop
from .board.replay import write_replay
from .board.store import (
    DEFAULT_BOARD,
    RevConflict,
    Store,
    add_image,
    add_stroke,
    apply_objects,
    finish_image,
    free_rect,
)

MAX_NODES = 9

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
            "actor": {"type": "string", "enum": ["agent", "human"], "default": "agent",
                      "description": "whose ink this is; agent ink sits under the human's"},
                    "u": {"type": "number"},
                    "v": {"type": "number"},
                },
                "required": ["label", "u", "v"],
            },
        },
    },
    "required": ["description"],
}

VISION_PROMPT = (
    "You are looking at a crop of a shared whiteboard, drawn by hand. "
    "Describe what it is in one short sentence. If asked to locate something, "
    "return each point as u,v in a 0-1000 space of THIS image, (0,0) top-left, "
    "(1000,1000) bottom-right, with a short label. Be conservative: no point "
    "unless you can see it."
)

SCHEMA_READ = {
    "name": "board_read",
    "description": (
        "Read the shared whiteboard: which boards exist, what is on the active one, "
        "where the newest activity is. Call this before drawing so your refs are real ids."
    ),
    "parameters": {
        "type": "object",
        "properties": {"board": {"type": "string"}, "limit": {"type": "integer", "default": 40}},
    },
}

SCHEMA_DRAW = {
    "name": "board_draw",
    "description": (
        "Draw on the shared board. kind=diagram places a laid-out node/edge diagram beside "
        "something; kind=annotate marks an existing object by id; kind=image puts a picture on "
        "the board. Never pass coordinates: geometry is laid out for you."
    ),
    "parameters": {
        "type": "object",
        "required": ["kind"],
        "properties": {
            "kind": {"enum": ["diagram", "annotate", "image", "shape", "table", "chart"]},
            "rows": {"type": "integer", "default": 3, "description": "kind=table: row count"},
            "cols": {"type": "integer", "default": 3, "description": "kind=table: column count"},
            "cells": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                      "description": "kind=table: row-major cell text, e.g. [[\"item\",\"cost\"],[\"lumber\",\"120\"]]"},
            "chart": {"enum": ["bar", "line"], "description": "kind=chart: bar or line"},
            "values": {"type": "array", "items": {"type": "number"}, "description": "kind=chart: the series"},
            "labels": {"type": "array", "items": {"type": "string"}, "description": "kind=chart: one label per value"},
            "title": {"type": "string", "description": "kind=chart: caption above the plot"},
            "shape": {
                "enum": ["rect", "ellipse", "diamond", "triangle", "poly"],
                "description": "kind=shape: geometry to fill or outline; poly traces the target's own points",
            },
            "over": {"type": "string", "description": "kind=shape: id of the object whose region to cover"},
            "fill": {"type": "string", "description": "kind=shape: CSS colour to fill with, e.g. #D92D20; omit for outline only"},
            "ink": {"type": "string", "description": "kind=shape: CSS colour of the outline"},
            "width": {"type": "integer", "description": "kind=shape: outline width in board units"},
            "board": {"type": "string"},
            "place": {
                "type": "object",
                "properties": {
                    "near": {"type": "string", "description": "object id, 'newest', or 'cursor'"},
                    "side": {"enum": ["right", "left", "above", "below"]},
                    "gap": {"type": "integer", "default": 64},
                },
            },
            "spec": {"type": "object", "description": "kind=diagram: {dir, nodes:[{key,label,shape?,tone?}], edges:[{from,to,label?,style?}]}"},
            "targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "style": {"enum": ["circle", "arrow", "ring", "cross", "dim"]},
                        "label": {"type": "string"},
                    },
                },
                "description": "kind=annotate",
            },
            "title": {"type": "string"},
            "image": {"type": "object", "description": "kind=image: forwarded to board_image"},
        },
    },
}

SCHEMA_EDIT = {
    "name": "board_edit",
    "description": (
        "Change what you drew: undo your last transaction, erase one of your own objects, "
        "relayout a diagram, restyle it, or relabel it. You may never edit the human's ink."
    ),
    "parameters": {
        "type": "object",
        "required": ["op"],
        "properties": {
            "op": {"enum": ["undo", "erase", "relayout", "restyle", "label"]},
            "board": {"type": "string"},
            "target": {"type": "string"},
            "direction": {"enum": ["LR", "TB"]},
            "tone": {"enum": ["neutral", "good", "bad", "warn", "highlight"]},
            "text": {"type": "string"},
        },
    },
}

SCHEMA_POINT = {
    "name": "board_point",
    "description": (
        "Mark points on the board (rings, crosses) using coordinates a vision call gave you, "
        "in 0-1000 space of a crop. The plugin projects them onto the board."
    ),
    "parameters": {
        "type": "object",
        "required": ["points"],
        "properties": {
            "points": {
                "type": "array", "minItems": 1, "maxItems": 8,
                "items": {
                    "type": "object", "required": ["u", "v"],
                    "properties": {"u": {"type": "number"}, "v": {"type": "number"},
                                   "label": {"type": "string"}},
                },
            },
            "ref": {"type": "string"},
            "crop": {"type": "array", "items": {"type": "number"}},
            "board": {"type": "string"},
        },
    },
}

SCHEMA_LOOK = {
    "name": "board_look",
    "description": (
        "Look at a region with vision: name what the human drew, and locate parts of it. "
        "mark=true puts rings on what you find. want=objects reads without a vision call."
    ),
    "parameters": {
        "type": "object",
        "required": ["region"],
        "properties": {
            "region": {"type": "object", "properties": {
                "ref": {"type": "string", "description": "object id or 'newest'"},
                "bbox": {"type": "array", "items": {"type": "number"}},
                "pad": {"type": "number", "default": 24},
            }},
            "want": {"enum": ["objects", "pixels", "both"], "default": "both"},
            "ask": {"type": "string"},
            "mark": {"type": "boolean", "default": False},
            "board": {"type": "string"},
        },
    },
}

SCHEMA_ASK = {
    "name": "board_ask",
    "description": (
        "Ask a question ON the board, anchored to an object, with up to three options. "
        "The human can click instead of typing; the answer shows on the card and in board_read."
    ),
    "parameters": {
        "type": "object",
        "required": ["question", "options"],
        "properties": {
            "question": {"type": "string"},
            "options": {
                "type": "array", "maxItems": 3,
                "items": {"type": "object", "required": ["id", "label"],
                          "properties": {"id": {"type": "string"}, "label": {"type": "string"}}},
            },
            "anchor": {"type": "object", "properties": {"ref": {"type": "string"}}},
            "board": {"type": "string"},
        },
    },
}

SCHEMA_IMAGE = {
    "name": "board_image",
    "description": (
        "Put an image on the board: search the web, generate from a prompt, edit the human's "
        "own drawing into a new image (img2img, e.g. a sketch into a photo), or take a URL or "
        "local path. Long renders reserve the space and land when ready, so keep talking."
    ),
    "parameters": {
        "type": "object",
        "required": ["source"],
        "properties": {
            "source": {"enum": ["search", "generate", "edit", "url", "path"]},
            "query": {"type": "string"},
            "prompt": {"type": "string"},
            "url": {"type": "string"},
            "path": {"type": "string"},
            "ref": {"type": "string", "description": "source=edit: object id or 'newest'"},
            "size": {"type": "string"},
            "steps": {"type": "integer", "minimum": 1, "maximum": 20},
            "label": {"type": "string"},
            "width": {"type": "integer", "default": 420},
            "place": {"type": "object", "properties": {
                "near": {"type": "string"}, "gap": {"type": "integer", "default": 48}}},
            "board": {"type": "string"},
        },
    },
}

SCHEMA_STROKE = {
    "name": "board_stroke",
    "description": "Append one freehand stroke, in board px. Used for tests and for underlining by hand.",
    "parameters": {
        "type": "object",
        "required": ["points"],
        "properties": {
            "points": {"type": "array", "minItems": 2,
                       "items": {"type": "array", "minItems": 2, "maxItems": 2,
                                 "items": {"type": "number"}}},
            "board": {"type": "string"},
            "ink": {"type": "string"},
            "width": {"type": "integer", "default": 3},
            "label": {"type": "string"},
            "if_rev": {"type": "integer"},
        },
    },
}

SCHEMA_CAPTURE = {
    "name": "board_capture",
    "description": (
        "Save a picture of part of the board as a PNG, rendered headlessly (no browser, no tab). "
        "Frame it with object ids or an explicit bbox, or omit both for the whole board. The path "
        "that comes back can be shown to the user, embedded in an artifact, or fed to an image model."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "board": {"type": "string"},
            "refs": {"type": "array", "items": {"type": "string"},
                     "description": "object ids to frame; default is the whole board"},
            "bbox": {"type": "array", "items": {"type": "number"},
                     "description": "explicit [x, y, w, h] in board units, overrides refs"},
            "scale": {"type": "number", "default": 2, "description": "output pixels per board unit"},
            "grid": {"type": "boolean", "default": True, "description": "include the dot grid"},
            "name": {"type": "string", "description": "file stem; default <board>-<timestamp>"},
        },
    },
}

SCHEMA_ASSETS = {
    "name": "board_assets",
    "description": (
        "Collect everything needed to build an artifact from a board into one folder: every image "
        "on the board, a full-board capture, the board document itself, and manifest.json naming "
        "each file with its object id, label and size."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "board": {"type": "string"},
            "name": {"type": "string", "description": "folder stem; default <board>-<timestamp>"},
            "scale": {"type": "number", "default": 2},
            "capture": {"type": "boolean", "default": True},
        },
    },
}

SCHEMA_SCENE = {
    "name": "board_scene",
    "description": (
        "Record what you just drew as a scene and write a replay page for it. The page draws the "
        "objects in order at human pen speed, starts when it scrolls into view, and is a single "
        "self-contained file: put it in your reply with ::preview{file=\"<path>\"} beside the "
        "paragraph that describes it, so the reader watches the board being made as they read."
    ),
    "parameters": {
        "type": "object",
        "required": ["text"],
        "properties": {
            "board": {"type": "string"},
            "text": {"type": "string",
                     "description": "the sentence this scene answers; it is the replay caption"},
            "refs": {"type": "array", "items": {"type": "string"},
                     "description": "object ids in the order they should be drawn; default: everything created since the last scene"},
            "speed": {"type": "number", "default": 1.0,
                      "description": "1.0 is human pen speed; 0.6 is slower"},
        },
    },
}

SCHEMAS = [SCHEMA_READ, SCHEMA_DRAW, SCHEMA_EDIT, SCHEMA_POINT, SCHEMA_LOOK,
           SCHEMA_ASK, SCHEMA_IMAGE, SCHEMA_STROKE, SCHEMA_CAPTURE, SCHEMA_ASSETS,
           SCHEMA_SCENE]


def _ok(**kw) -> str:
    return "ok " + " ".join(f"{k}={v}" for k, v in kw.items() if v is not None)


def _err(code: str, message: str, **kw) -> str:
    tail = " ".join(f"{k}={v}" for k, v in kw.items() if v is not None)
    return f"err {code} {message}" + (f" {tail}" if tail else "")


def _line(oid: str, obj: dict) -> str:
    bits = [f"{oid:<7}", f"{str(obj.get('kind', '?')):<9}"]
    bb = obj.get("bbox")
    if isinstance(bb, list) and len(bb) == 4:
        bits.append("bbox=" + ",".join(str(int(round(v))) for v in bb))
    elif obj.get("kind") == "scene":
        spec = obj.get("spec") or {}
        bits.append(f"spec={spec.get('type', 'diagram')}:{spec.get('dir', 'LR')} "
                    f"nodes={len(spec.get('nodes') or [])} edges={len(spec.get('edges') or [])}")
    if obj.get("label"):
        bits.append('label="' + str(obj["label"])[:40] + '"')
    if obj.get("text"):
        bits.append('text="' + str(obj["text"])[:48].replace("\n", " / ") + '"')
    if obj.get("question"):
        bits.append('q="' + str(obj["question"])[:60] + '"')
        bits.append("options=" + ",".join(o.get("id", "") for o in obj.get("options") or []))
    if obj.get("answer"):
        bits.append("answer=" + str(obj["answer"]))
    if obj.get("status") and obj.get("status") != "ready":
        bits.append("status=" + str(obj["status"]))
    if obj.get("kind") == "arrow":
        bits.append("from=" + str((obj.get("from") or {}).get("bind") or "pt"))
        bits.append("to=" + str((obj.get("to") or {}).get("bind") or "pt"))
    return " ".join(bits)


def _newest_human(doc: dict):
    best = None
    for oid, obj in (doc.get("objects") or {}).items():
        if obj.get("actor") == "human":
            best = (oid, obj)
    return best


def _next_tx(doc: dict) -> int:
    return int(doc.get("tx", 0)) + 1


def make_handlers(store: Store, dispatch=None, llm=None) -> dict:
    images_dir = store.root / "images"

    # ------------------------------------------------------------------ read
    def board_read(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        idx = store.index()
        limit = int(params.get("limit") or 40)
        boards = " · ".join(
            f"{b['id']} {b['name'][:20]} ({b['objects']} obj" +
            (f", newest {b['last_activity']['object_id']} by {b['last_activity']['actor']}"
             if b.get("last_activity") else "") + ")"
            for b in idx.get("boards", []))
        objs = list((doc.get("objects") or {}).items())
        rows = [_line(oid, obj) for oid, obj in objs[-limit:]]
        head = (f'<boards active="{bid}" count="{len(idx.get("boards", []))}">{boards}</boards>\n'
                f'<board id="{bid}" rev="{doc.get("rev", 0)}" objects="{len(objs)}">')
        return head + "\n" + "\n".join(rows) + "\n</board>"

    # ------------------------------------------------------------------ draw
    def _place_anchor(doc, place):
        near_id = (place or {}).get("near")
        if not near_id:
            return None
        if near_id in ("newest", "cursor"):
            hit = _newest_human(doc)
            if not hit:
                return None
            near_id, obj = hit
            return {"to": near_id, "side": (place or {}).get("side") or "right",
                    "gap": int((place or {}).get("gap") or 64)}
        if near_id not in (doc.get("objects") or {}):
            return None
        return {"to": near_id, "side": (place or {}).get("side") or "right",
                "gap": int((place or {}).get("gap") or 64)}

    def board_draw(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        kind = params.get("kind")
        doc = store.read(bid)

        if kind == "shape":
            shape = str(params.get("shape") or "")
            if shape not in ("rect", "ellipse", "diamond", "triangle", "poly"):
                return _err("E_ARGS", "kind=shape: shape must be rect, ellipse, diamond, triangle or poly")
            ref = str(params.get("over") or "")
            target = (doc.get("objects") or {}).get(ref)
            if not target:
                near = [k for k in (doc.get("objects") or {}) if k.startswith("obj_")][-3:]
                return _err("E_NO_OBJECT", f'no object "{ref}"', nearest=",".join(near) or "none")
            if not target.get("bbox"):
                return _err("E_ARGS", f'object "{ref}" has no bbox to fill')
            tx = _next_tx(doc)
            oid = f"s_{tx}"
            obj = {"id": oid, "kind": "shape", "shape": shape, "actor": "agent",
                   "layer": "agent-diagram", "z": f"a{len(doc.get('objects') or {}) + 1}",
                   "bbox": list(target["bbox"]), "tx": tx, "source_ref": ref}
            if shape == "poly" and target.get("pts"):
                obj["pts"] = [list(p) for p in target["pts"]]
            if params.get("fill"):
                obj["fill"] = str(params["fill"])
            if params.get("ink"):
                obj["ink"] = str(params["ink"])
            if params.get("width"):
                obj["w"] = int(params["width"])
            new = store.write(bid, apply_objects([obj]))
            new = store.write(bid, lambda d: {**d, "tx": tx})
            return _ok(object=oid, shape=shape, over=ref, filled=bool(obj.get("fill")),
                       points=len(obj.get("pts") or []), rev=new["rev"])

        if kind in ("table", "chart"):
            size = (420.0, 200.0) if kind == "table" else (380.0, 250.0)
            anchor = _place_anchor(doc, params.get("place") or {})
            target = (doc.get("objects") or {}).get(anchor["to"]) if anchor else None
            bb = (target or {}).get("bbox")
            place = params.get("place") or {}
            side = place.get("side") or "right"
            gap = int(place.get("gap") or 64)
            if bb:
                if side == "left":
                    box = [bb[0] - gap - size[0], bb[1], size[0], size[1]]
                elif side == "below":
                    box = [bb[0], bb[1] + bb[3] + gap, size[0], size[1]]
                elif side == "above":
                    box = [bb[0], bb[1] - gap - size[1], size[0], size[1]]
                else:
                    box = [bb[0] + bb[2] + gap, bb[1], size[0], size[1]]
            else:
                boxes = [o["bbox"] for o in (doc.get("objects") or {}).values() if o.get("bbox")]
                if boxes:
                    box = [min(b[0] for b in boxes), max(b[1] + b[3] for b in boxes) + gap,
                           size[0], size[1]]
                else:
                    box = [80.0, 80.0, size[0], size[1]]
            tx = _next_tx(doc)
            oid = f"tb_{tx}" if kind == "table" else f"ch_{tx}"
            obj = {"id": oid, "kind": kind, "actor": "agent", "layer": "agent-diagram",
                   "z": f"a{len(doc.get('objects') or {}) + 1}", "bbox": [round(v, 1) for v in box],
                   "tx": tx, "ink": params.get("ink") or "#0F766E", "w": 1}
            if kind == "table":
                rows = max(1, min(20, int(params.get("rows") or 3)))
                cols = max(1, min(8, int(params.get("cols") or 3)))
                given = params.get("cells") or []
                cells = []
                for r in range(rows):
                    row = list(given[r])[:cols] if r < len(given) else []
                    row += [""] * (cols - len(row))
                    cells.append([str(c)[:60] for c in row])
                obj.update({"rows": rows, "cols": cols, "cells": cells})
            else:
                values = [float(v) for v in (params.get("values") or []) if str(v).strip() != ""]
                if not values:
                    return _err("E_ARGS", "kind=chart needs values, e.g. [4, 9, 2, 7]")
                labels = [str(x)[:16] for x in (params.get("labels") or [])][:len(values)]
                obj.update({"chart": params.get("chart") or "bar", "values": values[:40],
                            "labels": labels})
                if params.get("title"):
                    obj["title"] = str(params["title"])[:60]
            new = store.write(bid, apply_objects([obj]))
            new = store.write(bid, lambda d: {**d, "tx": tx})
            return _ok(object=oid, kind=kind, bbox=",".join(f"{v:.0f}" for v in obj["bbox"]),
                       rev=new["rev"])

        if kind == "image":
            return handler_image({**(params.get("image") or {}), "board": bid})

        anchor = _place_anchor(doc, params.get("place") or {})
        tx = _next_tx(doc)

        if kind == "diagram":
            spec = dict(params.get("spec") or {})
            nodes = (spec.get("nodes") or [])[:MAX_NODES]
            if not nodes:
                return _err("E_ARGS", "spec.nodes is required for kind=diagram")
            spec["nodes"] = [{**n, "label": str(n.get("label") or "")[:28]} for n in nodes]
            spec["edges"] = (spec.get("edges") or [])[:24]
            oid = f"d_{tx}"
            obj = {"id": oid, "kind": "scene", "actor": "agent", "layer": "agent-diagram",
                   "z": f"a{len(doc.get('objects') or {}) + 1}", "spec": spec, "tx": tx,
                   "bbox": None}
            obj["anchor"] = anchor if anchor else None
            if not anchor:
                obj["placement"] = {"at": [0, 0]}
            new = store.write(bid, apply_objects([obj]))
            new = store.write(bid, lambda d: {**d, "tx": tx})
            return _ok(object=oid, diagram=True, nodes=len(spec["nodes"]),
                       edges=len(spec["edges"]), placement="pending", rev=new["rev"])

        if kind == "annotate":
            targets = params.get("targets") or []
            if not targets:
                return _err("E_ARGS", "kind=annotate needs targets")
            upserts, made = [], []
            for i, t in enumerate(targets[:8]):
                ref = t.get("ref")
                obj = (doc.get("objects") or {}).get(ref)
                if not obj:
                    cand = [k for k in (doc.get("objects") or {}) if k.startswith("obj_")][-3:]
                    return _err("E_NO_OBJECT", f'no object "{ref}"',
                                nearest=",".join(cand) or "none")
                bb = obj.get("bbox") or [0, 0, 40, 40]
                node_key = t.get("node")
                if node_key:
                    node_box = (obj.get("nodes") or {}).get(node_key)
                    if not node_box:
                        return _err("E_NODE", f'no node "{node_key}" in {ref}',
                                    nodes=",".join(sorted((obj.get("nodes") or {}).keys())) or "none")
                    bb = node_box
                style = t.get("style") or "circle"
                label = str(t.get("label") or "")[:40] or None
                if style == "arrow":
                    oid = f"a_{tx}{i}"
                    upserts.append({"id": oid, "kind": "arrow", "actor": "agent",
                                    "layer": "agent-annotation", "z": f"a{200 + i}", "tx": tx,
                                    "from": {"point": [bb[0] - 70, bb[1] + bb[3] / 2]},
                                    "to": {"bind": ref, "port": "auto"},
                                    "label": label, "ink": "#0F766E", "w": 2})
                elif style in ("ring", "cross"):
                    oid = f"an_{tx}{i}"
                    upserts.append({"id": oid, "kind": "annotation", "actor": "agent",
                                    "layer": "agent-annotation", "z": f"a{200 + i}", "tx": tx,
                                    "point": [bb[0] + bb[2] + 14, bb[1] + 10],
                                    "style": style, "label": label})
                else:
                    oid = f"s_{tx}{i}"
                    pad = 16
                    upserts.append({"id": oid, "kind": "shape", "shape": "ellipse",
                                    "actor": "agent", "layer": "agent-annotation",
                                    "z": f"a{200 + i}", "tx": tx,
                                    "bbox": [bb[0] - pad, bb[1] - pad, bb[2] + 2 * pad, bb[3] + 2 * pad],
                                    "ink": "#0F766E" if style != "dim" else "#5C6B70", "w": 2,
                                    "label": label})
                made.append(oid)
            new = store.write(bid, apply_objects(upserts))
            new = store.write(bid, lambda d: {**d, "tx": tx})
            return _ok(objects=",".join(made), on=",".join(t.get("ref", "") for t in targets[:8]),
                       rev=new["rev"])

        return _err("E_ARGS", f"unknown kind {kind!r}")

    # ------------------------------------------------------------------ edit
    def board_edit(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        op = params.get("op")
        doc = store.read(bid)
        objs = doc.get("objects") or {}

        if op == "undo":
            txs = [int(o.get("tx") or 0) for o in objs.values() if o.get("actor") == "agent"]
            if not txs:
                return _err("E_NOTHING", "nothing of yours to undo")
            top = max(txs)
            doomed = [k for k, o in objs.items()
                      if o.get("actor") == "agent" and int(o.get("tx") or 0) == top]
            human = sum(1 for o in objs.values() if o.get("actor") == "human")
            new = store.write(bid, apply_objects(deletes=doomed))
            return _ok(undo=f"tx{top}", removed=len(doomed), human_untouched=human,
                       rev=new["rev"])

        target = params.get("target")
        if not target:
            return _err("E_ARGS", f"op={op} needs a target id")
        obj = objs.get(target)
        if not obj:
            return _err("E_NO_OBJECT", f'no object "{target}"')
        if obj.get("actor") != "agent":
            return _err("E_NOT_YOURS", f"{target} is human ink; annotate it instead")

        if op == "erase":
            new = store.write(bid, apply_objects(deletes=[target]))
            return _ok(object=target, erased=True, rev=new["rev"])

        patch = {"id": target}
        if op == "relayout":
            spec = dict(obj.get("spec") or {})
            spec["dir"] = params.get("direction") or ("TB" if spec.get("dir") == "LR" else "LR")
            patch["spec"] = spec
            patch["bbox"] = None
        elif op == "restyle":
            tone = params.get("tone") or "neutral"
            patch["tone"] = tone
            spec = dict(obj.get("spec") or {})
            if spec.get("nodes"):
                spec["nodes"] = [{**n, "tone": tone} for n in spec["nodes"]]
                patch["spec"] = spec
        elif op == "label":
            patch["label"] = str(params.get("text") or "")[:40]
        else:
            return _err("E_ARGS", f"unknown op {op!r}")
        new = store.write(bid, apply_objects([patch]))
        return _ok(object=target, op=op, rev=new["rev"])

    # ----------------------------------------------------------------- point
    def board_point(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        crop = params.get("crop")
        ref = params.get("ref")
        if not crop:
            if ref in (None, "newest", "cursor"):
                hit = _newest_human(doc)
                crop = hit[1].get("bbox") if hit else None
            else:
                crop = ((doc.get("objects") or {}).get(ref) or {}).get("bbox")
        if not crop:
            return _err("E_NO_OBJECT", "no crop and no anchor object to project into")
        x, y, w, h = [float(v) for v in crop]
        tx = _next_tx(doc)
        upserts, outs = [], []
        for i, p in enumerate(params.get("points") or []):
            bx = x + (float(p.get("u", 0)) / 1000.0) * w
            by = y + (float(p.get("v", 0)) / 1000.0) * h
            oid = f"an_{tx}{i}"
            upserts.append({"id": oid, "kind": "annotation", "actor": "agent",
                            "layer": "agent-annotation", "z": f"a{300 + i}", "tx": tx,
                            "point": [round(bx, 1), round(by, 1)], "style": "ring",
                            "label": str(p.get("label") or "")[:28] or None})
            outs.append(f"{oid}@({round(bx)},{round(by)})")
        new = store.write(bid, apply_objects(upserts))
        new = store.write(bid, lambda d: {**d, "tx": tx})
        return _ok(points=",".join(outs), crop=",".join(str(int(v)) for v in crop),
                   rev=new["rev"])

    # ------------------------------------------------------------------ look
    def _region(params, doc):
        region = params.get("region") or {}
        if region.get("bbox"):
            return [float(v) for v in region["bbox"]], None
        ref = region.get("ref")
        if ref in (None, "newest", "cursor"):
            hit = _newest_human(doc)
            return (hit[1].get("bbox"), hit[0]) if hit else (None, None)
        obj = (doc.get("objects") or {}).get(ref)
        return (obj.get("bbox") if obj else None), ref

    def board_look(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        box, ref = _region(params, doc)
        if not box:
            return _err("E_NO_OBJECT", "nothing to look at on this board")
        want = params.get("want") or "both"
        lines = []
        if want in ("objects", "both"):
            for oid, obj in list((doc.get("objects") or {}).items())[-12:]:
                lines.append(_line(oid, obj))
        head = (f'<region ref="{ref or "bbox"}" crop="'
                + ",".join(str(int(v)) for v in box) + '">')
        out = head + ("\n" + "\n".join(lines) if lines else "") + "\n</region>"
        if want == "objects":
            return out
        if llm is None:
            return out + "\nerr E_NO_VISION no vision route available; reason from labels only"
        png, rect, _scale = render_crop(doc, bbox=box, images_dir=images_dir)
        try:
            res = llm.complete_structured(
                instructions=VISION_PROMPT,
                input=[
                    {"type": "text",
                     "text": f"Task: {params.get('ask') or 'What is this?'}\n"
                             f"The crop is {int(rect[2])}x{int(rect[3])} board px."},
                    {"type": "image", "data": png, "mime_type": "image/png"},
                ],
                json_schema=VISION_SCHEMA,
                system_prompt="You describe and locate things on a hand-drawn whiteboard.",
                purpose="charrette-look",
                timeout=90,
            )
            parsed = getattr(res, "parsed", None) or json.loads(getattr(res, "text", "{}") or "{}")
        except Exception as exc:
            return out + f"\nerr E_VISION {str(exc)[:140]}"
        desc = str(parsed.get("description") or "").strip()
        pts = parsed.get("points") or []
        out += f"\nvision: {desc}"
        if pts:
            tx = _next_tx(doc)
            upserts, marked = [], []
            x, y, w, h = rect
            for i, p in enumerate(pts[:8]):
                bx = x + (float(p.get("u", 0)) / 1000.0) * w
                by = y + (float(p.get("v", 0)) / 1000.0) * h
                oid = f"an_{tx}{i}"
                upserts.append({"id": oid, "kind": "annotation", "actor": "agent",
                                "layer": "agent-annotation", "z": f"a{320 + i}", "tx": tx,
                                "point": [round(bx, 1), round(by, 1)], "style": "ring",
                                "label": str(p.get("label") or "")[:28] or None})
                marked.append(f'{oid} "{p.get("label")}"@({round(bx)},{round(by)})')
            if params.get("mark"):
                store.write(bid, apply_objects(upserts))
                store.write(bid, lambda d: {**d, "tx": tx})
                out += "\nmarked: " + "; ".join(marked)
            else:
                out += ("\npoints (call board_point with crop="
                        + ",".join(str(int(v)) for v in rect) + "): " + "; ".join(marked))
        return out

    # ------------------------------------------------------------------- ask
    def board_ask(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        q = str(params.get("question") or "")[:90]
        opts = (params.get("options") or [])[:3]
        if not q or not opts:
            return _err("E_ARGS", "question and at least one option are required")
        ref = (params.get("anchor") or {}).get("ref")
        obj = (doc.get("objects") or {}).get(ref) if ref else None
        bb = (obj or {}).get("bbox") or [0, 0, 200, 60]
        tx = _next_tx(doc)
        oid = f"q_{tx}"
        card = {"id": oid, "kind": "question", "actor": "agent", "layer": "agent-annotation",
                "z": "a340", "tx": tx, "question": q,
                "options": [{"id": str(o.get("id"))[:12], "label": str(o.get("label"))[:28]}
                            for o in opts],
                "anchor": {"to": ref, "side": "right", "gap": 40} if ref else None,
                "bbox": [bb[0] + bb[2] + 52, bb[1] + 18, 256, 34 + 26 * len(opts)],
                "answer": None}
        new = store.write(bid, apply_objects([card]))
        new = store.write(bid, lambda d: {**d, "tx": tx})
        return _ok(question=oid, options=",".join(o["id"] for o in card["options"]),
                   rev=new["rev"], note="the human can click an option on the board")

    # ----------------------------------------------------------------- image
    def _aspect(path: Path, default: float = 0.5625) -> float:
        if Image is None:
            return default
        try:
            with Image.open(path) as im:
                return im.height / max(1, im.width)
        except Exception:
            return default

    def handler_image(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        src_kind = params.get("source")
        label = (params.get("label") or "")[:28] or None
        target_w = int(params.get("width") or 420)
        place = params.get("place") or {}
        gap = float(place.get("gap") or 48)
        near = None
        if place.get("near"):
            ref_id = place["near"]
            if ref_id in ("newest", "cursor"):
                hit = _newest_human(doc)
                near = hit[1] if hit else None
            else:
                near = (doc.get("objects") or {}).get(ref_id)

        if src_kind in ("search", "url", "path"):
            try:
                if src_kind == "search":
                    got = image_mod.search(params.get("query") or "", images_dir)
                elif src_kind == "url":
                    got = image_mod.fetch_url(params.get("url") or "", images_dir)
                else:
                    got = image_mod.read_path(params.get("path") or "", images_dir)
            except image_mod.ImageError as exc:
                return _err(exc.code, exc.message, **exc.extra)
            aspect = _aspect(images_dir / got["src"])
            bbox = free_rect(doc, target_w, target_w * aspect, near=near, gap=gap)
            new = store.write(bid, add_image(got["src"], bbox, label=label,
                                             source_url=got.get("source_url"),
                                             title=got.get("title"), status="ready"))
            return _ok(object=new.get("_last"), src=got["src"],
                       bbox=",".join(str(int(v)) for v in bbox),
                       source=got.get("source_url"), rev=new["rev"])

        if src_kind not in ("generate", "edit"):
            return _err("E_ARGS", f"unknown source {src_kind!r}")

        ref_png = None
        if src_kind == "edit":
            ref = params.get("ref") or "newest"
            if ref in ("newest", "cursor"):
                hit = _newest_human(doc)
                if not hit:
                    return _err("E_NO_OBJECT", "nothing on the board to edit")
                ids = [hit[0]]
            else:
                ids = [ref]
            if ids[0] not in (doc.get("objects") or {}):
                return _err("E_NO_OBJECT", f"no such object {ids[0]!r}")
            ref_png, _rect, _scale = render_crop(doc, ids=ids, images_dir=images_dir)

        placeholder = free_rect(doc, target_w, target_w * 0.5625, near=near, gap=gap)
        holder = store.write(bid, add_image(None, placeholder, label=label, status="rendering"))
        oid = holder.get("_last")
        if not oid:
            return _err("E_STATE", "could not reserve a place on the board")
        prompt = params.get("prompt") or ""
        size = params.get("size")
        steps = params.get("steps") or image_mod.default_steps(store.root)

        def work():
            try:
                if src_kind == "generate":
                    got = image_mod.generate(prompt, images_dir, store.root, size=size,
                                             dispatch=dispatch, steps=steps)
                else:
                    got = image_mod.edit(prompt, ref_png, images_dir, store.root, size=size,
                                         steps=steps, dispatch=dispatch)
                aspect = _aspect(images_dir / got["src"])
                bbox = [placeholder[0], placeholder[1], float(target_w), target_w * aspect]
                store.write(bid, finish_image(oid, got["src"], bbox=bbox,
                                              source_url=got.get("source_url"),
                                              title=got.get("title"),
                                              seconds=got.get("seconds")))
            except Exception as exc:
                store.write(bid, finish_image(oid, None, status="failed", title=str(exc)[:120]))

        threading.Thread(target=work, daemon=True, name="charrette-image").start()
        return _ok(object=oid, pending=True,
                   bbox=",".join(str(int(v)) for v in placeholder), steps=steps,
                   note="reserved; the image replaces the placeholder when ready")

    # ---------------------------------------------------------------- stroke
    def board_stroke(params, **kwargs):
        points = params.get("points") or []
        if len(points) < 2:
            return _err("E_ARGS", "points needs at least 2 [x,y] pairs")
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        try:
            doc = store.write(bid, add_stroke(points, ink=params.get("ink") or "#111318",
                                              width=int(params.get("width") or 3),
                                              label=(params.get("label") or "")[:28] or None,
                                              actor=params.get("actor") or "agent"),
                              if_rev=params.get("if_rev"))
        except RevConflict as exc:
            return _err("E_REV", "board moved; read again", rev=exc.rev)
        return _ok(object=doc.get("_last"), rev=doc["rev"])

    # --------------------------------------------------------------- capture
    def _stamp() -> str:
        return time.strftime("%Y%m%d-%H%M%S")

    def _stamp_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def board_capture(params, **kwargs):
        if Image is None:
            return _err("E_DEPS", "Pillow is not installed")
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        refs = [r for r in (params.get("refs") or []) if r] or None
        bbox = params.get("bbox")
        if refs:
            missing = [r for r in refs if r not in (doc.get("objects") or {})]
            if missing:
                near = [k for k in (doc.get("objects") or {})][-4:]
                return _err("E_NO_OBJECT", f'no object "{missing[0]}"', nearest=",".join(near) or "none")
        try:
            scale = float(params.get("scale") or 2.0)
        except (TypeError, ValueError):
            scale = 2.0
        scale = max(0.25, min(4.0, scale))
        grid = params.get("grid")
        grid = True if grid is None else bool(grid)
        png, rect, used = render_crop(doc, bbox=bbox, ids=refs, images_dir=store.root / "images",
                                      scale=scale, grid=grid, pad=44.0)
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(params.get("name") or "")).strip("-") \
            or f"{bid}-{_stamp()}"
        out_dir = store.root / "captures"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stem}.png"
        path.write_bytes(png)
        with Image.open(io.BytesIO(png)) as im:
            size = im.size
        return _ok(path=path, size=f"{size[0]}x{size[1]}", rect=",".join(f"{v:.0f}" for v in rect),
                   scale=used, bytes=len(png), board=bid, objects=len(doc.get("objects") or {}))

    def board_assets(params, **kwargs):
        if Image is None:
            return _err("E_DEPS", "Pillow is not installed")
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(params.get("name") or "")).strip("-") \
            or f"{bid}-{_stamp()}"
        out_dir = store.root / "exports" / stem
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        # the document itself, so an artifact can be rebuilt from source later
        (out_dir / "board.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        files.append({"kind": "document", "file": "board.json", "bytes": (out_dir / "board.json").stat().st_size})
        # every picture on the board
        for oid, obj in (doc.get("objects") or {}).items():
            if obj.get("kind") != "image" or not obj.get("src"):
                continue
            src = store.root / "images" / obj["src"]
            if not src.exists():
                files.append({"kind": "image", "id": oid, "missing": obj["src"]})
                continue
            dest = out_dir / f"{oid}{src.suffix or '.png'}"
            shutil.copyfile(src, dest)
            entry = {"kind": "image", "id": oid, "file": dest.name, "bytes": dest.stat().st_size,
                     "bbox": obj.get("bbox"), "label": obj.get("label")}
            files.append(entry)
        capture = None
        if params.get("capture") is not False:
            try:
                scale = max(0.5, min(4.0, float(params.get("scale") or 2.0)))
            except (TypeError, ValueError):
                scale = 2.0
            png, rect, used = render_crop(doc, images_dir=store.root / "images", scale=scale,
                                          grid=True, pad=48.0)
            (out_dir / "board.png").write_bytes(png)
            capture = {"kind": "capture", "file": "board.png", "bytes": len(png),
                       "rect": [round(v, 1) for v in rect], "scale": used}
            files.append(capture)
        manifest = {"board": bid, "name": (doc.get("name") or bid), "rev": doc.get("rev"),
                    "captured_at": _stamp(), "files": files}
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        return _ok(dir=out_dir, files=len(files), capture=(capture or {}).get("file"),
                   manifest=out_dir / "manifest.json", board=bid)

    # ----------------------------------------------------------------- scene
    def board_scene(params, **kwargs):
        bid = params.get("board") or store.index().get("active") or DEFAULT_BOARD
        doc = store.read(bid)
        text = str(params.get("text") or "").strip()
        if not text:
            return _err("E_ARGS", "text is required: it becomes the replay caption")
        objs = doc.get("objects") or {}
        scenes = list(doc.get("scenes") or [])
        refs = [r for r in (params.get("refs") or []) if r in objs]
        if not refs:
            since = scenes[-1].get("at") if scenes else ""
            refs = [k for k, v in objs.items() if str(v.get("created") or "") >= since]
            if not refs:
                ordered = sorted(objs.items(),
                                 key=lambda kv: str(kv[1].get("created") or kv[1].get("z") or ""))
                refs = [k for k, _ in ordered[-6:]]
        if not refs:
            return _err("E_EMPTY", "nothing on this board to replay yet")
        box = None
        for r in refs:
            bb = objs[r].get("bbox")
            if not bb:
                continue
            box = list(bb) if box is None else [
                min(box[0], bb[0]), min(box[1], bb[1]),
                max(box[0] + box[2], bb[0] + bb[2]) - min(box[0], bb[0]),
                max(box[1] + box[3], bb[1] + bb[3]) - min(box[1], bb[1])]
        scene = {"id": f"sc_{len(scenes) + 1}", "at": _stamp_iso(), "rev": doc.get("rev"),
                 "text": text, "objects": refs, "box": [round(v, 1) for v in box] if box else None}
        scenes = (scenes + [scene])[-40:]
        store.write(bid, lambda d: {**d, "scenes": scenes})
        try:
            speed = max(0.25, min(3.0, float(params.get("speed") or 1.0)))
        except (TypeError, ValueError):
            speed = 1.0
        path, total, n = write_replay(store, bid, scene_id=scene["id"], caption=text, speed=speed)
        return _ok(scene=scene["id"], objects=n, total_ms=total, path=path, board=bid)

    return {
        "board_read": board_read,
        "board_draw": board_draw,
        "board_edit": board_edit,
        "board_point": board_point,
        "board_look": board_look,
        "board_ask": board_ask,
        "board_image": handler_image,
        "board_stroke": board_stroke,
        "board_capture": board_capture,
        "board_assets": board_assets,
        "board_scene": board_scene,
    }
