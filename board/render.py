"""Rasterise board content to PNG.

Used for vision crops, for the reference image of an image-pipeline edit ("turn my cat into a
photo"), and for captures: a picture of a board region with no browser involved.

Strokes are polylines, so a crop is a faithful rendering rather than a screenshot: same geometry
as the live renderer, same layers, same colours. The sketch wobble is a per-frame jitter in the
engine and is deliberately not reproduced, so a capture is stable between runs.
"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = "#FFFFFF"
DOT = (215, 223, 230)
DOT_PITCH = 24
INK = "#111318"
AGENT_INK = "#0F766E"
AGENT_TINT = "#E6F4F1"
HAIRLINE = "#D6DEE6"
LABEL = "#5C6B70"
LAYER = {"agent-diagram": 10, "human-ink": 20, "agent-annotation": 30}

FONTS = Path(__file__).resolve().parent.parent / "renderer" / "fonts"
_EXCALI = FONTS / "CharretteHand.ttf"
_UI = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_CACHE: dict = {}


def _font(size: float, ui: bool = False):
    key = (max(6, int(round(size))), ui)
    if key not in _CACHE:
        path = _UI if ui else _EXCALI
        try:
            _CACHE[key] = ImageFont.truetype(str(path), key[0])
        except OSError:
            _CACHE[key] = ImageFont.load_default()
    return _CACHE[key]


# ------------------------------------------------------------------ geometry


def _objects(doc: dict):
    objs = doc.get("objects") or {}
    return sorted(objs.items(),
                  key=lambda kv: (LAYER.get(str(kv[1].get("layer") or ""), LAYER["human-ink"]),
                                  str(kv[1].get("z", ""))))


def _obj_bbox(obj: dict):
    if obj.get("bbox"):
        return list(obj["bbox"])
    pts = obj.get("pts") or []
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
    if obj.get("point"):
        p = obj["point"]
        return [p[0] - 16, p[1] - 16, 32, 32]
    return None


def _bbox_of(doc: dict, ids=None, pad: float = 24.0):
    boxes = []
    for oid, obj in (doc.get("objects") or {}).items():
        if ids is not None and oid not in ids:
            continue
        bb = _obj_bbox(obj)
        if bb:
            boxes.append(bb)
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return [x0 - pad, y0 - pad, (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad]


def content_box(doc: dict, pad: float = 48.0):
    return _bbox_of(doc, ids=None, pad=pad)


def _endpoint(doc: dict, end):
    if not end:
        return (0.0, 0.0)
    if end.get("bind"):
        target = (doc.get("objects") or {}).get(end["bind"])
        if target:
            bb = _obj_bbox(target) or [0, 0, 0, 0]
            port = end.get("port") or "auto"
            if port == "n":
                return (bb[0] + bb[2] / 2, bb[1])
            if port == "s":
                return (bb[0] + bb[2] / 2, bb[1] + bb[3])
            if port == "w":
                return (bb[0], bb[1] + bb[3] / 2)
            if port == "e":
                return (bb[0] + bb[2], bb[1] + bb[3] / 2)
            return (bb[0] + bb[2] / 2, bb[1] + bb[3] / 2)
    p = end.get("point") or [0, 0]
    return (float(p[0]), float(p[1]))


# ------------------------------------------------------------------- drawing


def _draw_board(img: Image.Image, doc: dict, rect, scale: float, images_dir, grid: bool) -> None:
    x, y, w, h = rect
    draw = ImageDraw.Draw(img)

    if grid:
        pitch = DOT_PITCH
        gx = math.floor(x / pitch) * pitch
        while gx < x + w:
            gy = math.floor(y / pitch) * pitch
            while gy < y + h:
                px, py = (gx - x) * scale, (gy - y) * scale
                draw.rectangle([px, py, px + max(1, scale), py + max(1, scale)], fill=DOT)
                gy += pitch
            gx += pitch

    def P(px, py):
        return ((px - x) * scale, (py - y) * scale)

    for _oid, obj in _objects(doc):
        kind = obj.get("kind")
        actor = obj.get("actor")
        ink = obj.get("ink") or (AGENT_INK if actor == "agent" else INK)
        if kind in ("stroke", "highlight"):
            pts = obj.get("pts") or []
            if len(pts) < 2:
                continue
            xy = [P(p[0], p[1]) for p in pts]
            if kind == "highlight" or obj.get("highlight"):
                draw.line(xy, fill=ink, width=max(2, int(round(16 * scale))), joint="curve")
            else:
                width = max(1, int(round(float(obj.get("w", 3)) * scale)))
                draw.line(xy, fill=ink, width=width, joint="curve")
                r = width / 2.0
                for px, py in (xy[0], xy[-1]):
                    draw.ellipse([px - r, py - r, px + r, py + r], fill=ink)
        elif kind == "shape":
            bb = obj.get("bbox") or _obj_bbox(obj)
            if not bb:
                continue
            box = [P(bb[0], bb[1]), P(bb[0] + bb[2], bb[1] + bb[3])]
            x0, y0 = min(box[0][0], box[1][0]), min(box[0][1], box[1][1])
            x1, y1 = max(box[0][0], box[1][0]), max(box[0][1], box[1][1])
            shape = obj.get("shape") or "rect"
            width = max(1, int(round(float(obj.get("w", 2)) * scale)))
            fill = obj.get("fill")
            if shape == "poly" and obj.get("pts"):
                poly = [P(p[0], p[1]) for p in obj["pts"]]
                if fill:
                    draw.polygon(poly, fill=fill)
                draw.line(poly + [poly[0]], fill=ink, width=width, joint="curve")
            elif shape == "ellipse":
                draw.ellipse([x0, y0, x1, y1], fill=fill, outline=ink, width=width)
            else:
                if shape == "diamond":
                    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                    poly = [(mx, y0), (x1, my), (mx, y1), (x0, my)]
                elif shape == "triangle":
                    poly = [((x0 + x1) / 2, y0), (x1, y1), (x0, y1)]
                elif shape == "line":
                    draw.line([x0, y0, x1, y1], fill=ink, width=width)
                    poly = None
                else:
                    poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                if poly:
                    if fill:
                        draw.polygon(poly, fill=fill)
                    draw.line(poly + [poly[0]], fill=ink, width=width, joint="curve")
            if obj.get("label"):
                draw.text(P(bb[0], bb[1] + bb[3] + 4), str(obj["label"]),
                          font=_font(max(9, 12 * scale)), fill=LABEL)
        elif kind in ("arrow", "line"):
            a = P(*_endpoint(doc, obj.get("from")))
            b = P(*_endpoint(doc, obj.get("to")))
            width = max(1, int(round(float(obj.get("w", 2)) * scale)))
            draw.line([a, b], fill=ink, width=width)
            angle = math.atan2(b[1] - a[1], b[0] - a[0])
            head = max(6.0, 9 * scale)
            draw.polygon([b,
                          (b[0] - head * math.cos(angle - 0.38), b[1] - head * math.sin(angle - 0.38)),
                          (b[0] - head * math.cos(angle + 0.38), b[1] - head * math.sin(angle + 0.38))],
                         fill=ink)
            if obj.get("label"):
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 8 * scale)
                draw.text(mid, str(obj["label"]), font=_font(max(9, 12 * scale)), fill=ink)
        elif kind == "text":
            size = float(obj.get("size") or 22) * scale
            pos = P(float(obj.get("x") or 0), float(obj.get("y") or 0))
            for i, line in enumerate(str(obj.get("text") or "").split("\n")):
                draw.text((pos[0], pos[1] + i * size * 1.15), line, font=_font(size), fill=ink)
        elif kind == "annotation" and obj.get("point"):
            style = obj.get("style") or "ring"
            colour = AGENT_INK if style != "emphasis" else "#B42318"
            cx, cy = P(obj["point"][0], obj["point"][1])
            r = max(6.0, 11 * scale)
            if style == "cross":
                draw.line([cx - r, cy - r, cx + r, cy + r], fill=colour, width=max(1, int(2 * scale)))
                draw.line([cx + r, cy - r, cx - r, cy + r], fill=colour, width=max(1, int(2 * scale)))
            else:
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=colour, width=max(1, int(2 * scale)))
            if obj.get("label"):
                draw.text((cx + r + 6, cy - 6 * scale), str(obj["label"]),
                          font=_font(max(9, 12 * scale)), fill=colour)
        elif kind == "question":
            bb = obj.get("bbox") or _obj_bbox(obj) or [0, 0, 260, 90]
            p0, p1 = P(bb[0], bb[1]), P(bb[0] + bb[2], bb[1] + bb[3])
            draw.rounded_rectangle([p0[0], p0[1], p1[0], p1[1]], radius=int(8 * scale),
                                   fill="#FFFFFF", outline=AGENT_INK, width=max(1, int(1.4 * scale)))
            draw.text((p0[0] + 10 * scale, p0[1] + 6 * scale), "the agent asks",
                      font=_font(max(8, 9 * scale), ui=True), fill=AGENT_INK)
            draw.text((p0[0] + 10 * scale, p0[1] + 18 * scale), str(obj.get("q") or ""),
                      font=_font(max(9, 12 * scale)), fill=INK)
            for i, opt in enumerate(obj.get("options") or []):
                oy = p0[1] + (44 + i * 18) * scale
                draw.rectangle([p0[0] + 8 * scale, oy, p1[0] - 8 * scale, oy + 12 * scale],
                               outline=HAIRLINE,
                               fill=AGENT_TINT if obj.get("answer") == opt.get("id") else None)
                draw.text((p0[0] + 12 * scale, oy + 1 * scale), str(opt.get("label") or opt.get("id") or ""),
                          font=_font(max(7, 8 * scale), ui=True), fill=LABEL)
        elif kind == "image" and images_dir and obj.get("src"):
            try:
                im = Image.open(Path(images_dir) / obj["src"]).convert("RGB")
                bb = obj.get("bbox") or [0, 0, im.width, im.height]
                target = (max(1, int(bb[2] * scale)), max(1, int(bb[3] * scale)))
                im = im.resize(target)
                img.paste(im, (int((bb[0] - x) * scale), int((bb[1] - y) * scale)))
                if obj.get("label"):
                    draw.text(((bb[0] - x) * scale, (bb[1] + bb[3] - y) * scale + 4),
                              str(obj["label"]), font=_font(max(9, 11 * scale)), fill=LABEL)
            except Exception:
                pass
        elif kind in ("table", "chart") and obj.get("bbox"):
            bb = obj["bbox"]
            p0, p1 = P(bb[0], bb[1]), P(bb[0] + bb[2], bb[1] + bb[3])
            colour = obj.get("ink") or (AGENT_INK if actor == "agent" else INK)
            if kind == "table":
                rows = max(1, min(60, obj.get("rows") or 3))
                cols = max(1, min(16, obj.get("cols") or 3))
                rh = (p1[1] - p0[1]) / rows
                cw = (p1[0] - p0[0]) / cols
                draw.rectangle([p0[0], p0[1], p1[0], p1[1]], fill="#FFFFFF")
                draw.rectangle([p0[0], p0[1], p1[0], p0[1] + rh],
                               fill=AGENT_TINT if actor == "agent" else "#F2F4F6")
                draw.rectangle([p0[0], p0[1], p1[0], p1[1]], outline=colour,
                               width=max(1, int(1.4 * scale)))
                for r in range(1, rows):
                    rowy = p0[1] + r * rh
                    draw.line([p0[0], rowy, p1[0], rowy], fill=colour, width=max(1, int(scale)))
                for c in range(1, cols):
                    colx = p0[0] + c * cw
                    draw.line([colx, p0[1], colx, p1[1]], fill=colour, width=max(1, int(scale)))
                fs = max(7.0, min(rh * 0.46, 15 * scale))
                cells = obj.get("cells") or []
                for r in range(rows):
                    for c in range(cols):
                        val = (cells[r][c] if r < len(cells) and c < len(cells[r]) else "") or ""
                        if not val:
                            continue
                        draw.text((p0[0] + c * cw + 5 * scale, p0[1] + r * rh + rh / 2), str(val),
                                  font=_font(fs), fill=colour, anchor="lm")
            else:
                values = [float(v or 0) for v in (obj.get("values") or [])]
                labels = obj.get("labels") or []
                title = obj.get("title")
                title_h = 24 * scale if title else 0
                axis_h = 16 * scale
                label_band = 14 * scale
                plot_h = max(8.0, (p1[1] - p0[1]) - title_h - axis_h - label_band)
                base_y = p0[1] + title_h + plot_h
                draw.line([p0[0], p0[1] + title_h, p0[0], base_y], fill=HAIRLINE, width=max(1, int(scale)))
                draw.line([p0[0], base_y, p1[0], base_y], fill=HAIRLINE, width=max(1, int(scale)))
                if title:
                    draw.text((p0[0], p0[1]), str(title), font=_font(13 * scale), fill=INK)
                if values:
                    top = max(1.0, max(abs(v) for v in values))
                    slot = (p1[0] - p0[0]) / max(1, len(values))
                    if (obj.get("chart") or "bar") == "line":
                        pts = []
                        for i, v in enumerate(values):
                            px = p0[0] + slot * (i + 0.5)
                            py = base_y - (abs(v) / top) * plot_h
                            pts.append((px, py))
                        if len(pts) > 1:
                            draw.line(pts, fill=colour, width=max(1, int(2 * scale)))
                        for px, py in pts:
                            r = max(1.6, 3 * scale)
                            draw.ellipse([px - r, py - r, px + r, py + r], fill=colour)
                    else:
                        bw = slot * 0.58
                        for i, v in enumerate(values):
                            h = (abs(v) / top) * plot_h
                            barx = p0[0] + slot * i + (slot - bw) / 2
                            draw.rectangle([barx, base_y - h, barx + bw, base_y], fill=colour)
                            if len(values) <= 12:
                                draw.text((p0[0] + slot * (i + 0.5), base_y - h - 3 * scale),
                                          str(int(v) if float(v).is_integer() else v),
                                          font=_font(max(7, 9 * scale), ui=True), fill=LABEL, anchor="mb")
                    for i, lab in enumerate(labels[:len(values)]):
                        draw.text((p0[0] + slot * (i + 0.5), base_y + 3 * scale), str(lab),
                                  font=_font(max(7, 9 * scale), ui=True), fill=LABEL, anchor="ma")
                else:
                    draw.text(((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2), "no values",
                              font=_font(max(8, 10 * scale), ui=True), fill=LABEL, anchor="mm")
        elif kind == "scene":
            spec = obj.get("spec") or {}
            nodes = obj.get("nodes") or {}
            labels = {n.get("key"): n.get("label") for n in (spec.get("nodes") or [])}
            tones = {n.get("key"): n.get("tone") for n in (spec.get("nodes") or [])}
            for key, nb in nodes.items():
                p0, p1 = P(nb[0], nb[1]), P(nb[0] + nb[2], nb[1] + nb[3])
                fill = AGENT_TINT if tones.get(key) in ("highlight", "accent") else "#FFFFFF"
                draw.rounded_rectangle([p0[0], p0[1], p1[0], p1[1]], radius=int(4 * scale),
                                       fill=fill, outline=AGENT_INK, width=max(1, int(1.4 * scale)))
                draw.text(((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2), str(labels.get(key) or key),
                          font=_font(max(8, 11 * scale)), fill=INK, anchor="mm")
            for edge in spec.get("edges") or []:
                a, b = nodes.get(edge.get("from")), nodes.get(edge.get("to"))
                if not a or not b:
                    continue
                ac = P(a[0] + a[2] / 2, a[1] + a[3] / 2)
                bc = P(b[0] + b[2] / 2, b[1] + b[3] / 2)
                draw.line([ac, bc], fill=LABEL, width=max(1, int(1.2 * scale)))
                if edge.get("label"):
                    draw.text(((ac[0] + bc[0]) / 2, (ac[1] + bc[1]) / 2 - 6 * scale),
                              str(edge["label"]), font=_font(max(7, 9 * scale)), fill=LABEL, anchor="mm")


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ----------------------------------------------------------------- public API


def render_crop(
    doc: dict,
    bbox=None,
    ids=None,
    max_side: int = 1024,
    pad: float = 24.0,
    images_dir: Path | None = None,
    scale: float | None = None,
    grid: bool = False,
) -> tuple[bytes, list, float]:
    """Return (png_bytes, src_rect, scale).

    `src_rect` is the board-space rect [x, y, w, h] the PNG covers, so a normalised point from a
    vision model projects back with board = src.xy + (u, v)/1000 * src.wh. `scale` overrides the
    max_side cap when a caller wants a specific resolution.
    """
    rect = bbox or _bbox_of(doc, ids=ids, pad=pad)
    if not rect:
        rect = [0.0, 0.0, 256.0, 256.0]
    x, y, w, h = [float(v) for v in rect]
    w = max(16.0, w)
    h = max(16.0, h)
    if scale is None:
        scale = min(1.0, float(max_side) / max(w, h)) if max(w, h) > max_side else 1.0
    W, H = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    img = Image.new("RGB", (W, H), BG)
    _draw_board(img, doc, (x, y, w, h), scale, images_dir, grid)
    return _png(img), [x, y, w, h], scale


def render_region(doc: dict, box=None, scale: float = 2.0, pad: float = 48.0,
                  images_dir: Path | None = None, grid: bool = True) -> tuple[bytes, list]:
    """Render a board region (default: everything, plus pad) at *scale*, with the dot grid."""
    rect = box or _bbox_of(doc, ids=None, pad=pad)
    if not rect:
        rect = [0.0, 0.0, 256.0, 256.0]
    png, used, _scale = render_crop(doc, bbox=rect, images_dir=images_dir, scale=scale, grid=grid)
    return png, used


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
