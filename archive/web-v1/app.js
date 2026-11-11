/**
 * Chalk web surface: thin DOM + network shell around the shared engine.
 * Everything that knows about pixels, camera or strokes lives in /chalk.mjs.
 */

import { createBoard, PALETTE } from '/chalk.mjs'

const params = new URLSearchParams(location.search)
const TOKEN = params.get('t') || ''
const BOARD = params.get('board') || 'b1'

const canvas = document.getElementById('board')
const stat = document.getElementById('stat')
const boardName = document.getElementById('boardName')
const hint = document.getElementById('hint')

const api = async (path, init = {}) => {
  const res = await fetch(path, {
    ...init,
    headers: { 'X-Chalk-Token': TOKEN, 'Content-Type': 'application/json', ...(init.headers || {}) }
  })
  if (res.status === 401) throw new Error('not authorised: open the URL the server printed')
  if (!res.ok) throw new Error(path + ' -> ' + res.status)
  return res.json()
}

const board = createBoard(canvas, {
  onStroke: async (points, meta) => {
    try {
      const out = await api('/api/stroke', {
        method: 'POST',
        body: JSON.stringify({ board: BOARD, points, ink: meta.ink, width: meta.width })
      })
      if (meta.tempId) board.dropTemp(meta.tempId)
      await load()
      return out
    } catch (err) {
      console.error(err)
    }
  },
  onCamera: cam => {
    clearTimeout(onCamera.timer)
    onCamera.timer = setTimeout(() => {
      api('/api/camera', { method: 'POST', body: JSON.stringify({ board: BOARD, camera: cam }) }).catch(() => {})
    }, 600)
  }
})

async function load() {
  try {
    const doc = await api('/api/board?board=' + encodeURIComponent(BOARD))
    board.setDoc(doc)
    const n = Object.keys(doc.objects || {}).length
    stat.textContent = 'rev ' + doc.rev + ' · ' + n + (n === 1 ? ' object' : ' objects')
    for (const [id, obj] of Object.entries(doc.objects || {})) {
      if (obj.kind === 'image' && obj.src && obj.status !== 'rendering' && obj.status !== 'failed') {
        const img = new Image()
        img.onload = () => board.setImage(obj.src, img)
        img.src = '/api/img?board=' + encodeURIComponent(BOARD) + '&id=' + encodeURIComponent(id) +
          '&t=' + encodeURIComponent(TOKEN)
      }
    }
  } catch (err) {
    stat.textContent = String(err.message || err)
  }
}

// ---------------------------------------------------------------- chrome
const swatches = document.getElementById('swatches')
for (const p of PALETTE) {
  const b = document.createElement('button')
  b.className = 'swatch'
  b.style.background = p.ink
  b.title = p.name
  b.dataset.ink = p.ink
  b.addEventListener('click', () => {
    board.setInk(p.ink)
    board.setWidth(3)
    document.querySelectorAll('.swatch').forEach(s => s.classList.toggle('on', s === b))
    document.querySelectorAll('.w').forEach(w => w.classList.toggle('on', w.dataset.w === '3'))
    board.setTool('pen')
    document.getElementById('tPen').classList.add('on')
    document.getElementById('tHand').classList.remove('on')
  })
  if (p.name === 'black') b.classList.add('on')
  swatches.appendChild(b)
}

for (const b of document.querySelectorAll('.w')) {
  b.addEventListener('click', () => {
    const w = Number(b.dataset.w)
    if (b.classList.contains('hi')) {
      board.setTool('highlighter')
      board.setWidth(18)
      document.getElementById('tPen').classList.add('on')
      document.getElementById('tHand').classList.remove('on')
    } else {
      board.setTool('pen')
      board.setWidth(w)
      document.getElementById('tPen').classList.add('on')
      document.getElementById('tHand').classList.remove('on')
    }
    document.querySelectorAll('.w').forEach(x => x.classList.toggle('on', x === b))
  })
}

document.getElementById('tPen').addEventListener('click', () => {
  board.setTool('pen')
  document.getElementById('tPen').classList.add('on')
  document.getElementById('tHand').classList.remove('on')
})
document.getElementById('tHand').addEventListener('click', () => {
  board.setTool('hand')
  document.getElementById('tHand').classList.add('on')
  document.getElementById('tPen').classList.remove('on')
})
document.getElementById('fit').addEventListener('click', () => board.fit())

window.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return
  if (e.key === 'p') document.getElementById('tPen').click()
  if (e.key === 'h') document.getElementById('tHand').click()
  if (e.key === 'f') board.fit()
  const n = Number(e.key)
  if (n >= 1 && n <= PALETTE.length) document.querySelectorAll('.swatch')[n - 1].click()
})

board.setTool('pen')
canvas.addEventListener('pointerdown', () => hint.classList.add('gone'), { once: true })

boardName.textContent = BOARD
load()
setInterval(load, 900)
