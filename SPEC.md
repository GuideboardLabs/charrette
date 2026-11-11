# Charrette: design spec (v4)

A Hermes Agent plugin: a shared whiteboard where the board is the message and the transcript is its caption.
Target: Hermes Agent Plugin Hackathon, deadline **2026-11-11 17:00 EST**. Entry = data-only catalog PR + sub-90s X video.

**No time estimates.** Work is ordered by dependency and every stage has a gate that can fail. Speed is model-bound, not labour-bound; what limits us is verification and review.

## 0. Built and verified so far

| piece | evidence |
|---|---|
| Plugin package loads, three tools declared | `hermes plugins validate .` clean: manifest, capability probe, declared tools match, security scan, no core override, desktop surface inside the SDK |
| Board store: single writer, `if_rev` guard, activity pointer | round-trip test: stroke → rev 1, second at rev 1 → rev 2, stale write at rev 0 → `RevConflict`, `last_activity` = `obj_2` |
| HTTP surface on the same store | `/health`, `/boards`, `/board?since=` (returns `unchanged`), `/stroke` 200/200/409, `/img` base64 by object id |
| Image generation against an OpenAI-shaped pipeline | `board_image source=generate` → object `img_1`, real file on disk, bearer auth enforced, `source_url` and `seconds` recorded |
| img2img from the board's own drawing | `source=edit ref=newest` → the rendered crop (168x148 for a 120x100 sketch) sent as `ref_images` data URL, image returned and placed |
| Vision point projection | crop `[100,200,400,300]`, point `(500,250)/1000` → board `(300.0, 275.0)` exactly |
| Image search with no API key | query "cpu die shot" → Wikimedia Commons, "DEC Alpha 21066-AA CPU die shot", 525 KB, direct URL |
| Async image reserve | `board_image` returned in **0.011s** with `pending:true`; placeholder `status:"rendering"` → `ready` with the real file; a pipeline 500 leaves `status:"failed"` with the error, never a silent hole |

## 1. What this is

Draw with a marker on a true-white board. Paste images, label things, pull arrows out of your own labels. Ask a question. The agent answers **on the board**: it names what you drew, points at the parts you ask about, turns your sketch into a rendered image, builds a diagram beside it, adds notes with numbers, and brings in reference images from search or from your own pipeline. Text is two sentences, capped.

The catalog already has `hermes-board` (community, v1.9.3), `boardstate` and `sketch-pad`. The delta, which goes in the README and the PR body:

| `hermes-board` | Charrette |
|---|---|
| Agent-first: the model authors nodes, edges, captions, coordinates | Human-ink-first: your strokes and images are the primary objects |
| The model may place geometry | The model places nothing; deterministic layout plus a lint pass owns geometry |
| A board beside a chat | The board is the message; text capped at two sentences |
| Agent objects are its own | Marks resolve **onto your object ids**; a point arrives as a normalised coordinate inside a crop the model was shown and is projected by us |
| Agent may edit the document | The agent annotates your ink but never edits, moves, relabels or erases it |
| One bounded board, text or shapes | Unbounded canvas, N boards with a newest-activity pointer, and vision-grounded pointing |
| No image pipeline | Search, generate, **edit your own drawing**, URL or local path, all placed on the board |

Three claims a chat surface cannot make, in the order the video proves them:

1. **The referent is your mark.** "Point at its ears" on a cat you just drew. Measured: the configured vision model returned ear centres within 3 and 11 units of 1000 of ground truth, on a wobbly mouse sketch, with no examples.
2. **Correction, not regeneration.** Relayout keeps ids stable, so your eye tracks the same boxes.
3. **Provenance.** Whose hand is whose is visible per object; undo is scoped to one hand.

The honest counter: Mermaid needs no install and most diagrams here are Mermaid-expressible. That is why beat one is the cat.

## 2. Scope

Everything below is in scope. There is no cut list. §13 orders the work and a slipped gate loses polish, never a demo beat.

