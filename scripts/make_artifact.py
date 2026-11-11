#!/usr/bin/env python3
"""Build a self-contained artifact from a board: capture + assets in, one HTML out.

Usage: make_artifact.py <board> [out-name]
Writes <hermes home>/charrette/exports/<name>/{index.html, assets...} and prints the path.
Images are inlined as data URLs so the HTML travels on its own.
"""
import base64
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'plugins'))
from charrette.board.render import render_crop  # noqa: E402
from charrette.board.store import Store  # noqa: E402

board = sys.argv[1] if len(sys.argv) > 1 else 'b3'
name = sys.argv[2] if len(sys.argv) > 2 else f'charrette-card-{board}'

store = Store()
doc = store.read(board)
objects = doc.get('objects') or {}
images_dir = store.root / 'images'
out = store.root / 'exports' / name
out.mkdir(parents=True, exist_ok=True)


def b64(png: bytes) -> str:
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


# the board as drawn, and the two objects that carry the story
whole, rect, _ = render_crop(doc, images_dir=images_dir, scale=2, grid=True, pad=48)
(out / 'board.png').write_bytes(whole)
photo_obj = next((o for o in objects.values() if o.get('kind') == 'image' and o.get('src')), None)
photo_bytes = (images_dir / photo_obj['src']).read_bytes() if photo_obj else None
if photo_bytes:
    (out / 'photo.png').write_bytes(photo_bytes)

doodle_refs = [oid for oid, o in objects.items() if o.get('kind') == 'stroke']
doodles, d_rect, _ = render_crop(doc, ids=doodle_refs, images_dir=images_dir, scale=2, grid=False, pad=36)
(out / 'ink.png').write_bytes(doodles)

now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
counts = {}
for o in objects.values():
    counts[o.get('kind')] = counts.get(o.get('kind'), 0) + 1
stats = ' · '.join(f'{v} {k}{"s" if v != 1 else ""}' for k, v in sorted(counts.items()))

manifest = {'board': board, 'rev': doc.get('rev'), 'built_at': now, 'objects': len(objects),
            'kinds': counts, 'files': ['index.html', 'board.png', 'photo.png', 'ink.png', 'board.json'],
            'rect': [round(v, 1) for v in rect]}
(out / 'board.json').write_text(json.dumps(doc, indent=1))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=1))

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Charrette · {board}</title>
<style>
  :root {{ --ink:#111318; --muted:#5C6B70; --teal:#0F766E; --hair:#D6DEE6; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:48px 40px; background:#F4F6F8; color:var(--ink);
         font:16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .sheet {{ max-width:1040px; margin:0 auto; background:#fff; border:1px solid var(--hair);
            border-radius:14px; overflow:hidden; box-shadow:0 1px 2px rgba(16,24,32,.06); }}
  header {{ display:flex; align-items:baseline; gap:14px; padding:22px 28px 0; }}
  .wordmark {{ font-size:15px; letter-spacing:.22em; text-transform:uppercase; color:var(--teal); font-weight:650; }}
  h1 {{ font-size:26px; margin:6px 28px 2px; letter-spacing:-.01em; }}
  .meta {{ margin:0 28px 22px; color:var(--muted); font-size:13px; }}
  .grid {{ display:grid; grid-template-columns:1.25fr 1fr; gap:0; border-top:1px solid var(--hair); }}
  .cell {{ padding:22px 28px; }}
  .cell + .cell {{ border-left:1px solid var(--hair); }}
  .label {{ font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); margin-bottom:10px; }}
  img {{ width:100%; height:auto; display:block; border-radius:8px; }}
  .board {{ border:1px solid var(--hair); }}
  .caption {{ margin-top:14px; font-size:14px; color:var(--muted); }}
  .caption b {{ color:var(--ink); font-weight:600; }}
  footer {{ border-top:1px solid var(--hair); padding:14px 28px; color:var(--muted); font-size:12px;
            display:flex; justify-content:space-between; gap:16px; }}
  code {{ font-size:12px; background:#F4F6F8; border:1px solid var(--hair); border-radius:4px; padding:1px 5px; }}
</style></head>
<body>
  <div class="sheet">
    <header><span class="wordmark">charrette</span></header>
    <h1>One board, one session</h1>
    <p class="meta">board <b>{board}</b> · rev {doc.get('rev')} · {stats} · captured {now}</p>
    <div class="grid">
      <div class="cell">
        <div class="label">the board as drawn</div>
        <img class="board" src="{b64(whole)}" alt="board capture">
        <p class="caption">The hand-drawn triangle, the red fill traced from its own points, the
        linked photo, and everything else that landed in the room. Rendered from the document by
        <code>render_crop</code>, no browser.</p>
      </div>
      <div class="cell">
        <div class="label">the asset it points at</div>
        <img src="{b64(photo_bytes) if photo_bytes else ''}" alt="linked photo">
        <p class="caption"><b>{photo_obj.get('label', 'image') if photo_obj else 'no image on this board'}</b>
        — the board's own image object, pulled with the capture and inlined here so this file needs nothing else.</p>
        <div class="label" style="margin-top:22px">the human's ink alone</div>
        <img src="{b64(doodles)}" alt="hand-drawn strokes">
      </div>
    </div>
    <footer>
      <span>built by <code>make_artifact.py {board}</code> from {store.root}</span>
      <span>{len(objects)} objects · {manifest['rect']}</span>
    </footer>
  </div>
</body></html>
"""
(out / 'index.html').write_text(html)
print(out / 'index.html')
print(out / 'manifest.json')
print('bytes:', (out / 'index.html').stat().st_size)
