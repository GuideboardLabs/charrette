/**
 * Charrette drawing engine — framework-free, no imports.
 *
 * Shared verbatim by the web page (module import) and the desktop pane
 * (inlined by scripts/bundle.py). It owns board coordinates, the camera, hit
 * testing, text measurement, diagram layout and painting. It never touches the
 * network: every mutation is reported through `opts.onCommit`.
 */

export const PALETTE = [
  { name: 'red', ink: '#D92D20' },
  { name: 'blue', ink: '#1570EF' },
  { name: 'green', ink: '#12805C' },
  { name: 'black', ink: '#111318' },
  { name: 'purple', ink: '#7A5AF8' }
]

export const THEME = {
  surface: '#FFFFFF',
  dot: '#D7DFE6',
  dotPitch: 24,
  ink: '#111318',
  agentInk: '#0F766E',
  agentTint: '#E6F4F1',
  emphasis: '#B42318',
  hairline: '#D6DEE6',
  label: '#5C6B70',
  font: "'Charrette Hand', 'Segoe Print', 'Bradley Hand', ui-rounded, ui-sans-serif, sans-serif",
  uiFont: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif'
}

const MIN_Z = 0.01
const MAX_Z = 4
const TEXT_SIZES = [17, 22, 28]
const LAYER = { agentDiagram: 10, humanInk: 20, agentAnnotation: 30 }

const cursorSvg = (body, hotX, hotY, fallback = 'crosshair') => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="white" stroke="#111318" stroke-width="1.5" stroke-linejoin="round">${body}</g></svg>`
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") ${hotX} ${hotY}, ${fallback}`
}

const TOOL_CURSORS = {
  select: 'default',
  hand: 'grab',
  pen: cursorSvg('<path d="m4 20 2-6L16.5 3.5l4 4L10 18z"/><path d="m6 14 4 4"/>', 4, 20),
  highlighter: cursorSvg('<path d="m5 17 3 3 11-11-4-4z"/><path d="M3 21h9"/>', 5, 19),
  eraser: cursorSvg('<path d="m4 15 8-10 7 6-7 9H8z"/><path d="m8 20 5-6"/>', 5, 18, 'default'),
  rect: cursorSvg('<rect x="4" y="4" width="16" height="16" rx="1"/>', 4, 4),
  ellipse: cursorSvg('<ellipse cx="12" cy="12" rx="9" ry="7"/>', 3, 5),
  diamond: cursorSvg('<path d="m12 2 10 10-10 10L2 12z"/>', 2, 12),
  triangle: cursorSvg('<path d="m12 3 10 18H2z"/>', 12, 3),
  line: cursorSvg('<path d="M4 20 20 4"/><circle cx="4" cy="20" r="2"/>', 4, 20),
  arrow: cursorSvg('<path d="M3 20 20 3M13 3h7v7"/>', 3, 20),
  table: cursorSvg('<rect x="3" y="4" width="18" height="16"/><path d="M3 10h18M9 4v16M15 4v16"/>', 3, 4, 'cell'),
  chart: cursorSvg('<path d="M3 21h18M5 18v-5h4v5m2 0V7h4v11m2 0v-9h4v9"/>', 3, 21, 'cell'),
  chartline: cursorSvg('<path d="M3 20h18M5 15l5-5 4 3 6-8"/><circle cx="5" cy="15" r="1.5"/><circle cx="20" cy="5" r="1.5"/>', 3, 20, 'cell'),
  text: 'text'
}

// ---------------------------------------------------------------- utilities