**Boards and space:** unbounded canvas (camera 0.05x to 4x, no page frame, content grows the world), N boards with a switcher, per-board newest-activity pointer, jump-to-newest, off-screen indicator when the agent draws outside your viewport.

**Editor:** select/move/resize, hand, pen with 6 colours and 3 widths, highlighter, eraser, shapes (rect, ellipse, diamond, line, arrow), text with four pull-out bound arrows, image paste and import.

**Agent:** diagram, annotation by id or by vision point, image search, image generation, **edit of the user's own drawing**, scoped undo, board digest grounding, vision.

**Surfaces:** desktop pane (primary, drawing), dashboard tab in a browser and on a phone over tailnet HTTPS (same renderer, touch input).

**Release:** `hermes plugins validate` clean, README with the delta and three disclosures, screenshots, catalog PR, sub-90s video.

**Not in this build** (intent, not schedule): collaboration and share links, KaTeX, Mermaid import, frames, grouping, rotation, partial-stroke erase, snapping, board templates, TUI, native mobile app, transcript directive, board history and diff.

## 3. Surfaces and transport

| Surface | Entry point | Transport |
|---|---|---|
| Desktop pane | `desktop/plugin.js`, `area:'panes'`, `placement:'right'`, `lifecycleKeepAlive:true` | `ctx.rest()`; `ctx.onEvent('*')` with a prefix filter |
| Browser / phone | **the plugin's own server**: `hermes charrette open`, `ctx.register_command('board')`, bound to 127.0.0.1 on a free port, per-process token in the URL | `fetch` with `X-Charrette-Token`; 900ms poll, no host auth involved |

**The web surface is not a dashboard tab.** A contest plugin has to be useful to anyone who installs it, so the board ships its own HTTP server (`web/server.py`) and its own page. No dependency on the admin dashboard, its auth gate, its tailnet binding or its plugin-tab registry, and nothing to enable twice. The only host seam it uses is `dashboard/plugin_api.py`, which is what gives the desktop pane's `ctx.rest` somewhere to land; that is a Hermes mechanism, not our deployment.

Constraints, each verified:

- Desktop plugin is **one file**, 16 MiB cap. Specifiers outside `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`, `react/jsx-dev-runtime` are refused before evaluation. Relative and bare imports fail at runtime **and pass `hermes plugins validate`**.
- `ctx.onEvent` takes an exact type or `'*'`; filter in the handler.
- `ctx.socket` is a no-op on OAuth remotes and browser-hosted viewers.
- Tool-to-pane push requires `plugins.isolation` not `host`; under `host` the dashboard router is buffered and WS/SSE die. Current install is `host`, so **poll is the primary transport** (`GET /board?since=<rev>` at 300ms) and push is an optimisation. This is a config note in the README, not a dependency.
- Renderer is framework-free ESM, no React, concatenated into both mounts by `scripts/bundle.py`.

Vendored inline, ~115 KB: rough.js 27.7 KB, perfect-freehand 4.5 KB, @dagrejs/dagre 49.3 KB, Excalifont Latin 25 KB. MIT or OFL, none uses `eval`, `new Function` or dynamic import.

## 4. Board state

**`board.json` is the sole authority.** `plugin_api.py` is the only writer, holding an in-process lock. Every write carries `if_rev` and returns 409 on mismatch. `oplog.jsonl` is advisory, fsynced after the json rename, rotated at 5 MB.

**Registry** `boards/index.json`: `{active, boards:[{id, name, rev, objects, created, last_activity:{at, actor, object_id, bbox}, thumb, camera}]}`.

- A board is created on demand and never auto-deleted.
- `last_activity` updates on every commit from either hand. It is **both** the UX pointer and the referent resolver: "what is this" with no lasso crops `last_activity.object_id` plus 40px.
- Switching restores that board's camera; jump-to-newest flies to `last_activity.bbox` in 900ms and pulses a ring for 600ms.
- Agent commits outside the viewport pin an indicator to the viewport edge with actor and a 3-word excerpt; it clears on arrival.

