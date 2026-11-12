"""Charrette HTTP surface. Mounted at /api/plugins/charrette/.

The plugin root is added to sys.path because the dashboard host imports THIS
file directly, so package-relative imports would otherwise fail. Nothing here
touches Hermes internals.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import APIRouter, Body, HTTPException, Query  # noqa: E402

from board.store import DEFAULT_BOARD, RevConflict, Store, add_stroke, apply_objects  # noqa: E402
from board import safety

router = APIRouter()
store = Store()


@router.get("/health")
def health():
    return {"ok": True, "root": str(store.root)}


@router.get("/img")
def img(bid: str = Query(DEFAULT_BOARD, alias="board"), oid: str = Query(..., alias="id")):
    """Bytes as base64. The pane caches by object id; board.json never carries pixels."""
    import base64
    import mimetypes

    doc = store.read(bid)
    obj = (doc.get("objects") or {}).get(oid)
    if not obj or obj.get("kind") != "image":
        raise HTTPException(status_code=404, detail="no such image")
    path = safety.image_path(store.root, obj.get("src"))
    if path is None:
        raise HTTPException(status_code=404, detail="no such image")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image file missing")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {
        "id": oid,
        "mime": mime,
        "b64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "source_url": obj.get("source_url"),
        "title": obj.get("title"),
    }


@router.get("/boards")
def boards():
    return store.index()


@router.post("/boards/new")
def boards_new(payload: dict = Body(default={})):
    return store.new_board(payload.get("name"))


@router.post("/boards/active")
def boards_active(payload: dict = Body(...)):
    bid = payload.get("board")
    if not bid:
        raise HTTPException(status_code=400, detail="board required")
    return store.set_active(bid)


@router.get("/board")
def board(bid: str = Query(DEFAULT_BOARD, alias="board"), since: int | None = None):
    doc = store.read(bid)
    if since is not None and int(doc.get("rev", 0)) <= int(since):
        return {"unchanged": True, "rev": doc.get("rev", 0)}
    return doc


@router.post("/board")
def board_write(payload: dict = Body(...)):
    """Replace the whole document (guarded by if_rev). Used by the renderer for
    camera moves and, later, batched edits."""
    bid = payload.get("board") or DEFAULT_BOARD
    doc = payload.get("doc")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=400, detail="doc required")
    try:
        return store.write(bid, lambda _cur: doc, if_rev=payload.get("if_rev"))
    except RevConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "E_REV", "rev": exc.rev})


@router.post("/objects")
def objects_write(payload: dict = Body(...)):
    bid = payload.get("board") or DEFAULT_BOARD
    try:
        doc = store.write(bid, apply_objects(payload.get("upsert") or [],
                                            payload.get("delete") or []),
                          if_rev=payload.get("if_rev"))
    except RevConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "E_REV", "rev": exc.rev})
    return {"ok": True, "rev": doc["rev"], "last": doc.get("_last")}


@router.post("/camera")
def camera(payload: dict = Body(...)):
    bid = payload.get("board") or DEFAULT_BOARD
    cam = payload.get("camera") or {}
    if not isinstance(cam, dict):
        raise HTTPException(status_code=400, detail="camera required")
    doc = store.write(bid, lambda d: {**d, "camera": cam})
    return {"ok": True, "rev": doc["rev"]}


@router.post("/stroke")
def stroke(payload: dict = Body(...)):
    bid = payload.get("board") or DEFAULT_BOARD
    points = payload.get("points") or []
    if len(points) < 2:
        raise HTTPException(status_code=400, detail="points needs >= 2 [x,y] pairs")
    try:
        doc = store.write(
            bid,
            add_stroke(
                points,
                ink=payload.get("ink") or "#111318",
                width=int(payload.get("width") or 3),
                label=(payload.get("label") or "")[:28] or None,
            ),
            if_rev=payload.get("if_rev"),
        )
    except RevConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "E_REV", "rev": exc.rev})
    return {"ok": True, "object": doc.get("_last"), "rev": doc["rev"]}
