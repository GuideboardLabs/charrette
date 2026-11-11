/**
 * Charrette web surface: DOM + network shell around the shared engine.
 * Pixels, camera, strokes, layout and hit testing all live in /charrette.mjs.
 */

import { createBoard, PALETTE, THEME } from '/charrette.mjs'
import { formatChatTime, isNearBottom, renderMessage } from '/chat-ui.mjs'
import { cameraForMiniPoint, directionToBounds, projectMinimap } from '/minimap.mjs'

const params = new URLSearchParams(location.search)
const TOKEN = params.get('t') || ''
const START_BOARD = params.get('board') || 'b1'

const el = id => document.getElementById(id)
const canvas = el('board')
const stat = el('stat')
const zoom = el('zoom')
const listEl = el('boardList')
const textEdit = el('textEdit')
const emptyBoard = el('emptyBoard')
const activityJump = el('activityJump')
const activityArrow = activityJump.querySelector('svg')
const minimap = el('minimap')
const minimapCanvas = el('minimapCanvas')
const shortcutSheet = el('shortcutSheet')
const shortcutHelp = el('shortcutHelp')
const shortcutClose = el('shortcutClose')

let boardId = START_BOARD
let lastSeen = { rev: -1, newest: null }
let focusedOnce = false
let pollTimer = null
let controlTimer = null
let controlSeq = 0
const knownIds = new Set()
let currentDoc = { objects: {} }
let minimapProjection = null
let pendingActivity = null

const api = async (path, init = {}) => {
  const res = await fetch(path, {
    ...init,
    headers: { 'X-Charrette-Token': TOKEN, 'Content-Type': 'application/json', ...(init.headers || {}) }
  })
  if (res.status === 401) throw new Error('not authorised: open the URL the server printed')
  if (res.status === 409) return { conflict: true }
  if (!res.ok) throw new Error(path + ' → ' + res.status)
  return res.json()
}

let cameraTimer = null
function saveCamera (payload) {
  clearTimeout(cameraTimer)
  cameraTimer = setTimeout(() => {
    api('/api/camera', { method: 'POST', body: JSON.stringify({ board: boardId, camera: payload }) })
      .catch(() => {})
  }, 500)
}

function updateZoom (camera) {
  const z = Number(camera?.z)
  if (Number.isFinite(z)) zoom.textContent = Math.round(z * 100) + '%'
}

function drawMinimap () {
  const rect = minimapCanvas.getBoundingClientRect()
  const width = Math.max(1, Math.round(rect.width || 140))
  const height = Math.max(1, Math.round(rect.height || 90))
  const dpr = Math.max(1, window.devicePixelRatio || 1)
  minimapCanvas.width = Math.round(width * dpr)
  minimapCanvas.height = Math.round(height * dpr)
  const ctx = minimapCanvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)
  minimapProjection = projectMinimap(currentDoc, board.camera,
    { w: canvas.clientWidth, h: canvas.clientHeight }, width, height)
  ctx.fillStyle = '#aebdb9'
  for (const box of minimapProjection.objects) {
    ctx.fillRect(box[0], box[1], Math.max(1.5, box[2]), Math.max(1.5, box[3]))
  }
  const view = minimapProjection.viewport
  ctx.fillStyle = 'rgba(15, 156, 128, .08)'
  ctx.fillRect(view[0], view[1], view[2], view[3])
  ctx.strokeStyle = '#0f9c80'
  ctx.lineWidth = 1.5
  ctx.strokeRect(view[0] + .75, view[1] + .75, Math.max(0, view[2] - 1.5), Math.max(0, view[3] - 1.5))
}

function updateCameraSurface (camera) {
  saveCamera(camera)
  updateZoom(camera)
  drawMinimap()
}

// The last rev this tab wrote. A poll that comes back older than this is a stale
// echo of the board before our own edit, and applying it snaps the work backwards.
let localRev = 0
function noteRev (out) {
  const rev = Number(out && out.rev)
  if (Number.isFinite(rev)) localRev = Math.max(localRev, rev)
}

const board = createBoard(canvas, {
  onCommit: (type, payload) => {
    if (type === 'upsert') {
      api('/api/objects', { method: 'POST', body: JSON.stringify({ board: boardId, upsert: payload.objects }) })
        .then(noteRev)
        .catch(err => { stat.textContent = String(err.message || err) })
    } else if (type === 'delete') {
      api('/api/objects', { method: 'POST', body: JSON.stringify({ board: boardId, delete: payload.ids }) })
        .then(noteRev)
        .catch(err => { stat.textContent = String(err.message || err) })
    } else if (type === 'answer') {
      api('/api/objects', { method: 'POST', body: JSON.stringify({ board: boardId, upsert: [{ id: payload.id, answer: payload.option }] }) })
        .catch(err => { stat.textContent = String(err.message || err) })
    } else if (type === 'camera') {
      updateCameraSurface(payload)
    }
  },
  // the wheel reports through its own callback, not through commit('camera'):
  // without this the zoom never leaves the tab and every reload snaps back
  onCamera: camera => { updateCameraSurface(camera) },
  onPlaced: (id, bbox, nodes) => {
    api('/api/objects', { method: 'POST', body: JSON.stringify({ board: boardId, upsert: [{ id, bbox, nodes, placement: { at: [bbox[0], bbox[1]] } }] }) })
      .catch(() => {})
  },
  onEditText: (spec, done) => openTextEditor(spec, done),
  onSelection: ids => { if (ids.length) activityJump.hidden = true }
})