**Document**, per board:

```jsonc
{ "schema":"charrette/1", "rev":41,
  "camera":{"x":-120,"y":-80,"z":1.0},
  "layers":[{"id":"agent-diagram","z":10},{"id":"human-ink","z":20},{"id":"agent-annotation","z":30}],
  "objects":{
    "obj_12":{"kind":"stroke","actor":"human","layer":"human-ink","z":"a3",
              "bbox":[1120,300,96,40],"pts":[[0,0,.4],[4,2,.5]],"ink":"#111318","w":3},
    "t_9":{"kind":"text","actor":"human","z":"a6","bbox":[1200,410,140,28],"text":"magic link","size":22},
    "a_3":{"kind":"arrow","actor":"human","z":"a7","from":{"bind":"t_9","port":"e"},
           "to":{"bind":"obj_12","port":"auto"}},
    "img_7":{"kind":"image","actor":"agent","z":"a8","bbox":[900,260,420,236],
             "src":"a1b2.png","status":"ready","source_url":"https://…","label":"photo"},
    "d_31":{"kind":"scene","actor":"agent","layer":"agent-diagram","z":"a4","tx":9,
            "anchor":{"to":"img_7","side":"right","gap":64},"placement":"pending",
            "spec":{"type":"diagram","dir":"LR","nodes":[{"key":"a","label":"L2→L3"}],"edges":[]}},
    "an_5":{"kind":"annotation","actor":"agent","layer":"agent-annotation","z":"a9",
            "point":[1234.5,312.0],"style":"ring","label":"left ear"}
  },
  "tx":[{"id":"tx_9","actor":"agent","rev":7,"ops":["create:d_31"],"undo":["delete:d_31"]}] }
```

| prefix | object |
|---|---|
| `obj_` stroke · `img_` image · `t_` text · `a_` arrow · `s_` shape | human or agent primitives |
| `d_` agent scene · `n_`/`e_` children · `an_` annotation · `q_` question · `tx_` transaction | agent structures |

Units are CSS px at z=1, y down. Z is a fractional index inside a fixed layer band. **One binding primitive for both hands:** an endpoint is `{bind:id, port:auto|n|e|s|w}` or `{point:[x,y]}`. Human pull-out arrows and agent leaders use it identically and re-route when an end moves.

Event envelope: `plugin.charrette.delta {board, rev, actor, ops[]}`. A mount drops events whose `board` is not its own.

## 5. Pipelines

**Agent drawing:** `spec -> commit (intent) -> measure -> layout -> lint -> place -> animate`.

1. Commit assigns ids and returns in under 50ms with `placement=pending`.
2. Measure in the renderer with `canvas.measureText` after `document.fonts.load()`. A silent serif fallback still measures, so font readiness is asserted.
3. Layout: dagre for graphs, grid for cards, linear/log scale for charts. Ports from geometry.
4. Lint `{overlaps, overflow[], offViewport, labelCollisions, crossings}`; escalation preferred side → free space → clamp with `placement:"clamped"`.
5. Renderer POSTs `/placed {id, bbox, nodes, placement}`; the next digest and every later annotation address nodes by key. **Shipped and measured:** a 5-node scene reported `bbox=[629,196,738,168]` and per-node rects such as `mem=[1255,196,112,46]`, which is what makes "circle the bottleneck" land on the right box.
6. Animate: reservation frame on tool-call emission, nodes `min(60, 360/n)` ms, edges 40ms, labels 12ms/char capped 180ms, total 600ms. Corrections snap at 120ms.

**Vision:** renderer crops the target to PNG (≤1024px long edge) with a known `src` rect; `ctx.llm` sends it with a fixed instruction to return centres in **0-1000 normalised space of this image**; the backend projects `board = src.xy + (u,v)/1000 * src.wh`. The model never sees or emits board coordinates. Results cache per `object.rev`, `board_look` is capped at 2 per turn, and `E_NO_VISION` leaves the board working.

**Images:** three sources, all placing a real object on the board.

