# SPEC v1.3 — canvas interaction, and edges v2

Status: **frozen for the v1.3 swarm**. Three lanes build against this file in parallel; the
orchestrator merges, dispositions and gates. Requirement ids (`W…`) are cited by the cards, the
tests, the commit messages and the verifier. If something here is wrong or impossible, say so in
your lane report — do not silently invent a different field name, enum value or coordinate.

## W0. Lane map (file ownership — do not touch another lane's files)

| Lane | Branch | Owns (only these files) | Features |
|------|--------|------------------------|----------|
| **I** interaction | `feat/v13-interaction` | `vendor/board_editor.js` | pan/zoom, marquee multi-select, group move, duplicate/copy-paste, keyboard (W1, W2) |
| **E** edges v2 | `feat/v13-edges` | `vendor/boardkit.py`, `vendor/board_editor.js`, `tests/test_render.py` | `from_side`/`to_side`, `head_size` in BOTH renderers + the panel HTML/handlers (W3), **the zoom controls' HTML in `page()` (W1.5) and the three added reads/disabled-state lines at the END of `openPanel()` (W3.4)** |
| **O** ops/docs | `feat/v13-ops` | `store.py`, `tools.py`, `tests/test_tools.py`, `README.md`, `plugin.yaml` | ops/validation/summary/diffs/docs for the new edge fields (W4) |

Lanes **I and E both edit `vendor/board_editor.js`** — different regions, and the split is exact:

* **E owns** in `board_editor.js`: `effectiveHeads`, `markerShape`, `markers`, `edgeSvg`, `route`,
  `sides`, and (only) the panel handlers it adds after the existing `bind("ck-end", …)` block.