// ------------------------------------------------------------------ loading
async function load (force = false) {
  try {
    const doc = await api('/api/board?board=' + encodeURIComponent(boardId))
    if (!doc || doc.unchanged) return
    // mid-gesture the local document is the truth; a poll now would pull the object
    // back to where the server last saw it
    if (board.busy) return
    if (Number(doc.rev) < localRev) return
    const firstLoad = !currentDoc
    currentDoc = doc
    board.setDoc(doc, { adoptCamera: firstLoad })
    updateZoom(doc.camera)
    const objs = doc.objects || {}
    const n = Object.keys(objs).length
    // Anything the agent wrote since the last look is drawn in instead of appearing:
    // that is what makes a chat turn feel like someone at the board. Only while the
    // reader is idle, visible, and not already watching a replay.
    if (!firstLoad) {
      const fresh = []
      for (const [id, obj] of Object.entries(objs)) {
        if (knownIds.has(id)) continue
        if (obj && obj.actor === 'agent' && obj.kind !== 'scene') fresh.push(id)
      }
      for (const id of Object.keys(objs)) knownIds.add(id)
      if (fresh.length && !board.replay && !board.busy && document.visibilityState === 'visible') {
        playReplay(fresh, null, { frame: false })
      }
    } else {
      for (const id of Object.keys(objs)) knownIds.add(id)
    }
    emptyBoard.hidden = n > 0
    minimap.hidden = n === 0
    drawMinimap()
    stat.textContent = 'rev ' + doc.rev + ' · ' + n + (n === 1 ? ' object' : ' objects')
    for (const [id, obj] of Object.entries(objs)) {
      if (obj.kind !== 'image' || !obj.src) continue
      if (obj.status === 'rendering' || obj.status === 'failed') continue
      const already = document.querySelector('img[data-charrette="' + id + '"]')
      if (already) continue
      const img = new Image()
      img.dataset.charrette = id
      img.onload = () => board.setImage(obj.src, img)
      img.src = '/api/img?board=' + encodeURIComponent(boardId) + '&id=' + encodeURIComponent(id) +
        '&t=' + encodeURIComponent(TOKEN)
    }
    const newest = newestActivity(objs)
    if (newest && newest.id !== lastSeen.newest) {
      lastSeen.newest = newest.id
      // No auto-fit: the camera the board was left at is the camera it opens at. Fit is F.
      if (focusedOnce && !onScreen(newest.bbox)) showAgentPill(newest)
      focusedOnce = true
    }
    if (force) drawTabs()
  } catch (err) {
    stat.textContent = String(err.message || err)
  }
}

function newestActivity (objs) {
  let best = null
  for (const [id, o] of Object.entries(objs)) {
    if (!o || (!o.bbox && !o.pts)) continue
    const z = String(o.z || '')
    if (!best || z > best.z) {
      const b = o.bbox || [0, 0, 0, 0]
      best = { id, z, actor: o.actor, bbox: b }
    }
  }
  return best
}

function onScreen (b) {
  const r = canvas.getBoundingClientRect()
  const cam = board.camera
  const x = (b[0] - cam.x) * cam.z
  const y = (b[1] - cam.y) * cam.z
  const w = b[2] * cam.z
  const h = b[3] * cam.z
  return x + w > 0 && y + h > 0 && x < r.width && y < r.height
}

function showAgentPill (item) {
  pendingActivity = item
  const direction = directionToBounds(board.camera,
    { w: canvas.clientWidth, h: canvas.clientHeight }, item.bbox)
  activityJump.hidden = false
  activityJump.dataset.direction = direction.cardinal
  activityJump.setAttribute('aria-label', 'Agent drew off-screen to the ' + direction.cardinal + '. Move camera to it.')
  activityArrow.style.transform = 'rotate(' + direction.angle + 'deg)'
}

activityJump.addEventListener('click', () => {
  if (!pendingActivity) return
  board.focusOn(pendingActivity.bbox, 140)
  activityJump.hidden = true
  pendingActivity = null
})

minimap.addEventListener('click', event => {
  if (!minimapProjection) return
  const rect = minimapCanvas.getBoundingClientRect()
  const point = event.detail === 0
    ? { x: minimapProjection.plot[0] + minimapProjection.plot[2] / 2,
        y: minimapProjection.plot[1] + minimapProjection.plot[3] / 2 }
    : { x: event.clientX - rect.left, y: event.clientY - rect.top }
  board.setCamera(cameraForMiniPoint(point, minimapProjection, board.camera,
    { w: canvas.clientWidth, h: canvas.clientHeight }))
})

// ------------------------------------------------------------------- boards
const menuEl = el('boardMenu')
const nameEl = el('boardName')

function closeBoards () {
  listEl.hidden = true
  menuEl.setAttribute('aria-expanded', 'false')
}

function openBoards () {
  listEl.hidden = false
  menuEl.setAttribute('aria-expanded', 'true')
}

