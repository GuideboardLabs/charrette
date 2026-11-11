import { escapeHtml, isNearBottom, renderMarkdownLite, renderMessage } from '../web/chat-ui.mjs'

function assert (condition, message) {
  if (!condition) throw new Error(message)
}

assert(escapeHtml('<img src=x onerror="boom"> &') === '&lt;img src=x onerror=&quot;boom&quot;&gt; &amp;',
  'escapeHtml must neutralise tags, attributes, quotes and ampersands')

const inline = renderMarkdownLite('**bold** and `x < y`')
assert(inline === '<strong>bold</strong> and <code>x &lt; y</code>',
  'markdown-lite must render bold and escaped inline code')

const lines = renderMarkdownLite('first\nsecond')
assert(lines === 'first<br>second', 'markdown-lite must preserve line breaks')

const fenced = renderMarkdownLite('before\n```js\n<script>alert(1)</script>\n```\nafter')
assert(fenced.includes('<pre><code class="language-js">&lt;script&gt;alert(1)&lt;/script&gt;</code></pre>'),
  'fenced code must be escaped and retain a safe language class')
assert(fenced.startsWith('before<br>'), 'content before a fence must remain in order')
assert(fenced.endsWith('<br>after'), 'content after a fence must remain in order')

const hostile = renderMarkdownLite('<img src=x onerror=alert(1)>\n**safe**')
assert(!hostile.includes('<img'), 'model HTML must never become an element')
assert(hostile.includes('&lt;img src=x onerror=alert(1)&gt;'), 'hostile HTML must remain readable as text')
assert(hostile.includes('<strong>safe</strong>'), 'supported markdown must survive beside escaped HTML')

const unmatched = renderMarkdownLite('```js\nconst x = "<tag>"')
assert(!unmatched.includes('<script'), 'an unmatched fence must stay inert')
assert(unmatched.includes('&lt;tag&gt;'), 'an unmatched fence must escape its body')

assert(isNearBottom({ scrollHeight: 1000, scrollTop: 700, clientHeight: 280 }) === true,
  'reader within threshold must follow new output')
assert(isNearBottom({ scrollHeight: 1000, scrollTop: 500, clientHeight: 280 }) === false,
  'reader away from bottom must keep their reading position')


// a replay named in a reply must play, not leak as a directive
const withReplay = renderMessage('drew it for you\n::preview{file="/tmp/replays/b1-house.html"}', 'tok123')
assert(withReplay.includes('<iframe src="/replay/b1-house.html?t=tok123"'),
  'a preview directive must become an iframe carrying the page token')
assert(!withReplay.includes('::preview'), 'the directive itself must not survive into the text')
assert(withReplay.startsWith('drew it for you'), 'prose before the directive must stay in order')
assert(withReplay.includes('open the replay in its own tab'), 'the embed must offer a plain link as a fallback')

const noToken = renderMessage('::preview{file="/tmp/r.html"}', '')
assert(noToken.includes('src="/replay/r.html"'), 'without a token the embed must still point at the file')

const unsafe = renderMessage('::preview{file="/tmp/../../etc/passwd"}', 'tok')
assert(!unsafe.includes('iframe'), 'a path that does not end in a plain html name must not become an iframe')

const unsafeJs = renderMessage('::preview{file="x.html\"><script>alert(1)</script>"}', 'tok')
assert(!unsafeJs.includes('<script'), 'a hostile file attribute must never reach the DOM as markup')

console.log('chat ui contract passed')
