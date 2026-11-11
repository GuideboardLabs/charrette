import fs from 'node:fs'

function assert (condition, message) {
  if (!condition) throw new Error(message)
}

const htmlPath = process.argv[2]
assert(Boolean(htmlPath), 'generated replay path is required')
const html = fs.readFileSync(htmlPath, 'utf8')
const match = html.match(/<script type="module">([\s\S]*?)<\/script>/)
assert(Boolean(match), 'generated replay must contain one module script')

class FakePath2D {
  moveTo () {}
  lineTo () {}
  closePath () {}
  rect () {}
  arc () {}
}
class FakeClassList {
  constructor () { this.values = new Set() }
  contains (name) { return this.values.has(name) }
  toggle (name, force) {
    if (force === undefined ? !this.values.has(name) : force) this.values.add(name)
    else this.values.delete(name)
  }
}
function element (id = '') {
  return {
    id,
    style: {},
    className: '',
    classList: new FakeClassList(),
    children: [],
    textContent: '',
    appendChild (child) { this.children.push(child); return child },
    addEventListener () {},
    getBoundingClientRect () { return { left: 0, top: 0, width: 640, height: 360 } }
  }
}

const elements = new Map()
for (const id of ['wrap', 'sentence', 'stage', 'progbar', 'again', 'speed', 'state']) elements.set(id, element(id))
const canvas = element('board')
canvas.width = 640
canvas.height = 360
canvas.clientWidth = 640
canvas.clientHeight = 360
canvas.listeners = new Map()
canvas.addEventListener = (name, fn) => canvas.listeners.set(name, fn)
canvas.removeEventListener = name => canvas.listeners.delete(name)
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
elements.set('board', canvas)

globalThis.Path2D = FakePath2D
globalThis.window = { devicePixelRatio: 1 }
globalThis.document = {
  fonts: { load: () => Promise.resolve([]) },
  getElementById: id => elements.get(id),
  createElement: () => element(),
  createTextNode: text => ({ textContent: text })
}
globalThis.Image = class { set src (value) { this._src = value } }
globalThis.IntersectionObserver = class { constructor (fn) { this.fn = fn } observe () {} disconnect () {} }
globalThis.performance = { now: () => 1234 }
globalThis.requestAnimationFrame = () => 1
globalThis.setTimeout = () => 1

const source = Buffer.from(match[1]).toString('base64')
await import('data:text/javascript;base64,' + source)
const replay = window.__replay
assert(Boolean(replay), 'generated page must expose window.__replay')
assert(replay.TIMELINE.length === 2, 'probe fixture must retain both timeline objects')
assert(replay.SEGMENTS.every(segment => segment.to > segment.from), 'caption segments must never be empty')
assert(replay.seek(-1) === 'point', 'seek before the first object must show pointer mode')
assert(replay.seek(100) === 'pen', 'seek inside first stroke must show pen mode')
assert(replay.seek(450) === 'point', 'seek in object gap must show pointer mode')
assert(replay.seek(700) === 'pen', 'seek inside second stroke must show pen mode even with one caption word')
assert(replay.seek(replay.TOTAL) === 'point', 'seek at finish must return to pointer mode')
assert(elements.get('wrap').classList.contains('finished'), 'seek at finish must expose finished chrome')
const first = replay.seek(700)
const second = replay.seek(700)
assert(first === second, 'repeated seek at one timestamp must be deterministic')
assert(!elements.get('wrap').classList.contains('finished'), 'seeking backward must clear finished chrome')
const progress = Number.parseFloat(elements.get('progbar').style.width)
assert(Number.isFinite(progress), 'seek must write finite progress')
assert(progress >= 0 && progress <= 100, 'seek progress must stay within zero and one hundred percent')
console.log('replay seek contract passed')