async function switchBoard (id) {
  closeBoards()
  if (id === boardId) return
  boardId = id
  focusedOnce = false
  lastSeen = { rev: -1, newest: null }
  localRev = 0
  await api('/api/boards/active', { method: 'POST', body: JSON.stringify({ board: id }) }).catch(() => null)
  await load(true)
  window.dispatchEvent(new CustomEvent('charrette:board', { detail: id }))
}

async function drawTabs () {
  try {
    const idx = await api('/api/boards')
    const boards = idx.boards || []
    const active = boards.find(b => b.id === boardId) || boards[0]
    nameEl.textContent = active ? active.name : boardId
    listEl.textContent = ''
    for (const b of boards) {
      const row = document.createElement('button')
      row.className = 'board-row' + (b.id === boardId ? ' on' : '')
      row.title = b.name + ' · ' + b.objects + ' objects' +
        (b.last_activity ? ' · newest ' + b.last_activity.object_id : '')
      const name = document.createElement('span')
      name.className = 'name'
      name.textContent = b.name
      row.appendChild(name)
      if (b.last_activity && b.last_activity.actor === 'agent') {
        const dot = document.createElement('span')
        dot.className = 'dot'
        row.appendChild(dot)
      }
      const count = document.createElement('span')
      count.className = 'count'
      count.textContent = b.objects + ' obj'
      row.appendChild(count)
      row.addEventListener('click', () => { switchBoard(b.id) })
      listEl.appendChild(row)
    }
    const sep = document.createElement('div')
    sep.className = 'sep-row'
    listEl.appendChild(sep)
    const form = document.createElement('form')
    form.className = 'board-new'
    form.innerHTML = '<input id="boardNameInput" maxlength="48" placeholder="board name" ' +
      'aria-label="board name" autocomplete="off" spellcheck="false">' +
      '<button type="submit" class="board-do">create</button>' +
      '<button type="button" class="board-do ghost" data-act="rename">rename this</button>'
    form.addEventListener('submit', (e) => {
      e.preventDefault()
      const value = form.querySelector('input').value
      form.querySelector('input').value = ''
      newBoard(value)
    })
    form.querySelector('[data-act="rename"]').addEventListener('click', async () => {
      const input = form.querySelector('input')
      if (await renameBoard(input.value)) input.value = ''
    })
    listEl.appendChild(form)
  } catch (err) { /* menu is chrome; never block the board */ }
}

// window.prompt is blocked in embedded webviews, so a prompt here is a silent dead button.
// The name lives in the board menu as a real field: type it, then create or rename.
async function suggestedBoardName () {
  const idx = await api('/api/boards').catch(() => null)
  return 'board ' + (((idx && idx.boards && idx.boards.length) || 0) + 1)
}

async function newBoard (rawName) {
  const name = String(rawName || '').trim() || await suggestedBoardName()
  const out = await api('/api/boards/new', { method: 'POST', body: JSON.stringify({ name }) }).catch(() => null)
  if (!out) return
  closeBoards()
  boardId = (out.active || 'b1')
  focusedOnce = false
  lastSeen = { rev: -1, newest: null }
  localRev = 0
  await load(true)
  await drawTabs()
}

async function renameBoard (rawName) {
  const name = String(rawName || '').trim()
  if (!name) return false
  await api('/api/boards/rename', { method: 'POST', body: JSON.stringify({ board: boardId, name }) }).catch(() => null)
  await drawTabs()
  return true
}

menuEl.addEventListener('click', async e => {
  e.stopPropagation()
  if (listEl.hidden) { await drawTabs(); openBoards() } else { closeBoards() }
})
document.addEventListener('click', e => {
  if (!listEl.hidden && !listEl.contains(e.target) && e.target !== menuEl) closeBoards()
})
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeBoards() })

el('newBoard').addEventListener('click', async () => {
  await drawTabs()
  openBoards()
  const input = listEl.querySelector('#boardNameInput')
  if (input) {
    if (!input.value) input.value = await suggestedBoardName()
    input.focus()
    input.select()
  }
})

// ------------------------------------------------------------------- tools
const tools = [...document.querySelectorAll('.tool')]
function setTool (name) {
  board.setTool(name)
  tools.forEach(t => t.classList.toggle('on', t.dataset.tool === name))
}
tools.forEach(btn => btn.addEventListener('click', () => {
  setTool(btn.dataset.tool)
}))

const swatches = el('swatches')
for (const p of PALETTE) {
  const b = document.createElement('button')
  b.className = 'swatch'
  b.style.setProperty('--swatch', p.ink)
  b.title = p.name
  b.addEventListener('click', () => {
    board.setInk(p.ink)
    swatches.querySelectorAll('.swatch').forEach(s => s.classList.toggle('on', s === b))
  })
  if (p.name === 'black') b.classList.add('on')
  swatches.appendChild(b)
}

document.querySelectorAll('.w').forEach(b => b.addEventListener('click', () => {
  const w = Number(b.dataset.w)
  board.setWidth(w)
  if (board.tool !== 'highlighter' && board.tool !== 'pen') setTool('pen')
  document.querySelectorAll('.w').forEach(x => x.classList.toggle('on', x === b))
}))

el('fit').addEventListener('click', () => board.fit())
el('undo').addEventListener('click', () => board.undo())

