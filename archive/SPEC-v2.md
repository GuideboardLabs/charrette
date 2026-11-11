# Whiteboard plugin: design spec (v2)

Working name: **Chalk**.
Target: Hermes Agent Plugin Hackathon, deadline **2026-11-11 17:00 EST**. Entry = data-only catalog PR + sub-90s X video.
Status: v2 incorporates an adversarial review. Five corrections to v1 are marked **[fix]** and are verified against source, not inferred.

## 0. What changed from v1

1. **[fix] `ctx.onEvent('plugin.chalk.*')` never fires.** The desktop event tap is an exact-key map plus `'*'` (`apps/desktop/src/contrib/events.ts::onGatewayEvent`). There are no globs. Subscribe with `ctx.onEvent('*', e => e.type.startsWith('plugin.chalk.'))`.
2. **[fix] `ctx.rest` has no server unless a dashboard plugin declares one.** `/api/plugins/<id>/*` is mounted only from `dashboard/manifest.json` with `api: plugin_api.py`, resolved inside the dashboard dir (`web_server_dashboard.py::_mount_one_plugin_api`). The backend is therefore a **day-1 artifact**, not a day-5 surface.
3. **[fix] Layout cannot run in Python.** `canvas.measureText` and dagre are renderer-side. The tool commits intent and returns `placed=pending`; the renderer lays out and posts `placed {id, bbox, clamped}`; the next turn's digest carries real geometry.
4. **[fix] No vision model is being served.** `:8101` runs `hauhau-35b-a3b`, `input_modalities:["text"]`, 32k ctx. Not Qwen3.8-27B, not multimodal. Grounding is text-first; vision is an optional call through `ctx.llm`, which routes images to the configured vision model.
5. **[fix] `board_ask` needs a third config key.** A click becomes a turn through `ctx.inject_message`, refused unless `plugins.entries.chalk.allow_gateway_injection: true`.

## 1. What this is, and why it is not `hermes-board`

A Hermes plugin where the board is the message and the transcript is its caption. You draw freehand, paste PNGs with typed labels, lasso things. The agent answers by drawing on the same board, at most two sentences of text, and its marks land **on the objects you drew**.

The catalog already contains `hermes-board` (community, v1.9.3): a Miro-like board the agent draws on with `board_apply`, a desktop pane, one JSON document, `board_watch`. `boardstate` and `sketch-pad` are also listed. A reviewer's first question is "what does this add", and the README must answer it in one paragraph:

| `hermes-board` | this plugin |
|---|---|
| Agent-first: the model authors nodes, edges, groups, captions, coordinates | Human-ink-first: your strokes and pasted images are the primary objects; the agent's content is anchored beside them |
| The model may place geometry | The model never places anything; deterministic layout plus a lint pass owns geometry |
| A board beside a chat | The board is the message; text is capped at two sentences per turn |
| Agent objects are its own | Agent marks resolve **onto your object ids** with a confidence floor, chips on failure, and a leader that terminates on the bbox edge of your stroke |
| Agent may edit the document | The agent may annotate your ink but never edit, move, relabel or erase it, enforced at ref resolution |

Three cases where this is not expressible as a diagram in chat, and the pitch:

1. **The referent is your own mark.** "Connect that to auth", where "that" is a scribble. No chat surface can carry the referent.
2. **The drawing must be corrected, not regenerated.** Turn three relayouts turn one with stable ids, so your eye tracks the same boxes.
3. **Provenance.** Whose hand is whose is visible per object, and undo is scoped to one hand.

The honest case against: Mermaid needs no install, no toggles, renders in the transcript, is copy-pasteable and phone-legible, and every diagram in the demo is Mermaid-expressible. That is why the first ten seconds of the video are case 1 and not a flowchart.

## 2. Scope

### In

