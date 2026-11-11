import assert from 'node:assert/strict'
import {
  cameraForMiniPoint,
  directionToBounds,
  projectMinimap,
  viewportBounds
} from '../web/minimap.mjs'

const doc = {
  objects: {
    left: { bbox: [-200, -40, 80, 60] },
    right: { bbox: [400, 200, 120, 90] },
    stroke: { pts: [[10, 15], [40, 45]] }
  }
}
const camera = { x: 0, y: 0, z: 2 }
const viewport = { w: 600, h: 400 }

assert.deepEqual(viewportBounds(camera, viewport), [0, 0, 300, 200])

const first = projectMinimap(doc, camera, viewport, 140, 90)
const again = projectMinimap(doc, camera, viewport, 140, 90)
assert.deepEqual(first, again, 'projection must be deterministic')
assert.equal(first.objects.length, 3)
assert.ok(first.viewport.every(Number.isFinite))
assert.ok(first.viewport[2] > 0 && first.viewport[3] > 0)
assert.ok(first.viewport[0] >= 0 && first.viewport[0] + first.viewport[2] <= 140)
assert.ok(first.viewport[1] >= 0 && first.viewport[1] + first.viewport[3] <= 90)

const centered = cameraForMiniPoint(
  { x: first.inner[0] + first.inner[2] / 2, y: first.inner[1] + first.inner[3] / 2 },
  first,
  camera,
  viewport
)
assert.equal(centered.z, camera.z, 'minimap pan must preserve zoom')
assert.ok(Number.isFinite(centered.x) && Number.isFinite(centered.y))

assert.equal(directionToBounds(camera, viewport, [500, 50, 40, 40]).cardinal, 'right')
assert.equal(directionToBounds(camera, viewport, [-300, 50, 40, 40]).cardinal, 'left')
assert.equal(directionToBounds(camera, viewport, [50, -300, 40, 40]).cardinal, 'up')
assert.equal(directionToBounds(camera, viewport, [50, 500, 40, 40]).cardinal, 'down')

console.log('minimap contract passed')