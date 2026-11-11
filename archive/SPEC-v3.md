# Chalk: design spec (v3)

A Hermes Agent plugin: a shared whiteboard where the board is the message and the transcript is its caption.
Target: Hermes Agent Plugin Hackathon, deadline **2026-11-11 17:00 EST**. Entry = data-only catalog PR + sub-90s X video.

**This document contains no time estimates.** Work is ordered by dependency, and every stage has a gate that can fail. Speed is model-bound, not labour-bound; what limits us is verification and review, not calendars.

## 0. Changelog

**v2**, from adversarial review, verified against source at v0.22.5+3133:

1. `ctx.onEvent` has no wildcards. Exact key or `'*'` (`apps/desktop/src/contrib/events.ts::onGatewayEvent`).
2. `ctx.rest` has no server unless the package ships `dashboard/manifest.json` with `api: plugin_api.py`. Backend is the first artifact, not the last.
3. Layout cannot run in Python. `measureText` and dagre are renderer-side, so the tool commits intent and returns `placement=pending`.
4. No vision model was being served locally on `:8101` (it is `hauhau-35b-a3b`, text-only, 32k). Vision routes through `auxiliary.vision`.
5. A click-to-answer card needs `plugins.entries.chalk.allow_gateway_injection: true`, a third switch.

**v3**, from Seth:

6. Multiple boards, unbounded growth, and a tracked pointer to the newest activity on every board.
7. True white surface with a gloss sheen. No texture, no beige, no paper.
8. A real tool palette: markers, highlighter, shapes, text with pull-out bound arrows, images.
9. The demo is now a fixed script (cat, name it, ears, then a computing diagram with pointing and new images). It is the acceptance test, not an illustration.

**Vision is proven, not assumed.** A crude mouse-drawn cat was sent through the configured vision model with a request for ear centres in 0-1000 normalised space. Answer: left `(373,214)`, right `(617,211)`. Ground truth from the drawing's own triangle centroids: `(372,217)` and `(628,217)`. Error 3 and 11 units on a 1000-unit frame, on a wobbly sketch, with no examples given. Pointing works. Artifact: `cat-test.png`.

## 1. What this is

Draw with a marker on a true-white board. Paste images, label things, pull arrows out of your own labels. Ask a question. The agent answers **on the board**: it points at your drawing, marks the part you asked about, builds a diagram beside it, adds notes with numbers, and brings in reference images. Text is two sentences, capped.

The catalog already contains `hermes-board` (community, v1.9.3) plus `boardstate` and `sketch-pad`. The delta is one paragraph and it goes in the README:

| `hermes-board` | Chalk |
|---|---|
| Agent-first: the model authors nodes, edges, captions, coordinates | Human-ink-first: your strokes and images are the primary objects |
| The model may place geometry | The model never places anything on the board; deterministic layout plus lint owns geometry |
| A board beside a chat | The board is the message; text capped at two sentences |
| Agent objects are its own | Agent marks resolve **onto your object ids**; a point comes back as a normalised coordinate inside a crop the model was shown and is projected by us |
| Agent may edit the document | The agent may annotate your ink but never edit, move, relabel or erase it |
| One bounded board | Unbounded canvas plus N boards, with a tracked newest-activity pointer |

Three claims a chat surface cannot make, and the order the video proves them:

1. **The referent is your own mark.** "Point at its ears" on a cat you just drew. No transcript can carry that.
2. **Correction, not regeneration.** Relayout keeps ids stable, so your eye tracks the same boxes.
3. **Provenance.** Whose hand is whose is visible per object, and undo is scoped to one hand.

The honest counter: Mermaid needs no install and every diagram here is Mermaid-expressible. That is why beat one is the cat, not a flowchart.

## 2. Scope

Everything below is in scope. There is no time-based cut list; sequencing in §13 decides what exists when, and a slipped gate loses polish, never a demo beat.

**Boards and space:** unbounded canvas (camera 0.05x to 4x, no page frame, content grows the world), N boards with a switcher, per-board newest-activity pointer, jump-to-newest, off-screen indicator when the agent draws outside your viewport.