1. Desktop app pane: canvas beside the chat, pointer input, pan/zoom, infinite board.
2. Human tools: freehand ink, PNG paste, typed label, lasso/marquee selection, undo.
3. Agent tools: `board_draw` (diagram, annotate), `board_edit`, `board_ask`, `board_look`.
4. Declarative drawing: model emits a spec, renderer owns geometry, lint before commit.
5. Grounding: per-turn digest plus lasso contents; optional vision crop via `ctx.llm`.
6. Scoped undo, per-transaction, human ink untouchable.
7. Second mount: dashboard plugin tab (browser and phone over tailnet HTTPS), same renderer.
8. First-run card that checks all three switches and says which is missing.
9. Catalog submission: `hermes plugins validate` clean, README with the delta and disclosures, screenshots, 90s video.

### Out this week

Board history, snapshots and diff (cut after review: no data model, no day in the plan, and the only demo beat that would have to be staged). Plot and matrix kinds (return only if day 4 lands early). KaTeX, Mermaid import, share links, multi-user, real-time collaboration, TUI, native mobile surface, transcript directive, dark palette (behind a flag, not shipped), `/chalk` full-page route, file export, templates, voice, board search.

## 3. Surfaces and transport

Verified against `~/.hermes/hermes-agent` at v0.22.5+3133.

| Surface | Mount | Transport |
|---|---|---|
| Desktop pane | `desktop/plugin.js`, `area:'panes'`, `placement:'right'`, `dock:{pane:'workspace',pos:'right'}`, `lifecycleKeepAlive:true` | `ctx.rest()` for commands; `ctx.onEvent('*')` + prefix filter for push |
| Dashboard tab | `dashboard/manifest.json` + `dashboard/dist/index.js` (classic IIFE script, no modules) | `SDK.fetchJSON('/api/plugins/chalk/...')`; WS or 300ms poll |
| Phone | the dashboard tab over HTTPS | same, full-screen board only |

Hard constraints, each of which has already cost someone a day:

- Desktop plugin is **one file**, ≤16 MiB. Specifiers outside `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`, `react/jsx-dev-runtime` are refused by the loader before evaluation. Relative and bare imports fail at runtime **and pass `hermes plugins validate`**. A library must be inlined; nothing external is loaded.
- `ctx.onEvent` takes an exact type or `'*'`. Filter in the handler.
- `ctx.socket` is a no-op on OAuth remotes and browser-hosted viewers. Never the only path.
- Tool-to-pane push requires `plugins.isolation` not `host`. Fallback: both mounts poll `GET /board?since=<rev>` at 300ms when an event has not arrived in 400ms. The poll is required anyway for the dashboard.
- The dashboard router is mounted from `dashboard/plugin_api.py` and under `isolation: host` it is forwarded and buffered, so WebSocket and SSE die there. Poll first, WS as an optimisation.
- Renderer is framework-free ESM with no React dependency. `renderer/chalk.mjs` is concatenated into `desktop/plugin.js` and into `dashboard/dist/index.js` by `scripts/bundle.py`. Shipped files are static.

Vendored inline, ~115 KB: rough.js 27.7 KB, perfect-freehand 4.5 KB, @dagrejs/dagre 49.3 KB, Excalifont Latin 25 KB (33 KB base64). All MIT or OFL, none uses eval, `new Function` or dynamic import.

## 4. Board document

**`board.json` is the sole authority.** `plugin_api.py` is the only writer, holding an in-process lock. Every write carries `if_rev` and returns 409 on mismatch, so the pane, the tab and a tool handler cannot silently overwrite each other. `oplog.jsonl` is advisory (audit and undo), fsynced after the json rename, rotated at 5 MB. One board per session, created as `draft:<pane-instance>` when the chat has no session id yet and re-keyed on the first turn; the dashboard takes `?board=` and defaults to most recent.