// --------------------------------------------------------------- text input
let textDone = null
let textOpenedAt = 0
function openTextEditor (spec, done) {
  textDone = done
  textOpenedAt = Date.now()
  const p = board.toScreen(spec.x, spec.y)
  const size = Math.max(12, (spec.size || 22) * board.camera.z)
  textEdit.style.display = 'block'
  textEdit.style.left = p.x + 'px'
  textEdit.style.top = p.y + 'px'
  textEdit.style.fontSize = size + 'px'
  textEdit.style.lineHeight = '1.15'
  textEdit.value = spec.text || ''
  textEdit.dataset.editing = spec.id || ''
  textEdit.focus()
  textEdit.select()
}

function closeTextEditor (value) {
  if (textEdit.style.display === 'none') return
  if (Date.now() - textOpenedAt < 250) return // the opening click must not close it
  textEdit.style.display = 'none'
  const done = textDone
  textDone = null
  if (done) done(value)
}

textEdit.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); closeTextEditor(textEdit.value) }
  if (e.key === 'Escape') { e.preventDefault(); closeTextEditor(null) }
  e.stopPropagation()
})
textEdit.addEventListener('blur', () => closeTextEditor(textEdit.value))

// --------------------------------------------------------------- shortcuts
window.addEventListener('keydown', e => {
  if (!shortcutSheet.hidden) return
  const typing = e.target instanceof Element && e.target.closest('input, textarea, [contenteditable="true"]')
  if (typing) return
  const key = e.key.toLowerCase()
  if ((e.metaKey || e.ctrlKey) && key === 'z') { e.preventDefault(); board.undo(); return }
  if (e.metaKey || e.ctrlKey || e.altKey) return
  const map = { v: 'select', h: 'hand', p: 'pen', m: 'highlighter', e: 'eraser',
    r: 'rect', o: 'ellipse', d: 'diamond', g: 'triangle', l: 'line', a: 'arrow',
    b: 'table', c: 'chart', t: 'text' }
  if (e.shiftKey && key === 'c') { setTool('chartline'); return }
  if (map[key]) { setTool(map[key]); return }
  if (key === 'f') { board.fit(); return }
  // shift+t opens the history scrubber; plain t is the text tool
  if (e.shiftKey && key === 't') {
    e.preventDefault()
    if (scrubber && !scrubber.hidden) scrubCloseIt()
    else scrubOpen()
    return
  }
  if (e.code === 'Space' && scrubber && !scrubber.hidden) { e.preventDefault(); scrubPlay.click(); return }
  if (key === 'delete' || key === 'backspace') { e.preventDefault(); board.deleteSelection() }
  const n = Number(key)
  if (n >= 1 && n <= PALETTE.length) swatches.querySelectorAll('.swatch')[n - 1].click()
})

canvas.addEventListener('pointerdown', () => { activityJump.hidden = true })

// -------------------------------------------------------------------- boot
board.setTool('pen')
window.__charrette = { board, openTextEditor, get tool () { return board.tool } }
if (!params.get('board')) {
  // open the board the server last had active, not always b1
  const idx = await api('/api/boards').catch(() => null)
  if (idx && idx.active) boardId = idx.active
}
drawTabs()
function stopPolling () {
  if (pollTimer === null) return
  clearInterval(pollTimer)
  pollTimer = null
  if (controlTimer !== null) { clearInterval(controlTimer); controlTimer = null }
}
function startPolling () {
  if (document.hidden) return
  load()
  if (pollTimer !== null) return
  pollTimer = setInterval(load, 900)

  // The director tick: what the chat side asked this page to do, performed here.
  controlTimer = setInterval(controlTick, 700)
}
startPolling()
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopPolling()
  else startPolling()
})
window.addEventListener('focus', startPolling)
window.addEventListener('beforeunload', stopPolling)

void THEME

// ---------------------------------------------------------------- board session
// A chat attached to this board, so the page is usable as its own piece anywhere it is served.
// The server runs the agent with the board's own verbs as tools; every tool event refetches the
// board, so marks appear while the sentence is still being written.
const chatEl = el('chat')
const chatLog = el('chatLog')
const chatText = el('chatText')
const chatForm = el('chatForm')
const chatDot = el('chatDot')
const chatModel = el('chatModel')
const chatSend = el('chatSend')
const chatSendLabel = el('chatSendLabel')
const chatToggle = el('chatToggle')
const chatEmpty = el('chatEmpty')
const chatThinking = el('chatThinking')
let chatBusy = false
let chatBubble = null
let chatController = null
let chatStopTimer = null
let chatStopping = false
let chatStoppedSeen = false
let activeToolGroup = null
let lastPrompt = ''
let chatEventError = false

// ---------------------------------------------------------------- replay
// A reply that drew something can show that work again, on this board, at the
// reader's speed. One speed for the page: pick it once and every reply follows.
const REPLAY_SPEED_KEY = 'charrette.replaySpeed'
let replaySpeed = Number(localStorage.getItem(REPLAY_SPEED_KEY)) || 1
let replayCamera = null
// One replay session at a time: it owns the camera, the teardown timer and the bar.
// A generation counter is not enough, because the *timer* from an old session has to be
// cancelled, not merely ignored: it was stopping the replay you had just started.
let replaySession = 0
let teardownTimer = null

