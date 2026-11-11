/**
 * Charrette desktop pane. Appended to renderer/charrette.mjs by scripts/bundle.py.
 * Symbols in scope from the engine: PALETTE, THEME, createBoard.
 * Imports in scope from the bundle header: host, useEffect, useRef, useState,
 * jsx, jsxs.
 */

const ID = 'charrette'
const TOOLS = [
  ['select', 'select', 'V'],
  ['hand', 'hand', 'H'],
  ['pen', 'pen', 'P'],
  ['highlighter', 'highlighter', 'M'],
  ['eraser', 'eraser', 'E'],
  ['rect', 'rectangle', 'R'],
  ['ellipse', 'ellipse', 'O'],
  ['diamond', 'diamond', 'D'],
  ['triangle', 'triangle', 'G'],
  ['arrow', 'arrow', 'A'],
  ['table', 'table', 'B'],
  ['chart', 'bar chart', 'C'],
  ['text', 'text', 'T']
]

const ICONS = {
  select: [['path', { d: 'M4 2.75 13.2 9 9 10.1l-2.25 4.65z' }], ['path', { d: 'm9.3 10.2 3.25 3.25' }]],
  hand: [['path', { d: 'M5.25 8V5.5a1 1 0 0 1 2 0V8 4.25a1 1 0 0 1 2 0V8 5a1 1 0 0 1 2 0v3.25-1.5a1 1 0 0 1 2 0v3.5c0 3.1-1.8 5-4.75 5H8c-1.5 0-2.45-.8-3.1-1.9L3 10.1a1.05 1.05 0 0 1 1.75-1.15l1.5 1.8' }]],
  pen: [['path', { d: 'm11.2 3.1 3.7 3.7-8.4 8.4-3.9.8.8-3.9z' }], ['path', { d: 'm9.8 4.5 3.7 3.7M3.4 12.1l3.1 3.1' }]],
  highlighter: [['path', { d: 'm10.9 2.7 4.4 4.4-7.1 7.1-4.4-4.4z' }], ['path', { d: 'm3.8 9.8-1.3 3.7 2 2 3.7-1.3M9.4 4.2l4.4 4.4M2.5 15.5h5' }]],
  eraser: [['path', { d: 'm9.7 2.8 5.5 5.5-6.9 6.9H5.2L2.8 12.8z' }], ['path', { d: 'm6.1 9.5 4.4 4.4M8.3 15.2h7' }]],
  rect: [['rect', { x: '2.75', y: '4', width: '12.5', height: '10', rx: '1' }]],
  ellipse: [['ellipse', { cx: '9', cy: '9', rx: '6.25', ry: '5' }]],
  diamond: [['path', { d: 'M9 2.5 15.5 9 9 15.5 2.5 9z' }]],
  triangle: [['path', { d: 'M9 2.75 15.5 15H2.5z' }]],
  arrow: [['path', { d: 'M3 15 15 3M9 3h6v6' }]],
  table: [['rect', { x: '2.5', y: '3', width: '13', height: '12', rx: '1' }], ['path', { d: 'M2.5 7h13M7 3v12M11.5 3v12' }]],
  chart: [['path', { d: 'M2.5 15.5h13M4 13V9h2.5v4M8 13V4h2.5v9M12 13V6.5h2.5V13' }]],
  text: [['path', { d: 'M4 4h10M9 4v10M6.5 14h5' }]]
}

function toolIcon(name) {
  return jsx('svg', {
    viewBox: '0 0 18 18', width: '18', height: '18', fill: 'none', stroke: 'currentColor',
    strokeWidth: '1.75', strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
    children: ICONS[name].map(([tag, attrs], index) => jsx(tag, { ...attrs, key: index }))
  })
}