```jsonc
{ "schema":"chalk/1", "rev":41,
  "camera":{"x":-120,"y":-80,"z":1.0},
  "layers":[{"id":"agent-diagram","z":10},{"id":"human-ink","z":20},{"id":"agent-annotation","z":30}],
  "objects":{
    "obj_12":{"kind":"stroke","actor":"human","layer":"human-ink","z":"a3",
              "bbox":[1120,300,96,40],"pts":[[0,0,.4],[4,2,.5]],"label":"sso?","rev":2},
    "img_7":{"kind":"image","actor":"human","layer":"human-ink","z":"a2",
             "bbox":[900,260,300,180],"src":"a1b2.png","label":"pricing.png"},
    "d_31":{"kind":"scene","actor":"agent","layer":"agent-diagram","z":"a4","rev":7,"tx":9,
            "anchor":{"to":"img_7","side":"right","gap":64},"placement":"pending",
            "spec":{"type":"diagram","dir":"LR","nodes":[{"key":"a","label":"magic link"}],
                    "edges":[{"from":"a","to":"b","label":"1 day"}]}},
    "an_5":{"kind":"annotate","actor":"agent","layer":"agent-annotation","z":"a5",
            "target":"obj_12","style":"arrow","label":"this kills it","rev":7}
  },
  "tx":[{"id":"tx_9","actor":"agent","rev":7,"ops":["create:d_31"],"undo":["delete:d_31"]}] }
```

Id table (one table, no exceptions):

| prefix | object |
|---|---|
| `obj_` | human stroke |
| `img_` | human pasted image |
| `d_` | agent scene (a laid-out group) |
| `n_` / `e_` | child node / edge of a scene |
| `an_` | agent annotation |
| `q_` | question card |
| `tx_` | transaction |

Rules: units are CSS px at z=1, y down, camera separate from content. Z is a fractional index string inside a fixed layer band, never array position. Per-object `rev`, per-transaction `tx`, agent undo pops its own last `tx` and nothing else. Anchoring is `board | {to,side,gap} | {target,port}`; annotations terminate on the bbox **edge**, never the centre.

Event envelope for both mounts:

```
plugin.chalk.delta { board: "sess_9f2", rev: 42, actor: "human"|"agent", ops: [...] }
```

Every mount drops events whose `board` is not its own. Human ops broadcast on the same channel as agent ops, so two windows stay coherent.

## 5. Drawing pipeline

`spec -> commit (intent only) -> measure -> layout -> lint -> place -> animate`

1. **Commit** assigns ids, applies the spec, returns in under 50 ms with `placed=pending`. No geometry is computed here.
2. **Measure** in the renderer with `canvas.measureText` after `document.fonts.load()` resolves. Paint is gated on the font; a silent serif fallback is the single biggest perceptual failure and it still measures, so it must be asserted, not hoped for.
3. **Layout**: dagre for graphs, grid packer for cards, ~40 lines of linear/log scale for any chart that survives. Ports from box geometry, never from the model.
4. **Lint** returns `{overlaps, overflow[], offViewport, labelCollisions, crossings}`. Placement escalation: preferred side ±gap for up to 3 slots, then free-space search in reading order, then clamp with a "back to where you were" pill and `placement:"clamped"`.
5. **Place**: renderer POSTs `/placed {id, bbox, placement}`. The backend folds it into `board.json` so the next turn's digest carries truth. This is also how the model learns it was clamped, one turn later, without a poll.
6. **Animate**: reservation frame on tool-call emission, nodes fade in reading order at `min(60, 360/n)` ms, edges 40ms, labels type at 12ms/char capped at 180ms per label, total cap 600ms. Corrections snap in 120ms.

## 6. Tool surface

Four tools, and no state-fetch: state is pushed.

**`board_draw`**: `kind: diagram|annotate`, `place:{near,side,gap,region?}`, `spec`, `title?`, `replaces?`.
- diagram: `nodes[{key,label,shape:box|pill|diamond|note,tone}]`, `edges[{from,to,label,style}]`. Caps 9 nodes, 28-char labels.
- annotate: `targets[{ref,style:arrow|circle|bracket|underline|dim|spotlight,label}]`, `note?{text,at}`.
- Any chart takes `data:[[x,y],...]` (≤64 points) or `fit: linear|log` with coefficients. **[fix] No `expr`. Evaluating a model-supplied string server-side is the exact seam reviewers look for.**