1. `search` → Wikimedia Commons API (no key, direct file URLs, `filetype:bitmap`), falls back to the next hit if a file fails. Synchronous.
2. `generate` → the local pipeline if configured, else Hermes's own `image_generate` via `ctx.dispatch_tool`. Asynchronous.
3. `edit` → the local pipeline's `/v1/images/edits` with the board's own rendered crop as `ref_images`, i.e. "turn my cat into a photo". Asynchronous.
4. `url` / `path` → fetched or read from disk, synchronous.

**Async is not optional.** A Qwen-Image edit on this card takes tens of seconds. `board_image` therefore reserves the rectangle, commits an `img_` object with `status:"rendering"`, returns in ~10ms, and a worker thread replaces the placeholder with `finish_image` when the pipeline answers. A pipeline error sets `status:"failed"` with the message. Measured: 0.011s to return, placeholder → ready, 500 → `failed` with `/v1/images/generations -> 500: {...}`. This is what lets the demo fire the photo and keep talking while it renders.

## 6. Tool surface

Ten verbs, one job each. No state-fetch; state is pushed.

| tool | does |
|---|---|
| `board_read` | the digest: boards, objects, ids, newest activity |
| `board_stroke` | freehand polyline, board px. Rarely used by the model; the human draws |
| `board_draw` | diagram, annotate, image, or a traced `shape` fill by `ref`, placement by `near`+`side`, no coordinates |
| `board_point` | vision points: `[{u,v,label}]` in 0-1000 of a crop, projected by us |
| `board_image` | `search` / `generate` / `edit` / `url` / `path`, with `ref` for edit |
| `board_edit` | undo (own tx only) / erase-own / relayout / restyle / move / label |
| `board_look` | objects, and pixels through vision, capped 2 per turn |
| `board_ask` | question card with ≤3 clickable options anchored to an object |
| `board_capture` | a PNG of a region (`refs` or `bbox`), rendered from the document with Pillow, no browser |
| `board_assets` | one folder with the board document, every image on it, a full capture and `manifest.json` |
| `board_scene` | records a scene and writes a self-contained replay page timed at human pen speed |

## Routes

| route | does |
|---|---|
| `GET /api/board`, `POST /api/stroke`, `POST /api/objects`, `POST /api/camera` | the shared document |
| `GET /api/boards`, `POST /api/boards/new`, `/rename`, `/active` | boards |
| `GET /api/export`, `GET /api/assets` | pictures and manifests of a region |
| `GET /api/session` | the board's chat backend, model and history |
| `POST /api/session/send` | one message, streamed as server-sent events; tools run as they arrive |
| `POST /api/session/reset` | clear this board's conversation |

Keys: `~/.hermes/charrette/keys.json`, 0600,
`{"keys": [{name, base_url, api_key, models, rpm, stream, priority}]}`. `priority` ascending, host
provider 40, extra keys 60, so promoting a key means priority 10. `stream: false` for an endpoint that
leaks tool calls as XML text while streaming.
Mixed with Hermes' own provider credentials. Rotation, a per-minute budget and a 45 s cooldown after
any failure. `scripts/keys.py` is the only writer.

Ink authorship: `board_stroke` writes `actor=agent`, layer `agent-diagram`; the human's `/api/stroke`
writes `actor=human`, layer `human-ink`. Agent ink sits under the human's by design.

The export pair exists because the board is a source, not just a surface: a region can be shown to the user, fed to an image model, or dropped into an artifact. Both have HTTP twins (`/api/export`, `/api/assets`) so a page can pull them without a tool call, and `scripts/make_artifact.py` is the reference consumer.

Return grammar, success and failure both instructive:

```
ok d_31 accepted rev=41 place=right-of img_7 placement=pending
ok an_5 ring at (1234.5,312.0) from pt(740,180)/1000 of crop [900,260,420,236]
ok img_7 reserved bbox=[1360,260,420,236] pending eta=40s — keep talking
err E_NO_OBJECT ref="auth box" — nearest: obj_12 "oauth?"(0.71), obj_14 "auth svc"(0.64)
err E_PIPELINE /v1/images/edits unreachable: connection refused
```