function finishReplay (gen) {
  if (teardownTimer) { clearTimeout(teardownTimer); teardownTimer = null }
  teardownTimer = setTimeout(() => {
    teardownTimer = null
    if (gen !== replaySession) return          // a newer replay took over
    if (scrubber && !scrubber.hidden) return   // the bar is holding a frame on purpose
    board.stopReplay()
    if (replayCamera) { board.setCamera(replayCamera); replayCamera = null }
  }, 900)
}

function syncSpeeds () {
  document.querySelectorAll('.speeds [data-speed]').forEach(btn => {
    const on = Number(btn.dataset.speed) === replaySpeed
    btn.classList.toggle('on', on)
    btn.setAttribute('aria-pressed', on ? 'true' : 'false')
  })
}

function wireSpeeds (root) {
  root.querySelectorAll('[data-speed]').forEach(btn => {
    btn.addEventListener('click', () => {
      replaySpeed = Number(btn.dataset.speed)
      localStorage.setItem(REPLAY_SPEED_KEY, String(replaySpeed))
      syncSpeeds()
      if (board.replay) board.setReplaySpeed(replaySpeed)
    })
  })
}

function frameForReplay (ids) {
  const box = board.bboxOf(ids)
  if (!box) return
  const stage = document.getElementById('stage')
  const w = stage.clientWidth
  const h = stage.clientHeight
  const pad = 70
  const bw = Math.max(40, box[2] - box[0])
  const bh = Math.max(40, box[3] - box[1])
  const z = Math.min((w - pad * 2) / bw, (h - pad * 2) / bh, 1.6)
  if (!replayCamera) replayCamera = { ...board.camera }
  board.setCamera({ x: box[0] - (w / z - bw) / 2, y: box[1] - (h / z - bh) / 2, z })
}

function messageActions (root, refs) {
  const row = document.createElement('div')
  row.className = 'msg-actions'
  const again = document.createElement('button')
  again.type = 'button'
  again.className = 'replay-btn'
  again.textContent = 'replay'
  again.title = 'watch this reply drawn again (on this board)'
  again.addEventListener('click', () => playReplay(refs, again))
  row.appendChild(again)
  const speeds = document.createElement('div')
  speeds.className = 'speeds'
  speeds.setAttribute('role', 'group')
  speeds.setAttribute('aria-label', 'replay speed')
  for (const value of [0.5, 1, 2, 5]) {
    const b = document.createElement('button')
    b.type = 'button'
    b.dataset.speed = String(value)
    b.textContent = value + 'x'
    speeds.appendChild(b)
  }
  row.appendChild(speeds)
  root.appendChild(row)
  wireSpeeds(row)
  syncSpeeds()
}

async function playReplay (ids, trigger, opts2 = {}) {
  const query = '/api/replay?board=' + encodeURIComponent(boardId) +
    (ids && ids.length ? '&ids=' + encodeURIComponent(ids.join(',')) : '')
  let data = null
  try { data = await api(query) } catch (err) { return null }
  if (!data || !data.segments || !data.segments.length) return null
  if (opts2.frame !== false) frameForReplay(ids && ids.length ? ids : data.objects)
  if (trigger) trigger.textContent = 'drawing…'
  const gen = ++replaySession
  if (teardownTimer) { clearTimeout(teardownTimer); teardownTimer = null }
  const run = board.playReplay(data.segments, data.total, {
    speed: replaySpeed,
    onDone: () => {
      if (trigger) trigger.textContent = 'replay'
      finishReplay(gen)
    }
  })
  return run
}

// ---------------------------------------------------------------- scrubber
// The whole board history on one clock. Hidden until asked for: `t`, or the
// replay chip in the header. Dragging is a seek, so a frame is a frame.
const scrubber = document.getElementById('scrubber')
const scrubToggle = document.getElementById('scrubToggle')
const scrubRange = document.getElementById('scrubRange')
const scrubPlay = document.getElementById('scrubPlay')
const scrubClock = document.getElementById('scrubClock')
const scrubClose = document.getElementById('scrubClose')
let scrubData = null

function clockText (ms) {
  const total = Math.max(0, Math.round(ms / 1000))
  const m = Math.floor(total / 60)
  const s = String(total % 60).padStart(2, '0')
  return m + ':' + s
}

function scrubTick (t, total) {
  if (!total) return
  scrubRange.value = String(Math.round((t / total) * 1000))
  scrubClock.textContent = clockText(t) + ' / ' + clockText(total)
  scrubPlay.textContent = board.replay && board.replay.playing ? 'pause' : 'play'
}

async function scrubOpen () {
  if (!scrubData) {
    scrubData = await api('/api/replay?board=' + encodeURIComponent(boardId)).catch(() => null)
  }
  const data = scrubData
  if (!data || !data.segments || !data.segments.length) return
  replaySession += 1
  if (teardownTimer) { clearTimeout(teardownTimer); teardownTimer = null }
  scrubber.hidden = false
  scrubToggle.setAttribute('aria-pressed', 'true')
  if (!replayCamera) replayCamera = { ...board.camera }
  board.playReplay(data.segments, data.total, { speed: replaySpeed, onTick: scrubTick })
  board.seekReplay(0)
  scrubTick(0, data.total)
}