**Editor:** select/move/resize, hand, pen with 6 colours and 3 widths, highlighter, eraser, shapes (rect, ellipse, diamond, line, arrow), text with four pull-out bound arrows, image paste and import.

**Agent:** four tools, declarative drawing, deterministic layout, lint before commit, vision pointing, image placement, scoped undo, board digest grounding.

**Surfaces:** desktop pane (primary, drawing), dashboard tab in a browser and on a phone over tailnet HTTPS (same renderer, touch input).

**Release:** `hermes plugins validate` clean, README with the delta and three disclosures, screenshots, catalog PR, sub-90s video.

**Not in this build** (intent, not schedule): collaboration or share links, KaTeX, Mermaid import, frames, grouping, rotation, partial-stroke erase, snapping, board templates, TUI, native mobile app, transcript directive, board history and diff.

## 3. Surfaces and transport

| Surface | Mount | Transport |
|---|---|---|
| Desktop pane | `desktop/plugin.js`, `area:'panes'`, `placement:'right'`, `dock:{pane:'workspace',pos:'right'}`, `lifecycleKeepAlive:true` | `ctx.rest()`; `ctx.onEvent('*')` with a prefix filter |
| Browser / phone | `dashboard/manifest.json`, `dashboard/dist/index.js` (classic IIFE, no modules) | `SDK.fetchJSON`; WS or 300ms poll |

Constraints, each already verified:

- Desktop plugin is **one file**, 16 MiB cap. Specifiers outside `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`, `react/jsx-dev-runtime` are refused before evaluation. Relative and bare imports fail at runtime **and pass `hermes plugins validate`**.
- `ctx.onEvent` takes an exact type or `'*'`. Filter in the handler.
- `ctx.socket` is a no-op on OAuth remotes and browser-hosted viewers. Never the only path.
- Tool-to-pane push requires `plugins.isolation` not `host`; under `host` the dashboard router is forwarded and buffered, killing WS/SSE. Both mounts poll `GET /board?since=<rev>` at 300ms when no event arrives within 400ms.
- Renderer is framework-free ESM, no React. `renderer/chalk.mjs` is concatenated into `desktop/plugin.js` and `dashboard/dist/index.js` by `scripts/bundle.py`. Shipped files are static.

Vendored inline, ~115 KB: rough.js 27.7 KB, perfect-freehand 4.5 KB, @dagrejs/dagre 49.3 KB, Excalifont Latin 25 KB. MIT or OFL, none uses `eval`, `new Function` or dynamic import.

## 4. Board state

**`board.json` is the sole authority.** `plugin_api.py` is the only writer, holding an in-process lock. Every write carries `if_rev` and returns 409 on mismatch. `oplog.jsonl` is advisory (audit, undo), fsynced after the json rename, rotated at 5 MB.

**Registry** `boards/index.json`:

```jsonc
{ "active":"b2", "boards":[
  { "id":"b1","name":"cat","rev":88,"objects":14,"created":"…",
    "last_activity":{"at":"…","actor":"human","object_id":"obj_88","bbox":[1120,300,96,40]},
    "thumb":"b1.png","camera":{"x":0,"y":0,"z":1} },
  { "id":"b2","name":"npu pipeline","rev":31,"objects":57,"…":"…" } ] }
```

- New board gets `b<n>`, an empty object map and camera at origin; a board is created on demand and never auto-deleted.
- `last_activity` updates on every commit from either hand. It is **both** the UX pointer and the referent resolver: "what is this" with no lasso crops to `last_activity.object_id` plus 40px.
- Switching boards restores that board's camera when it has activity, else its stored camera.
- Jump-to-newest flies the camera to `last_activity.bbox` with a 900ms ease and pulses a ring for 600ms.
- When the agent commits outside the visible viewport, an indicator pins to the viewport edge in its direction, showing actor and a 3-word excerpt. It clears on arrival.

**Board document**, per board:

