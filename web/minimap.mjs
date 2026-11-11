const finite = value => Number.isFinite(Number(value)) ? Number(value) : 0

function normaliseBox (box) {
  if (!Array.isArray(box) || box.length < 4) return null
  const x = finite(box[0])
  const y = finite(box[1])
  const w = finite(box[2])
  const h = finite(box[3])
  return [Math.min(x, x + w), Math.min(y, y + h), Math.abs(w), Math.abs(h)]
}

function boxForObject (object) {
  const direct = normaliseBox(object?.bbox)
  if (direct && (direct[2] || direct[3])) return direct
  const points = Array.isArray(object?.pts) ? object.pts : []
  const valid = points.filter(point => Array.isArray(point) && point.length >= 2)
    .map(point => [finite(point[0]), finite(point[1])])
  if (!valid.length) return null
  const xs = valid.map(point => point[0])
  const ys = valid.map(point => point[1])
  const x0 = Math.min(...xs)
  const y0 = Math.min(...ys)
  return [x0, y0, Math.max(1, Math.max(...xs) - x0), Math.max(1, Math.max(...ys) - y0)]
}

function unionBoxes (boxes) {
  const x0 = Math.min(...boxes.map(box => box[0]))
  const y0 = Math.min(...boxes.map(box => box[1]))
  const x1 = Math.max(...boxes.map(box => box[0] + box[2]))
  const y1 = Math.max(...boxes.map(box => box[1] + box[3]))
  return [x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0)]
}

export function viewportBounds (camera, viewport) {
  const z = Math.max(0.01, finite(camera?.z) || 1)
  return [finite(camera?.x), finite(camera?.y),
    Math.max(1, finite(viewport?.w) / z), Math.max(1, finite(viewport?.h) / z)]
}

export function projectMinimap (doc, camera, viewport, width, height, pad = 6) {
  const boxes = Object.values(doc?.objects || {}).map(boxForObject).filter(Boolean)
  const view = viewportBounds(camera, viewport)
  const rawWorld = unionBoxes([...boxes, view])
  const margin = Math.max(18, Math.max(rawWorld[2], rawWorld[3]) * 0.06)
  const world = [rawWorld[0] - margin, rawWorld[1] - margin,
    rawWorld[2] + margin * 2, rawWorld[3] + margin * 2]
  const inner = [pad, pad, Math.max(1, width - pad * 2), Math.max(1, height - pad * 2)]
  const scale = Math.min(inner[2] / world[2], inner[3] / world[3])
  const plot = [
    inner[0] + (inner[2] - world[2] * scale) / 2,
    inner[1] + (inner[3] - world[3] * scale) / 2,
    world[2] * scale,
    world[3] * scale
  ]
  const project = box => [
    plot[0] + (box[0] - world[0]) * scale,
    plot[1] + (box[1] - world[1]) * scale,
    Math.max(1, box[2] * scale),
    Math.max(1, box[3] * scale)
  ]
  return { width, height, inner, plot, world, scale,
    objects: boxes.map(project), viewport: project(view) }
}

export function cameraForMiniPoint (point, projection, camera, viewport) {
  const z = Math.max(0.01, finite(camera?.z) || 1)
  const px = Math.max(projection.plot[0], Math.min(projection.plot[0] + projection.plot[2], finite(point?.x)))
  const py = Math.max(projection.plot[1], Math.min(projection.plot[1] + projection.plot[3], finite(point?.y)))
  const boardX = projection.world[0] + (px - projection.plot[0]) / projection.scale
  const boardY = projection.world[1] + (py - projection.plot[1]) / projection.scale
  return {
    x: boardX - finite(viewport?.w) / z / 2,
    y: boardY - finite(viewport?.h) / z / 2,
    z
  }
}

export function directionToBounds (camera, viewport, bbox) {
  const view = viewportBounds(camera, viewport)
  const box = normaliseBox(bbox) || [view[0], view[1], 1, 1]
  const dx = box[0] + box[2] / 2 - (view[0] + view[2] / 2)
  const dy = box[1] + box[3] / 2 - (view[1] + view[3] / 2)
  const cardinal = Math.abs(dx) >= Math.abs(dy) ? (dx < 0 ? 'left' : 'right') : (dy < 0 ? 'up' : 'down')
  return { cardinal, angle: Math.atan2(dy, dx) * 180 / Math.PI }
}