function scrubCloseIt () {
  replaySession += 1
  if (teardownTimer) { clearTimeout(teardownTimer); teardownTimer = null }
  scrubber.hidden = true
  scrubToggle.setAttribute('aria-pressed', 'false')
  board.stopReplay()
  if (replayCamera) { board.setCamera(replayCamera); replayCamera = null }
}

function wireScrubber () {
  if (!scrubber) return
  wireSpeeds(document.getElementById('scrubSpeeds'))
  syncSpeeds()
  scrubToggle.addEventListener('click', () => { scrubber.hidden ? scrubOpen() : scrubCloseIt() })
  scrubClose.addEventListener('click', scrubCloseIt)
  scrubRange.addEventListener('input', () => {
    const run = board.replay
    if (!run) return
    board.seekReplay((Number(scrubRange.value) / 1000) * run.total)
    scrubPlay.textContent = 'play'
  })
  scrubPlay.addEventListener('click', () => {
    const run = board.replay
    if (!run) return
    if (run.playing) { board.seekReplay(run.t); scrubPlay.textContent = 'play' }
    else { board.resumeReplay(run.t, { onTick: scrubTick }); scrubPlay.textContent = 'pause' }
  })
}


// ---------------------------------------------------------------- director
// A chat turn outside the page can leave a command: open this board, replay these
// objects, frame that. The page performs it where the reader is already looking,
// which is what makes the agent feel present rather than asynchronous.
let controlTickCount = 0

async function followActiveBoard () {
  controlTickCount += 1
  if (controlTickCount % 4 !== 0) return
  const idx = await api('/api/boards').catch(() => null)
  if (idx && idx.active && idx.active !== boardId) await switchBoard(idx.active)
}

let controlPrimed = false

async function controlTick () {
  followActiveBoard()
  let out = null
  try { out = await api('/api/control?since=' + controlSeq) } catch (err) { return }
  if (!out) return
  if (!controlPrimed) {
    // a freshly opened page must not replay the cues that were aimed at the last one
    controlPrimed = true
    controlSeq = Number(out.seq) || 0
    return
  }
  if (!Array.isArray(out.items) || !out.items.length) {
    if (Number.isFinite(out.seq)) controlSeq = out.seq
    return
  }
  controlSeq = Math.max(controlSeq, Number(out.seq) || 0)
  for (const item of out.items) {
    try { await runControl(item) } catch (err) { /* one bad cue must not stop the queue */ }
  }
}

async function runControl (item) {
  if (item.cmd === 'board' && typeof item.board === 'string' && item.board !== boardId) {
    await switchBoard(item.board)
    return
  }
  if (item.cmd === 'stop') {
    board.stopReplay()
    if (scrubber) scrubber.hidden = true
    return
  }
  if (item.cmd === 'focus') {
    frameForReplay(Array.isArray(item.ids) ? item.ids : null)
    return
  }
  if (item.cmd === 'replay') {
    const ids = Array.isArray(item.ids) && item.ids.length ? item.ids : null
    const speed = Number(item.speed) > 0 ? Number(item.speed) : replaySpeed
    const keepSpeed = replaySpeed
    replaySpeed = speed
    await playReplay(ids, null, { frame: item.focus !== false })
    replaySpeed = keepSpeed
    syncSpeeds()
  }
}

function chatScroll () { chatLog.scrollTop = chatLog.scrollHeight }

function preserveChatScroll (change) {
  const follow = isNearBottom(chatLog)
  change()
  if (follow) chatScroll()
}

function updateChatEmpty () {
  chatEmpty.hidden = chatLog.children.length > 0 || chatBusy
}

function stampMessage (root, at) {
  const time = document.createElement('time')
  const instant = at || new Date().toISOString()
  time.className = 'chat-time'
  time.dateTime = instant
  time.textContent = formatChatTime(instant)
  root.appendChild(time)
}

function renderAgentBubble (bubble, streaming = false) {
  bubble.body.innerHTML = renderMessage(bubble.raw, new URL(location.href).searchParams.get('t') || '')
  if (streaming) {
    const caret = document.createElement('span')
    caret.className = 'stream-caret'
    caret.setAttribute('aria-hidden', 'true')
    bubble.body.appendChild(caret)
  }
}

function chatMessage (role, text, at, refs) {
  activeToolGroup = null
  const root = document.createElement('article')
  root.className = 'chat-msg ' + role
  const body = document.createElement('div')
  body.className = 'chat-copy'
  const bubble = { root, body, raw: String(text || '') }
  if (role === 'agent') renderAgentBubble(bubble)
  else body.textContent = bubble.raw
  // a reply that drew but said nothing keeps its body out of the way
  if (role !== 'agent' || bubble.raw.trim() || !Array.isArray(refs) || !refs.length) root.appendChild(body)
  stampMessage(root, at)
  if (role === 'agent' && Array.isArray(refs) && refs.length) messageActions(root, refs)
  preserveChatScroll(() => chatLog.appendChild(root))
  updateChatEmpty()
  return bubble
}