```jsonc
{ "schema":"chalk/1", "rev":41,
  "camera":{"x":-120,"y":-80,"z":1.0},
  "layers":[{"id":"agent-diagram","z":10},{"id":"human-ink","z":20},{"id":"agent-annotation","z":30}],
  "objects":{
    "obj_12":{"kind":"stroke","actor":"human","layer":"human-ink","z":"a3",
              "bbox":[1120,300,96,40],"pts":[[0,0,.4],[4,2,.5]],"ink":"#111318","w":3,"label":"sso?"},
    "img_7":{"kind":"image","actor":"human","layer":"human-ink","z":"a2",
             "bbox":[900,260,300,180],"src":"a1b2.png","label":"pricing.png"},
    "t_9":{"kind":"text","actor":"human","layer":"human-ink","z":"a6","bbox":[…],"text":"magic link","size":22},
    "a_3":{"kind":"arrow","actor":"human","layer":"human-ink","z":"a7",
           "from":{"bind":"t_9","port":"e"},"to":{"bind":"obj_12","port":"auto"}},
    "d_31":{"kind":"scene","actor":"agent","layer":"agent-diagram","z":"a4","tx":9,
            "anchor":{"to":"img_7","side":"right","gap":64},"placement":"pending",
            "spec":{"type":"diagram","dir":"LR","nodes":[{"key":"a","label":"DMA"}],"edges":[]}},
    "an_5":{"kind":"annotate","actor":"agent","layer":"agent-annotation","z":"a5",
            "target":"obj_12","style":"arrow","label":"this kills it"}
  },
  "tx":[{"id":"tx_9","actor":"agent","rev":7,"ops":["create:d_31"],"undo":["delete:d_31"]}] }
```

| prefix | object |
|---|---|
| `obj_` | human stroke |
| `img_` | image, human or agent |
| `t_` | text |
| `a_` | arrow (bound or free) |
| `s_` | shape (rect, ellipse, diamond, line) |
| `d_` | agent scene |
| `n_` / `e_` | child node / edge of a scene |
| `an_` | agent annotation |
| `q_` | question card |
| `tx_` | transaction |

Units are CSS px at z=1, y down. Z is a fractional index string inside a fixed layer band. **One binding primitive for both hands:** an endpoint is `{bind: id, port: auto|n|e|s|w}` or `{point:[x,y]}`. Human pull-out arrows and agent leaders use it identically, and bound arrows re-route when either end moves.

Event envelope: `plugin.chalk.delta {board, rev, actor, ops[]}`. Every mount drops events whose `board` is not its own.

## 5. Drawing and vision pipeline

**Agent drawing:** `spec -> commit (intent) -> measure -> layout -> lint -> place -> animate`.

1. Commit assigns ids and returns in under 50ms with `placement=pending`.
2. Measure with `canvas.measureText` after `document.fonts.load()` resolves. A silent serif fallback still measures, so font readiness is asserted, not hoped for.
3. Layout: dagre for graphs, grid for cards, linear/log scale for charts. Ports from geometry, never from the model.
4. Lint: `{overlaps, overflow[], offViewport, labelCollisions, crossings}`. Escalation: preferred side, then free-space scan, then clamp with `placement:"clamped"`.
5. Renderer POSTs `/placed {id, bbox, placement}`; the backend folds it in so the next digest carries truth.
6. Animate: reservation frame on tool-call emission, nodes at `min(60, 360/n)` ms, edges 40ms, labels 12ms/char capped at 180ms, total 600ms. Corrections snap at 120ms.

**Vision and pointing:**

1. The renderer crops the target region to PNG at device pixel ratio, capped at 1024px on the long edge, with a known `src` rect in board coordinates.
2. `ctx.llm` sends the crop with a fixed instruction: identify, and for each requested part return a centre in **0-1000 normalised space of this image**.
3. The backend projects: `board = src.xy + (u,v)/1000 * src.wh`. The model never sees or emits board coordinates.
4. The mark is drawn as `an_` with `point`, so the ring or arrow is anchored, undoable and inspectable like any other annotation.
5. Cache per `object.rev` so a repeated question is instant, and cap `board_look` at 2 per turn.
6. If no vision model answers, the tool returns `E_NO_VISION` and the board keeps working; the agent says what it can from labels only.

## 6. Tools

Four tools, no state-fetch: state is pushed.

