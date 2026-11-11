import { createAgentHand, stepAgentHand, drawAgentHand } from '../renderer/charrette.mjs'

function assert (condition, message) {
  if (!condition) throw new Error(message)
}

// A board-shaped stub: the hand only needs two lookups.
const anchors = { a: { x: 100, y: 100 }, b: { x: 300, y: 100 }, c: { x: 300, y: 300 } }
const pens = { a: { x: 100, y: 100, angle: 0 }, b: { x: 300, y: 100, angle: 0 }, c: { x: 300, y: 300, angle: 1.2 } }
const anchorOf = id => anchors[id]
const penAt = id => pens[id]

const timeline = [
  { id: 'a', kind: 'stroke', t0: 0, t1: 1000 },
  { id: 'b', kind: 'image', t0: 1260, t1: 2160 },
  { id: 'c', kind: 'stroke', t0: 2420, t1: 3420 }
]
const total = 3680

const hand = createAgentHand()
assert(hand.mode === 'point' && hand.x === 0 && hand.modeT === 0,
  'a fresh hand is a pointer at the origin, with no history')

// mid-stroke: the pen sits on the ink head and squints
const drawing = stepAgentHand(hand, 500, timeline, penAt, anchorOf, total)
assert(drawing.mode === 'pen', 'while a stroke is being drawn the body is the pen')
assert(drawing.x === 100 && drawing.y === 100, 'the pen tip follows penAt for the current segment')
assert(drawing.curl === 0 && drawing.open === 1, 'a pen does not curl')

// the same moment twice must be the same frame, or a scrubber cannot seek
const again = stepAgentHand(createAgentHand(), 500, timeline, penAt, anchorOf, total)
assert(again.x === drawing.x && again.y === drawing.y && again.mode === drawing.mode,
  'stepAgentHand is a function of t, not of how many times it ran')

// placing an image: the glove closes on the object, then lets go
const grabbing = stepAgentHand(createAgentHand(), 1500, timeline, penAt, anchorOf, total)
assert(grabbing.mode === 'grab', 'an image is placed with the glove, not drawn with the pen')
assert(grabbing.curl > 0.5, 'the glove closes around the object it is placing')
const releasing = stepAgentHand(createAgentHand(), 2140, timeline, penAt, anchorOf, total)
assert(releasing.curl < 0.4, 'the glove opens again at the end of the placement')

// between segments the hand travels, pointing, with a small bob
const travelling = stepAgentHand(createAgentHand(), 1100, timeline, penAt, anchorOf, total)
assert(travelling.mode === 'point', 'between objects the hand is a pointer again')
assert(travelling.x > 100 && travelling.x < 300, 'the pointer moves toward the next object')
assert(travelling.bob !== 0, 'a pointer that slides perfectly still reads as a sprite')

// the clock starts on the first mark, pen down, not drifting in from nowhere
const entering = stepAgentHand(createAgentHand(), 0, timeline, penAt, anchorOf, total)
assert(entering.x === 100 && entering.y === 100 && entering.mode === 'pen',
  'at t0 the hand is already on the first mark with the pen down')

// the body cross-fades: one hand, two moments, morph reaches 1 within 150 ms
const morphing = createAgentHand()
const justPlaced = stepAgentHand(morphing, 1500, timeline, penAt, anchorOf, total)
assert(justPlaced.mode === 'grab' && justPlaced.morph === 0,
  'the body starts its cross-fade the moment the mode changes')
const settled = stepAgentHand(morphing, 1700, timeline, penAt, anchorOf, total)
assert(settled.morph === 1, 'a settled mode change finishes its morph')

// a blink is periodic and bounded, so it never freezes a face mid-blink
const blinkSamples = [0, 60, 200, 3400, 3460, 6800].map(t => stepAgentHand(createAgentHand(), t, timeline, penAt, anchorOf, total).blink)
assert(blinkSamples.every(v => v >= 0 && v <= 1), 'blink stays between 0 and 1')
assert(blinkSamples[1] > 0.5, 'the blink is visible at 60 ms into its 140 ms squash')
assert(blinkSamples[2] === 0, 'the eye is open again well before the next blink')

// drawing must not throw without a canvas: geometry only
const ctxStub = new Proxy({}, { get: (target, prop) => (prop === 'canvas' ? {} : () => target), set: () => true })
drawAgentHand(ctxStub, hand)
assert(true, 'drawAgentHand tolerates a canvas-less context (it only issues calls)')

console.log('replay engine contract passed')