## 7. Image pipeline contract

Config resolves env → `<data>/config.json`:

```
CHARRETTE_IMAGE_ENDPOINT   http://127.0.0.1:8191     (the keyed Qwen gateway)
CHARRETTE_IMAGE_KEY        bearer token
CHARRETTE_IMAGE_MODEL      label only
CHARRETTE_IMAGE_STEPS      sampling steps for generate/edit; 2-4 is plenty for a sketch
```

**Speed.** The gateway honours a caller `steps` when its own `FORCE_REQUEST_STEPS` is unset, so the plugin forwards `steps` per call and falls back to `CHARRETTE_IMAGE_STEPS`. One rule to remember: the **first job of a fresh gateway process always runs `COLD_STEPS=6`** regardless of the request, so warm it with one throwaway generation before a take. Measured passthrough: `steps:2` on the wire for `source=edit`, `CHARRETTE_IMAGE_STEPS=3` when the call omits it, nothing on the wire when neither is set.

The gateway this is built against (`~/qwen21-api/gateway.py`) offers:

- `POST /v1/images/generations` `{prompt, size?, n?, seed?}` → `{data:[{b64_json}], size, model, generation_seconds, request_id}`, bearer auth, 429 on rate limit, 503 while warming.
- `POST /v1/images/edits` `{prompt, ref_images:["data:image/png;base64,…"], size?}` → same shape. Reference images are downscaled server-side; the budget is one reference, and the output grid is 1024x576 on this card.

The plugin sends the board's own raster: strokes rendered by PIL from the same geometry the renderer draws, so no browser round trip and no screenshot. `render.render_crop` returns `(png, src_rect, scale)` and the same `src_rect` is what a vision point is projected into.

If the pipeline is not running, `board_image` still works for `search`, `url` and `path`, `generate` falls back to `image_generate` if that tool is enabled, and `edit` returns `E_NO_PIPELINE` with the env var named. The README says all of this.

**Demo-day note:** the LLM runs on `ollama-cloud`, not on the local card, so Qwen-Image can hold the GPU while the agent talks. The local `:8101` model and the image server cannot be resident together; do not switch the demo to a local model.

## 8. Grounding

```
<boards active="b2" count="3"> b1 cat (14 obj, idle 6m) · b2 npu pipeline (57 obj, active) </boards>
<board id="b2" rev="41" viewport="[-200,-100 2400x1600]">
obj_12 stroke bbox=[1120,300 96x40] label="sso?"
t_9    text   bbox=[1200,410 140x28] "magic link"
img_7  image  bbox=[900,260 420x236] status=rendering label="photo"
c_1    cluster bbox=[1500,900 620x380] "sketch, 14 strokes" thumb=0.4
</board>
<selection kind="lasso" bbox=[820,280 420x90] contains=[obj_9,obj_10,obj_12]/>
```

- Text-first; a crop happens only when pixels are needed.
- More than 8 nearby strokes collapse to one `cluster` line. Only viewport ∪ selection ∪ referenced-this-session ∪ everything the agent drew.
- Deixis: "this" with a lasso = `sel`; otherwise `last_activity`.
- Ref resolution scored (id, label, bbox proximity, containment), 0.6 floor, three nearest candidates on failure, chips after two refusals.
- Budget ~1.8k tokens per turn against ~12k for a naive stroke dump.

## 9. Permissions

- The agent annotates human objects; never edits, moves, relabels or erases them. Enforced at ref resolution.
- The human may erase anything, including agent marks.
- Layers fixed: agent diagrams below human ink, annotations above.
- Agent undo is per-transaction; human undo may cross hands but names whose it was.

## 10. Visual spec: true white, gloss, markers

Surface is **pure white**. No paper, no beige, no grain, no noise.

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
| selection | `#0F766E` 1.8px + 10% fill | handles 7x7 white, teal border |
| emphasis | `#B42318` | the one red, agent only |
| muted | `#5C6B70` | secondary text |