**`board_draw`** — `kind: diagram|annotate|image`, `place:{near,side,gap,region?}`, `spec`, `title?`, `replaces?`.
- diagram: `nodes[{key,label,shape,tone}]`, `edges[{from,to,label,style}]`. Caps 9 nodes, 28-char labels.
- annotate: `targets[{ref|point,style:arrow|circle|ring|bracket|dim|spotlight,label}]`, `note?{text,at}`.
- image: `src` (https URL or a local path the user can read), `place`, `label?`. Fetched once, cached under the plugin data dir, disclosed in the README.

**`board_edit`** — `op: undo|erase|relayout|restyle|move|label`, `target`, `direction?`, `tone?`, `text?`. Undo pops the model's own last `tx`. No clear-all, no op that reaches human ink.

**`board_ask`** — `question(90)`, `options[<=3]{id,label(32),hint?}`, `anchor{ref,side}`. Commits a card, ends the turn, the click returns as the next user message via `ctx.inject_message`.

**`board_look`** — `region{ref|bbox|'newest',pad}`, `want: objects|pixels|both`, `ask?`. `newest` crops `last_activity`. Returns object lines plus, for pixels, the vision paragraph and any normalised points the model offered.

Return grammar, success and failure both instructive:

```
ok d_31 accepted rev=41 place=right-of img_7 placement=pending
ok an_5 arrow→obj_12 edge=(1216,320) from vision pt(740,180)/1000
err E_NO_OBJECT ref="auth box" — nearest: obj_12 "oauth?"(0.71), obj_14 "auth svc"(0.64)
```

## 7. Grounding

```
<boards active="b2" count="3"> b1 cat (14 obj, idle 6m) · b2 npu pipeline (57 obj, active) </boards>
<board id="b2" rev="41" viewport="[-200,-100 2400x1600]">
obj_12 stroke bbox=[1120,300 96x40] label="sso?"
t_9    text   bbox=[1200,410 140x28] "magic link"
img_7  image  bbox=[900,260 300x180] label="pricing.png"
c_1    cluster bbox=[1500,900 620x380] "sketch, 14 strokes" thumb=0.4
</board>
<selection kind="lasso" bbox=[820,280 420x90] contains=[obj_9,obj_10,obj_12]/>
```

- Text-first. A crop happens only when the model asks for pixels or the question needs identification.
- More than 8 nearby strokes collapse to one `cluster` line. Only viewport ∪ selection ∪ referenced-this-session ∪ everything the agent drew.
- Deixis: if the human says "this" and a lasso exists, inject `"this" = sel`; otherwise bind to `last_activity`.
- Ref resolution scored (id, label, bbox proximity, containment), 0.6 floor, three nearest candidates on failure, chips after two refusals.
- Budget ~1.8k tokens per turn, against ~12k for a naive stroke dump.

## 8. Permissions

- The agent annotates human objects; it never edits, moves, relabels or erases them. Enforced at ref resolution.
- The human may erase anything on the board, including agent marks.
- Layers fixed: agent diagrams below human ink, annotations above.
- Agent undo is per-transaction; human undo may cross hands but names whose it was.

## 9. Visual spec: true white, gloss, markers

Surface is **pure white**. No paper, no beige, no grain, no noise layer.

| token | hex | use |
|---|---|---|
| surface | `#FFFFFF` | the board, exactly |
| frame / edge | `#0B0F11` / `#1A2326` | app chrome, dark |
| cards | `#FFFFFF` fill, `#D6DEE6` hairline | agent nodes |
| dot grid | `#E4E9EE` at 55%, 24px pitch | dots, never lines |
| gloss | `linear-gradient(180deg, rgba(255,255,255,0) 55%, rgba(11,15,17,0.035))` + `inset 0 1px 0 rgba(255,255,255,0.9)` on cards | sheen only |
| human ink | `#111318` | 2/3/5px |
| agent ink | `#0F766E` | strokes, rings, leaders |
| agent tint | `#E6F4F1` | fills |
| selection | `#0F766E` 1.8px + 10% fill | handles 7x7 white with teal border |
| annotation | `#B42318` | the one red, agent emphasis only |
| muted | `#5C6B70` | secondary text |