function Rail(props) {
  return jsx('div', {
    style: {
      display: 'flex', flexDirection: 'column', gap: '2px', padding: '6px 4px',
      borderRight: '1px solid var(--ui-stroke-secondary)', flex: '0 0 auto'
    },
    children: TOOLS.map(([tool, label, shortcut]) => jsx('button', {
      key: tool,
      type: 'button',
      title: label + ' (' + shortcut + ')',
      'aria-label': label + ' tool',
      onClick: () => props.onTool(tool),
      style: {
        width: '30px', height: '30px', padding: 0, borderRadius: '6px', cursor: 'pointer',
        border: '1px solid ' + (props.tool === tool ? 'var(--ui-stroke-secondary)' : 'transparent'),
        borderTopColor: ['rect', 'table', 'text'].includes(tool) ? 'var(--ui-stroke-secondary)' : undefined,
        marginTop: ['rect', 'table', 'text'].includes(tool) ? '5px' : 0,
        background: props.tool === tool ? 'rgba(45,212,191,0.16)' : 'transparent',
        color: props.tool === tool ? '#2dd4bf' : 'var(--ui-text-tertiary)',
        display: 'grid', placeItems: 'center'
      },
      children: toolIcon(tool)
    }))
  })
}

function Pane(props) {
  const canvasRef = useRef(null)
  const textRef = useRef(null)
  const boardRef = useRef(null)
  const editRef = useRef(null)
  const [tool, setTool] = useState('pen')
  const [ink, setInk] = useState(PALETTE[0].ink)
  const [status, setStatus] = useState('')
  const [edit, setEdit] = useState(null)

  const post = (path, body) => props.rest(path, { method: 'POST', body })

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    let alive = true

    const board = createBoard(canvas, {
      onCommit: (type, payload) => {
        if (type === 'upsert') post('/objects', { board: props.boardId, upsert: payload.objects }).catch(() => {})
        else if (type === 'delete') post('/objects', { board: props.boardId, delete: payload.ids }).catch(() => {})
        else if (type === 'camera') post('/camera', { board: props.boardId, camera: payload }).catch(() => {})
      },
      onPlaced: (id, bbox, nodes) => {
        post('/objects', { board: props.boardId, upsert: [{ id, bbox, nodes, placement: { at: [bbox[0], bbox[1]] } }] }).catch(() => {})
      },
      onEditText: (spec, done) => {
        editRef.current = done
        setEdit(spec)
      },
      onCamera: () => {}
    })
    boardRef.current = board
    board.setInk(PALETTE[0].ink)

    const load = async () => {
      try {
        const doc = await props.rest('/board?board=' + encodeURIComponent(props.boardId))
        if (!alive) return
        board.setDoc(doc)
        const n = Object.keys(doc.objects || {}).length
        setStatus('rev ' + doc.rev + ' · ' + n + (n === 1 ? ' object' : ' objects'))
        for (const [id, obj] of Object.entries(doc.objects || {})) {
          if (obj.kind !== 'image' || !obj.src) continue
          if (obj.status === 'rendering' || obj.status === 'failed') continue
          const img = new Image()
          img.onload = () => board.setImage(obj.src, img)
          props.rest('/img?id=' + encodeURIComponent(id) + '&board=' + props.boardId)
            .then(blob => { img.src = 'data:' + blob.mime + ';base64,' + blob.b64 })
            .catch(() => {})
        }
      } catch (err) {
        if (alive) setStatus('backend not mounted')
      }
    }

    load()
    const timer = window.setInterval(load, 900)
    const off = props.onEvent('*', e => {
      if (e && typeof e.type === 'string' && e.type.startsWith('plugin.' + ID + '.')) load()
    })

    return () => {
      alive = false
      window.clearInterval(timer)
      if (off) off()
      board.destroy()
    }
  }, [])

  const applyTool = t => {
    setTool(t)
    if (boardRef.current) boardRef.current.setTool(t)
  }

  const commitText = value => {
    const done = editRef.current
    editRef.current = null
    setEdit(null)
    if (done) done(value)
  }

  return jsxs('div', {
    className: 'flex h-full w-full flex-col',
    style: { background: 'var(--ui-editor-background, transparent)' },
    children: [
      jsxs('div', {
        style: {
          display: 'flex', alignItems: 'center', gap: '8px', padding: '5px 8px',
          borderBottom: '1px solid var(--ui-stroke-secondary)', flex: '0 0 auto'
        },
        children: [
          jsx('span', {
            className: 'text-[0.6875rem]',
            style: { letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ui-text-quaternary)' },
            children: 'charrette'
          }),
          jsx('div', {
            style: { display: 'flex', gap: '5px' },
            children: PALETTE.map(p => jsx('button', {
              key: p.name,
              type: 'button',
              title: p.name,
              onClick: () => { setInk(p.ink); boardRef.current && boardRef.current.setInk(p.ink) },
              style: {
                width: '12px', height: '12px', padding: 0, borderRadius: '50%',
                background: p.ink,
                border: ink === p.ink ? '1.5px solid var(--ui-accent)' : '1px solid rgba(255,255,255,0.22)'
              }
            }))
          }),
          jsx('button', {
            type: 'button',
            onClick: () => boardRef.current && boardRef.current.undo(),
            className: 'text-[0.6875rem]',
            style: { marginLeft: 'auto', color: 'var(--ui-text-tertiary)' },
            children: 'undo'
          }),
          jsx('button', {
            type: 'button',
            onClick: () => boardRef.current && boardRef.current.fit(),
            className: 'text-[0.6875rem]',
            style: { color: 'var(--ui-text-tertiary)' },
            children: 'fit'
          }),
          jsx('span', {
            className: 'text-[0.6875rem]',
            style: { color: 'var(--ui-text-quaternary)', fontVariantNumeric: 'tabular-nums' },
            children: status
          })
        ]
      }),
      jsxs('div', {
        className: 'flex min-h-0 flex-1',
        children: [
          jsx(Rail, { tool, onTool: applyTool }),
          jsx('div', {
            className: 'relative min-w-0 flex-1',
            style: { background: '#FFFFFF' },
            children: [
              jsx('canvas', {
                ref: canvasRef,
                style: { display: 'block', width: '100%', height: '100%', touchAction: 'none' }
              }),
              edit
                ? jsx('input', {
                    ref: textRef,
                    autoFocus: true,
                    defaultValue: edit.text || '',
                    spellCheck: false,
                    onKeyDown: e => {
                      if (e.key === 'Enter') { e.preventDefault(); commitText(e.currentTarget.value) }
                      if (e.key === 'Escape') { e.preventDefault(); commitText(null) }
                      e.stopPropagation()
                    },
                    onBlur: e => commitText(e.currentTarget.value),
                    style: {
                      position: 'absolute', left: '18%', top: '16%', width: '46%',
                      fontFamily: 'Charrette Hand, ui-rounded, sans-serif',
                      fontSize: '20px', color: '#111318',
                      background: 'rgba(255,255,255,0.97)',
                      border: '1px dashed rgba(15,118,110,0.7)',
                      borderRadius: '3px', padding: '2px 4px', outline: 'none'
                    }
                  })
                : null
            ]
          })
        ]
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Charrette',
  register(ctx) {
    // the hand font travels with the bundle: no serif fallback, no network
    if (typeof document !== 'undefined' && !document.getElementById('charrette-font')) {
      const style = document.createElement('style')
      style.id = 'charrette-font'
      style.textContent = "@font-face{font-family:'Charrette Hand';src:url(data:font/woff2;base64," +
        FONT_B64 + ") format('woff2');font-display:swap;}"
      document.head.appendChild(style)
      if (document.fonts && document.fonts.load) document.fonts.load('17px Charrette Hand')
    }
    ctx.register({
      id: 'board',
      area: 'panes',
      title: 'charrette',
      data: { placement: 'right', width: '560px', minWidth: '340px', lifecycleKeepAlive: true },
      render: () => jsx(Pane, { rest: ctx.rest, onEvent: ctx.onEvent, boardId: 'b1' })
    })
  }
}