Return grammar, success and failure both instructive:

```
ok d_31 accepted rev=41 place=right-of img_7 placement=pending
keys a=n_41 … e=n_45
board 17 objects (human 11, yours 6) rev=41
err E_NO_OBJECT ref="auth box" — nearest: obj_12 "oauth?"(0.71), obj_14 "auth svc"(0.64)
```

**`board_edit`**: `op: undo|erase|relayout|restyle|move|label`, `target`, `direction?`, `tone?`, `text?`. Undo is scoped to the model's own last `tx`. There is no clear-all and no op that reaches human ink.

**`board_ask`**: `question(90)`, `options[<=3]{id,label(32),hint?}`, `anchor{ref,side}`. Non-blocking: commits a card, ends the turn, the click returns as the next user message via `ctx.inject_message`. **[fix] Requires `plugins.entries.chalk.allow_gateway_injection: true`; the first-run card checks for it.**

**`board_look`**: `region{ref|bbox,pad}`, `want: objects|pixels|both`. Objects come from the board document. Pixels go through `ctx.llm` with the crop, which routes to the configured vision model and degrades to `objects only` if none answers. Capped at 2 per turn, then `E_LOOK_BUDGET`.

## 7. Grounding

```
<board rev="41" viewport="[-200,-100 2400x1600]">
obj_7  image  bbox=[900,260 300x180] label="pricing.png"
obj_12 stroke bbox=[1120,300 96x40]  label="sso?"
c_1    cluster bbox=[1500,900 620x380] "sketch, 14 strokes" thumb=0.4
</board>
<selection kind="lasso" bbox=[820,280 420x90] contains=[obj_9,obj_10,obj_12]/>
```

- **Text-first. [fix]** No crop is required for the demo, because every object the human cares about can carry a typed label. A crop is one optional `ctx.llm` call behind `board_look`, never on the hot path.
- Digest plus selection, not a whole-board raster, unless there is no selection.
- More than 8 nearby strokes collapse into one `cluster` line. Only viewport ∪ selection ∪ referenced-this-session ∪ everything the agent drew.
- Deixis binding: if the human says "this" and a lasso exists, inject `"this" = sel`; otherwise bind to the object nearest the cursor at send.
- Ref resolution is scored (id, label, bbox proximity, containment) with a 0.6 floor. Below it, refuse and return the three nearest candidates; two refusals in a row and the model must `board_ask` with those as chips.
- Budget ~1.8k tokens per turn: digest at 15 tok/object, tool deltas 60-90. The naive alternative (full stroke geometry plus a full-res raster) measures ~12k and is worse, because geometry invites the model to invent coordinates.

## 8. Permissions

- The agent may annotate human objects, never edit, move, relabel or erase them. Enforced at ref resolution.
- Layers fixed by the plugin: agent diagrams below human ink, annotations above.
- Agent undo is per-transaction. Human undo may revert either side but names whose it was first.

## 9. Visual spec

Daylight paper, dark frame, teal.

| token | hex | use |
|---|---|---|
| frame / edge | `#0B0F11` / `#1A2326` | app chrome |
| surface | `#F7F5EF` | warm paper, never `#FFFFFF` |
| card | `#FFFFFF` | node fill |
| grid | `#E9E4D8` at 45%, 24px | |
| ink / ink-2 | `#1B2326` / `#5C6B70` | text / secondary |
| ink-3 | `#8A979B` | non-semantic chrome only, never a control |
| teal / teal-bright / tint | `#0F766E` / `#14B8A6` / `#E6F4F1` | stroke+selection / fills only / focus fill |
| sticky | `#FDF3C7` fill, `#1B2326` text | never an accent fill with white text |