Markers: `#111318` black, `#D92D20` red, `#1570EF` blue, `#12805C` green, `#DC6803` orange, `#7A5AF8` purple. Highlighter is the same six at multiply, 40% alpha, 18px nib. Nothing off-white, nothing textured.

Type (Excalifont): 28/32 title, 22/28 label, 17/22 node, 13/16 edge. UI chrome in ui-sans 11/14.
Strokes: ink by perfect-freehand, velocity-modulated width; shapes rough.js roughness 1.0, bowing 0.8; agent edges roughness 0.55 at 1.5px. Radii 9/7/10/18. Gaps 40, margin 48, padding 16x12. No shadows, no gradients on objects.
Provenance: agent labels carry a 1px dotted underline and the `an_`/`d_` prefix in the inspector. Two hands, visibly.

## 10. Editor tools

Left rail, 44px, dark, ink icons, active tool marked by a 2px teal bar and a tooltip.

- **Select (V)** click, shift-click, marquee, drag to move, 8 handles to resize (shift locks aspect), double-click text to edit.
- **Hand (H)** or space-drag to pan. Wheel zooms to cursor, 0.05-4.
- **Pen (P)** 6 colours, 3 widths, pressure from the pointer when present, velocity otherwise.
- **Highlighter** multiply, 18px, 40% alpha.
- **Eraser (E)** deletes whole objects on contact, no partial stroke erase.
- **Shapes (R)** rect, ellipse, diamond, line, arrow. Shift constrains to square, circle, 45 degrees.
- **Text (T)** click to place, 3 sizes. On select, four grab dots appear at edge midpoints; dragging from one spawns an arrow bound to that text at that port, and releasing over an object binds the other end to it. This is the same binding primitive the agent uses, which is why it is nearly free to add.
- **Image** paste with the keyboard, drag from the file manager, or the rail.

Undo: 200 levels, per hand, arrow bindings restored with their endpoints.

## 11. Distribution

`~/.hermes/plugins/chalk/`: `plugin.yaml`, `__init__.py`, `tools.py`, `board/`, `dashboard/manifest.json`, `dashboard/plugin_api.py`, `dashboard/dist/index.js`, `desktop/plugin.js`, `renderer/chalk.mjs`, `scripts/bundle.py`, `README.md`, `LICENSE`.

Three switches, checked by the first-run card, each named if missing:

1. `plugins.enabled` must include `chalk`, or the agent half is inert and `ctx.rest` 404s.
2. Capabilities ▸ Plugins must show Desktop enabled; Electron force-sets the package half `defaultEnabled: false`.
3. `plugins.entries.chalk.allow_gateway_injection: true`, or `board_ask` chips do nothing.

Skills: call `ctx.register_skill("board-grammar", path)`; a native plugin's `skills/` dir is not auto-scanned.
Catalog: entry PR touches only `plugin-catalog/**` and `contributors/emails/**`; full 40-hex sha; quoted version; screenshots filled; capabilities matching what is registered.
Disclosures: board files live in the plugin data dir; when vision is used, a crop of the selection is sent to the configured vision model; `board_draw kind:image` fetches the URL the user supplies, nothing else, and stores no credentials.

## 12. The demo is the acceptance script

Cold board, no rehearsal state, one take. Beats are fixed; wording is not.

| t | Seth | the agent |
|---|---|---|
| 0-3 | title card: "a board, not a chat" | |
| 3-14 | draws a cat with the pen: head, two ears, eyes, whiskers, body, tail | |
| 14-18 | "what is this" | crops `newest`, vision: "A cat." one sentence naming the parts |
| 18-24 | "point at its ears" | two normalised points projected, two teal rings with "left ear" / "right ear" |
| 24-30 | "now go 0 to 100. diagram a <computing part>" | 6-8 node diagram laid out beside the cat, arrows bound, no overlap |
| 30-40 | "which part is the bottleneck, and why" | annotates two nodes, one red note with a number, two sentences |
| 40-50 | "add the write-back path" | new nodes and edges, relayout, ids stable, human ink untouched |
| 50-58 | "bring in a reference image" | places an image, labels it, one leader from the image to a node |
| 58-68 | pulls an arrow out of a new text label onto a node, asks "is that right?" | reads the text and the binding, confirms or corrects, points at the node |
| 68-78 | "jump to newest" and switches to board 1 then back | camera lands on activity, the other board's state intact |
| 78-88 | pulls back to fit | whole map, two hands visible, transcript under four lines |
| 88-90 | title card | |

