"""Chalk tool handlers. Each returns a JSON string the model reads."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from PIL import Image

from .board import image as image_mod
from .board.render import render_crop
from .board.store import (
    DEFAULT_BOARD,
    RevConflict,
    Store,
    add_image,
    add_point,
    add_stroke,
    finish_image,
    free_rect,
)

SCHEMA_STROKE = {
    "name": "board_stroke",
    "description": (
        "Append one freehand stroke to the shared whiteboard. Points are board "
        "coordinates in CSS px, y down. Used for testing and for the agent to "
        "underline or circle something by hand."
    ),
    "parameters": {
        "type": "object",
        "required": ["points"],
        "properties": {
            "points": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "array", "minItems": 2, "maxItems": 2,
                          "items": {"type": "number"}},
                "description": "[[x,y], ...] board coordinates",
            },
            "board": {"type": "string", "default": DEFAULT_BOARD},
            "ink": {"type": "string", "description": "hex colour, default #111318"},
            "width": {"type": "integer", "default": 3},
            "label": {"type": "string", "description": "optional label, <=28 chars"},
            "if_rev": {"type": "integer", "description": "reject if the board moved"},
        },
    },
}

SCHEMA_IMAGE = {
    "name": "board_image",
    "description": (
        "Put an image on the shared whiteboard from one of four sources: search the "
        "web, generate from a prompt, edit an existing drawing on the board into a new "
        "image (img2img, e.g. turn a sketch into a photo), or take a URL/path. "
        "Returns the image object id and where it landed."
    ),
    "parameters": {
        "type": "object",
        "required": ["source"],
        "properties": {
            "source": {"enum": ["search", "generate", "edit", "url", "path"]},
            "query": {"type": "string", "description": "for source=search"},
            "prompt": {"type": "string", "description": "for source=generate|edit"},
            "url": {"type": "string", "description": "for source=url"},
            "path": {"type": "string", "description": "for source=path, absolute"},
            "ref": {"type": "string",
                    "description": "for source=edit: object id, or 'newest' for the last mark"},
            "size": {"type": "string", "description": "optional WxH hint, pipeline may override"},
            "steps": {"type": "integer", "minimum": 1, "maximum": 20,
                      "description": "sampling steps for generate/edit; lower is faster, 2-4 is plenty for a sketch"},
            "label": {"type": "string", "description": "caption on the image, <=28 chars"},
            "width": {"type": "integer", "default": 420, "description": "display width in board px"},
            "place": {
                "type": "object",
                "properties": {
                    "near": {"type": "string", "description": "object id or 'newest'"},
                    "gap": {"type": "integer", "default": 48},
                },
            },
            "board": {"type": "string", "default": DEFAULT_BOARD},
            "if_rev": {"type": "integer"},
        },
    },
}

SCHEMA_POINT = {
    "name": "board_point",
    "description": (
        "Mark a single point on the board, e.g. 'point at its ears'. Coordinates come "
        "from vision in normalised 0-1000 space of a crop, and are projected by the "
        "plugin. Never invent board coordinates."
    ),
    "parameters": {
        "type": "object",
        "required": ["points"],
        "properties": {
            "points": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "required": ["u", "v"],
                    "properties": {
                        "u": {"type": "number", "description": "0-1000, left to right in the crop"},
                        "v": {"type": "number", "description": "0-1000, top to bottom in the crop"},
                        "label": {"type": "string"},
                    },
                },
            },
            "ref": {"type": "string", "description": "object id or 'newest' the crop was of"},
            "crop": {"type": "array", "items": {"type": "number"},
                     "description": "[x,y,w,h] of the crop the coordinates refer to"},
            "board": {"type": "string", "default": DEFAULT_BOARD},
        },
    },
}


def _ok(**kw) -> str:
    return json.dumps({"success": True, **kw})


def _err(code: str, message: str, **kw) -> str:
    return json.dumps({"success": False, "error": code, "message": message, **kw})


def _newest(doc: dict):
    best = None
    for oid, obj in (doc.get("objects") or {}).items():
        if obj.get("actor") != "human":
            continue
        best = (oid, obj) if best is None else best
    return best


def _aspect(path: Path, default: float = 0.5625) -> float:
    try:
        with Image.open(path) as im:
            return im.height / max(1, im.width)
    except Exception:
        return default


def make_handlers(store: Store, dispatch=None) -> dict:
    images_dir = store.root / "images"

    def board_stroke(params, **kwargs):
        points = params.get("points") or []
        if len(points) < 2:
            return _err("E_ARGS", "points needs at least 2 [x,y] pairs")
        label = (params.get("label") or "")[:28] or None
        bid = params.get("board") or DEFAULT_BOARD
        try:
            doc = store.write(
                bid,
                add_stroke(points, ink=params.get("ink") or "#111318",
                           width=int(params.get("width") or 3), label=label),
                if_rev=params.get("if_rev"),
            )
        except RevConflict as exc:
            return _err("E_REV", "board moved; re-read and retry", rev=exc.rev)
        except ValueError as exc:
            return _err("E_ARGS", str(exc))
        return _ok(object=doc.get("_last"), board=bid, rev=doc["rev"],
                   objects=len(doc.get("objects", {})))

    def board_point(params, **kwargs):
        bid = params.get("board") or DEFAULT_BOARD
        doc = store.read(bid)
        pts = params.get("points") or []
        crop = params.get("crop")
        ref = params.get("ref")
        if crop is None:
            if ref in (None, "newest"):
                hit = _newest(doc)
                crop = hit[1].get("bbox") if hit else None
            else:
                obj = (doc.get("objects") or {}).get(ref)
                crop = obj.get("bbox") if obj else None
        if not crop:
            return _err("E_NO_OBJECT", "no crop and no anchor object to project into")
        x, y, w, h = [float(v) for v in crop]
        out = []
        for p in pts:
            bx = x + (float(p.get("u", 0)) / 1000.0) * w
            by = y + (float(p.get("v", 0)) / 1000.0) * h
            label = (p.get("label") or "")[:28] or None
            d = store.write(bid, add_point(bx, by, label=label))
            out.append({"object": d.get("_last"), "board_xy": [round(bx, 1), round(by, 1)], "label": label})
        return _ok(points=out, crop=[x, y, w, h], rev=store.read(bid)["rev"])

    def board_image(params, **kwargs):
        bid = params.get("board") or DEFAULT_BOARD
        doc = store.read(bid)
        src_kind = params.get("source")
        label = (params.get("label") or "")[:28] or None
        target_w = int(params.get("width") or 420)
        place = params.get("place") or {}
        gap = float(place.get("gap") or 48)
        near = None
        if place.get("near"):
            ref_id = place["near"]
            if ref_id == "newest":
                hit = _newest(doc)
                near = hit[1] if hit else None
            else:
                near = (doc.get("objects") or {}).get(ref_id)

        # ---- fast sources: one request, return the ready object
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
            return _ok(object=new.get("_last"), src=got["src"], bbox=bbox,
                       source_url=got.get("source_url"), title=got.get("title"),
                       bytes=got.get("bytes"), rev=new["rev"])

        # ---- slow sources: reserve the space, render in the background
        if src_kind not in ("generate", "edit"):
            return _err("E_ARGS", f"unknown source {src_kind!r}")

        ref_png = None
        if src_kind == "edit":
            ref = params.get("ref") or "newest"
            if ref == "newest":
                hit = _newest(doc)
                if not hit:
                    return _err("E_NO_OBJECT", "nothing on the board to edit")
                ids = [hit[0]]
            else:
                ids = [ref]
            if ids[0] not in (doc.get("objects") or {}):
                return _err("E_NO_OBJECT", f"no such object {ids[0]!r}")
            ref_png, _rect, _scale = render_crop(doc, ids=ids, images_dir=images_dir)  # type: ignore[assignment]

        placeholder_bbox = free_rect(doc, target_w, target_w * 0.5625, near=near, gap=gap)
        holder = store.write(bid, add_image(None, placeholder_bbox, label=label, status="rendering"))
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
                                         steps=steps)
                aspect = _aspect(images_dir / got["src"])
                bbox = [placeholder_bbox[0], placeholder_bbox[1], float(target_w), target_w * aspect]
                store.write(bid, finish_image(oid, got["src"], bbox=bbox,
                                              source_url=got.get("source_url"),
                                              title=got.get("title"),
                                              seconds=got.get("seconds")))
            except Exception as exc:  # never lose the pane to a pipeline error
                store.write(bid, finish_image(oid, None, status="failed",
                                              title=str(exc)[:120]))

        threading.Thread(target=work, daemon=True, name="chalk-image").start()
        return _ok(pending=True, object=oid, bbox=placeholder_bbox, steps=steps,
                   note="reserved on the board; the image replaces the placeholder when ready. Keep talking.")

    return {"board_stroke": board_stroke, "board_point": board_point, "board_image": board_image}