function hash32(str) {
  let h = 2166136261
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** Deterministic jitter, so an object looks the same on every frame. */
function jitter(seed, i, amp) {
  const h = hash32(seed + ':' + i)
  return ((h % 2000) / 1000 - 1) * amp
}

function bboxOf(points) {
  const xs = points.map(p => p[0])
  const ys = points.map(p => p[1])
  const x0 = Math.min(...xs)
  const y0 = Math.min(...ys)
  return [x0, y0, Math.max(...xs) - x0, Math.max(...ys) - y0]
}

function normBBox(b) {
  if (!Array.isArray(b)) return [0, 0, 0, 0]
  const [x, y, w, h] = b
  return [w < 0 ? x + w : x, h < 0 ? y + h : y, Math.abs(w), Math.abs(h)]
}

function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1
  const dy = y2 - y1
  const len = dx * dx + dy * dy
  let t = len ? ((px - x1) * dx + (py - y1) * dy) / len : 0
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

function pointInBox(px, py, b, pad = 6) {
  const [x, y, w, h] = normBBox(b)
  return px >= x - pad && px <= x + w + pad && py >= y - pad && py <= y + h + pad
}

function uid(prefix) {
  return prefix + Math.random().toString(36).slice(2, 7)
}

function overlap(a, b) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

// ------------------------------------------------------------------ layout

function measureLabel(ctx, text, size) {
  ctx.save()
  ctx.font = size + 'px ' + THEME.font
  const w = ctx.measureText(text || '').width
  ctx.restore()
  return w
}

function wrapLabel(ctx, text, size, maxWidth) {
  const words = String(text || '').split(/\s+/).filter(Boolean)
  if (!words.length) return ['']
  const lines = []
  let line = words[0]
  for (let i = 1; i < words.length; i += 1) {
    const next = line + ' ' + words[i]
    if (measureLabel(ctx, next, size) <= maxWidth) line = next
    else { lines.push(line); line = words[i] }
  }
  lines.push(line)
  return lines.slice(0, 3)
}

/**
 * Deterministic layered layout for a diagram spec. No dagre: the cap is nine
 * nodes, and owning it means owning ports, label wrapping and the lint pass.
 */
function layoutDiagram(ctx, spec) {
  const nodes = (spec.nodes || []).slice(0, 9)
  const edges = (spec.edges || []).slice(0, 24)
  const dir = spec.dir === 'TB' ? 'TB' : 'LR'
  const index = new Map(nodes.map((n, i) => [n.key || String(i), i]))
  const depth = new Array(nodes.length).fill(-1)

  const incoming = new Map()
  for (const e of edges) {
    const a = index.get(e.from)
    const b = index.get(e.to)
    if (a === undefined || b === undefined) continue
    if (!incoming.has(b)) incoming.set(b, [])
    incoming.get(b).push(a)
  }
  const roots = nodes.map((_, i) => i).filter(i => !(incoming.get(i) || []).length)
  const queue = (roots.length ? roots : [0]).slice()
  for (const r of queue) depth[r] = 0
  let guard = 0
  while (queue.length && guard < 200) {
    const i = queue.shift()
    for (const e of edges) {
      const a = index.get(e.from)
      const b = index.get(e.to)
      if (a !== i || b === undefined) continue
      const d = depth[i] + 1
      if (depth[b] < d) { depth[b] = d; queue.push(b) }
    }
    guard += 1
  }
  for (let i = 0; i < depth.length; i += 1) if (depth[i] < 0) depth[i] = 0

  const size = 17
  const boxes = nodes.map((n, i) => {
    const lines = wrapLabel(ctx, n.label || '', size, 168)
    const textW = Math.max(...lines.map(l => measureLabel(ctx, l, size)), 24)
    const w = Math.max(112, Math.min(212, textW + 32))
    const h = Math.max(46, lines.length * (size + 4) + 18)
    return { key: n.key || String(i), i, lines, w, h, rank: depth[i],
      tone: n.tone || 'neutral', shape: n.shape || 'box', label: n.label || '' }
  })

  const ranks = []
  for (const b of boxes) {
    if (!ranks[b.rank]) ranks[b.rank] = []
    ranks[b.rank].push(b)
  }
  const rankGap = 92
  const nodeGap = 36
  let cursor = 0
  for (let r = 0; r < ranks.length; r += 1) {
    const row = ranks[r]
    if (!row) continue
    const extent = Math.max(...row.map(b => (dir === 'LR' ? b.w : b.h)))
    let along = 0
    for (const b of row) {
      if (dir === 'LR') { b.x = cursor; b.y = along } else { b.x = along; b.y = cursor }
      along += (dir === 'LR' ? b.h : b.w) + nodeGap
    }
    cursor += extent + rankGap
  }

  // lint: no node may overlap another; nudge along the stacked axis on collision
  const placed = []
  for (const b of boxes.slice().sort((p, q) => (p.rank - q.rank) || (p.y - q.y))) {
    let tries = 0
    while (placed.some(o => overlap(o, b)) && tries < 24) {
      if (dir === 'LR') b.y += 16
      else b.x += 16
      tries += 1
    }
    placed.push(b)
  }

  const minX = Math.min(...boxes.map(b => b.x))
  const minY = Math.min(...boxes.map(b => b.y))
  for (const b of boxes) { b.x -= minX; b.y -= minY }

  const width = Math.max(...boxes.map(b => b.x + b.w))
  const height = Math.max(...boxes.map(b => b.y + b.h))
  const byKey = new Map(boxes.map(b => [b.key, b]))

  let extra = 0
  const links = edges.map((e, i) => {
    const a = byKey.get(e.from)
    const b = byKey.get(e.to)
    if (!a || !b) return null
    const span = b.rank - a.rank
    const outside = span > 1 || span <= 0 // skip-rank or backward: route around
    if (outside) {
      const yOut = height + 40
      extra = Math.max(extra, 40)
      const from = [a.x + a.w / 2, a.y + a.h]
      const to = [b.x + b.w / 2, b.y + b.h]
      return { key: 'e' + i, pts: [from, [from[0], yOut], [to[0], yOut], to],
        label: e.label || '', style: e.style || 'solid', from: a.key, to: b.key }
    }
    if (dir === 'LR') {
      const from = [a.x + a.w, a.y + a.h / 2]
      const to = [b.x, b.y + b.h / 2]
      const midX = (from[0] + to[0]) / 2
      return { key: 'e' + i, pts: [from, [midX, from[1]], [midX, to[1]], to],
        label: e.label || '', style: e.style || 'solid', from: a.key, to: b.key }
    }
    const from = [a.x + a.w / 2, a.y + a.h]
    const to = [b.x + b.w / 2, b.y]
    const midY = (from[1] + to[1]) / 2
    return { key: 'e' + i, pts: [from, [from[0], midY], [to[0], midY], to],
      label: e.label || '', style: e.style || 'solid', from: a.key, to: b.key }
  }).filter(Boolean)

  return { boxes, links, width, height: height + extra, dir }
}

// ------------------------------------------------------------------ engine

export function createBoard(canvas, opts = {}) {
  const ctx = canvas.getContext('2d')
  const state = {
    doc: { objects: {}, camera: { x: 0, y: 0, z: 1 }, rev: 0 },
    cam: { x: 0, y: 0, z: 1 },
    tool: 'pen',
    ink: PALETTE[0].ink,
    width: 3,
    size: TEXT_SIZES[1],
    selection: new Set(),
    images: new Map(),
    scenes: new Map(),
    placedSent: new Map(),
    drag: null,
    hover: null,
    history: [],
    fontReady: false,
    // replay: id -> 0..1 progress, and the timeline that drives it. Empty = draw everything.
    reveal: new Map(),
    timeline: []
  }

  function revealOf(o) {
    if (!state.timeline.length) return 1
    const f = state.reveal.get(o._key || o.id)
    return f === undefined ? 0 : f
  }

  // ---------------------------------------------------------- coordinates
  function dpr() { return window.devicePixelRatio || 1 }

  function resize() {
    const r = canvas.getBoundingClientRect()
    const w = Math.max(1, Math.round(r.width))
    const h = Math.max(1, Math.round(r.height))
    const d = dpr()
    if (canvas.width !== Math.round(w * d) || canvas.height !== Math.round(h * d)) {
      canvas.width = Math.round(w * d)
      canvas.height = Math.round(h * d)
    }
    ctx.setTransform(d, 0, 0, d, 0, 0)
    return { w, h }
  }

  function toBoard(ev) {
    const r = canvas.getBoundingClientRect()
    const sx = ev.clientX - r.left
    const sy = ev.clientY - r.top
    return { x: sx / state.cam.z + state.cam.x, y: sy / state.cam.z + state.cam.y, sx, sy }
  }

  function toScreen(x, y) {
    return { x: (x - state.cam.x) * state.cam.z, y: (y - state.cam.y) * state.cam.z }
  }

  // --------------------------------------------------------------- objects
  function layerOf(o) {
    if (o.layer === 'agent-diagram') return LAYER.agentDiagram
    if (o.layer === 'agent-annotation') return LAYER.agentAnnotation
    return LAYER.humanInk
  }

  function objects() {
    return Object.entries(state.doc.objects || {})
      .sort((a, b) => layerOf(a[1]) - layerOf(b[1]) ||
        String(a[1].z || '').localeCompare(String(b[1].z || '')))
  }

  function measureTextObject(o) {
    const lines = String(o.text || '').split('\n')
    const size = o.size || 22
    const w = Math.max(...lines.map(l => measureLabel(ctx, l, size)), 12) + 4
    const h = lines.length * size * 1.15
    return [o.x, o.y, w, h]
  }

  function bboxFor(o) {
    if (o.kind === 'stroke' || o.kind === 'highlight') return bboxOf(o.pts && o.pts.length ? o.pts : [[0, 0]])
    if (o.kind === 'arrow' || o.kind === 'line') {
      return bboxOf([endpointPos(o.from), endpointPos(o.to)])
    }
    if (o.kind === 'scene') {
      const lay = layoutFor(o)
      if (!lay) return o.bbox || [0, 0, 0, 0]
      const [ax, ay] = anchorOrigin(o, lay)
      return [ax, ay, lay.width, lay.height]
    }
    if (o.kind === 'text') return measureTextObject(o)
    return o.bbox || [0, 0, 0, 0]
  }

  function endpointPos(end) {
    if (!end) return [0, 0]
    if (end.bind) {
      const target = (state.doc.objects || {})[end.bind]
      if (!target) return end.point || [0, 0]
      const b = normBBox(bboxFor(target))
      const port = end.port || 'auto'
      if (port === 'n') return [b[0] + b[2] / 2, b[1]]
      if (port === 's') return [b[0] + b[2] / 2, b[1] + b[3]]
      if (port === 'w') return [b[0], b[1] + b[3] / 2]
      if (port === 'e') return [b[0] + b[2], b[1] + b[3] / 2]
      return [b[0] + b[2] / 2, b[1] + b[3] / 2]
    }
    return end.point || [0, 0]
  }

  function anchorOrigin(o, lay) {
    const a = o.anchor
    if (a && a.to) {
      const target = (state.doc.objects || {})[a.to]
      if (target) {
        const b = normBBox(bboxFor(target))
        const gap = a.gap || 64
        const side = a.side || 'right'
        if (side === 'right') return [b[0] + b[2] + gap, b[1]]
        if (side === 'left') return [b[0] - gap - lay.width, b[1]]
        if (side === 'below') return [b[0], b[1] + b[3] + gap]
        return [b[0], b[1] - gap - lay.height]
      }
    }
    if (Array.isArray(o.placement)) return o.placement
    // `placement` is geometry ({at:[x,y]}) everywhere the engine and the agent tools write it.
    // Guard the type: a string here ('placed' as a status) makes `.at` resolve to
    // String.prototype.at, and this function then returns a method instead of a point.
    if (o.placement && Array.isArray(o.placement.at) && o.placement.at.length >= 2) return o.placement.at
    if (o.bbox) return [o.bbox[0], o.bbox[1]]
    return [0, 0]
  }

  function layoutFor(o) {
    const key = o._key || o.id || 'scene'
    const sig = key + '|' + JSON.stringify(o.spec || {})
    const cached = state.scenes.get(key)
    if (cached && cached.sig === sig) return cached.layout
    if (!o.spec) return null
    const layout = layoutDiagram(ctx, o.spec)
    state.scenes.set(key, { sig, layout })
    // the renderer owns geometry, so it reports the rect back once it knows it
    if (opts.onPlaced) {
      const [ax, ay] = anchorOrigin(o, layout)
      state.lastPlaced = { id: o._key, ax, ay, w: layout.width, h: layout.height, anchor: o.anchor }
      const rect = [Math.round(ax), Math.round(ay), Math.round(layout.width), Math.round(layout.height)]
      const nodes = {}
      for (const b of layout.boxes) nodes[b.key] = [Math.round(ax + b.x), Math.round(ay + b.y), Math.round(b.w), Math.round(b.h)]
      const sigRect = rect.join(',') + '|' + JSON.stringify(nodes)
      const prev = state.placedSent.get(key)
      if (prev !== sigRect) {
        state.placedSent.set(key, sigRect)
        opts.onPlaced(o._key, rect, nodes)
      }
    }
    return layout
  }

  // --------------------------------------------------------------- painting
  function wobble(points, seed, amp, passes = 2) {
    ctx.beginPath()
    for (let p = 0; p < passes; p += 1) {
      for (let i = 0; i < points.length; i += 1) {
        const x = points[i][0] + jitter(seed, p * 97 + i * 2, amp)
        const y = points[i][1] + jitter(seed, p * 97 + i * 2 + 1, amp)
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
    }
  }

  function arrowHead(x, y, angle, size, colour) {
    ctx.save()
    ctx.fillStyle = colour
    ctx.beginPath()
    ctx.moveTo(x, y)
    ctx.lineTo(x - size * Math.cos(angle - 0.38), y - size * Math.sin(angle - 0.38))
    ctx.lineTo(x - size * Math.cos(angle + 0.38), y - size * Math.sin(angle + 0.38))
    ctx.closePath()
    ctx.fill()
    ctx.restore()
  }

  function screenPoints(pts) {
    return pts.map(([x, y]) => [(x - state.cam.x) * state.cam.z, (y - state.cam.y) * state.cam.z])
  }

  // Ink appears at human pen speed: a replay hands this a fraction and gets the
  // first N units of the path, not the whole stroke at once.
  function upToFraction(pts, frac) {
    if (frac >= 1 || pts.length < 2) return pts
    const lengths = []
    let total = 0
    for (let i = 1; i < pts.length; i += 1) {
      const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
      lengths.push(d)
      total += d
    }
    if (!total) return [pts[0]]
    const target = total * Math.max(0, frac)
    const out = [pts[0]]
    let acc = 0
    for (let i = 1; i < pts.length; i += 1) {
      const seg = lengths[i - 1]
      if (acc + seg <= target) {
        out.push(pts[i])
        acc += seg
      } else {
        const r = seg > 0 ? (target - acc) / seg : 0
        out.push([pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * r,
          pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * r])
        break
      }
    }
    return out
  }

  function paintStroke(o, frac = 1) {
    const raw = o.pts || []
    if (raw.length < 2) return
    const pts = screenPoints(upToFraction(raw, frac))
    ctx.save()
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.strokeStyle = o.ink || (o.actor === 'agent' ? THEME.agentInk : THEME.ink)
    if (o.kind === 'highlight' || o.highlight) {
      ctx.globalCompositeOperation = 'multiply'
      ctx.globalAlpha = 0.38
      ctx.lineWidth = Math.max(2, 18 * state.cam.z)
    } else {
      ctx.lineWidth = Math.max(0.7, (o.w || 3) * state.cam.z)
    }
    ctx.beginPath()
    ctx.moveTo(pts[0][0], pts[0][1])
    for (let i = 1; i < pts.length - 1; i += 1) {
      ctx.quadraticCurveTo(pts[i][0], pts[i][1], (pts[i][0] + pts[i + 1][0]) / 2,
        (pts[i][1] + pts[i + 1][1]) / 2)
    }
    const last = pts[pts.length - 1]
    ctx.lineTo(last[0], last[1])
    ctx.stroke()
    ctx.restore()

    // A stroke label is a caption beside the mark: it was stored by board_stroke but never painted,
    // so an agent could say "labelled it" and leave nothing visible.
    if (o.label && frac > 0.5) {
      const bb = normBBox(o.bbox)
      const p = toScreen(bb[0] + bb[2] + 8, bb[1] + bb[3] / 2)
      ctx.save()
      ctx.globalAlpha *= frac >= 1 ? 1 : 0.55
      ctx.font = `${Math.max(9, 15 * state.cam.z)}px ${THEME.font}`
      ctx.fillStyle = o.ink || (o.actor === 'agent' ? THEME.agentInk : THEME.ink)
      ctx.textBaseline = 'middle'
      ctx.fillText(String(o.label), p.x, p.y)
      ctx.restore()
    }
  }

  // Where an object starts, in board coords. Arrows are the awkward case: they store a `from`
  // point and a `to` that may be a bind to another object, so they have no bbox at all and a
  // bbox fallback drops the cursor at the origin.
  function bindPoint(spec) {
    if (!spec) return null
    if (Array.isArray(spec.point)) return spec.point
    if (spec.bind) {
      const t = state.doc.objects && state.doc.objects[spec.bind]
      if (t && t.bbox) return [t.bbox[0] + t.bbox[2] / 2, t.bbox[1] + t.bbox[3] / 2]
      if (t && t.x !== undefined) return [t.x, t.y]
    }
    return null
  }

  function anchorBoardPoint(o) {
    if (o.pts && o.pts.length) return o.pts[0]
    if (o.kind === 'arrow' || o.kind === 'line') {
      const a = bindPoint(o.from)
      const b = bindPoint(o.to)
      if (a && b) return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
      if (a || b) return a || b
    }
    if (o.bbox) return [o.bbox[0] + o.bbox[2] / 2, o.bbox[1] + o.bbox[3] / 2]
    return [o.x || 0, o.y || 0]
  }

  // One poly builder for both the painter and the cursor: if these drift, the pen tip walks
  // beside the line instead of on it.
  function shapePolyScreen(o) {
    const box = normBBox(o.bbox)
    const z = state.cam.z
    const sx = (box[0] - state.cam.x) * z
    const sy = (box[1] - state.cam.y) * z
    const sw = Math.max(1, box[2] * z)
    const sh = Math.max(1, box[3] * z)
    let poly
    if (o.shape === 'poly' && Array.isArray(o.pts) && o.pts.length > 2) {
      // A polygon traced from real ink: vertices are board points, so the fill hugs the
      // outline it was derived from instead of the bounding box that contains it.
      poly = o.pts.map(([px, py]) => { const p = toScreen(px, py); return [p.x, p.y] })
      poly.push(poly[0])
    } else if (o.shape === 'ellipse') {
      const steps = 30
      poly = []
      for (let i = 0; i < steps; i += 1) {
        const a = (i / steps) * Math.PI * 2
        poly.push([sx + sw / 2 + (sw / 2) * Math.cos(a), sy + sh / 2 + (sh / 2) * Math.sin(a)])
      }
      poly.push(poly[0])
    } else if (o.shape === 'diamond') {
      poly = [[sx + sw / 2, sy], [sx + sw, sy + sh / 2], [sx + sw / 2, sy + sh],
        [sx, sy + sh / 2], [sx + sw / 2, sy]]
    } else if (o.shape === 'triangle') {
      poly = [[sx + sw / 2, sy], [sx + sw, sy + sh], [sx, sy + sh], [sx + sw / 2, sy]]
    } else if (o.shape === 'line') {
      poly = [[sx, sy], [sx + sw, sy + sh]]
    } else {
      poly = [[sx, sy], [sx + sw, sy], [sx + sw, sy + sh], [sx, sy + sh], [sx, sy]]
    }
    return poly
  }

  function paintShape(o, frac = 1) {
    const box = normBBox(o.bbox)
    const z = state.cam.z
    const sx = (box[0] - state.cam.x) * z
    const sy = (box[1] - state.cam.y) * z
    const sw = Math.max(1, box[2] * z)
    const sh = Math.max(1, box[3] * z)
    const colour = o.ink || (o.actor === 'agent' ? THEME.agentInk : THEME.ink)
    const amp = Math.min(1.7, 2.2 * z)
    const seed = o.id || 'shape'
    ctx.save()
    ctx.strokeStyle = colour
    ctx.lineWidth = Math.max(0.8, (o.w || 2) * z)
    const poly = shapePolyScreen(o)
    // A fill is a clean polygon under the sketched outline: the wobble belongs to the stroke,
    // and an unfilled shape keeps the marker-only look.
    // the fill lands after the outline closes, and fades in the way someone colours it in
    if (o.fill && o.shape !== 'line' && frac >= 0.75) {
      ctx.save()
      ctx.fillStyle = o.fill
      if (frac < 1) ctx.globalAlpha *= (frac - 0.75) / 0.25
      ctx.beginPath()
      poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
      ctx.closePath()
      ctx.fill()
      ctx.restore()
    }
    // A polygon traced from ink is filled, not outlined: a wobbled red edge of its own
    // wanders a pixel or two outside the black line it was derived from, which reads as
    // the fill leaking. The ink already supplies the outline.
    if (!(o.fill && o.shape === 'poly')) {
      const drawn = frac >= 1 ? poly : upToFraction(poly.concat([poly[0]]), frac)
      wobble(drawn.length > 1 ? drawn : poly, seed, amp)
      ctx.stroke()
    }
    ctx.restore()
  }

  function paintArrow(o, frac = 1) {
    const z = state.cam.z
    // screenPoints yields [x, y] pairs, not {x, y}: reading .x/.y here produced NaN for every
    // arrow on every board, so bound arrows were invisible rather than merely misplaced.
    const [ax, ay] = screenPoints([endpointPos(o.from)])[0]
    const [bx, by] = screenPoints([endpointPos(o.to)])[0]
    const colour = o.ink || (o.actor === 'agent' ? THEME.agentInk : THEME.ink)
    // a replay draws the shaft, then lands the head
    const tipX = frac >= 1 ? bx : ax + (bx - ax) * frac
    const tipY = frac >= 1 ? by : ay + (by - ay) * frac
    ctx.save()
    ctx.strokeStyle = colour
    ctx.lineWidth = Math.max(0.8, (o.w || 2) * z)
    ctx.setLineDash(o.style === 'dashed' ? [6 * z, 5 * z] : [])
    ctx.beginPath()
    ctx.moveTo(ax, ay)
    ctx.lineTo(tipX, tipY)
    ctx.stroke()
    ctx.restore()
    if (frac > 0.9) arrowHead(bx, by, Math.atan2(by - ay, bx - ax), Math.max(7, 9 * z), colour)
    if (o.label && frac > 0.85) {
      ctx.save()
      ctx.font = `${Math.max(9, 13 * z)}px ${THEME.font}`
      ctx.fillStyle = colour
      ctx.fillText(o.label, (ax + bx) / 2 + 6, (ay + by) / 2 - 6)
      ctx.restore()
    }
  }

  function paintText(o, frac = 1) {
    const z = state.cam.z
    const p = toScreen(o.x, o.y)
    const size = Math.max(7, (o.size || 22) * z)
    ctx.save()
    ctx.font = `${size}px ${THEME.font}`
    ctx.fillStyle = o.ink || (o.actor === 'agent' ? THEME.agentInk : THEME.ink)
    ctx.textBaseline = 'top'
    const full = String(o.text || '')
    const shown = frac >= 1 ? full : full.slice(0, Math.ceil(full.length * Math.max(0, frac)))
    shown.split('\n').forEach((line, i) => {
      ctx.fillText(line, p.x, p.y + i * size * 1.15)
    })
    ctx.restore()
  }

  function paintScene(o) {
    const lay = layoutFor(o)
    if (!lay) return
    const [ax, ay] = anchorOrigin(o, lay)
    const z = state.cam.z
    const seed = o.id || 'scene'
    ctx.save()
    ctx.translate((ax - state.cam.x) * z, (ay - state.cam.y) * z)
    ctx.scale(z, z)
    for (const l of lay.links) {
      ctx.save()
      ctx.strokeStyle = THEME.agentInk
      ctx.lineWidth = 1.5
      ctx.setLineDash(l.style === 'dashed' ? [6, 5] : [])
      wobble(l.pts, seed + l.key, 1.0, 1)
      ctx.stroke()
      ctx.restore()
      const tip = l.pts[l.pts.length - 1]
      const prev = l.pts[l.pts.length - 2]
      arrowHead(tip[0], tip[1], Math.atan2(tip[1] - prev[1], tip[0] - prev[0]), 9, THEME.agentInk)
      if (l.label) {
        const seg = Math.min(l.pts.length - 2, Math.max(0, Math.floor(l.pts.length * 0.35)))
        const p0 = l.pts[seg]
        const p1 = l.pts[seg + 1]
        ctx.save()
        ctx.font = `13px ${THEME.font}`
        ctx.textBaseline = 'middle'
        const mx = (p0[0] + p1[0]) / 2
        const my = (p0[1] + p1[1]) / 2
        const vertical = Math.abs(p1[0] - p0[0]) < 1
        ctx.fillStyle = '#FFFFFF'
        const w = ctx.measureText(l.label).width
        ctx.fillRect(mx + (vertical ? 5 : -w / 2), my - 9, w + 6, 18)
        ctx.fillStyle = THEME.agentInk
        ctx.fillText(l.label, mx + (vertical ? 8 : -w / 2) + 3, my)
        ctx.restore()
      }
    }
    for (const b of lay.boxes) {
      const tone = b.tone === 'good' ? '#12805C' : b.tone === 'bad' ? THEME.emphasis
        : b.tone === 'warn' ? '#B45309' : THEME.agentInk
      ctx.save()
      ctx.fillStyle = b.tone === 'highlight' ? THEME.agentTint : '#FFFFFF'
      const rect = [[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h], [b.x, b.y]]
      wobble(rect, seed + b.key, 1.2, 2)
      ctx.fill()
      ctx.strokeStyle = tone
      ctx.lineWidth = 1.5
      ctx.stroke()
      ctx.fillStyle = THEME.ink
      ctx.font = `17px ${THEME.font}`
      ctx.textBaseline = 'middle'
      ctx.textAlign = 'center'
      const cy = b.y + b.h / 2 - ((b.lines.length - 1) * 20) / 2
      b.lines.forEach((line, i) => ctx.fillText(line, b.x + b.w / 2, cy + i * 20))
      ctx.restore()
    }
    ctx.restore()
  }

  function paintAnnotation(o) {
    const z = state.cam.z
    const p = toScreen((o.point || [0, 0])[0], (o.point || [0, 0])[1])
    const colour = o.style === 'emphasis' ? THEME.emphasis : THEME.agentInk
    const r = Math.max(7, 13 * z)
    ctx.save()
    ctx.strokeStyle = colour
    ctx.lineWidth = 2
    if (o.style === 'circle') {
      const steps = 30
      const pts = []
      for (let i = 0; i <= steps; i += 1) {
        const a = (i / steps) * Math.PI * 2
        pts.push([p.x + r * 1.7 * Math.cos(a), p.y + r * 1.25 * Math.sin(a)])
      }
      wobble(pts, o.id || 'an', 1.1, 1)
      ctx.stroke()
    } else if (o.style === 'cross') {
      ctx.beginPath()
      ctx.moveTo(p.x - r, p.y - r); ctx.lineTo(p.x + r, p.y + r)
      ctx.moveTo(p.x + r, p.y - r); ctx.lineTo(p.x - r, p.y + r)
      ctx.stroke()
    } else {
      ctx.beginPath()
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2)
      ctx.stroke()
    }
    if (o.label) {
      ctx.fillStyle = colour
      ctx.font = `${Math.max(10, 13 * z)}px ${THEME.font}`
      ctx.textBaseline = 'middle'
      ctx.fillText(o.label, p.x + r + 8, p.y)
    }
    ctx.restore()
  }

  function paintImage(o) {
    const box = normBBox(o.bbox)
    const z = state.cam.z
    const x = (box[0] - state.cam.x) * z
    const y = (box[1] - state.cam.y) * z
    const w = box[2] * z
    const h = box[3] * z
    const img = state.images.get(o.src)
    if (o.status === 'rendering' || o.status === 'failed') {
      ctx.save()
      ctx.fillStyle = o.status === 'failed' ? 'rgba(180,35,24,0.05)' : 'rgba(15,118,110,0.05)'
      ctx.fillRect(x, y, w, h)
      ctx.setLineDash([6, 5])
      ctx.strokeStyle = o.status === 'failed' ? THEME.emphasis : THEME.agentInk
      ctx.lineWidth = 1.2
      ctx.strokeRect(x, y, w, h)
      ctx.setLineDash([])
      ctx.fillStyle = o.status === 'failed' ? THEME.emphasis : THEME.agentInk
      ctx.font = `11px ${THEME.uiFont}`
      ctx.fillText(o.status === 'failed' ? 'image failed' : 'rendering…', x + 10, y + 18)
      ctx.restore()
      return
    }
    if (!img || !img.complete) return
    ctx.save()
    ctx.drawImage(img, x, y, w, h)
    ctx.restore()
    if (o.label) {
      ctx.save()
      ctx.font = `${Math.max(9, 12 * z)}px ${THEME.font}`
      ctx.fillStyle = THEME.label
      ctx.fillText(o.label, x, y + h + 13 * z)
      ctx.restore()
    }
  }

  function questionMetrics(o) {
    const bb = normBBox(o.bbox)
    const titleSize = 15
    const titleLines = wrapLabel(ctx, o.question || '', titleSize, bb[2] - 24).slice(0, 3)
    const headH = 16 + titleLines.length * (titleSize + 5)
    const optH = 28
    const opts = o.options || []
    return { bb, titleSize, titleLines, headH, optH,
      height: headH + opts.length * optH + 10 }
  }

  function paintQuestion(o) {
    const z = state.cam.z
    const m = questionMetrics(o)
    // a leader from the card back to whatever it is asking about
    if (o.anchor && o.anchor.to) {
      const target = (state.doc.objects || {})[o.anchor.to]
      if (target) {
        const tb = normBBox(bboxFor(target))
        const from = toScreen(m.bb[0], m.bb[1] + 20)
        const to = toScreen(tb[0] + tb[2], tb[1] + tb[3] / 2)
        ctx.save()
        ctx.strokeStyle = 'rgba(15,118,110,0.45)'
        ctx.lineWidth = 1.2
        ctx.setLineDash([3, 4])
        ctx.beginPath()
        ctx.moveTo(from.x, from.y)
        ctx.lineTo(to.x, to.y)
        ctx.stroke()
        ctx.restore()
      }
    }
    const x = (m.bb[0] - state.cam.x) * z
    const y = (m.bb[1] - state.cam.y) * z
    const w = m.bb[2] * z
    const h = m.height * z
    ctx.save()
    ctx.fillStyle = '#FFFFFF'
    ctx.strokeStyle = THEME.agentInk
    ctx.lineWidth = 1.5
    const r = 9 * z
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()
    ctx.fill()
    ctx.stroke()
    ctx.fillStyle = THEME.agentInk
    ctx.font = `${Math.max(9, 10 * z)}px ${THEME.uiFont}`
    ctx.fillText('the agent asks', x + 12 * z, y + 13 * z)
    ctx.fillStyle = THEME.ink
    ctx.font = `${Math.max(10, m.titleSize * z)}px ${THEME.font}`
    ctx.textBaseline = 'top'
    m.titleLines.forEach((line, i) => {
      ctx.fillText(line, x + 12 * z, y + (16 + i * (m.titleSize + 5)) * z)
    })
    const opts = o.options || []
    opts.forEach((opt, i) => {
      const oy = y + (m.headH + i * m.optH) * z
      const chosen = o.answer === opt.id
      if (chosen) {
        ctx.fillStyle = THEME.agentTint
        ctx.fillRect(x + 10 * z, oy, (m.bb[2] - 20) * z, (m.optH - 6) * z)
      } else {
        ctx.strokeStyle = THEME.hairline
        ctx.lineWidth = 1
        ctx.strokeRect(x + 10 * z, oy, (m.bb[2] - 20) * z, (m.optH - 6) * z)
      }
      ctx.fillStyle = chosen ? THEME.agentInk : THEME.ink
      ctx.font = `${Math.max(9, 13 * z)}px ${THEME.uiFont}`
      ctx.fillText((chosen ? '✓ ' : '') + opt.label, x + 18 * z, oy + 7 * z)
    })
    ctx.restore()
  }

  function handlePorts(x, y, w, h) {
    return [
      [x + w / 2, y, 'n'],
      [x + w, y + h / 2, 'e'],
      [x + w / 2, y + h, 's'],
      [x, y + h / 2, 'w']
    ]
  }

  function paintSelection() {
    const z = state.cam.z
    for (const id of state.selection) {
      const o = (state.doc.objects || {})[id]
      if (!o) continue
      const box = normBBox(bboxFor(o))
      const x = (box[0] - state.cam.x) * z
      const y = (box[1] - state.cam.y) * z
      const w = box[2] * z
      const h = box[3] * z
      ctx.save()
      ctx.strokeStyle = THEME.agentInk
      ctx.lineWidth = 1.3
      ctx.setLineDash([4, 3])
      ctx.strokeRect(x - 3, y - 3, w + 6, h + 6)
      ctx.setLineDash([])
      for (const [px, py] of handlePorts(x, y, w, h)) {
        if (o.kind === 'text') {
          ctx.beginPath()
          ctx.arc(px, py, 4.4, 0, Math.PI * 2)
          ctx.fillStyle = '#FFFFFF'
          ctx.fill()
          ctx.strokeStyle = THEME.agentInk
          ctx.lineWidth = 1.6
          ctx.stroke()
        } else {
          ctx.fillStyle = '#FFFFFF'
          ctx.strokeStyle = THEME.agentInk
          ctx.lineWidth = 1.3
          ctx.fillRect(px - 3.5, py - 3.5, 7, 7)
          ctx.strokeRect(px - 3.5, py - 3.5, 7, 7)
        }
      }
      ctx.restore()
    }
  }

  function paintHover() {
    if (!state.hover || state.selection.has(state.hover.id)) return
    const box = normBBox(bboxFor(state.hover.o))
    const z = state.cam.z
    ctx.save()
    ctx.strokeStyle = 'rgba(15,118,110,0.4)'
    ctx.lineWidth = 1
    ctx.setLineDash([3, 4])
    ctx.strokeRect((box[0] - state.cam.x) * z - 3, (box[1] - state.cam.y) * z - 3,
      box[2] * z + 6, box[3] * z + 6)
    ctx.restore()
  }

  function paintEphemeral() {
    const d = state.drag
    if (!d) return
    const z = state.cam.z
    ctx.save()
    if (d.type === 'ink') {
      paintStroke({ pts: d.points, ink: d.ink, w: d.width, highlight: d.highlight })
    } else if (d.type === 'shape') {
      paintShape({ shape: d.shape, bbox: d.bbox, ink: d.ink, w: d.width, id: 'ghost' })
    } else if (d.type === 'arrow' || d.type === 'portArrow') {
      paintArrow({ from: d.type === 'portArrow' ? { bind: d.fromId, port: d.port } : { point: [d.from.x, d.from.y] },
        to: { point: [d.to.x, d.to.y] }, ink: d.ink, w: 2 })
    } else if (d.type === 'marquee') {
      const x0 = Math.min(d.from.x, d.to.x)
      const y0 = Math.min(d.from.y, d.to.y)
      const w = Math.abs(d.to.x - d.from.x)
      const h = Math.abs(d.to.y - d.from.y)
      ctx.fillStyle = 'rgba(15,118,110,0.05)'
      ctx.fillRect((x0 - state.cam.x) * z, (y0 - state.cam.y) * z, w * z, h * z)
      ctx.strokeStyle = 'rgba(15,118,110,0.45)'
      ctx.lineWidth = 1
      ctx.strokeRect((x0 - state.cam.x) * z, (y0 - state.cam.y) * z, w * z, h * z)
    }
    ctx.restore()
  }

  // ----------------------------------------------------------- tables, charts

  function clipTo(ctx, text, width) {
    const str = String(text || '')
    if (ctx.measureText(str).width <= width) return str
    let cut = str
    while (cut.length > 1 && ctx.measureText(cut + '…').width > width) cut = cut.slice(0, -1)
    return cut + '…'
  }

  function tableMetrics(o) {
    const b = normBBox(o.bbox)
    const cells = o.cells || []
    const rows = Math.max(1, Math.min(60, o.rows || cells.length || 3))
    const cols = Math.max(1, Math.min(16, o.cols || (cells[0] || []).length || 3))
    return { b, cells, rows, cols, rh: b[3] / rows, cw: b[2] / cols }
  }

  function tableCellAt(o, pt) {
    const { b, rows, cols, rh, cw } = tableMetrics(o)
    if (pt.x < b[0] || pt.y < b[1] || pt.x > b[0] + b[2] || pt.y > b[1] + b[3]) return null
    return { r: Math.min(rows - 1, Math.floor((pt.y - b[1]) / rh)),
      c: Math.min(cols - 1, Math.floor((pt.x - b[0]) / cw)) }
  }

  function paintTable(o) {
    const z = state.cam.z
    const { b, cells, rows, cols, rh, cw } = tableMetrics(o)
    const [bx, by] = [(b[0] - state.cam.x) * z, (b[1] - state.cam.y) * z]
    const W = b[2] * z
    const H = b[3] * z
    const colour = o.ink || (o.actor === 'agent' ? THEME.agentInk : THEME.ink)
    ctx.save()
    ctx.fillStyle = '#FFFFFF'
    ctx.fillRect(bx, by, W, H)
    ctx.fillStyle = o.actor === 'agent' ? THEME.agentTint : 'rgba(17,19,24,0.045)'
    ctx.fillRect(bx, by, W, rh * z)
    ctx.strokeStyle = colour
    ctx.lineWidth = Math.max(0.8, 1.4 * z)
    ctx.strokeRect(bx, by, W, H)
    ctx.lineWidth = Math.max(0.6, 1 * z)
    ctx.beginPath()
    for (let r = 1; r < rows; r += 1) {
      ctx.moveTo(bx, by + r * rh * z)
      ctx.lineTo(bx + W, by + r * rh * z)
    }
    for (let c = 1; c < cols; c += 1) {
      ctx.moveTo(bx + c * cw * z, by)
      ctx.lineTo(bx + c * cw * z, by + H)
    }
    ctx.stroke()
    const fs = Math.max(7, Math.min(rh * 0.46, 15) * z)
    ctx.font = `${fs}px ${THEME.font}`
    ctx.fillStyle = colour
    ctx.textBaseline = 'middle'
    for (let r = 0; r < rows; r += 1) {
      for (let c = 0; c < cols; c += 1) {
        const val = (cells[r] && cells[r][c]) || ''
        if (!val) continue
        ctx.fillText(clipTo(ctx, val, cw * z - 10 * z), bx + c * cw * z + 5 * z, by + r * rh * z + rh * z / 2)
      }
    }
    if (o.label) {
      ctx.fillStyle = THEME.label
      ctx.font = `${Math.max(9, 12 * z)}px ${THEME.font}`
      ctx.fillText(o.label, bx, by + H + 14 * z)
    }
    ctx.restore()
  }

  function paintChart(o) {
    const z = state.cam.z
    const b = normBBox(o.bbox)
    const [bx, by] = [(b[0] - state.cam.x) * z, (b[1] - state.cam.y) * z]
    const W = Math.max(1, b[2] * z)
    const H = Math.max(1, b[3] * z)
    const values = (o.values || []).map(v => Number(v) || 0)
    const labels = o.labels || []
    const colour = o.ink || THEME.agentInk
    const titleH = o.title ? Math.min(28 * z, H * 0.22) : 0
    const axisH = 18 * z
    // reserve a band above the plot for the value labels, or the tallest bar's label
    // overflows the box and collides with the title
    const labelBand = 15 * z
    const plotH = Math.max(10, H - titleH - axisH - labelBand)
    const maxV = Math.max(1, ...values.map(v => Math.abs(v)))
    const baseY = by + titleH + plotH
    ctx.save()
    ctx.strokeStyle = THEME.hairline
    ctx.lineWidth = Math.max(0.6, 1 * z)
    ctx.beginPath()
    ctx.moveTo(bx, by + titleH)
    ctx.lineTo(bx, baseY)
    ctx.lineTo(bx + W, baseY)
    ctx.stroke()
    if (o.title) {
      ctx.fillStyle = THEME.ink
      ctx.font = `${Math.max(9, Math.min(15, 13 * z))}px ${THEME.font}`
      ctx.textBaseline = 'top'
      ctx.fillText(o.title, bx, by)
    }
    const n = Math.max(1, values.length)
    const slot = W / n
    if (!values.length) {
      ctx.fillStyle = THEME.label
      ctx.font = `${Math.max(8, Math.min(13, 11 * z))}px ${THEME.uiFont}`
      ctx.textAlign = 'center'
      ctx.fillText('double-click to set values', bx + W / 2, by + H / 2)
      ctx.textAlign = 'start'
      ctx.restore()
      return
    }
    if ((o.chart || 'bar') === 'line') {
      ctx.strokeStyle = colour
      ctx.lineWidth = Math.max(1, 2 * z)
      ctx.beginPath()
      values.forEach((v, i) => {
        const px = bx + slot * (i + 0.5)
        const py = baseY - (Math.abs(v) / maxV) * plotH
        if (i === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      })
      ctx.stroke()
      values.forEach((v, i) => {
        const px = bx + slot * (i + 0.5)
        const py = baseY - (Math.abs(v) / maxV) * plotH
        ctx.beginPath()
        ctx.arc(px, py, Math.max(2, 3 * z), 0, Math.PI * 2)
        ctx.fillStyle = colour
        ctx.fill()
      })
    } else {
      const bw = slot * 0.58
      values.forEach((v, i) => {
        const h = (Math.abs(v) / maxV) * plotH
        ctx.fillStyle = colour
        ctx.globalAlpha = 0.85
        ctx.fillRect(bx + slot * i + (slot - bw) / 2, baseY - h, bw, h)
        ctx.globalAlpha = 1
        if (values.length <= 12) {
          ctx.fillStyle = THEME.label
          ctx.font = `${Math.max(7, 10 * z)}px ${THEME.uiFont}`
          ctx.textAlign = 'center'
          ctx.fillText(String(v), bx + slot * (i + 0.5), baseY - h - 4 * z)
          ctx.textAlign = 'start'
        }
      })
    }
    ctx.fillStyle = THEME.label
    ctx.font = `${Math.max(7, 10 * z)}px ${THEME.uiFont}`
    ctx.textAlign = 'center'
    labels.slice(0, n).forEach((lab, i) => {
      ctx.fillText(clipTo(ctx, lab, slot * 0.92), bx + slot * (i + 0.5), baseY + 4 * z)
    })
    ctx.textAlign = 'start'
    ctx.restore()
  }

  function draw() {
    const { w, h } = resize()
    ctx.save()
    ctx.fillStyle = THEME.surface
    ctx.fillRect(0, 0, w, h)
    const pitch = THEME.dotPitch * state.cam.z
    if (pitch >= 7) {
      ctx.fillStyle = THEME.dot
      const ox = ((-state.cam.x * state.cam.z) % pitch + pitch) % pitch
      const oy = ((-state.cam.y * state.cam.z) % pitch + pitch) % pitch
      for (let x = ox; x < w; x += pitch) {
        for (let y = oy; y < h; y += pitch) ctx.fillRect(x, y, 1.1, 1.1)
      }
    }
    for (const [, o] of objects()) {
      const f = revealOf(o)
      if (f <= 0) continue
      ctx.save()
      // a replay fades each object in as its first ink lands
      if (f < 1) ctx.globalAlpha = 0.2 + 0.8 * f
      if (o.kind === 'stroke' || o.kind === 'highlight') paintStroke(o, f)
      else if (o.kind === 'shape') paintShape(o, f)
      else if (o.kind === 'arrow' || o.kind === 'line') paintArrow(o, f)
      else if (o.kind === 'text') paintText(o, f)
      else if (o.kind === 'scene') paintScene(o)
      else if (o.kind === 'annotation') paintAnnotation(o)
      else if (o.kind === 'question') paintQuestion(o)
      else if (o.kind === 'image') paintImage(o)
      else if (o.kind === 'table') paintTable(o)
      else if (o.kind === 'chart') paintChart(o)
      ctx.restore()
    }
    paintEphemeral()
    paintSelection()
    paintHover()
    if (state.hand) drawAgentHand(ctx, state.hand)
    ctx.restore()
  }

  // ------------------------------------------------------------- hit test
  function hitTest(pt) {
    for (const [id, o] of objects().reverse()) {
      if (o.kind === 'stroke' || o.kind === 'highlight') {
        const pts = o.pts || []
        for (let i = 1; i < pts.length; i += 1) {
          if (distToSegment(pt.x, pt.y, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
            < 6 / state.cam.z + (o.w || 3) / 2) return { id, o }
        }
      } else if (o.kind === 'question') {
        const m = questionMetrics(o)
        const bb = m.bb
        if (pointInBox(pt.x, pt.y, bb, 0)) {
          const rel = pt.y - bb[1] - m.headH
          const idx = Math.floor(rel / m.optH)
          const opts = o.options || []
          const option = idx >= 0 && idx < opts.length ? opts[idx].id : null
          return { id, o, option }
        }
      } else if (o.kind === 'annotation') {
        const [u, v] = o.point || [0, 0]
        if (Math.hypot(pt.x - u, pt.y - v) < 18 / state.cam.z) return { id, o }
      } else if (pointInBox(pt.x, pt.y, bboxFor(o), 4 / state.cam.z)) {
        return { id, o }
      }
    }
    return null
  }

  function handleUnder(p) {
    for (const id of state.selection) {
      const o = state.doc.objects[id]
      if (!o) continue
      const box = normBBox(bboxFor(o))
      const z = state.cam.z
      const sx = (box[0] - state.cam.x) * z
      const sy = (box[1] - state.cam.y) * z
      for (const [hx, hy, port] of handlePorts(sx, sy, box[2] * z, box[3] * z)) {
        if (Math.hypot(p.sx - hx, p.sy - hy) < 9) return { id, o, port, box }
      }
    }
    return null
  }

  // ----------------------------------------------------------- mutations
  function pushHistory(inverse) {
    state.history.push(inverse)
    if (state.history.length > 80) state.history.shift()
  }

  function strip(obj) {
    const out = {}
    for (const [k, v] of Object.entries(obj || {})) if (!k.startsWith('_')) out[k] = v
    return out
  }

  function commit(type, payload) {
    if (!opts.onCommit) return
    if (type === 'upsert' && payload && payload.objects) {
      opts.onCommit(type, { ...payload, objects: payload.objects.map(strip) })
      return
    }
    opts.onCommit(type, payload)
  }

  function upsertLocal(obj) {
    const objs = { ...(state.doc.objects || {}) }
    const key = obj._key || obj.id
    objs[key] = { ...(objs[key] || {}), ...obj, _key: key }
    state.doc = { ...state.doc, objects: objs }
  }

  function removeLocal(ids) {
    const objs = { ...(state.doc.objects || {}) }
    for (const id of ids) delete objs[id]
    state.doc = { ...state.doc, objects: objs }
  }

  function snapshot(ids) {
    const out = {}
    for (const id of ids) if (state.doc.objects[id]) out[id] = JSON.parse(JSON.stringify(state.doc.objects[id]))
    return out
  }

  // ------------------------------------------------------------ pointer in
  function onPointerDown(ev) {
    canvas.setPointerCapture(ev.pointerId)
    const p = toBoard(ev)
    const tool = state.tool
    const panKey = ev.button === 1 || ev.shiftKey && tool === 'pen'

    if (tool === 'hand' || ev.button === 1 || panKey) {
      state.drag = { type: 'pan', sx: p.sx, sy: p.sy, cam: { ...state.cam } }
      canvas.style.cursor = 'grabbing'
      return
    }

    if (tool === 'select') {
      const h = handleUnder(p)
      if (h) {
        if (h.o.kind === 'text') {
          state.drag = { type: 'portArrow', fromId: h.id, port: h.port, from: p, to: p, ink: THEME.ink }
        } else {
          state.drag = { type: 'resize', id: h.id, port: h.port, box: normNBox(h.box), to: p }
        }
        return
      }
      const hit = hitTest(p)
      if (hit && hit.o.kind === 'question' && hit.option) {
        const next = { ...hit.o, answer: hit.option }
        upsertLocal(next)
        commit('answer', { id: hit.id, option: hit.option })
        draw()
        return
      }
      if (hit) {
        if (ev.shiftKey) {
          if (state.selection.has(hit.id)) state.selection.delete(hit.id)
          else state.selection.add(hit.id)
        } else if (!state.selection.has(hit.id)) {
          state.selection = new Set([hit.id])
        }
        opts.onSelection && opts.onSelection([...state.selection])
        state.drag = { type: 'move', from: p, start: snapshot([...state.selection]) }
        draw()
        return
      }
      if (!ev.shiftKey) {
        state.selection = new Set()
        opts.onSelection && opts.onSelection([])
      }
      state.drag = { type: 'marquee', from: p, to: p }
      draw()
      return
    }

    if (tool === 'eraser') {
      const hit = hitTest(p)
      if (hit) {
        pushHistory({ upsert: [snapshot([hit.id])[hit.id]] })
        removeLocal([hit.id])
        commit('delete', { ids: [hit.id] })
        draw()
      }
      return
    }

    if (tool === 'text') {
      ev.preventDefault() // keep the click from stealing focus back from the editor
      if (opts.onEditText) {
        opts.onEditText({ x: p.x, y: p.y, text: '', size: state.size, ink: state.ink }, value => {
          if (!value) return
          const id = uid('t_')
          const obj = { _key: id, id, kind: 'text', actor: 'human', layer: 'human-ink', x: p.x, y: p.y,
            text: value, size: state.size, ink: state.ink,
            z: 'a' + (Object.keys(state.doc.objects || {}).length + 1) }
          upsertLocal(obj)
          pushHistory({ delete: [id] })
          commit('upsert', { objects: [obj] })
          draw()
        })
      }
      return
    }

    if (tool === 'pen' || tool === 'highlighter') {
      state.drag = { type: 'ink', points: [[p.x, p.y]], ink: state.ink,
        width: tool === 'highlighter' ? 18 : state.width, highlight: tool === 'highlighter' }
      draw()
      return
    }

    if (tool === 'arrow') {
      const hit = hitTest(p)
      state.drag = { type: 'arrow', from: p, to: p, ink: state.ink,
        fromBind: hit ? { bind: hit.id, port: 'auto' } : null }
      return
    }

    if (['rect', 'ellipse', 'diamond', 'triangle', 'line'].includes(tool)) {
      state.drag = { type: 'shape', shape: tool, from: p, bbox: [p.x, p.y, 0, 0],
        ink: state.ink, width: Math.max(2, state.width) }
      return
    }

    if (tool === 'table') {
      state.drag = { type: 'table', from: p, bbox: [p.x, p.y, 0, 0], ink: state.ink }
      return
    }

    if (tool === 'chart' || tool === 'chartline') {
      state.drag = { type: 'chart', chart: tool === 'chartline' ? 'line' : 'bar', from: p,
        bbox: [p.x, p.y, 0, 0], ink: state.ink }
      return
    }
  }

  function normNBox(box) { return normBBox(box) }

  function onPointerMove(ev) {
    const p = toBoard(ev)
    const d = state.drag
    if (!d) {
      if (state.tool === 'select') {
        const hit = hitTest(p)
        const changed = (hit && hit.id) !== (state.hover && state.hover.id)
        state.hover = hit
        canvas.style.cursor = hit ? 'move' : 'default'
        if (changed) draw()
      }
      return
    }
    if (d.type === 'pan') {
      state.cam.x = d.cam.x - (p.sx - d.sx) / d.cam.z
      state.cam.y = d.cam.y - (p.sy - d.sy) / d.cam.z
      draw()
      return
    }
    if (d.type === 'ink') {
      const last = d.points[d.points.length - 1]
      if (Math.hypot(p.x - last[0], p.y - last[1]) * state.cam.z < 1.1) return
      d.points.push([p.x, p.y])
      draw()
      return
    }
    if (d.type === 'shape') {
      d.bbox = d.shape === 'line'
        ? [d.from.x, d.from.y, p.x - d.from.x, p.y - d.from.y]
        : [Math.min(d.from.x, p.x), Math.min(d.from.y, p.y),
          Math.abs(p.x - d.from.x), Math.abs(p.y - d.from.y)]
      draw()
      return
    }
    if (d.type === 'table' || d.type === 'chart') {
      d.bbox = [Math.min(d.from.x, p.x), Math.min(d.from.y, p.y),
        Math.abs(p.x - d.from.x), Math.abs(p.y - d.from.y)]
      draw()
      return
    }
    if (d.type === 'arrow' || d.type === 'portArrow') { d.to = p; draw(); return }
    if (d.type === 'marquee') { d.to = p; draw(); return }
    if (d.type === 'move') {
      const dx = p.x - d.from.x
      const dy = p.y - d.from.y
      for (const [id, orig] of Object.entries(d.start)) {
        const o = { ...orig }
        if (o.kind === 'stroke' || o.kind === 'highlight') {
          o.pts = orig.pts.map(([x, y]) => [x + dx, y + dy])
          // the box travels with the ink, or every later hit test aims at the old spot
          o.bbox = bboxOf(o.pts)
        } else if (o.kind === 'text') {
          o.x = orig.x + dx
          o.y = orig.y + dy
        } else if (o.kind === 'scene') {
          const lay = layoutFor(orig)
          const [ax, ay] = lay ? anchorOrigin(orig, lay) : (orig.bbox || [0, 0])
          o.placement = { at: [ax + dx, ay + dy] }
          o.anchor = null
        } else {
          o.bbox = [orig.bbox[0] + dx, orig.bbox[1] + dy, orig.bbox[2], orig.bbox[3]]
        }
        upsertLocal(o)
      }
      draw()
      return
    }
    if (d.type === 'resize') {
      const b = d.box
      let [x, y, w, h] = b
      if (d.port === 'e') w = Math.max(14, p.x - b[0])
      if (d.port === 's') h = Math.max(14, p.y - b[1])
      if (d.port === 'w') { const nx = Math.min(p.x, b[0] + b[2] - 14); w = b[0] + b[2] - nx; x = nx }
      if (d.port === 'n') { const ny = Math.min(p.y, b[1] + b[3] - 14); h = b[1] + b[3] - ny; y = ny }
      upsertLocal({ ...state.doc.objects[d.id], bbox: [x, y, w, h] })
      draw()
    }
  }

  function onPointerUp() {
    const d = state.drag
    state.drag = null
    canvas.style.cursor = TOOL_CURSORS[state.tool] || 'crosshair'
    if (!d) return

    if (d.type === 'pan') { commit('camera', { ...state.cam }); return }

    if (d.type === 'ink') {
      if (d.points.length < 2) { draw(); return }
      const id = uid('obj_')
      const obj = { _key: id, id, kind: d.highlight ? 'highlight' : 'stroke', actor: 'human',
        layer: 'human-ink', z: 'a' + (Object.keys(state.doc.objects || {}).length + 1),
        bbox: bboxOf(d.points), pts: d.points, ink: d.ink, w: d.width,
        ...(d.highlight ? { highlight: true } : {}) }
      upsertLocal(obj)
      pushHistory({ delete: [id] })
      commit('upsert', { objects: [obj] })
      draw()
      return
    }

    if (d.type === 'shape') {
      const b = normBBox(d.bbox)
      if (b[2] < 5 || b[3] < 5) { draw(); return }
      const id = uid('s_')
      const obj = { _key: id, id, kind: 'shape', shape: d.shape, actor: 'human', layer: 'human-ink',
        z: 'a' + (Object.keys(state.doc.objects || {}).length + 1),
        bbox: b, ink: d.ink, w: d.width }
      upsertLocal(obj)
      pushHistory({ delete: [id] })
      commit('upsert', { objects: [obj] })
      draw()
      return
    }

    if (d.type === 'arrow' || d.type === 'portArrow') {
      const hit = hitTest({ x: d.to.x, y: d.to.y })
      const to = hit ? { bind: hit.id, port: 'auto' } : { point: [d.to.x, d.to.y] }
      const from = d.type === 'portArrow' ? { bind: d.fromId, port: d.port }
        : (d.fromBind || { point: [d.from.x, d.from.y] })
      if (Math.hypot(d.to.x - d.from.x, d.to.y - d.from.y) < 8) { draw(); return }
      const id = uid('a_')
      const obj = { _key: id, id, kind: 'arrow', actor: 'human', layer: 'human-ink',
        z: 'a' + (Object.keys(state.doc.objects || {}).length + 1), from, to, ink: d.ink, w: 2 }
      upsertLocal(obj)
      pushHistory({ delete: [id] })
      commit('upsert', { objects: [obj] })
      draw()
      return
    }

    if (d.type === 'table') {
      const b = normBBox(d.bbox)
      if (b[2] < 40 || b[3] < 30) { draw(); return }
      const id = uid('tb_')
      const rows = 3
      const cols = 3
      const obj = { _key: id, id, kind: 'table', actor: 'human', layer: 'human-ink',
        z: 'a' + (Object.keys(state.doc.objects || {}).length + 1),
        bbox: b, rows, cols, cells: Array.from({ length: rows }, () => Array(cols).fill('')),
        ink: d.ink, w: 1 }
      upsertLocal(obj)
      pushHistory({ delete: [id] })
      commit('upsert', { objects: [obj] })
      draw()
      return
    }

    if (d.type === 'chart') {
      const b = normBBox(d.bbox)
      if (b[2] < 60 || b[3] < 50) { draw(); return }
      const id = uid('ch_')
      const obj = { _key: id, id, kind: 'chart', actor: 'human', layer: 'human-ink',
        z: 'a' + (Object.keys(state.doc.objects || {}).length + 1),
        bbox: b, chart: d.chart, values: [], labels: [], ink: d.ink }
      upsertLocal(obj)
      pushHistory({ delete: [id] })
      commit('upsert', { objects: [obj] })
      draw()
      return
    }

    if (d.type === 'marquee') {
      const box = [Math.min(d.from.x, d.to.x), Math.min(d.from.y, d.to.y),
        Math.abs(d.to.x - d.from.x), Math.abs(d.to.y - d.from.y)]
      if (box[2] > 4 || box[3] > 4) {
        const picked = objects().filter(([, o]) => {
          const b = normBBox(bboxFor(o))
          return b[0] < box[0] + box[2] && box[0] < b[0] + b[2] &&
            b[1] < box[1] + box[3] && box[1] < b[1] + b[3]
        }).map(([id]) => id)
        state.selection = new Set(picked)
        opts.onSelection && opts.onSelection(picked)
      }
      draw()
      return
    }

    if (d.type === 'move') {
      const moved = [...state.selection].map(id => state.doc.objects[id]).filter(Boolean)
      if (moved.length) {
        pushHistory({ upsert: Object.values(d.start) })
        commit('upsert', { objects: moved })
      }
      draw()
      return
    }

    if (d.type === 'resize') {
      const o = state.doc.objects[d.id]
      if (o) {
        pushHistory({ upsert: [snapshot([d.id])[d.id]] })
        commit('upsert', { objects: [o] })
      }
      draw()
    }
  }

  function onDoubleClick(ev) {
    const p = toBoard(ev)
    const hit = hitTest(p)
    if (hit && hit.o.kind === 'chart' && opts.onEditText) {
      // one line: "3,5,2,8" or "3,5,2,8 | mon,tue,wed,thu"
      const o = hit.o
      const cur = [((o.values || []).join(',')), ((o.labels || []).join(','))].filter(Boolean).join(' | ')
      opts.onEditText({ id: hit.id, x: o.bbox[0], y: o.bbox[1], text: cur,
        size: 12, ink: o.ink || THEME.ink }, value => {
        if (value === null || value === undefined) return
        const [numPart, labelPart] = String(value).split('|')
        const values = String(numPart || '').split(/[,\s]+/).filter(t => t !== '').map(t => Number(t)).filter(t => Number.isFinite(t))
        if (!values.length) return
        const labels = String(labelPart || '').split(/[,\s]+/).map(t => t.trim()).filter(Boolean)
        const next = { ...o, values, labels }
        upsertLocal(next)
        pushHistory({ upsert: [o] })
        commit('upsert', { objects: [next] })
        draw()
      })
      return
    }
    if (hit && hit.o.kind === 'table' && opts.onEditText) {
      const m = tableMetrics(hit.o)
      const cell = tableCellAt(hit.o, p)
      if (!cell) return
      const cells = (hit.o.cells || []).map(row => row.slice())
      while (cells.length < m.rows) cells.push([])
      cells.forEach(row => { while (row.length < m.cols) row.push('') })
      const b = m.b
      opts.onEditText({
        x: b[0] + cell.c * m.cw + 4,
        y: b[1] + cell.r * m.rh + m.rh * 0.15,
        text: cells[cell.r][cell.c] || '',
        size: Math.max(9, Math.min(m.rh * 0.46, 15)),
        ink: hit.o.ink || THEME.ink
      }, value => {
        if (value === null || value === undefined) return
        cells[cell.r][cell.c] = value
        const next = { ...hit.o, cells, rows: m.rows, cols: m.cols }
        upsertLocal(next)
        pushHistory({ upsert: [hit.o] })
        commit('upsert', { objects: [next] })
        draw()
      })
      return
    }
    if (hit && hit.o.kind === 'text' && opts.onEditText) {
      opts.onEditText({ id: hit.id, x: hit.o.x, y: hit.o.y, text: hit.o.text,
        size: hit.o.size, ink: hit.o.ink }, value => {
        if (value === null || value === undefined) return
        const next = { ...hit.o, text: value }
        upsertLocal(next)
        pushHistory({ upsert: [hit.o] })
        commit('upsert', { objects: [next] })
        draw()
      })
    }
  }

  function onWheel(ev) {
    ev.preventDefault()
    const p = toBoard(ev)
    const factor = Math.exp(-ev.deltaY * 0.0015)
    const z = Math.min(MAX_Z, Math.max(MIN_Z, state.cam.z * factor))
    state.cam.x = p.x - p.sx / z
    state.cam.y = p.y - p.sy / z
    state.cam.z = z
    draw()
    opts.onCamera && opts.onCamera({ ...state.cam })
  }

  canvas.addEventListener('pointerdown', onPointerDown)
  canvas.addEventListener('pointermove', onPointerMove)
  canvas.addEventListener('pointerup', onPointerUp)
  canvas.addEventListener('pointercancel', onPointerUp)
  canvas.addEventListener('dblclick', onDoubleClick)
  canvas.addEventListener('wheel', onWheel, { passive: false })
  canvas.addEventListener('contextmenu', e => e.preventDefault())

  if (document.fonts && document.fonts.load) {
    document.fonts.load('17px Charrette Hand').then(() => {
      state.fontReady = true
      state.scenes.clear()
      draw()
    }).catch(() => {})
  }

  const api = {
    draw,
    camera: state.cam,
    toScreen,
    get tool() { return state.tool },
    setTool(t) {
      state.tool = t
      state.selection = new Set()
      canvas.style.cursor = TOOL_CURSORS[t] || 'crosshair'
      draw()
    },
    setInk(ink) { state.ink = ink },
    setWidth(w) { state.width = w },
    setSize(s) { state.size = s },
    setCamera(camera, notify = true) {
      const x = Number(camera && camera.x)
      const y = Number(camera && camera.y)
      const z = Number(camera && camera.z)
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return
      state.cam.x = x
      state.cam.y = y
      state.cam.z = Math.min(MAX_Z, Math.max(MIN_Z, z))
      draw()
      if (notify && opts.onCamera) opts.onCamera({ ...state.cam })
    },
    setDoc(doc, opts2 = {}) {
      if (!doc || doc.unchanged) return
      const adoptCamera = opts2.adoptCamera === true
      for (const [id, o] of Object.entries(doc.objects || {})) {
        // `id` lives in the map key, and every edit path builds its upsert from the object:
        // without this the commit posts an object the store cannot address and drops it
        o._key = id
        if (!o.id) o.id = id
      }
      state.doc = doc
      state.scenes.clear()
      // The reader owns the camera. A poll reports what the server has, and the server is
      // always a few hundred ms behind a wheel: adopting its camera is what made zoom
      // snap back unless you threw it hard enough to outrun the write. A replay owns the
      // camera too, for the same reason, and so does a gesture in progress.
      if (doc.camera && adoptCamera && !state.replay && !state.drag) {
        state.cam.x = doc.camera.x
        state.cam.y = doc.camera.y
        state.cam.z = doc.camera.z
      }
      draw()
    },
    setImage(src, img) { state.images.set(src, img); draw() },
    // Replay: hand in [{id, t0, t1}] in milliseconds and drive it with revealAt(ms).
    setTimeline(items) {
      state.timeline = Array.isArray(items) ? items : []
      state.reveal = new Map(state.timeline.map(it => [it.id, 0]))
      draw()
    },
    revealAt(ms) {
      for (const it of state.timeline) {
        const span = Math.max(1, it.t1 - it.t0)
        state.reveal.set(it.id, Math.max(0, Math.min(1, (ms - it.t0) / span)))
      }
      draw()
    },
    clearTimeline() { state.timeline = []; state.reveal.clear(); draw() },

    // ---------------------------------------------------------------- replay
    // Play a timeline on this board: the same reveal the replay page uses, plus the
    // hand, driven by one absolute clock so a speed change cannot desync them.
    playReplay(items, total, opts = {}) {
      api.stopReplay({ keep: true })
      const segments = (items || []).filter(it => it && it.id)
      if (!segments.length) return null
      const span = Number(total) || Math.max(...segments.map(it => it.t1 ?? it.t0 + (it.dur || 0)))
      state.hand = createAgentHand()
      state.hand.seeded = true
      state.timeline = segments
      state.reveal = new Map(segments.map(it => [it.id, 0]))
      const run = { segments, total: span, speed: opts.speed || 1, t: 0, playing: true,
                    start: performance.now(), base: 0, raf: 0, onTick: opts.onTick || null,
                    onDone: opts.onDone || null }
      state.replay = run
      const frame = (now) => {
        if (state.replay !== run || !run.playing) return
        run.t = Math.min(run.total, run.base + (now - run.start) * run.speed)
        for (const it of run.segments) {
          const spanMs = Math.max(1, (it.t1 ?? it.t0 + (it.dur || 0)) - it.t0)
          state.reveal.set(it.id, Math.max(0, Math.min(1, (run.t - it.t0) / spanMs)))
        }
        stepAgentHand(state.hand, run.t, run.segments, id => api.penAt(id), id => api.anchorOf(id), run.total)
        draw()
        if (typeof opts.onTick === 'function') opts.onTick(run.t, run.total)
        if (run.t >= run.total) {
          run.playing = false
          if (typeof opts.onDone === 'function') opts.onDone()
          return
        }
        run.raf = requestAnimationFrame(frame)
      }
      run.raf = requestAnimationFrame(frame)
      return run
    },
    // dragging the scrubber: pause and hold the frame, so what you see is what t is
    seekReplay(t) {
      const run = state.replay
      if (!run) return 0
      run.playing = false
      cancelAnimationFrame(run.raf)
      run.t = Math.max(0, Math.min(run.total, Number(t) || 0))
      for (const it of run.segments) {
        const spanMs = Math.max(1, (it.t1 ?? it.t0 + (it.dur || 0)) - it.t0)
        state.reveal.set(it.id, Math.max(0, Math.min(1, (run.t - it.t0) / spanMs)))
      }
      state.hand = state.hand || createAgentHand()
      stepAgentHand(state.hand, run.t, run.segments, id => api.penAt(id), id => api.anchorOf(id), run.total)
      draw()
      if (typeof run.onTick === 'function') run.onTick(run.t, run.total)
      return run.t
    },
    // board-space box around a set of objects, for framing a replay
    bboxOf(ids) {
      const objs = state.doc.objects || {}
      let box = null
      for (const id of (ids && ids.length ? ids : Object.keys(objs))) {
        const o = objs[id]
        if (!o) continue
        const b = o.bbox || [anchorBoardPoint(o)[0], anchorBoardPoint(o)[1], 0, 0]
        if (!box) box = [b[0], b[1], b[0] + b[2], b[1] + b[3]]
        else {
          box[0] = Math.min(box[0], b[0])
          box[1] = Math.min(box[1], b[1])
          box[2] = Math.max(box[2], b[0] + b[2])
          box[3] = Math.max(box[3], b[1] + b[3])
        }
      }
      return box
    },
    resumeReplay(t) {
      const run = state.replay
      if (!run) return null
      run.base = typeof t === 'number' ? Math.max(0, Math.min(run.total, t)) : run.t
      run.t = run.base
      run.playing = true
      run.start = performance.now()
      state.hand = state.hand || createAgentHand()
      const tick = run.onTick
      const frame = (now) => {
        if (state.replay !== run || !run.playing) return
        run.t = Math.min(run.total, run.base + (now - run.start) * run.speed)
        for (const it of run.segments) {
          const spanMs = Math.max(1, (it.t1 ?? it.t0 + (it.dur || 0)) - it.t0)
          state.reveal.set(it.id, Math.max(0, Math.min(1, (run.t - it.t0) / spanMs)))
        }
        stepAgentHand(state.hand, run.t, run.segments, id => api.penAt(id), id => api.anchorOf(id), run.total)
        draw()
        if (typeof tick === 'function') tick(run.t, run.total)
        if (run.t >= run.total) {
          run.playing = false
          if (typeof run.onDone === 'function') run.onDone()
          return
        }
        run.raf = requestAnimationFrame(frame)
      }
      run.raf = requestAnimationFrame(frame)
      return run
    },
    setReplaySpeed(speed) {
      const run = state.replay
      if (!run) return 1
      const now = performance.now()
      run.t = Math.min(run.total, run.base + (now - run.start) * run.speed)
      run.base = run.t
      run.start = now
      run.speed = Math.max(0.1, Number(speed) || 1)
      return run.speed
    },
    stopReplay(opts2 = {}) {
      const run = state.replay
      if (run) cancelAnimationFrame(run.raf)
      state.replay = null
      state.hand = null
      if (!opts2.keep) {
        state.timeline = []
        state.reveal.clear()
        draw()
      }
      return true
    },
    get replay() { return state.replay },
    // true while a finger is down on a gesture: a poll landing now would replace the
    // document mid-move and the object would jump back under the pointer
    get busy() { return !!state.drag }, 
    // Where a hand would be, in screen coords, while an object is being revealed. The replay
    // draws its cursor here so the pen tip sits on the ink head, not near it.
    penAt(id) {
      const o = state.doc.objects && state.doc.objects[id]
      if (!o) return null
      const f = Math.max(0.001, revealOf(o))
      if (o.kind === 'stroke' || o.kind === 'highlight') {
        const pts = upToFraction(o.pts || [], f)
        if (pts.length < 2) return null
        const a = pts[pts.length - 2]
        const b = pts[pts.length - 1]
        const p = toScreen(b[0], b[1])
        return { x: p.x, y: p.y, angle: Math.atan2(b[1] - a[1], b[0] - a[0]) }
      }
      if (o.kind === 'shape') {
        const poly = shapePolyScreen(o)
        const drawn = upToFraction(poly, f)
        if (drawn.length < 2) return null
        const a = drawn[drawn.length - 2]
        const b = drawn[drawn.length - 1]
        return { x: b[0], y: b[1], angle: Math.atan2(b[1] - a[1], b[0] - a[0]) }
      }
      if (o.kind === 'text') {
        const z = state.cam.z
        const size = Math.max(7, (o.size || 22) * z)
        const chars = Math.ceil(String(o.text || '').length * Math.min(1, f))
        const p = toScreen(o.x, o.y)
        return { x: p.x + chars * size * 0.5, y: p.y + size * 0.78, angle: -0.32 }
      }
      if (o.kind === 'arrow' || o.kind === 'line') {
        const a = bindPoint(o.from)
        const b = bindPoint(o.to)
        if (a && b) {
          const k = Math.max(0, Math.min(1, f))
          const p2 = toScreen(a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
          return { x: p2.x, y: p2.y, angle: Math.atan2(b[1] - a[1], b[0] - a[0]) }
        }
      }
      const pt = anchorBoardPoint(o)
      const p = toScreen(pt[0], pt[1])
      return { x: p.x, y: p.y, angle: 0 }
    },
    anchorOf(id) {
      const o = state.doc.objects && state.doc.objects[id]
      if (!o) return { x: 0, y: 0 }
      const pt = anchorBoardPoint(o)
      const p = toScreen(pt[0], pt[1])
      return { x: p.x, y: p.y }
    },
    dropTemp() {},
    selection() { return [...state.selection] },
    select(ids) { state.selection = new Set(ids || []); draw() },
    deleteSelection() {
      const ids = [...state.selection]
      if (!ids.length) return
      pushHistory({ upsert: Object.values(snapshot(ids)) })
      removeLocal(ids)
      state.selection = new Set()
      commit('delete', { ids })
      draw()
    },
    undo() {
      const entry = state.history.pop()
      if (!entry) return
      if (entry.delete) {
        removeLocal(entry.delete)
        commit('delete', { ids: entry.delete })
      }
      if (entry.upsert) {
        for (const o of entry.upsert) upsertLocal(o)
        commit('upsert', { objects: entry.upsert })
      }
      draw()
    },
    get lastPlaced() { return state.lastPlaced },
    hitAt(x, y) {
      const h = hitTest({ x, y })
      return h ? { id: h.id, kind: h.o.kind } : null
    },
    bboxOf(id) {
      const o = (state.doc.objects || {})[id]
      return o ? normBBox(bboxFor(o)) : null
    },
    fit() {
      const boxes = objects().map(([, o]) => normBBox(bboxFor(o))).filter(b => b[2] || b[3])
      const r = canvas.getBoundingClientRect()
      if (!boxes.length) {
        state.cam = { x: 0, y: 0, z: 1 }
        draw()
        commit('camera', { ...state.cam })
        return
      }
      const x0 = Math.min(...boxes.map(b => b[0]))
      const y0 = Math.min(...boxes.map(b => b[1]))
      const x1 = Math.max(...boxes.map(b => b[0] + b[2]))
      const y1 = Math.max(...boxes.map(b => b[1] + b[3]))
      const pad = 70
      // Fit is allowed to zoom out as far as the wheel is: a big board has to be seeable whole.
      const z = Math.min(MAX_Z, Math.max(MIN_Z, Math.min(
        (r.width - pad * 2) / Math.max(1, x1 - x0),
        (r.height - pad * 2) / Math.max(1, y1 - y0))))
      state.cam.z = z
      state.cam.x = x0 - (r.width / z - (x1 - x0)) / 2
      state.cam.y = y0 - (r.height / z - (y1 - y0)) / 2
      draw()
      commit('camera', { ...state.cam })
    },
    focusOn(box, pad = 90) {
      const r = canvas.getBoundingClientRect()
      const z = Math.min(MAX_Z, Math.max(MIN_Z, Math.min(
        (r.width - pad * 2) / Math.max(60, box[2]),
        (r.height - pad * 2) / Math.max(60, box[3]))))
      state.cam.z = z
      state.cam.x = box[0] - (r.width / z - box[2]) / 2
      state.cam.y = box[1] - (r.height / z - box[3]) / 2
      draw()
      commit('camera', { ...state.cam })
    },
    destroy() {
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('dblclick', onDoubleClick)
      canvas.removeEventListener('wheel', onWheel)
    }
  }
  return api
}


// ---------------------------------------------------------------- the agent hand
// One hand, drawn by the engine, so the live board and a replay page cannot drift
// apart. The pointer travels, the pen draws, the glove places; the eyes stay put
// while the body swaps under them, which is what makes it read as one creature.
// ---- eyes: two white capsules, no pupil. The shape carries the expression: squint while drawing,
// wide while grabbing, squash on a blink. A white eye on a white body disappears, so the pointer
// carries a dark body and a light rim.
function capsuleEyes(c, lx, ly, spread, look, blink, squint, tilt, len, wid) {
  const h = Math.max(1.0, (len || 7.6) * (1 - 0.94 * blink) * (squint || 1));
  const w = wid || 3.4;
  const ox = look[0] * 1.6;
  const oy = look[1] * 1.6;
  c.save();
  c.shadowColor = 'transparent';
  c.translate(lx, ly);
  if (tilt) c.rotate(tilt);
  c.fillStyle = '#ffffff';
  for (const dx of [-spread, spread]) {
    c.beginPath();
    if (c.roundRect) c.roundRect(dx - w / 2 + ox, -h / 2 + oy, w, h, w / 2);
    else c.rect(dx - w / 2 + ox, -h / 2 + oy, w, h);
    c.fill();
  }
  c.restore();
}

function bodyPointer(c, look, blink) {
  // A round-headed pointer, not the OS arrow: the stock silhouette is a thin diagonal wedge, so a
  // pair of upright capsules placed where a face belongs always crosses one of its edges. A head
  // with a tail holds the face and still reads as a cursor.
  c.fillStyle = '#101a18';
  c.strokeStyle = 'rgba(255,255,255,0.92)';
  c.lineWidth = 1.6;
  c.lineJoin = 'round';
  c.beginPath();
  c.moveTo(10.2, 12.8); c.lineTo(14.4, 21.6); c.lineTo(18.0, 19.8); c.lineTo(13.6, 11.2);
  c.closePath();
  c.fill(); c.stroke();
  c.beginPath();
  c.arc(7.8, 7.8, 8.6, 0, Math.PI * 2);
  c.fill(); c.stroke();
  c.save();
  c.beginPath();
  c.arc(7.8, 7.8, 8.4, 0, Math.PI * 2);
  c.clip();
  capsuleEyes(c, 7.8, 7.4, 2.9, look, blink, 1, 0, 7.8, 3.05);
  c.restore();
}

function bodyPen(c, look, blink, angle = 0) {
  c.save();
  // A held pen: the barrel goes up and to the right at a steady angle while the tip sits on the
  // line. Following the stroke exactly would lay the body along the fresh ink and hide it.
  const tilt = -0.95 + Math.max(-0.45, Math.min(0.45, (angle || 0) * 0.22));
  c.rotate(tilt);
  c.beginPath();
  c.moveTo(0, 0); c.lineTo(-5.8, -4.9); c.lineTo(-8.2, -3.5); c.lineTo(-8.2, 3.5);
  c.lineTo(-5.8, 4.9); c.closePath();
  c.fillStyle = '#101a18'; c.fill();
  c.beginPath();
  if (c.roundRect) c.roundRect(-28, -6.5, 21, 13, 6.0); else c.rect(-28, -6.5, 21, 13);
  c.fillStyle = '#1f2b28'; c.fill();
  c.beginPath();
  if (c.roundRect) c.roundRect(-31.8, -7.0, 4.8, 14, 2.6); else c.rect(-31.8, -7.0, 4.8, 14);
  c.fillStyle = '#0f9c80'; c.fill();
  // The face rides on the barrel and is clipped to it: white capsules on a dark body disappear into
  // the page the moment one corner crosses the silhouette, and a rotated body makes that easy.
  c.save();
  c.beginPath();
  if (c.roundRect) c.roundRect(-28.2, -6.6, 21.4, 13.2, 6.1); else c.rect(-28.2, -6.6, 21.4, 13.2);
  c.clip();
  capsuleEyes(c, -17.2, 0, 4.3, look, blink, 1, 0, 9.6, 4.3);
  c.restore();
  c.restore();
}

// A grab hand built the way an app cursor hand is built: back of the hand, four fingers that fold
// from pointing up to lying across the palm, a thumb rooted low that sweeps over them, and a cuff.
// `curl` runs 0 (open, reaching) to 1 (closed on the object) and every part interpolates, so it is
// one object changing shape rather than two sprites swapping.
function capsule(c, cx, cy, angle, len, wid, fill) {
  c.save();
  c.translate(cx, cy);
  c.rotate(angle);
  c.beginPath();
  if (c.roundRect) c.roundRect(-len / 2, -wid / 2, len, wid, wid / 2);
  else c.rect(-len / 2, -wid / 2, len, wid);
  c.fillStyle = fill; c.fill(); c.stroke();
  c.restore();
}

// The glove is a single silhouette, not a pile of blobs: an index finger with a real taper, a V
// notch, three knuckle arcs stepping down to the pinky, a thumb lobe with a notch above it, and one
// rounded base. Flat black, no outline, exactly the rubber-hose cursor language: every cut is
// negative space in the path, which is what a union of circles can never give you.
// The glove is the traced contour of a rubber-hose pointing hand (the shape Seth pointed at), stored
// as points so the hand can FOLD rather than swap: the index retracts into a rolled knuckle, the top
// edge steps down toward the pinky, and the thumb hooks in. One path, one silhouette, k from 0 to 1.
const GLOVE_PTS = [[-5.02,-11.93],[-4.66,-11.87],[-4.33,-11.75],[-4.03,-11.6],[-3.76,-11.39],[-3.51,-11.15],[-3.2,-10.66],[-2.83,-9.93],[-2.4,-8.96],[-1.91,-7.75],[-1.44,-6.89],[-1.0,-6.37],[-0.59,-6.2],[-0.21,-6.38],[0.16,-6.51],[0.53,-6.57],[0.9,-6.59],[1.26,-6.54],[1.59,-6.46],[1.91,-6.35],[2.2,-6.2],[2.47,-6.03],[2.76,-5.93],[3.09,-5.92],[3.44,-5.99],[3.82,-6.15],[4.18,-6.27],[4.5,-6.34],[4.8,-6.38],[5.07,-6.38],[5.34,-6.36],[5.6,-6.3],[5.87,-6.22],[6.14,-6.1],[6.41,-5.95],[6.66,-5.74],[6.92,-5.5],[7.16,-5.21],[7.47,-5.01],[7.82,-4.89],[8.24,-4.87],[8.71,-4.94],[9.12,-4.97],[9.47,-4.95],[9.75,-4.9],[9.98,-4.81],[10.18,-4.71],[10.37,-4.58],[10.55,-4.44],[10.71,-4.29],[10.85,-4.09],[10.99,-3.86],[11.11,-3.58],[11.22,-3.27],[11.3,-2.68],[11.34,-1.83],[11.36,-0.71],[11.33,0.68],[11.28,1.84],[11.2,2.77],[11.1,3.47],[10.96,3.94],[10.73,4.51],[10.39,5.18],[9.95,5.96],[9.42,6.83],[8.98,7.7],[8.66,8.55],[8.44,9.39],[8.33,10.22],[8.19,10.87],[8.04,11.36],[7.86,11.66],[7.66,11.8],[7.4,11.9],[7.08,11.97],[6.71,12.0],[6.29,12.0],[5.89,11.97],[5.52,11.9],[5.18,11.8],[4.87,11.66],[4.58,11.51],[4.32,11.33],[4.09,11.13],[3.89,10.9],[3.67,10.8],[3.44,10.82],[3.18,10.97],[2.91,11.24],[2.66,11.46],[2.43,11.64],[2.21,11.78],[2.01,11.87],[1.58,11.93],[0.93,11.98],[0.06,12.0],[-1.04,12.0],[-1.91,11.98],[-2.55,11.95],[-2.96,11.9],[-3.14,11.83],[-3.3,11.73],[-3.45,11.6],[-3.58,11.43],[-3.69,11.23],[-3.77,11.01],[-3.81,10.79],[-3.82,10.55],[-3.8,10.31],[-3.8,10.07],[-3.82,9.85],[-3.87,9.63],[-3.93,9.43],[-4.19,9.07],[-4.64,8.54],[-5.28,7.85],[-6.11,7.0],[-6.84,6.17],[-7.47,5.36],[-7.99,4.58],[-8.42,3.82],[-8.87,3.07],[-9.34,2.33],[-9.83,1.6],[-10.35,0.88],[-10.75,0.3],[-11.04,-0.13],[-11.22,-0.43],[-11.29,-0.59],[-11.33,-0.8],[-11.36,-1.05],[-11.36,-1.36],[-11.33,-1.72],[-11.28,-2.04],[-11.19,-2.32],[-11.06,-2.56],[-10.91,-2.76],[-10.73,-2.94],[-10.53,-3.1],[-10.3,-3.23],[-10.06,-3.35],[-9.79,-3.42],[-9.49,-3.47],[-9.18,-3.48],[-8.84,-3.46],[-8.42,-3.32],[-7.92,-3.08],[-7.33,-2.72],[-6.66,-2.25],[-6.26,-2.34],[-6.14,-3.0],[-6.29,-4.23],[-6.71,-6.03],[-7.03,-7.47],[-7.25,-8.57],[-7.35,-9.32],[-7.35,-9.72],[-7.33,-10.08],[-7.29,-10.4],[-7.22,-10.67],[-7.13,-10.89],[-6.98,-11.1],[-6.77,-11.3],[-6.49,-11.5],[-6.15,-11.67],[-5.9,-11.82],[-5.72,-11.92],[-5.62,-11.98],[-5.59,-12.0]];
const GLOVE_YK = -4.0;
const GLOVE_PIVOT = [-5.4, -3.0];

function glovePath(k) {
  const p = new Path2D();
  for (let i = 0; i < GLOVE_PTS.length; i += 1) {
    let [x, y] = GLOVE_PTS[i];
    if (y < GLOVE_YK) {
      // the finger rolls into a knuckle: compress it, but leave a rounded crest so the top edge
      // reads as a curled finger rather than a slice
      const t = Math.min(1, Math.max(0, (x + 8.6) / 6.2));
      const keep = 0.30 + 0.18 * Math.sin(Math.PI * t);
      y = GLOVE_YK - (GLOVE_YK - y) * (1 - k * (1 - keep));
      // ease the step down toward the pinky; a linear clamp leaves a visible kink
      y += k * 0.34 * Math.min(1, Math.max(0, (x + 1.4) / 4.0));
    }
    // the thumb stays put and the fingers fold onto it, which is how a hand actually closes;
    // rotating the traced thumb tore the outline into spikes
    if (k > 0) {
      const a = -0.10 * k;
      const dx = x - GLOVE_PIVOT[0];
      const dy = y - GLOVE_PIVOT[1];
      x = GLOVE_PIVOT[0] + dx * Math.cos(a) - dy * Math.sin(a);
      y = GLOVE_PIVOT[1] + dx * Math.sin(a) + dy * Math.cos(a);
    }
    if (i) p.lineTo(x, y); else p.moveTo(x, y);
  }
  p.closePath();
  return p;
}

function bodyGlove(c, look, blink, curl) {
  const k = Math.max(0, Math.min(1, curl || 0));
  c.save();
  // The traced shape stays unchanged. This glove-only scale keeps its finger notches and two eyes
  // legible at the native replay size without changing the pen marker or pointer silhouettes.
  c.scale(1.16, 1.16);
  c.fillStyle = '#111a18';
  c.fill(glovePath(k));
  capsuleEyes(c, 0.4, 4.0, 3.0, look, blink, 1 - 0.08 * k, 0, 5.6, 3.0);
  c.restore();
}

export function createAgentHand () {
  return { x: 0, y: 0, mode: 'point', from: 'point', morph: 1, modeT: 0,
           angle: 0, look: [0, 0], blink: 0, blinkForce: 0, bob: 0,
           open: 1, curl: 0, ripple: 0, placed: false };
}

export function drawAgentHand (c, h) {
  if (!h) return;
  const paintBody = (mode, alpha) => {
    if (alpha <= 0.01) return;
    c.save();
    c.globalAlpha = alpha;
    c.translate(h.x, h.y + (h.bob || 0));
    c.scale(1.4, 1.4);
    c.shadowColor = 'rgba(6, 20, 17, 0.28)';
    c.shadowBlur = 3.2;
    c.shadowOffsetY = 1.6;
    if (mode === 'pen') bodyPen(c, h.look, h.blink, h.angle);
    else if (mode === 'grab') bodyGlove(c, h.look, h.blink, h.curl || 0);
    else bodyPointer(c, h.look, h.blink);
    c.restore();
  };
  if (h.morph < 1 && h.from !== h.mode) {
    paintBody(h.from, 1 - h.morph);
    paintBody(h.mode, h.morph);
  } else paintBody(h.mode, 1);
  if (h.ripple > 0) {
    const r = 5 + (1 - h.ripple) * 22;
    c.save();
    c.globalAlpha = h.ripple * 0.5;
    c.beginPath(); c.arc(h.x, h.y, r, 0, Math.PI * 2);
    c.strokeStyle = '#ffffff'; c.lineWidth = 2.2; c.stroke();
    c.beginPath(); c.arc(h.x, h.y, Math.max(0, r - 2.6), 0, Math.PI * 2);
    c.strokeStyle = '#0f9c80'; c.lineWidth = 1.2; c.stroke();
    c.restore();
  }
}

// a blink every 3.4 s, as a function of the clock, so a seek reproduces it exactly
function handBlinkAt (t) {
  const dt = ((t % 3400) + 3400) % 3400;
  return dt <= 140 ? Math.sin((dt / 140) * Math.PI) : 0;
}

// Position, mode and expression are all a function of t. Nothing here accumulates,
// so seeking to any moment reproduces that moment.
export function stepAgentHand (h, t, timeline, penAt, anchorOf, total) {
  if (!h || !timeline || !timeline.length) return h;
  const anchors = timeline.map(it => anchorOf(it.id) || { x: 0, y: 0 });
  let idx = -1;
  for (let i = 0; i < timeline.length; i += 1) {
    if (t < timeline[i].t0) break;
    idx = i;
  }
  const seg = idx >= 0 ? timeline[idx] : null;
  const next = timeline[idx + 1];
  const place = !!(seg && ['image', 'table', 'chart', 'scene'].includes(seg.kind));
  const segEnd = seg ? (seg.t1 ?? seg.t0 + seg.dur) : 0;
  let mode = 'point';
  let lookX = 0;
  let lookY = 0;

  if (seg && t < segEnd) {
    const p = (t - seg.t0) / Math.max(1, segEnd - seg.t0);
    const at = penAt(seg.id);
    if (at) { h.x = at.x; h.y = at.y; h.angle = at.angle; }
    mode = place ? 'grab' : 'pen';
    if (place) {
      const close = Math.min(1, Math.max(0, (p - 0.03) / 0.18));
      const release = Math.min(1, Math.max(0, (p - 0.88) / 0.12));
      h.curl = (1 - Math.pow(1 - close, 3)) * (1 - release);
      h.open = 1 - h.curl;
    } else {
      h.curl = 0;
      h.open = 1;
    }
    lookX = Math.cos(h.angle) * 0.9;
    lookY = Math.sin(h.angle) * 0.9;
  } else if (!seg) {
    const k = Math.min(1, Math.max(0, t / Math.max(1, timeline[0].t0)));
    h.x = anchors[0].x + 180 * (1 - k);
    h.y = anchors[0].y + 260 * (1 - k);
    mode = 'point';
    lookX = Math.cos(Math.atan2(-260, -180));
    lookY = Math.sin(Math.atan2(-260, -180));
  } else {
    const a = anchors[idx] || { x: 0, y: 0 };
    const b = next ? anchors[idx + 1] : a;
    const from = segEnd;
    const to = next ? next.t0 : (total || from);
    const k = Math.min(1, Math.max(0, (t - from) / Math.max(1, to - from)));
    const ease = Math.min(1.015, 1 - Math.pow(1 - k, 3) + 0.015 * Math.sin(Math.PI * k));
    h.x = a.x + (b.x - a.x) * ease;
    h.y = a.y + (b.y - a.y) * ease;
    if (next) {
      const ang = Math.atan2(b.y - a.y, b.x - a.x);
      h.angle = ang;
      lookX = Math.cos(ang) * 0.9;
      lookY = Math.sin(ang) * 0.9;
    }
    mode = 'point';
  }
  h.look = [lookX, lookY];

  if (h.mode !== mode) { h.from = h.mode; h.mode = mode; h.modeT = t; }
  h.morph = h.from === h.mode ? 1 : Math.min(1, (t - (h.modeT || 0)) / 150);
  h.blink = h.blinkForce > 0 ? h.blinkForce : handBlinkAt(t);
  h.bob = mode === 'point' ? Math.sin(t / 125) * 1.7 : 0;
  if (mode === 'grab' && seg) {
    const p = (t - seg.t0) / Math.max(1, segEnd - seg.t0);
    h.ripple = p > 0.9 ? Math.max(0, 1 - (p - 0.9) / 0.09) : 0;
    h.placed = p > 0.9;
  } else h.ripple = 0;
  return h;
}