Markers: `#111318`, `#D92D20`, `#1570EF`, `#12805C`, `#DC6803`, `#7A5AF8`. Highlighter is the same six at multiply, 40% alpha, 18px.

Type (Excalifont): 28/32 title, 22/28 label, 17/22 node, 13/16 edge. Chrome in ui-sans 11/14.
Strokes: ink by perfect-freehand, velocity-modulated; shapes and scenes rough.js at roughness 1.0 / 0.55. Radii 9/7/10/18, gaps 40, margin 48, padding 16x12. No object shadows or gradients.
Provenance: agent labels carry a 1px dotted underline and the `an_`/`d_`/`img_` prefix in the inspector. Two hands, visibly.

## 11. Editor tools

Left rail, 44px, dark, ink icons, active tool marked by a 2px teal bar.

- **Select (V)** click, shift-click, marquee, drag to move, 8 resize handles (shift locks aspect), double-click text to edit.
- **Hand (H)** or space-drag to pan; wheel zooms to cursor, 0.05-4.
- **Pen (P)** 6 colours, 3 widths, pointer pressure when present, velocity otherwise.
- **Highlighter** multiply, 18px, 40% alpha.
- **Eraser (E)** deletes whole objects.
- **Shapes (R)** rect, ellipse, diamond, line, arrow; shift constrains.
- **Text (T)** click to place, 3 sizes. On select, four grab dots at edge midpoints; dragging from one spawns an arrow bound to that text at that port, and releasing over an object binds the other end. Same primitive the agent uses.
- **Image** keyboard paste, drag from the file manager, or the rail.

Undo: 200 levels per hand, arrow bindings restored with their endpoints.

## 12. The demo is the acceptance script

Cold board, one take, no rehearsal state. Wording is Seth's; the beats are fixed.

| t | Seth | the agent |
|---|---|---|
| 0-3 | title card: "a board, not a chat" | |
| 3-12 | draws a cat with the pen: head, ears, eyes, whiskers, body, tail | |
| 12-16 | "what is this" | crops newest, vision: "A cat." one sentence naming parts |
| 16-21 | "point at its ears" | two rings from normalised points, labelled left and right |
| 21-26 | "make it a photo" | `board_image source=edit ref=newest prompt=photorealistic cat`, reserves space, **returns immediately**, keeps talking |
| 26-32 | "now go 0 to 100: diagram a CPU die, the cache hierarchy" | 8-node diagram laid out beside the cat, ids stable |
| 32-40 | "which part is the bottleneck, and why" | annotates two nodes, one red note with a number, two sentences |
| 40-46 | — | the photo lands in its reserved rectangle, labelled, unasked. Nobody waited for it. |
| 46-54 | "bring in a real die shot" | `board_image source=search query="cpu die shot"` → a real image placed, leader to the node it illustrates |
| 54-62 | "add the write-back path" | new nodes and edges, relayout, human ink untouched |
| 62-70 | pulls an arrow out of a new text label onto a node, asks "is that right?" | reads the text and the binding, confirms or corrects, points at the node |
| 70-78 | "jump to newest", switches to board 1 and back | camera lands on activity; the other board intact |
| 78-88 | pulls back to fit | whole map, two hands, transcript under four lines |
| 88-90 | title card | |

What must be true, and is measured or gated: vision under 5s; ear points within 3% of frame width; an 8-node spec without overlap at temperature 0; the edit returns in under 50ms and lands unattended; search returns a real image with a source URL; board switching preserves camera and content.

## 13. Build sequence and gates

Ordered by dependency. Each gate is end-to-end visible and can fail; a failing gate is fixed before the next opens.

**G0 transport and store.** DONE. `plugin.yaml`, `dashboard/manifest.json`, `plugin_api.py` with `/board`, `/board?since=`, `/stroke`, `/img`, `if_rev`; store with registry and activity pointer; three tools registered; pane rendering one stroke.
Gate: passed — validate clean, round-trip and 409 proven, HTTP surface proven, async image proven.