**[fix] Contrast:** focus and selection strokes use `#0F766E` (5.02:1 on paper). `#14B8A6` is 2.28:1 and is therefore fills only, never a stroke. `ink-3` at 2.76:1 is never text or a control. Verified ratios that hold: ink 14.65, ink-2 5.08, teal 5.02, ink on tint 14.13.

Type (Excalifont): 28/32 title, 20/24 card, 17/22 node, 13/16 edge, 15/22 note, 11/14 axis (11px floor), 11/14 chrome in ui-sans.
Strokes: node 1.5px roughness 1.0 bowing 0.8; focus 1.8px teal; edge 1.5px roughness 0.55; human ink 2.2px `#1F2A2C`; arrowhead 9x8. Radii 9/7/10/18. Gaps 40, margin 48, padding 16x12. No node shadows, gradients, filled header bars or decorative underlines.
Provenance: agent-authored labels carry a 1px dotted underline and the `an_`/`d_` prefix in the inspector, so the agent's hand is visibly a different hand.
Dark palette exists in `renderer/theme.mjs` behind a flag and is not shipped this week.

## 10. Distribution

`~/.hermes/plugins/chalk/`: `plugin.yaml`, `__init__.py`, `tools.py`, `board/`, `dashboard/manifest.json`, `dashboard/plugin_api.py`, `dashboard/dist/index.js`, `desktop/plugin.js`, `README.md`, `LICENSE`, `renderer/chalk.mjs`, `scripts/bundle.py`.

**[fix] Three switches, not two**, and the first-run card checks all three and names the missing one:

1. `plugins.enabled` must include `chalk`, or the agent half is inert and `ctx.rest` 404s.
2. Capabilities ▸ Plugins must show Desktop enabled; Electron force-sets the package half to `defaultEnabled: false`.
3. `plugins.entries.chalk.allow_gateway_injection: true` for `board_ask` clicks, otherwise the chip does nothing.

**[fix] Skills:** a native plugin's `skills/` dir is not auto-scanned. Call `ctx.register_skill("board-grammar", path)` in `register()` or the grammar skill sits on disk doing nothing.

**[fix] Catalog mechanics:** the entry PR must touch only `plugin-catalog/**` and `contributors/emails/**`, or CI fails. One file, `plugin-catalog/chalk.yaml`, full 40-hex sha, quoted version, `screenshots:` filled, `requires_hermes` set, capabilities matching what is registered.

**[fix] Disclosures, three lines in README and the PR:** board files live in the plugin data dir; when `board_look` is used, a crop of the selection is sent to the configured vision model; the plugin makes no network calls of its own and stores no credentials.

## 11. Demo (90s, anchoring-first)

**[fix] No history act.** "What changed since Tuesday" needs snapshots that do not exist on day 6; shooting it would mean hand-authoring a prior state, which is indistinguishable on camera from a fabricated diff, and a judge who installs finds no such feature.

| t | human | agent |
|---|---|---|
| 0-4 | empty board, title card: "a board, not a chat" | |
| 4-10 | scribbles a rough box, writes "auth" in it, arrows to a second scribble "magic link?" | |
| 10-16 | lassoes both scribbles, types "explain this" | six words: "One is a service. One is a link." |
| 16-24 | | reservation frame, then a 4-node diagram lays itself out to the right, arrow from the diagram's edge to the **bbox edge of her first scribble** |
| 24-30 | "no, put the token step in" | `board_edit relayout`, ids stable, one node inserted, 120ms snap. Her ink does not move a pixel. |
| 30-36 | circles the second scribble in red, types "this kills it" | arrow lands on the red circle's edge, label "this kills it", two sentences of text |
| 36-42 | "undo that" | `board_edit undo`: the arrow and label vanish, `human ink untouched (17 marks)` |
| 42-50 | pastes a PNG of a pricing table over empty space, labels it | reads the label, draws one callout beside it: "the 400 ms row" |
| 50-56 | scribbles in the margin, says nothing | does nothing. The board visibly belongs to her. |
| 56-64 | "connect that to auth", pointing at the margin scribble | one leader from that scribble to the auth node, resolved by id |
| 64-72 | | `board_ask` on the fork: "monthly" / "per-call". She clicks, no typing. |
| 72-80 | hovers a stroke | tooltip from the digest: `obj_88 · human · tag "why slow"` |
| 80-90 | pulls back to fit | 17 hand marks, 1 image, 2 scenes, 2 callouts, 1 question. Transcript: 3 lines. Title card. |

