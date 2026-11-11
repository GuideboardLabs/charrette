/**
 * Chalk desktop pane. Appended to renderer/chalk.mjs by scripts/bundle.py.
 * Symbols in scope from the engine: PALETTE, THEME, createBoard.
 * Imports in scope from the bundle header: host, useEffect, useRef, useState,
 * jsx, jsxs.
 */

const ID = 'chalk'
const BOARD = 'b1'

function Toolbar(props) {
  const { tool, ink, onTool, onInk, onFit, status } = props
  return jsxs('div', {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '6px 8px',
      borderBottom: '1px solid var(--ui-stroke-secondary)',
      flex: '0 0 auto'
    },
    children: [
      jsx('button', {
        type: 'button',
        onClick: () => onTool('pen'),
        title: 'pen',
        className: 'rounded px-1.5 py-0.5 text-[0.6875rem]',
        style: {
          color: tool === 'pen' ? 'var(--ui-text-primary)' : 'var(--ui-text-tertiary)',
          background: tool === 'pen' ? 'rgba(45,212,191,0.14)' : 'transparent'
        },
        children: 'pen'
      }),
      jsx('button', {
        type: 'button',
        onClick: () => onTool('hand'),
        title: 'hand',
        className: 'rounded px-1.5 py-0.5 text-[0.6875rem]',
        style: {
          color: tool === 'hand' ? 'var(--ui-text-primary)' : 'var(--ui-text-tertiary)',
          background: tool === 'hand' ? 'rgba(45,212,191,0.14)' : 'transparent'
        },
        children: 'hand'
      }),
      jsx('div', { style: { display: 'flex', gap: '5px', marginLeft: '4px' },
        children: PALETTE.map(p => jsx('button', {
          type: 'button',
          title: p.name,
          onClick: () => onInk(p.ink),
          style: {
            width: '13px',
            height: '13px',
            borderRadius: '50%',
            background: p.ink,
            border: ink === p.ink ? '1.5px solid var(--ui-accent)' : '1px solid rgba(255,255,255,0.22)',
            padding: 0
          }
        }, p.name)) }),
      jsx('button', {
        type: 'button',
        onClick: onFit,
        className: 'rounded px-1.5 py-0.5 text-[0.6875rem]',
        style: { marginLeft: 'auto', color: 'var(--ui-text-tertiary)' },
        children: 'fit'
      }),
      jsx('span', {
        className: 'text-[0.6875rem]',
        style: { color: 'var(--ui-text-quaternary)', fontVariantNumeric: 'tabular-nums' },
        children: status
      })
    ]
  })
}

function BoardPane(props) {
  const canvasRef = useRef(null)
  const boxRef = useRef(null)
  const boardRef = useRef(null)
  const [status, setStatus] = useState('')
  const [tool, setTool] = useState('pen')
  const [ink, setInk] = useState(PALETTE[0].ink)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    let alive = true

    const board = createBoard(canvas, {
      onStroke: async (points, meta) => {
        try {
          await props.rest('/stroke', {
            method: 'POST',
            body: { board: BOARD, points, ink: meta.ink, width: meta.width }
          })
          if (meta.tempId) board.dropTemp(meta.tempId)
          load()
        } catch (err) {
          setStatus('offline')
        }
      },
      onCamera: cam => {
        props.rest('/camera', { method: 'POST', body: { board: BOARD, camera: cam } }).catch(() => {})
      }
    })
    boardRef.current = board
    board.setInk(PALETTE[0].ink)

    const load = async () => {
      try {
        const doc = await props.rest('/board?board=' + encodeURIComponent(BOARD))
        if (!alive) return
        board.setDoc(doc)
        const n = Object.keys(doc.objects || {}).length
        setStatus('rev ' + doc.rev + ' · ' + n + (n === 1 ? ' object' : ' objects'))
        for (const [id, obj] of Object.entries(doc.objects || {})) {
          if (obj.kind !== 'image' || !obj.src) continue
          if (obj.status === 'rendering' || obj.status === 'failed') continue
          try {
            const blob = await props.rest('/img?id=' + encodeURIComponent(id) + '&board=' + BOARD)
            const img = new Image()
            img.onload = () => board.setImage(obj.src, img)
            img.src = 'data:' + blob.mime + ';base64,' + blob.b64
          } catch (err) { /* leave the placeholder */ }
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

  const setToolBoth = t => {
    setTool(t)
    if (boardRef.current) boardRef.current.setTool(t)
  }
  const setInkBoth = c => {
    setInk(c)
    if (boardRef.current) boardRef.current.setInk(c)
  }

  return jsxs('div', {
    className: 'flex h-full w-full flex-col',
    children: [
      jsx(Toolbar, {
        tool,
        ink,
        status,
        onTool: setToolBoth,
        onInk: setInkBoth,
        onFit: () => boardRef.current && boardRef.current.fit()
      }),
      jsx('div', {
        ref: boxRef,
        className: 'relative min-h-0 flex-1',
        style: { background: '#FFFFFF' },
        children: jsx('canvas', {
          ref: canvasRef,
          style: { display: 'block', width: '100%', height: '100%', touchAction: 'none' }
        })
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Chalk',
  register(ctx) {
    ctx.register({
      id: 'board',
      area: 'panes',
      title: 'chalk',
      data: { placement: 'right', width: '540px', minWidth: '320px', lifecycleKeepAlive: true },
      render: () => jsx(BoardPane, { rest: ctx.rest, onEvent: ctx.onEvent })
    })
  }
}
