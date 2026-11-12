"""Scene replay: turn a group of board objects into a self-contained animated page.

The point is not a video. It is that reading a paragraph and watching the ink arrive should feel
like the same event: the page plays when it scrolls into view, the sentence lights up word by word as
its objects appear, and a small hand with eyes travels to each mark, turns into a pen to draw it and
a glove to place it. The pace is a person's, not the agent's: the machine drew all of it in
milliseconds, so the timeline is built from ink length at pen speed.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path

from . import safety

ROOT = Path(__file__).resolve().parent.parent
PEN_SPEED = 620.0   # board px per second: someone with a marker in their hand
GAP_MS = 260        # a beat between objects
FIXED_MS = {"table": 1300, "chart": 1500, "image": 900, "scene": 1800,
            "question": 900, "annotation": 600}
SCENE_CAP = 40
PLACE_KINDS = {"image", "table", "chart"}   # the hand grabs these rather than drawing them


def _length(pts) -> float:
    return sum(math.dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def duration_ms(obj: dict) -> float:
    kind = obj.get("kind")
    if kind in ("stroke", "highlight"):
        return max(320.0, _length(obj.get("pts") or []) / PEN_SPEED * 1000)
    if kind == "shape":
        pts = obj.get("pts")
        if pts:
            return max(320.0, _length(list(pts) + [pts[0]]) / PEN_SPEED * 1000)
        bb = obj.get("bbox") or [0, 0, 100, 100]
        return max(320.0, (2 * (bb[2] + bb[3])) / PEN_SPEED * 1000)
    if kind == "text":
        return max(280.0, 45.0 * len(str(obj.get("text") or "")))
    return float(FIXED_MS.get(kind, 700))


def created_order(obj: dict):
    """`created` is stamped by the store on first write; older objects fall back to layer + z."""
    stamp = obj.get("created")
    if stamp:
        return (0, stamp)
    layer = {"agent-diagram": 10, "human-ink": 20, "agent-annotation": 30}.get(
        str(obj.get("layer") or ""), 20)
    z = str(obj.get("z") or "a0")
    try:
        seq = int(z[1:]) if z.startswith("a") else 0
    except ValueError:
        seq = 0
    return (1, f"{layer:03d}{seq:06d}")


def build_timeline(objects: list[tuple[str, dict]], speed: float = 1.0):
    t = 0.0
    timeline = []
    for oid, obj in objects:
        d = duration_ms(obj) / max(0.1, speed)
        timeline.append({"id": oid, "t0": round(t, 1), "t1": round(t + d, 1)})
        t += d + GAP_MS
    return timeline, round(t, 1)


def split_sentence(text: str, timeline: list[dict]):
    """Give each object a slice of the sentence, weighted by how long that object takes to draw.

    The caption is written after the fact, so nothing in it marks where one object ends and the next
    begins. Weighting by draw time is close enough, and it keeps the highlight in step with the hand.
    """
    words = [w for w in str(text or "").split() if w]
    if not words or not timeline:
        return [], words
    caption_timeline = timeline[:min(len(words), len(timeline))]
    weights = [max(1.0, it["t1"] - it["t0"]) for it in caption_timeline]
    total = sum(weights)
    segs, acc, start = [], 0.0, 0
    for it, w in zip(caption_timeline, weights):
        acc += w
        end = int(round(len(words) * acc / total))
        end = max(end, start + 1)
        end = min(end, len(words))
        segs.append({"id": it["id"], "t0": it["t0"], "t1": it["t1"],
                     "from": start, "to": end})
        start = end
    if segs:
        segs[-1]["to"] = len(words)
    return segs, words


def pick_objects(doc: dict, ids=None, scene=None) -> list[tuple[str, dict]]:
    objects = doc.get("objects") or {}
    if scene and scene.get("objects"):
        order = [i for i in scene["objects"] if i in objects]
        if order:
            return [(i, objects[i]) for i in order]
    if ids:
        # an explicit id list is a sequence the caller chose, not a set to be re-sorted
        return [(i, objects[i]) for i in ids if i in objects]
    pool = [(i, objects[i]) for i in objects]
    return sorted(pool, key=lambda kv: created_order(kv[1]))


def build_html(doc: dict, store_root: Path, picked, caption: str = "", speed: float = 1.0):
    timeline, total = build_timeline(picked, speed)
    segs, words = split_sentence(caption, timeline)
    mini = {"schema": doc.get("schema") or "charrette/1", "rev": doc.get("rev"),
            "objects": {i: o for i, o in picked},
            "camera": {"x": 0, "y": 0, "z": 1}, "layers": doc.get("layers") or []}
    engine = (ROOT / "renderer" / "charrette.mjs").read_text()
    engine = engine.replace("export const ", "const ").replace("export function ", "function ")
    font_path = ROOT / "renderer" / "fonts" / "CharretteHand.woff2"
    font_b64 = base64.b64encode(font_path.read_bytes()).decode()
    images = {}
    for _i, obj in picked:
        if obj.get("kind") == "image":
            path = safety.image_path(store_root, obj.get("src"))
            if path is not None and path.exists():
                images[obj["src"]] = ("data:image/png;base64,"
                                      + base64.b64encode(path.read_bytes()).decode())
    html = (_TEMPLATE
            .replace("__FONT__", font_b64)
            .replace("__ENGINE__", engine)
            .replace("__DOC__", json.dumps(mini))
            .replace("__TIMELINE__", json.dumps(timeline))
            .replace("__SEGMENTS__", json.dumps(segs))
            .replace("__WORDS__", json.dumps(words))
            .replace("__IMAGES__", json.dumps(images))
            .replace("__TOTAL__", str(total))
            .replace("__SPEED__", f"{speed:g}"))
    return html, total


def write_replay(store, board: str, ids=None, scene_id: str = "", caption: str = "",
                 speed: float = 1.0, out: str | None = None) -> tuple[Path, float, int]:
    doc = store.read(board)
    scene = None
    if scene_id:
        scene = next((s for s in (doc.get("scenes") or []) if s.get("id") == scene_id), None)
        if scene and not caption:
            caption = scene.get("text") or ""
    picked = pick_objects(doc, ids=ids, scene=scene)
    if not picked:
        raise ValueError("no objects to replay")
    html, total = build_html(doc, store.root, picked, caption, speed)
    path = Path(out) if out else store.root / "replays" / f"{board}-{scene_id or 'scene'}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return path, total, len(picked)


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>charrette · replay</title>
<style>
  @font-face { font-family: 'Charrette Hand'; src: url(data:font/woff2;base64,__FONT__) format('woff2'); font-display: block; }
  * { box-sizing: border-box; }
  html, body { margin:0; background:#f5f7f6; color:#101a18;
    font:14px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
  #wrap { display:flex; flex-direction:column; width:min(960px, 100%); min-height:100vh;
    margin:0 auto; padding:clamp(12px, 3vw, 30px); justify-content:center; }
  /* the paragraph reads first, the board second: the caption is the reason the drawing exists */
  #prose { padding:18px 20px 16px; background:#fff; border:1px solid #dce5e2;
    border-bottom:0; border-radius:10px 10px 0 0; }
  #prose .label { display:block; font-size:10px; letter-spacing:.09em; text-transform:uppercase;
    color:#7d918d; font-weight:600; margin-bottom:5px; }
  #prose p { margin:0; font-size:14.5px; line-height:1.66; max-width:70ch; }
  #prose span.w { color:#2b3835; border-radius:3px; padding:1px 0;
    transition: background-color .18s linear, color .18s linear; }
  #prose span.w.lit { background:#d9f2ea; color:#0b1b17; }
  #prose span.w.now { background:#a5e9d6; }
  #stage { position:relative; background:#fff; aspect-ratio:16 / 9; min-height:210px;
    max-height:min(430px, 62vh); overflow:hidden; border:1px solid #dce5e2; border-top:0; }
  canvas { display:block; width:100%; height:100%; }
  #prog { height:2px; background:#e2e8e6; }
  #prog i { display:block; height:2px; width:0; background:#0f9c80; }
  #bar { display:flex; align-items:center; gap:10px; padding:7px 12px; background:#0d1315;
    color:#e8eeea; border-radius:0 0 9px 9px; }
  #bar .sp { flex:1 1 auto; }
  #bar small { color:#93a5a1; font-size:11px; }
  #bar #state { color:#7fb8ad; }
  #wrap.finished #bar #state { color:#2dd4bf; }
  #wrap.finished #stage { box-shadow:inset 0 0 0 1px rgba(15,156,128,.22); }
  button { appearance:none; border:1px solid #24322f; background:#131d1f; color:#e8eeea;
    border-radius:6px; padding:3px 9px; font:inherit; font-size:12px; cursor:pointer; }
  button:hover { background:#1a2528; }
</style></head>
<body><div id="wrap">
  <div id="prose"><span class="label">drawing this</span><p id="sentence"></p></div>
  <div id="stage"><canvas id="board"></canvas></div>
  <div id="prog"><i id="progbar"></i></div>
  <div id="bar"><small id="state">playing</small><span class="sp"></span><small id="speed">__SPEED__×</small><button id="again">again</button></div>
</div>
<script type="module">
__ENGINE__

const DOC = __DOC__;
const TIMELINE = __TIMELINE__;
const SEGMENTS = __SEGMENTS__;
const WORDS = __WORDS__;
const IMAGES = __IMAGES__;
const TOTAL = __TOTAL__;

const canvas = document.getElementById('board');
const ctx2d = canvas.getContext('2d');
const board = createBoard(canvas, {});
for (const [src, url] of Object.entries(IMAGES)) {
  const img = new Image();
  img.onload = () => board.setImage(src, img);
  img.src = url;
}
if (document.fonts && document.fonts.load) document.fonts.load('17px "Charrette Hand"');

board.setDoc(DOC);
board.fit();
board.setTimeline(TIMELINE);

// ---- the sentence, as spans we can light up
const sentenceEl = document.getElementById('sentence');
const spans = WORDS.map((w, i) => {
  const s = document.createElement('span');
  s.className = 'w';
  s.textContent = w;
  sentenceEl.appendChild(s);
  if (i < WORDS.length - 1) sentenceEl.appendChild(document.createTextNode(' '));
  return s;
});

// ---- where the hand enters and where it starts each mark
// geometry lives in the engine: a second copy here would drift from where the pen tip actually is
const anchorOf = id => board.anchorOf(id);
const ANCHORS = TIMELINE.map(it => anchorOf(it.id));
const KIND = id => (DOC.objects[id] || {}).kind || 'stroke';
const PLACE = id => ['image', 'table', 'chart'].includes(KIND(id));

// ---- the hand ships with the engine: createAgentHand, stepAgentHand, drawAgentHand

// ---- driver
function segmentAt(t, items = TIMELINE) {
  let idx = -1;
  for (let i = 0; i < items.length; i += 1) {
    if (t < items[i].t0) break;
    idx = i;
  }
  return idx;
}

// Time is the only state: every animation below is a function of the replay clock `t`, so
// seek(t) reproduces a frame exactly and a screenshot cannot catch a half-advanced timer.
const hand = createAgentHand();

let lastT = 0;
function paint(t, now) {
  board.revealAt(t);

  // the hand's position, mode and expression are the engine's job; the page only
  // owns the caption and the progress bar
  stepAgentHand(hand, t, TIMELINE, id => board.penAt(id), id => board.anchorOf(id), TOTAL);

  // the sentence lights up with the ink
  if (SEGMENTS.length && spans.length) {
    const captionIdx = segmentAt(t, SEGMENTS);
    const active = captionIdx >= 0 ? SEGMENTS[captionIdx] : null;
    if (active) {
      const p = (t - active.t0) / Math.max(1, active.t1 - active.t0);
      const span = active.to - active.from;
      const cut = active.from + Math.floor(Math.min(1, Math.max(0, p)) * span + 0.0001);
      for (let i = 0; i < spans.length; i += 1) {
        const lit = i < cut;
        if (spans[i].classList.contains('lit') !== lit) spans[i].classList.toggle('lit', lit);
        const isNow = lit && i === cut - 1;
        if (spans[i].classList.contains('now') !== isNow) spans[i].classList.toggle('now', isNow);
      }
    }
  }

  drawAgentHand(ctx2d, hand);
  document.getElementById('progbar').style.width = Math.min(100, (t / TOTAL) * 100) + '%';
  const finished = t >= TOTAL;
  document.getElementById('wrap').classList.toggle('finished', finished);
  document.getElementById('state').textContent = finished ? 'finished' : 'playing';
  lastT = t;
}

let begin = 0, running = false;
function play() {
  running = true;
  begin = performance.now();
  hand.ripple = 0;
  hand.morph = 1;
  hand.from = hand.mode;
  const step = (now) => {
    if (!running) return;
    const t = now - begin;
    paint(Math.min(TOTAL, t), now);
    if (t < TOTAL) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// play when the reader gets here, not before
const io = new IntersectionObserver(entries => {
  for (const e of entries) if (e.isIntersecting && !running) { io.disconnect(); play(); }
}, { threshold: 0.35 });
io.observe(document.getElementById('stage'));
document.getElementById('again').addEventListener('click', () => {
  running = false;
  setTimeout(play, 30);
});
// seek() stops the clock and holds a frame: the test surface, and what a scrub bar would call
window.__replay = {
  board, TIMELINE, SEGMENTS, TOTAL, hand,
  seek(t) { running = false; paint(t, performance.now()); return hand.mode; },
  play
};
</script>
</body></html>
"""