**G1 editor.** Camera, pan/zoom, pen, highlighter, eraser, shapes, text, select/move/resize, image paste, binding, undo.
Gate: draw the cat, pull an arrow from a label onto a stroke, move the target, the arrow follows; 60fps with 200 objects.

**G2 boards.** Tabs, switch, jump-to-newest, edge indicator, draft board adoption.
Gate: three boards, correct activity pointers, cameras restored, nothing lost on reload.

**G3 layout.** Measure after font load, dagre, grid, lint, escalation, `/placed`.
Gate: an 8-node spec places without overlap 20 of 20 at temperature 0.

**G4 vision.** Crop, normalise, project, rings, cache, `E_NO_VISION`.
Gate: 10 of 10 ear localisations across 5 cat scribbles; a repeated question is instant.

**G5 images.** Search, generate, edit, url/path, async placeholders, failure states, `/img` fetch in the pane.
Gate: the three sources each put a real image on the board; an edit of a fresh sketch returns in under 50ms and resolves unattended; a killed pipeline leaves a visible failed object, not a hole.

**G6 agent loop.** Digest, refs, annotate by id and point, edit, relayout, scoped undo, `board_look`.
Gate: lasso then arrow lands on the bbox edge 10 of 10; undo leaves every human `rev` unchanged.

**G7 second mount.** Dashboard tab, shared renderer, touch, poll fallback.
Gate: the same board renders in a browser and on the phone over tailscale HTTPS with no fork.

**G8 release.** Validate, README with delta and disclosures, screenshots, catalog PR, three takes of §12.
Gate: fresh profile, three switches flipped, every beat in §12 passes cold.

## 14. Risks

| risk | mitigation |
|---|---|
| Vision late or wrong on stage | cache per rev; measure on the real path first; if over 5s, crop tighter and ask for fewer parts |
| Ear points drift on a messy scribble | 5 scribbles x 10 runs; reject below 0.6 confidence and say so instead of guessing |
| An 8-node spec overlaps | caps, lint, escalation, 20-run temperature-0 sweep |
| Image pipeline not running on demo day | the LLM is cloud so the card is free; start `qwen21-image-api` + gateway before the take and warm it with one throwaway generation |
| Photo render slow or ugly | async placeholder means the demo never waits; at `steps=2-4` a sketch edit is a few seconds, and the agent keeps talking either way. Warm the gateway once first, or the first job pays the 6-step cold cost. If the render is bad, the beat is "here is what it made" and he asks for another |
| Editor feels worse than Excalidraw | small tool set; binding and text editing are fixed first because they are the differentiator |
| Dead air before the first mark | first sentence under 8 words before the tool call; reservation frame on emission; 600ms cap |
| Judge installs and sees nothing | three switches in the README, first-run card naming the missing one |
| Reviewer says `hermes-board` already does this | §1 table in README and PR body; the video opens on the cat |
| Validator passes imports the loader rejects | no imports beyond SDK and React; renderer inlined; boot test in a fresh profile |

## 15. Acceptance

Done when §12 passes cold in one take, and:

1. Ink, shapes, text, images at 60fps with 200 objects on a 1080p pane.
2. Agent commit under 50ms; a 5-node scene finishes drawing under 600ms.
3. A vision point lands within 3% of frame width of the intended part, 10 of 10.
4. A lasso-anchored arrow terminates on the target's bbox edge, 10 of 10.
5. Agent undo restores the board and leaves every human `rev` unchanged.
6. An image edit of a fresh sketch reserves in under 50ms, resolves unattended, and a failure is visible.
7. Three boards survive switch, reload and session resume with cameras and content.
8. The same board renders in the pane and a browser from one renderer.
9. Digest under 2k tokens for a 40-object board.
10. No `eval`, no `new Function`, no dynamic import; network calls only to the configured image endpoint, the Wikimedia Commons API, a user-supplied URL, and the vision route.
