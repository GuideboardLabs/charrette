/**
 * Chalk drawing engine.
 *
 * Framework-free and DOM-light so three mounts can share it: the web page
 * imports it as a module, the dashboard bundle and the desktop plugin inline
 * it at build time. It owns board coordinates, the camera, hit testing and
 * painting; it never talks to the network and never formats UI.
 */

export const PALETTE = [
  { name: 'black', ink: '#111318' },
  { name: 'red', ink: '#D92D20' },
  { name: 'blue', ink: '#1570EF' },
  { name: 'green', ink: '#12805C' },
  { name: 'orange', ink: '#DC6803' },
  { name: 'purple', ink: '#7A5AF8' }
]

export const THEME = {
  surface: '#FFFFFF',
  dot: '#DCE3E9',
  dotPitch: 24,
  agentInk: '#0F766E',
  agentTint: '#E6F4F1',
  emphasis: '#B42318',
  grid: '#D6DEE6'
}

const MIN_Z = 0.05
const MAX_Z = 4

export function createBoard(canvas, opts = {}) {
  const state = {
    doc: { objects: {}, camera: { x: 0, y: 0, z: 1 } },
    cam: { x: 0, y: 0, z: 1 },
    tool: 'pen',
    ink: '#111318',
    width: 3,
    highlighter: false,
    drawing: null,
    images: new Map(),
    hover: null
  }

  const ctx = canvas.getContext('2d')

  function dpr() {
    return window.devicePixelRatio || 1
  }

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

  function sortedObjects() {
    const objs = state.doc.objects || {}
    return Object.entries(objs).sort((a, b) => String(a[1].z || '').localeCompare(String(b[1].z || '')))
  }

  function paintGrid(w, h) {
    const pitch = THEME.dotPitch * state.cam.z
    if (pitch < 7) return
    ctx.fillStyle = THEME.dot
    const ox = ((-state.cam.x * state.cam.z) % pitch + pitch) % pitch
    const oy = ((-state.cam.y * state.cam.z) % pitch + pitch) % pitch
    for (let x = ox; x < w; x += pitch) {
      for (let y = oy; y < h; y += pitch) ctx.fillRect(x, y, 1.2, 1.2)
    }
  }

  function paintStroke(obj) {
    const pts = obj.pts || []
    if (pts.length < 2) return
    const z = state.cam.z
    ctx.save()
    ctx.strokeStyle = obj.ink || (obj.actor === 'agent' ? THEME.agentInk : '#111318')
    ctx.lineWidth = Math.max(0.6, (obj.w || 3) * z)
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    if (obj.highlight || obj.kind === 'highlight') {
      ctx.globalCompositeOperation = 'multiply'
      ctx.globalAlpha = 0.4
      ctx.lineWidth = Math.max(1, 18 * z)
    }
    ctx.beginPath()
    for (let i = 0; i < pts.length; i += 1) {
      const x = (pts[i][0] - state.cam.x) * z
      const y = (pts[i][1] - state.cam.y) * z
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
    ctx.restore()
  }

  function paintImage(obj) {
    const img = state.images.get(obj.src)
    const [x, y, w, h] = obj.bbox || []
    if (!img || !img.complete || !w) return
    ctx.save()
    ctx.drawImage(img, (x - state.cam.x) * state.cam.z, (y - state.cam.y) * state.cam.z,
      w * state.cam.z, h * state.cam.z)
    ctx.restore()
  }

  function paintPlaceholder(obj) {
    const [x, y, w, h] = obj.bbox || []
    if (!w) return
    const z = state.cam.z
    const px = (x - state.cam.x) * z
    const py = (y - state.cam.y) * z
    ctx.save()
    ctx.fillStyle = THEME.agentTint
    ctx.fillRect(px, py, w * z, h * z)
    ctx.setLineDash([6, 5])
    ctx.strokeStyle = obj.status === 'failed' ? THEME.emphasis : THEME.agentInk
    ctx.lineWidth = 1.2
    ctx.strokeRect(px, py, w * z, h * z)
    ctx.setLineDash([])
    ctx.fillStyle = obj.status === 'failed' ? THEME.emphasis : THEME.agentInk
    ctx.font = '11px ui-sans-serif, system-ui, sans-serif'
    ctx.fillText(obj.status === 'failed' ? 'image failed' : 'rendering…', px + 10, py + 18)
    ctx.restore()
  }

  function paintPoint(obj) {
    const [u, v] = obj.point || []
    if (u === undefined) return
    const z = state.cam.z
    const cx = (u - state.cam.x) * z
    const cy = (v - state.cam.y) * z
    ctx.save()
    ctx.strokeStyle = THEME.agentInk
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(6, 13 * z), 0, Math.PI * 2)
    ctx.stroke()
    if (obj.label) {
      ctx.fillStyle = THEME.agentInk
      ctx.font = `${Math.max(10, 13 * z)}px ui-sans-serif, system-ui, sans-serif`
      ctx.fillText(obj.label, cx + 16, cy + 4)
    }
    ctx.restore()
  }

  function paintLiveStroke() {
    if (!state.drawing) return
    paintStroke({ pts: state.drawing.points, ink: state.drawing.ink, w: state.drawing.width,
      highlight: state.drawing.highlighter })
  }

  function draw() {
    const { w, h } = resize()
    ctx.save()
    ctx.fillStyle = THEME.surface
    ctx.fillRect(0, 0, w, h)
    paintGrid(w, h)
    for (const [, obj] of sortedObjects()) {
      if (obj.kind === 'stroke' || obj.kind === 'highlight') paintStroke(obj)
      else if (obj.kind === 'image') (obj.status === 'rendering' || obj.status === 'failed')
        ? paintPlaceholder(obj) : paintImage(obj)
      else if (obj.kind === 'annotation') paintPoint(obj)
    }
    paintLiveStroke()
    // surface stays exactly #FFFFFF: no veil, no texture. Gloss is chrome-side.
    ctx.restore()
  }

  // ------------------------------------------------------------- interaction
  let panning = null

  function onPointerDown(ev) {
    canvas.setPointerCapture(ev.pointerId)
    const p = toBoard(ev)
    if (ev.button === 1 || state.tool === 'hand' || ev.shiftKey && state.tool === 'pen' && ev.altKey) {
      panning = { sx: p.sx, sy: p.sy, cam: { ...state.cam } }
      return
    }
    if (state.tool !== 'pen' && state.tool !== 'highlighter') return
    state.drawing = {
      points: [[p.x, p.y]],
      ink: state.ink,
      width: state.width,
      highlighter: state.tool === 'highlighter'
    }
    draw()
  }

  function onPointerMove(ev) {
    if (panning) {
      const p = toBoard(ev)
      // convert screen delta to board delta using the frozen camera
      state.cam.x = panning.cam.x - (p.sx - panning.sx) / panning.cam.z
      state.cam.y = panning.cam.y - (p.sy - panning.sy) / panning.cam.z
      draw()
      return
    }
    if (!state.drawing) return
    const p = toBoard(ev)
    const last = state.drawing.points[state.drawing.points.length - 1]
    const dx = p.x - last[0]
    const dy = p.y - last[1]
    if (dx * dx + dy * dy < 0.8) return
    state.drawing.points.push([p.x, p.y])
    draw()
  }

  function onPointerUp() {
    if (panning) {
      panning = null
      opts.onCamera && opts.onCamera({ ...state.cam })
      return
    }
    const s = state.drawing
    state.drawing = null
    if (!s || s.points.length < 2) {
      draw()
      return
    }
    // optimistic local echo, then the server's id becomes authoritative
    const tempId = 'local_' + Math.random().toString(36).slice(2, 8)
    state.doc.objects[tempId] = {
      kind: s.highlighter ? 'highlight' : 'stroke',
      actor: 'human', z: 'a' + (Object.keys(state.doc.objects).length + 1),
      bbox: bboxOf(s.points), pts: s.points, ink: s.ink, w: s.width,
      highlight: s.highlighter
    }
    draw()
    opts.onStroke && opts.onStroke(s.points, {
      ink: s.ink, width: s.width, highlight: s.highlighter, tempId
    })
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
  }

  canvas.addEventListener('pointerdown', onPointerDown)
  canvas.addEventListener('pointermove', onPointerMove)
  canvas.addEventListener('pointerup', onPointerUp)
  canvas.addEventListener('pointercancel', onPointerUp)
  canvas.addEventListener('wheel', onWheel, { passive: false })
  canvas.addEventListener('contextmenu', e => e.preventDefault())

  return {
    draw,
    camera: state.cam,
    get tool() { return state.tool },
    setTool(t) { state.tool = t; canvas.style.cursor = t === 'hand' ? 'grab' : 'crosshair' },
    setInk(ink) { state.ink = ink },
    setWidth(w) { state.width = w },
    setDoc(doc) {
      if (!doc || doc.unchanged) return
      const prev = state.doc
      state.doc = doc
      if (doc.camera && prev !== doc) {
        state.cam.x = doc.camera.x
        state.cam.y = doc.camera.y
        state.cam.z = doc.camera.z
      }
      draw()
    },
    setImage(src, img) { state.images.set(src, img); draw() },
    dropTemp(tempId) { delete state.doc.objects[tempId]; draw() },
    fit() {
      const boxes = Object.values(state.doc.objects || {})
        .map(o => o.bbox).filter(b => Array.isArray(b))
      if (!boxes.length) { state.cam = { x: 0, y: 0, z: 1 }; draw(); return }
      const r = canvas.getBoundingClientRect()
      const x0 = Math.min(...boxes.map(b => b[0]))
      const y0 = Math.min(...boxes.map(b => b[1]))
      const x1 = Math.max(...boxes.map(b => b[0] + b[2]))
      const y1 = Math.max(...boxes.map(b => b[1] + b[3]))
      const pad = 60
      const z = Math.min(MAX_Z, Math.max(MIN_Z,
        Math.min((r.width - pad * 2) / Math.max(1, x1 - x0), (r.height - pad * 2) / Math.max(1, y1 - y0))))
      state.cam.z = z
      state.cam.x = x0 - (r.width / z - (x1 - x0)) / 2
      state.cam.y = y0 - (r.height / z - (y1 - y0)) / 2
      draw()
      opts.onCamera && opts.onCamera({ ...state.cam })
    },
    destroy() {
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('wheel', onWheel)
    }
  }
}

function bboxOf(points) {
  const xs = points.map(p => p[0])
  const ys = points.map(p => p[1])
  const x0 = Math.min(...xs)
  const y0 = Math.min(...ys)
  return [x0, y0, Math.max(...xs) - x0, Math.max(...ys) - y0]
}