What must be true for this to be honest: vision answers in under 5s; ear points land on the ears in 10 of 10 scribbles; an 8-node spec lands without overlap at temperature 0; image placement works with the network on; board switching preserves camera and content.

## 13. Build sequence and gates

Ordered by dependency. Each gate is end-to-end visible and can fail; a failing gate is fixed before the next opens. No gate is allowed to be "mostly done".

**G0 transport.** `plugin.yaml`, `dashboard/manifest.json`, `plugin_api.py` with `GET/POST /board` and `if_rev`, one tool that writes one stroke, a pane that renders it.
Gate: `hermes plugins validate` clean; a tool call makes a stroke appear in the pane; 409 on a stale write.

**G1 editor.** Camera, pan/zoom, pen, highlighter, eraser, shapes, text, select/move/resize, image paste, binding.
Gate: draw the cat, pull an arrow from a label onto a stroke, move the target, the arrow follows.

**G2 boards.** Registry, tabs, switch, jump-to-newest, edge indicator, activity pointer, draft board adoption.
Gate: three boards, correct activity pointers, camera restored on switch, nothing lost on reload.

**G3 layout.** Measure after font load, dagre, grid, lint, escalation, `/placed` round trip.
Gate: an 8-node spec places without overlap 20 of 20 at temperature 0.

**G4 vision.** Crop, normalise, project, rings, cache, `E_NO_VISION` path.
Gate: 10 of 10 correct ear localisations across 5 different cat scribbles; a repeated question is instant.

**G5 agent loop.** Digest, refs, annotate by id and by point, edit, relayout, scoped undo, `board_look`.
Gate: lasso then arrow lands on the bbox edge 10 of 10; undo leaves every human `rev` unchanged.

**G6 second mount.** Dashboard tab, shared renderer, touch input, poll fallback.
Gate: the same board renders in a browser and on the phone over tailscale HTTPS with no fork in the drawing code.

**G7 release.** Validate, README with delta and disclosures, screenshots, catalog PR, three takes of §12.
Gate: fresh profile, three switches flipped, every beat in §12 passes cold.

## 14. Risks

| risk | mitigation |
|---|---|
| Vision answers late or badly on stage | cache per rev; measure latency on the actual path before shooting; if it exceeds 5s, crop tighter and ask for fewer parts |
| Ear points drift on a messy scribble | test 5 scribbles, 10 runs; reject below 0.6 confidence and let the agent say so rather than guess |
| An 8-node spec overlaps | hard caps, lint, escalation, and a 20-run temperature-0 sweep before the shoot |
| Editor feels worse than Excalidraw | the tool set is deliberately small; if binding or text editing is the weak point, fix that first, it is the differentiator |
| Dead air before the first mark | first sentence under 8 words before the tool call; reservation frame on emission; 600ms cap |
| Judge installs and sees nothing | three switches in the README, first-run card naming the missing one |
| Reviewer says `hermes-board` already does this | §1 table in README and PR body; the video opens on the cat, not a diagram |
| Validator passes imports the loader rejects | no imports beyond the SDK and React; renderer inlined; boot test in a fresh profile |

## 15. Acceptance

The build is done when §12 passes cold in one take, and:

1. Ink, shapes, text and images hold 60fps with 200 objects on a 1080p pane.
2. Agent commit returns under 50ms; a 5-node scene finishes drawing under 600ms.
3. A point from vision lands within 3% of frame width of the intended part, 10 of 10.
4. A lasso-anchored arrow terminates on the target's bbox edge, 10 of 10.
5. Agent undo restores the board and leaves every human object's `rev` unchanged.
6. Three boards survive switch, reload and session resume with cameras and content intact.
7. The same board renders in the desktop pane and a browser from one renderer.
8. Digest under 2k tokens for a 40-object board.
9. No `eval`, no `new Function`, no dynamic import, no network call except the vision route and an image URL the user supplied.