Every beat above is supported by the tool surface in §6. The only beat that wants the vision path is the PNG callout, and it degrades to the typed label.

## 12. Build order (6 days, ~52h)

**Day 1, walking skeleton, end-to-end by 18:00:** `plugin.yaml` + `dashboard/manifest.json` + `plugin_api.py` with `GET/POST /board` and `if_rev`, one tool `board_ping`, a pane that renders one stroke from the digest. No layout, no ink, no paste. The point is that transport exists before anything depends on it.

| day | work | hours |
|---|---|---|
| 1 | walking skeleton: manifest, api, one tool, one stroke on screen | 9 |
| 2 | renderer core: camera, pan/zoom, ink (perfect-freehand), PNG paste, typed labels, selection, undo | 12 |
| 3 | layout: measure after font load, dagre + grid, lint, placement escalation, `/placed` round trip | 11 |
| 4 | tools: draw, edit, ask, look; return grammar; transaction log; digest; ref scoring; `board_ask` clicks | 11 |
| 5 | desktop mount polish, first-run card with three switches, dashboard tab reusing the renderer | 8 |
| 6 | `hermes plugins validate`, README with delta and disclosures, screenshots, catalog PR, three takes of the video | 8 |

Cut if a day slips, in order: the dashboard tab (keep `plugin_api.py`), `board_look` pixels, `annotate` spotlights, `board_ask`. Never cut: the anchoring beat, undo, lint.

## 13. Risks

| risk | mitigation |
|---|---|
| Dead air before the first mark | first sentence ≤8 words before the tool call; reservation frame on tool-call emission; 600ms cap |
| Callout lands on the wrong object | ref-only anchoring, 0.6 floor, chips on failure; rehearse the exact lasso geometry ten times |
| Layout goes ugly under a greedy spec | hard caps, collision push, ellipsis not shrink; run the demo specs 20x at temperature 0 |
| Demo model stalls | whatever is actually serving on `:8101` (today `hauhau-35b-a3b`, 32k ctx, text-only), pre-warmed, temperature 0, three takes. Confirm the served model the morning of the shoot. |
| Judge installs and sees a blank screen | three switches in the README, first-run card, `host.revealPane('chalk:board')` |
| Validator passes imports the loader rejects | no imports beyond the SDK and react; renderer inlined by our bundler; boot test in a fresh profile |
| Reviewer says "hermes-board already does this" | §1 delta table in the README and PR body; first 10 seconds of the video is anchoring, not a diagram |

## 14. Acceptance criteria

1. Ink, paste and label at 60fps with 200 objects on a 1080p pane.
2. A five-node diagram commits in under 50ms and finishes drawing in under 600ms, with the longest label capped at 180ms of type-on.
3. An arrow from a lasso-anchored annotate lands on the target's bbox edge, no overlap, ten of ten.
4. Agent undo reverts its last transaction and leaves every human object's `rev` unchanged.
5. Board survives a pane remount, a session resume and a plugin reload at identical `rev`.
6. Two mounts, one renderer: the dashboard tab renders the same board with no fork in the drawing code.
7. `hermes plugins validate` clean, and the plugin loads in a fresh profile with exactly the three documented switches flipped.
8. Digest under 2k tokens for a 40-object board.
9. No `eval`, no `new Function`, no dynamic import, no network call except `ctx.llm` when `board_look` asks for pixels.