function chatError (message, prompt = lastPrompt) {
  activeToolGroup = null
  const card = document.createElement('section')
  card.className = 'chat-error'
  card.setAttribute('role', 'alert')
  const eyebrow = document.createElement('span')
  eyebrow.className = 'chat-error-label'
  eyebrow.textContent = 'The board lost the thread'
  const reason = document.createElement('p')
  reason.textContent = String(message || 'Something went wrong')
  const retry = document.createElement('button')
  retry.type = 'button'
  retry.textContent = 'Retry'
  retry.disabled = !prompt
  retry.addEventListener('click', () => {
    card.remove()
    updateChatEmpty()
    chatSendMessage(prompt)
  })
  card.append(eyebrow, reason, retry)
  preserveChatScroll(() => chatLog.appendChild(card))
  updateChatEmpty()
}

function chatStopped () {
  activeToolGroup = null
  const card = document.createElement('section')
  card.className = 'chat-stopped'
  card.setAttribute('role', 'status')
  const label = document.createElement('strong')
  label.textContent = 'Stopped'
  const note = document.createElement('span')
  note.textContent = 'Finished marks stay on the board.'
  card.append(label, note)
  preserveChatScroll(() => chatLog.appendChild(card))
  updateChatEmpty()
}

function chatChip (name, result) {
  const follow = isNearBottom(chatLog)
  if (!activeToolGroup || !activeToolGroup.isConnected) {
    activeToolGroup = document.createElement('section')
    activeToolGroup.className = 'chat-tool-group'
    const heading = document.createElement('span')
    heading.className = 'chat-tool-label'
    heading.textContent = 'On the board'
    activeToolGroup.appendChild(heading)
    chatLog.appendChild(activeToolGroup)
  }
  const chip = document.createElement('span')
  chip.className = 'chat-tool'
  const label = document.createElement('b')
  label.textContent = name.replace(/^board_/, '')
  const status = document.createElement('span')
  const failed = result && (result.err || result.ok === false)
  status.className = failed ? 'bad' : ''
  status.textContent = failed
    ? String(result.err || result.message || 'failed')
    : String(result.object || (result.rev ? 'rev ' + result.rev : 'done'))
  chip.append(label, status)
  activeToolGroup.appendChild(chip)
  if (follow) chatScroll()
  updateChatEmpty()
}

function setChatBusy (busy) {
  chatBusy = busy
  chatThinking.hidden = !busy
  chatDot.classList.toggle('busy', busy)
  chatSend.classList.toggle('stop', busy)
  const label = busy ? 'Stop response' : 'Send message'
  chatSend.setAttribute('aria-label', label)
  chatSend.title = busy ? 'stop (escape)' : 'send (enter)'
  chatSendLabel.textContent = busy ? 'stop' : 'send'
  updateChatEmpty()
}

async function chatHistory () {
  try {
    const s = await api('/api/session?board=' + encodeURIComponent(boardId))
    const routeName = s.ready ? s.provider + ' · ' + s.model : 'no keys: run scripts/keys.py add'
    chatModel.textContent = routeName
    chatDot.classList.toggle('off', !s.ready)
    chatLog.replaceChildren()
    activeToolGroup = null
    chatBubble = null
    const msgs = s.messages || []
    for (const m of msgs) chatMessage(m.role === 'user' ? 'user' : 'agent', m.text, m.at, m.refs)
    updateChatEmpty()
    chatScroll()
  } catch (err) {
    chatError(String(err.message || err), '')
  }
}

function chatHandle (evt) {
  if (evt.type === 'start') {
    chatModel.textContent = (evt.keys > 1 ? evt.keys + ' keys · ' : '') + 'working'
  } else if (evt.type === 'text') {
    chatThinking.hidden = true
    activeToolGroup = null
    if (!chatBubble) chatBubble = chatMessage('agent', '')
    const follow = isNearBottom(chatLog)
    chatBubble.raw += evt.delta
    renderAgentBubble(chatBubble, true)
    if (follow) chatScroll()
  } else if (evt.type === 'route') {
    chatModel.textContent = evt.name + ' · ' + evt.model
  } else if (evt.type === 'tool') {
    chatThinking.hidden = true
    if (chatBubble) renderAgentBubble(chatBubble, false)
    chatBubble = null
    chatChip(evt.name, evt.result)
    load()                        // the mark appears as it is made, not at the end
  } else if (evt.type === 'error') {
    chatEventError = true
    chatError(evt.error || 'Something went wrong')
  } else if (evt.type === 'stopped') {
    chatStoppedSeen = true
    clearTimeout(chatStopTimer)
    if (chatBubble) renderAgentBubble(chatBubble, false)
    chatStopped()
    chatModel.textContent = chatModel.textContent.replace(' · working', '').replace('working', 'session')
  } else if (evt.type === 'done') {
    if (chatBubble) renderAgentBubble(chatBubble, false)
    chatModel.textContent = chatModel.textContent.replace(' · working', '')
    load(true)
  }
}

