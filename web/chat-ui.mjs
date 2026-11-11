const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

export function escapeHtml (value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ESCAPES[character])
}

function renderInline (line) {
  const parts = String(line).split(/(`[^`\n]+`)/g)
  return parts.map(part => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return '<code>' + escapeHtml(part.slice(1, -1)) + '</code>'
    }
    return escapeHtml(part).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
  }).join('')
}

// The board agent ends a drawing reply with ::preview{file="..."}. That is a
// directive, not prose: escaped as text it reads like a leak. Turn it into the
// replay it names, served from the same token-guarded origin.
const PREVIEW = /::preview\{file="([^"]+)"\}/g

function previewEmbed (file, token) {
  const name = String(file || '').split(/[\\/]/).pop()
  if (!/^[A-Za-z0-9_.-]+\.html$/.test(name)) return ''
  const src = '/replay/' + encodeURIComponent(name) + (token ? '?t=' + encodeURIComponent(token) : '')
  return '<figure class="replay-embed">' +
    '<iframe src="' + src + '" loading="lazy" title="drawing replay"></iframe>' +
    '<figcaption><a href="' + src + '" target="_blank" rel="noopener">open the replay in its own tab</a></figcaption>' +
    '</figure>'
}

export function renderMessage (value, token) {
  const text = String(value ?? '')
  const parts = []
  let last = 0
  let match
  PREVIEW.lastIndex = 0
  while ((match = PREVIEW.exec(text))) {
    parts.push(renderMarkdownLite(text.slice(last, match.index)))
    parts.push(previewEmbed(match[1], token))
    last = match.index + match[0].length
  }
  parts.push(renderMarkdownLite(text.slice(last)))
  return parts.filter(Boolean).join('<br>')
}

export function renderMarkdownLite (value) {
  const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n')
  const rendered = []
  let fence = null
  let code = []

  for (const line of lines) {
    const marker = line.match(/^```([A-Za-z0-9_-]{0,24})\s*$/)
    if (!fence && marker) {
      fence = marker[1] || ''
      code = []
      continue
    }
    if (fence !== null && /^```\s*$/.test(line)) {
      const language = fence ? ' class="language-' + escapeHtml(fence.toLowerCase()) + '"' : ''
      rendered.push('<pre><code' + language + '>' + escapeHtml(code.join('\n')) + '</code></pre>')
      fence = null
      code = []
      continue
    }
    if (fence !== null) code.push(line)
    else rendered.push(renderInline(line))
  }

  if (fence !== null) {
    rendered.push(renderInline('```' + fence))
    rendered.push(...code.map(renderInline))
  }
  return rendered.join('<br>')
}

export function isNearBottom (node, threshold = 24) {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= threshold
}

export function formatChatTime (value) {
  const date = value ? new Date(value) : new Date()
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(date)
}
