import { createBoard } from '../renderer/charrette.mjs'

function assert (condition, message) {
  if (!condition) throw new Error(message)
}

class FakePath2D {
  moveTo () {}
  lineTo () {}
  closePath () {}
  rect () {}
  arc () {}
}

globalThis.Path2D = FakePath2D
globalThis.window = { devicePixelRatio: 1 }
globalThis.document = { fonts: { load: () => Promise.resolve([]) } }

const canvas = {
  width: 640,
  height: 360,
  clientWidth: 640,
  clientHeight: 360,
  style: {},
  listeners: new Map(),
  addEventListener (name, fn) { this.listeners.set(name, fn) },
  removeEventListener (name) { this.listeners.delete(name) },
  getBoundingClientRect () { return { left: 0, top: 0, width: 640, height: 360 } }
}
const base = {
  canvas,
  measureText: text => ({ width: String(text).length * 7 }),
  createPattern: () => null,
  createLinearGradient: () => ({ addColorStop () {} }),
  getTransform: () => ({ a: 1, d: 1, e: 0, f: 0 })
}
const ctx = new Proxy(base, {
  get (target, key) {
    if (key in target) return target[key]
    return () => {}
  },
  set (target, key, value) { target[key] = value; return true }
})
canvas.getContext = () => ctx

const board = createBoard(canvas, {})
assert(typeof createBoard === 'function', 'createBoard export must be callable')
assert(typeof board.draw === 'function', 'draw entry point must exist')
assert(typeof board.setDoc === 'function', 'setDoc entry point must exist')
assert(typeof board.setTool === 'function', 'setTool entry point must exist')
assert(typeof board.setInk === 'function', 'setInk entry point must exist')
assert(typeof board.setWidth === 'function', 'setWidth entry point must exist')
assert(typeof board.setTimeline === 'function', 'setTimeline entry point must exist')
assert(typeof board.revealAt === 'function', 'revealAt entry point must exist')
assert(typeof board.penAt === 'function', 'penAt entry point must exist')
assert(typeof board.anchorOf === 'function', 'anchorOf entry point must exist')
assert(typeof board.fit === 'function', 'fit entry point must exist')
assert(typeof board.destroy === 'function', 'destroy entry point must exist')

board.setDoc({ rev: 1, camera: { x: 0, y: 0, z: 1 }, objects: {
  stroke: { kind: 'stroke', pts: [[0, 0], [100, 0]], bbox: [0, 0, 100, 1], layer: 'human-ink' }
} })
board.setTimeline([{ id: 'stroke', t0: 0, t1: 1000 }])
board.revealAt(500)
const pen = board.penAt('stroke')
assert(pen !== null, 'penAt must resolve a partially revealed stroke')
assert(Number.isFinite(pen.x) && Number.isFinite(pen.y), 'penAt coordinates must be finite')
board.destroy()
console.log('renderer contract passed')