async function chatSendMessage (text) {
  const prompt = String(text || '').trim()
  if (chatBusy || !prompt) return
  lastPrompt = prompt
  chatController = new AbortController()
  chatEventError = false
  chatStopping = false
  chatStoppedSeen = false
  chatBubble = null
  activeToolGroup = null
  chatMessage('user', prompt)
  setChatBusy(true)
  try {
    const res = await fetch('/api/session/send', {
      method: 'POST',
      signal: chatController.signal,
      headers: { 'X-Charrette-Token': TOKEN, 'Content-Type': 'application/json' },
      body: JSON.stringify({ board: boardId, text: prompt, view: { w: canvas.clientWidth, h: canvas.clientHeight } })
    })
    if (!res.ok) throw new Error('session → ' + res.status)
    if (!res.body) throw new Error('session returned no stream')
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let cut
      while ((cut = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, cut)
        buf = buf.slice(cut + 2)
        const line = frame.split('\n').find(line => line.startsWith('data:'))
        if (!line) continue
        try { chatHandle(JSON.parse(line.slice(5))) } catch (err) { chatError('Unreadable session event: ' + String(err)) }
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError' && !chatEventError) chatError(String(err.message || err), prompt)
  } finally {
    clearTimeout(chatStopTimer)
    if (chatStopping && !chatStoppedSeen && chatController?.signal.aborted) chatStopped()
    if (chatBubble) renderAgentBubble(chatBubble, false)
    chatController = null
    chatStopping = false
    setChatBusy(false)
  }
}

function chatStop () {
  if (!chatBusy || chatStopping) return
  chatStopping = true
  chatSendLabel.textContent = 'stopping'
  chatSend.setAttribute('aria-label', 'Stopping response')
  api('/api/session/stop', { method: 'POST', body: JSON.stringify({ board: boardId }) })
    .catch(() => {})
  // Keep reading until the server's terminal frame. Abort only if that path is gone.
  chatStopTimer = setTimeout(() => {
    if (chatBusy && !chatStoppedSeen) chatController?.abort()
  }, 2000)
}

function chatTogglePanel (force) {
  const open = force === undefined ? chatEl.hidden : force
  chatEl.hidden = !open
  chatToggle.setAttribute('aria-pressed', String(open))
  chatToggle.classList.toggle('on', open)
  if (open) { chatHistory(); chatText.focus() }
  setTimeout(() => window.dispatchEvent(new Event('resize')), 60)
}

chatToggle?.addEventListener('click', () => chatTogglePanel())
el('chatReset')?.addEventListener('click', async () => {
  await api('/api/session/reset', { method: 'POST', body: JSON.stringify({ board: boardId }) }).catch(() => {})
  chatHistory()
})
chatForm?.addEventListener('submit', e => {
  e.preventDefault()
  if (chatBusy) {
    chatStop()
    return
  }
  const text = chatText.value
  if (!text.trim()) return
  chatText.value = ''
  chatText.style.height = 'auto'
  chatSendMessage(text)
})
chatText?.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); chatForm.requestSubmit() }
})
chatText?.addEventListener('input', () => {
  chatText.style.height = 'auto'
  chatText.style.height = Math.min(132, chatText.scrollHeight) + 'px'
})

let shortcutReturnFocus = null

function closeShortcutSheet () {
  if (shortcutSheet.hidden) return
  shortcutSheet.hidden = true
  shortcutHelp.setAttribute('aria-expanded', 'false')
  if (shortcutReturnFocus?.isConnected) shortcutReturnFocus.focus()
  shortcutReturnFocus = null
}

function toggleShortcutSheet () {
  if (!shortcutSheet.hidden) { closeShortcutSheet(); return }
  shortcutReturnFocus = document.activeElement
  shortcutSheet.hidden = false
  shortcutHelp.setAttribute('aria-expanded', 'true')
  shortcutClose.focus()
}

shortcutHelp?.addEventListener('click', toggleShortcutSheet)
shortcutClose?.addEventListener('click', closeShortcutSheet)
shortcutSheet?.addEventListener('click', e => { if (e.target === shortcutSheet) closeShortcutSheet() })

window.addEventListener('keydown', e => {
  const typing = e.target instanceof Element && e.target.closest('input, textarea, [contenteditable="true"]')
  const key = e.key
  if (!shortcutSheet.hidden) {
    if (key === 'Escape' || key === '?') {
      e.preventDefault()
      closeShortcutSheet()
      return
    }
    if (e.key === 'Tab') {
      e.preventDefault()
      shortcutClose.focus()
      return
    }
    e.preventDefault()
    return
  }
  if (e.key === 'j' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); chatTogglePanel(); return }
  if (key === '?' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault()
    toggleShortcutSheet()
    return
  }
  if (e.key === 'Escape' && !chatEl.hidden) {
    e.preventDefault()
    if (chatBusy) chatStop()
    chatTogglePanel(false)
  }
})
el('emptyChat')?.addEventListener('click', () => chatTogglePanel(true))
el('emptyShortcuts')?.addEventListener('click', toggleShortcutSheet)
window.addEventListener('resize', drawMinimap)

// the session is per board: switching boards switches conversations
window.addEventListener('charrette:board', () => { if (!chatEl.hidden) chatHistory() })
if (window.matchMedia('(min-width: 1100px)').matches) chatTogglePanel(true)

// wire the replay controls once every declaration above exists: a module runs top to bottom
wireScrubber()
syncSpeeds()

// A small handle for the offline browser tests (and for me, when I need to look at
// what the page actually did rather than what the code says it does).
window.__charrette = { board, playReplay, scrubOpen, scrubClose: scrubCloseIt, get speed () { return replaySpeed } }