* **I owns** in `board_editor.js`: the view state and `toSvgPoint`, every pointer/keyboard handler,
  the bindings for the `zoom-in`/`zoom-out`/`fit` buttons and the `#ck-zoom` readout text (the HTML
  itself is lane E's, in `boardkit.page()`),
  `selection`, `nodeSvg`'s selection ring, `noteSvg`'s ring, `edgeSvg`'s *selected* highlight only if
  it must (prefer leaving `edgeSvg`'s head code alone), `addNode`/`addNote`/`addEdge`/`removeSelected`
  as far as selection is concerned, `render()`'s body except the `markers()`/`edgeSvg()` calls,
  `syncButtons`, the toolbar buttons it adds, and the `#selftest` block.
* A merge conflict inside `board_editor.js` is expected and is the orchestrator's job: keep both
  sides' additions, never "resolve" by dropping a lane's work.

Orchestrator (not a lane): `docs/SPEC-v1.3-canvas.md` (this file), `docs/fixture-v1.3.json`,
`tests/test_editor.py`, `.github/workflows/tests.yml`, the `vendor/` → chat-charts skill sync, the merge, the
disposition commit, the demo render, the MR.

Rules for every lane:

* Work only in the worktree you were handed; **commit only** — never `git checkout`, `branch`,
  `merge`, `rebase`, `push`, `stash`, and never write under `~/.hermes/skills/**`. One commit,
  message `v1.3 <lane>: <what>`, body naming the `W…` ids implemented.
* `vendor/boardkit.py` and `vendor/board_editor.js` are the **only** copies you edit. The chat-charts
  skill copy under `~/.hermes/skills/**` is stale on purpose (the orchestrator syncs it at
  integration): run `tests/test_vendor.py` with `HERMES_HOME=$(mktemp -d)` so the drift check skips
  loudly instead of failing on a file you must not touch.
* Existing behaviour must not change: a board without the new fields renders, routes, snaps and
  behaves exactly as today (v1.2's `tests/test_render.py`, `tests/test_tools.py`, `tests/test_api.py`,
  `tests/test_editor.py` and `node tests/pane_smoke.mjs` stay green — the first three are the
  orchestrator's gates for your merge, run the ones you can).
* No new imports or files beyond the ones listed; no external references; no hardcoded colors — use
  `var(--ck-…)`/`var(--ck-tone-<tone>-fill|line)`/`var(--ck-edge-<tone>)`.
* Numbers: emit computed values as-is (no rounding), both renderers alike — `tests/test_editor.py`
  normalises float noise, so only STRUCTURAL differences fail.

## W1. Pan and zoom (lane I, editor only — the document is untouched)

**W1.1** The editor keeps a view in memory: `view = { x: 0, y: 0, k: 1 }` where `k` is the zoom
factor. It is **not** written into the document (the Python renderer always draws the whole board,
so the PNG and the chat artifact are unaffected and the parity gate stays meaningful) and it is not
persisted between sessions. On load the view is `{0, 0, 1}` (the board drawn whole).

**W1.2** Rendering: the SVG `viewBox` becomes `"{view.x} {view.y} {W/k} {H/k}"`, where `W`,`H` are
the document's canvas numbers — the viewBox aspect therefore stays `W:H`, so the scale in
`toSvgPoint` stays uniform (`rect.width / viewBox width`). `width="100%"`, `height="auto"`,
`preserveAspectRatio`, `role` and `aria-label` on the `<svg>` are unchanged.

**W1.2b** `toSvgPoint` must ADD the viewBox origin, or every drag, marquee bound and pointer-anchored
zoom jumps by the pan offset: `x = parseFloat(vb[0]) + (clientX - rect.left) / scale`, and the same
for `y` with `vb[1]`/`rect.top`. (Today's function returns only the offset term, which is correct
only while the origin is 0,0 — it is not a pan-aware transform and must not be left as-is.) A test
must exercise the conversion at a non-zero origin and assert a dragged item lands under the pointer.
The same rule applies to the pointer-anchored zoom of W1.3 and the marquee of W2.2.

**W1.3** Zoom: `k ∈ [0.25, 4]`, multiplied/divided by 1.15 per notch, **anchored at the pointer**
(the document point under the cursor stays under the cursor). Inputs: `ctrl`/`meta` + `wheel`
(zoom), the toolbar's `−` / `+` buttons (zoom anchored at the view centre), and the `+` / `-` keys.
`0` (and the toolbar's `fit`) resets to `{0, 0, 1}`. Clamp after every change; never allow `k` to
pass the bounds even transiently.

**W1.4** Pan: `wheel` without a modifier pans by the wheel deltas (converted to document units via
the current scale), a **middle-button** drag pans, and `space` + left-drag pans. A left-drag on
empty canvas is a marquee (W2.2), never a pan.

**W1.5** The toolbar gains, after the existing `grid` button:
`<button data-act="zoom-out" aria-label="zoom out">−</button>`,
`<button data-act="zoom-in" aria-label="zoom in">+</button>`,
`<button data-act="fit">fit</button>` and a readout `<span class="ck-hint" id="ck-zoom"></span>`
showing `Math.round(k * 100) + "%"`, refreshed by `render()`. Keep the toolbar to one row.

## W2. Multi-select, marquee, group move, duplicate (lane I, editor only)

**W2.1** The editor's selection becomes an ordered list of
`{ type: 'node'|'note'|'edge'|'group', id }` (`selection`). A **group** is selectable, draggable,
duplicable and deletable, and gets the same dashed ring as the others, but it has **no panel** (the
panel opens only for a node/note/edge primary, and stays closed for a group-only selection) — this is
what lane I had already built when the amendment landed, and it is accepted: a Miro-like canvas is
expected to let you move a zone, and groups still have no resize anywhere in the editor. The FIRST
entry is the *primary*: the panel (text/sub/kind/tone/align/parts/split,
heads) shows and edits the primary item exactly as it edits the single selection today. A plain
click on an item selects it alone; `shift` + click toggles it in the list (and never opens the panel
for a removal); a click on empty canvas clears the selection. Selection rings are drawn for every
selected item (the existing dashed ring, unchanged in style), and the panel is open only when the
selection is non-empty.

**W2.2** Marquee: a left-drag that starts on empty canvas draws a marquee
`<rect class="ck-marquee">` — a dashed `var(--ck-1)` border over a translucent accent band
(`fill="var(--ck-1)" fill-opacity="0.08"`; a var, never a hardcoded color, which is what the
Miro-like band needs and what lane I shipped) and selects every **node, note and group** whose rect **intersects** it (edges have
no rect of their own and are never marquee-selected; a zero-size drag — a click — clears the
selection instead). The marquee is drawn last, above everything, and is removed
on pointerup.

**W2.3** Dragging any selected item moves **all** selected items by the same delta (the grid
snap of W2.6 of the v1.2 spec applies to the primary item; the others move by the same snapped
delta). Edges follow their endpoints as they do today — an edge has no position of its own, so a
selection that contains only edges ignores a drag.

**W2.3b** Click and drag must not fight each other (today's `pointerdown` replaces the selection
immediately, which would collapse a multi-selection the moment you start dragging one of its items):
* `pointerdown` on an item that is ALREADY selected keeps the whole selection (it starts a group
  drag);
* `pointerdown` on an item that is not selected replaces the selection with it (plain) or toggles it
  (shift);
* a `pointerup` after less than 3 px of movement is a CLICK: it collapses the selection to that one
  item (or removes it, with shift) and opens the panel for it;
* the connect mode and the marquee keep their current precedence.
A test must cover both orders: multi-select → drag one item → every selected item moved;
multi-select → click one item → only that item stays selected.

**W2.4** `Delete`/`Backspace` removes every selected item (a node still takes its edges with it; an
edge is removed on its own). `⌘/ctrl+D` duplicates the selection: new ids generated from the
existing scheme (`n<N>-<base36>` / `note-<base36>`, never colliding with an existing id), offset by
`(+20, +20)`, and an edge is duplicated only when BOTH of its endpoints were duplicated (it then
connects the copies). `⌘/ctrl+C` stores the selection in an in-editor clipboard and `⌘/ctrl+V`
pastes it with the same offset and new ids — the SYSTEM clipboard is not touched.

**W2.6** The `#selftest` block gains steps named exactly `zoom`, `pan`, `marquee`, `mselect`,
`groupmove`, `duplicate` (lane I), next to lane E's `attach` — each a predicate over the rendered
DOM or the state, the same style as the v1.2 steps. The orchestrator's gate requires those names.

**W2.5** Group drags, marquee and keyboard handlers must not fire while the pointer is in a panel
field: keep the existing guard style (`evt.target.closest('.ck-node, .ck-note, .ck-edge')` etc.) and
ignore key events when `document.activeElement` is an `input`/`select`/`textarea`.

## W3. Edges v2 — explicit attach sides and a head size (lane E, both renderers)

**W3.1** New edge fields:
`"from_side"`/`"to_side"` ∈ `"auto"` (default, absent means this) | `"left"` | `"right"` | `"top"` |
`"bottom"`, and `"head_size"` ∈ `"small"` | `"medium"` (default, absent means this) | `"large"`.
An unknown value is treated as the default (`auto`, resp. `medium`), never rendered raw.

**W3.2** Anchor points. `sides(a_rect, b_rect)` currently returns the pair of anchor points chosen
from the relative positions (`vendor/boardkit.py`, mirrored by `sides()` in the editor). Replace it
with `anchors(a_rect, b_rect, from_side, to_side)`:
* for an end whose side is `auto`, keep today's rule for THAT end (the existing `sides()` logic;
  with two `auto` ends the result must be **identical** to today's — this is the back-compat rule);
* for an explicit side, the anchor is that side's midpoint: `left` → `(x, y + h/2)`,
  `right` → `(x + w, y + h/2)`, `top` → `(x + w/2, y)`, `bottom` → `(x + w/2, y + h)`.
  An explicit side on ONE end and `auto` on the other keeps the auto end's position-based rule.
* when only one side is explicit, the `dx/dy` that today's `sides()` uses to choose BOTH ends is
  computed as before (centre-to-centre), so the auto end's choice does not depend on the explicit
  end (pin this: it keeps the two renderers trivially equal).
* `route`: `auto`/`h`/`v` keep today's elbow (bend at the midpoint between the two anchors);
  `straight` stays the two anchors joined; `waypoints` keep working (the first/last anchor of the
  waypoint path is the anchor of W3.2 for that end).

**W3.3** Head size. `head_size` maps to a marker box in user space: `small` = 8, `medium` = 11
(today's value), `large` = 16, and ONLY `markerWidth`/`markerHeight` change with it:
`refX="10"`, `refY="5"` stay EXACTLY as today for every size, because they are coordinates in the
marker's `0 0 10 10` viewBox (its tip sits at (10,5) in that box) — the box is then scaled to the
requested pixel size. `refX = box / 2` is wrong: it moves the tip off the endpoint and breaks the
byte-identity of an existing document. Markers are emitted per (tone, type, size); the edge
references the marker for its own size, and the default (`medium`) ids must stay exactly
`ck-head-<tone>-<type>` (emit `ck-head-<tone>-<type>` for `medium`, `ck-head-<tone>-<type>-<size>`
otherwise), so a v1.2 document renders byte-identically. A test must assert the default marker's
`refX`/`refY`/`markerWidth`/`markerHeight` values, not merely its id.

**W3.4** Panel (lane E adds the HTML in `boardkit.page()` and the handlers in the editor):
`<label>from side</label><select id="ck-from-side">…</select>`,
`<label>to side</label><select id="ck-to-side">…</select>`,
`<label>head size</label><select id="ck-head-size">…</select>` — options exactly
`auto/left/right/top/bottom`, resp. `small/medium/large`, in that order; `openPanel()` fills them
from the effective values; the handlers set `item.from_side`/`item.to_side`/`item.head_size`,
writing `"auto"`/`"medium"` explicitly is allowed (the reader treats them as the default), and they
are enabled only while an edge is the primary selection.

**W3.5** `tests/test_render.py` (lane E extends it, it is not another lane's file): assert both new
fields on the Python side — every explicit side produces the documented anchor point as the first or
last polyline point; `auto` + `auto` is byte-identical to `v1.2`'s output for the same edge;
`head_size` `small`/`large` change `markerWidth`/`markerHeight`/`refX`, `medium` and absent produce
the plain `ck-head-<tone>-<type>` id; an unknown side/size falls back to the default. Plus one
`#selftest` step `attach` (lane E writes it inside the selftest block, next to the existing `heads`
step) that builds an edge with both sides explicit and asserts the rendered polyline's first and last
points are the two side midpoints.

## W4. Ops, tool surface and docs (lane O)

* `add_edge`/`update_edge` accept `from_side`, `to_side`, `head_size`; an unknown value raises
  `ValueError` naming the valid list. As in v1.2, the plugin defines those enums itself
  (`store.py`: `SIDES = ("auto", "left", "right", "top", "bottom")`,
  `HEAD_SIZES = ("small", "medium", "large")`) — never import them from `boardkit`, whose `HEADS`
  trick keeps your suite green against the library as it is today. Write the fields onto the dict
  `boardkit.edge(...)` returns; never pass them as kwargs.
* `update_edge` with `"auto"`/`"medium"` stores the value as given (it means the default).
* `OPS_HELP` documents the three fields; `_summary` reports `from_side`/`to_side`/`head_size` per edge
  (effective values); `diff_boards` describes them in plain language (`edge a→b attaches right→top`,
  `edge a→b head size large`) and still emits NO event for a re-save that changed nothing.
* `README.md`: a `## Canvas (v1.3)` section — pan/zoom, marquee/multi-select, duplicate/paste,
  the attach sides and the head sizes, plus the new `tests/test_editor.py` coverage and any
  limitation you can see. Do not rewrite the existing sections.
* `plugin.yaml`: `version: 1.3.0`.

## W5. Non-goals (state them; do not build them)

Images/asset nodes (a storage + HTTP concern — wave 3), per-part tones, node rotation, text
formatting, alignment/distribution tools, smart guides, lock/hide, view persistence in the document,
persisting the viewport across sessions, cross-tab clipboard, touch gestures, edge
`start_label`/`end_label`.

## Round-1 dispositions (independent gap pass, `docs/SPEC-v1.3-canvas.gaps-r1.md`)

The three lanes started from the pre-disposition text. The code half of these dispositions is
applied by the orchestrator **after** the lane merge, in one commit; the verifier checks the merged
head against this table as well as against W0–W6.

| Gap | Disposition |
|-----|-------------|
| 1 MAJOR the zoom controls had no permitted writer | W0 amended: the toolbar HTML belongs in `boardkit.page()` (W1.5's exact markup) and lane I owns the bindings/readout. Lane I shipped an idempotent injection of the same three buttons from the editor instead (it started before the amendment); the orchestrator moved the markup into `page()` and kept the injection as a fallback — it stands down when `#ck-zoom` exists, so the rendered toolbar has each control exactly once (the control-surface gate asserts the count). |
| 2 MAJOR the edge panel cannot show the new fields | W0 amended: lane E owns the three added reads and the disabled-state lines at the END of `openPanel()` (append-only, marked with a comment); lane I must not restructure that function beyond the primary-selection handling. |
| 3 MAJOR the default marker geometry broke byte-identity | W3.3 corrected: `refX="10"`/`refY="5"` stay unchanged for EVERY size (viewBox units); only `markerWidth`/`markerHeight` differ. `refX = box/2` is wrong. Both renderers fixed, and lane E's two `tests/test_render.py` checks (written from the pre-amendment text) now assert the box scale AND the constant anchor, plus a v1.2-defs identity check in `tests/test_editor.py`. |
| 4 MAJOR the pointer math was only correct at origin 0,0 | W1.2b added: `toSvgPoint` must add `vb[0]`/`vb[1]`, and a test must exercise a non-zero origin. |
| 5 MAJOR marquee groups had no representation | Resolved in the other direction: lane I had already implemented groups as selectable/draggable/duplicable/deletable-with-a-ring (no panel) before the amendment landed, coherently and with a `mselect` step. Accepted as the richer, more Miro-like behaviour; W2.1/W2.2 amended to pin it. The orchestrator verified the ring, the delete path and that duplicate/paste cover groups. |
| 10 MAJOR (gate round 2) the `origin` step passed with W1.2b broken, because a drag's delta cancels the very error it was meant to catch, and the first marquee-based attempt still overlapped a wide node. | Fixed: the step now asserts the marquee band's OWN document coordinates against the document point under the start pixel — a comparison with no delta to cancel — and it was proven by re-breaking `toSvgPoint` (only `origin` fails). The same round's `groupdup` step also leaked its two clones into the state; it now removes them and asserts the counts are back. |
| 11 NOTE (gate round 2) `tests/test_vendor.py` failed with the real HOME during the round: bundled `boardkit.py` differed from the chat-charts copy, because the orchestrator's own sync of the next commit landed while the gate was running. | Not a code defect — the two copies are identical on the pushed head and the gate is green (`vendor/boardkit.py matches the skill copy`). Recorded because the gate legitimately saw a red tree; re-run it after any vendor edit and sync together in one step. |
| 8 MINOR (gate round 1) the marquee filled with a translucent accent instead of `fill="none"` | Accepted: the fill is `var(--ck-1)` at 0.08 opacity, so the "no hardcoded color" rule holds and the band is what a Miro-like canvas draws; W2.2 amended to pin it. |
| 9 MAJOR (gate round 1) `selectedSet()` omitted groups, so duplicate and copy/paste could not clone a selected group | Fixed: `selectedSet()` collects groups and `pasteSet()` pushes the copy into `state.groups`, with `freshId("group")` producing a non-colliding `g<N>-<base>` id. Two self-test steps now assert both orders of W2.3b (`groupdrag`, `clickdrag`, the latter with a sub-3-pixel jitter) and one asserts W1.2b at a non-zero view origin (`origin`); the gate requires all three names. |
| 7 MAJOR (found by the orchestrator while rendering the demo, pre-existing) the PNG window was sized from the board's canvas while the page adds a title, a toolbar and padding, so the shot cut off the bottom of any board whose content reached low — measured: a note at y=460 of a 620-tall board is absent from a 900x620 shot and present in a 900x1000 one (pixel-probed, not eyeballed). Fixed in `boardkit.render_png`: `page_size()` measures the page in the browser (the document's scroll size is read back out of `--dump-dom`) at the requested width and the window is built from that; `store.render_to_file` passes only the board's width. `tests/test_editor.py` now renders the v1.3 fixture and asserts the PNG's IHDR is at least the measured page size. |
| 6 MAJOR click and group-drag timing | W2.3b added. Lane I implemented the preserve/collapse split (`drag.collapse`) but set `moved` on ANY pointermove, so a jittery click was treated as a drag; the orchestrator added the <3 px threshold in `pointermove` (below it the item is not moved at all and the pointerup collapses the selection). |

## W6. Gates

```bash
PY=$(ls -d ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
# lane E and lane I, in their own worktrees:
HERMES_HOME=$(mktemp -d) $PY tests/test_vendor.py
node --check vendor/board_editor.js
node tests/pane_smoke.mjs
# lane E: $PY tests/test_render.py
# lane O: $PY tests/test_tools.py && $PY tests/test_api.py
# lane I: the editor's own self-test on the new fixture (Chrome is on PATH):
#   $PY - <<'PYEOF'
#   import sys; sys.path.insert(0, "vendor")
#   import boardkit
#   b = boardkit.load("docs/fixture-v1.3.json")
#   boardkit.report("/tmp/fx13.html", b, bridge=True)
#   print(boardkit.selftest("/tmp/fx13.html"))
#   PYEOF
```
The orchestrator runs `tests/test_editor.py` (the cross-renderer parity gate, extended with the v1.3
fixture and probe) after the merge, and syncs `vendor/` into the chat-charts skill.
