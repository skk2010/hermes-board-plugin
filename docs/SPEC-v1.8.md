# SPEC v1.8 — a gesture must not shake the board, and a straight line you drag

Frozen before the first card exists. Two lanes (`R`, `S`), one verifier. Every requirement id below
is quoted by a card body; `grep '^## <id>'` must find each one here.

## W0. Evidence (measured, not eyeballed)

Probe `probe_repaint.py` (real `Input.dispatchMouseEvent` over CDP, headless Chrome, the v1.6
fixture, a 1000×900 window) on the v1.7 head:

| gesture | host childList mutations | `<svg>` element | per-move browser time |
|---|---|---|---|
| line-mode stroke, 20 real pointer moves | **22** | **REPLACED at every step** | median 15.6 ms, max 25.8 ms |
| node drag, 20 real pointer moves | **22** | **REPLACED at every step** | median 13.2 ms, max 15.4 ms |

`render()` ends with `host.innerHTML = out.join("")`: every live interaction throws the whole board
away — `<defs>`, the grid pattern, every node, note, edge and line — and re-parses it. The `<svg>`
element the user is drawing on is a different object after every mouse move. That is the "shaking".
It is not a geometry bug: the svg's client rect and the page scroll are stable across the gesture
(measured, `svgTops` constant, `scrollY` 0), and a dragged node tracks the pointer exactly.

The fix therefore has one requirement: **during a gesture the board's scene is not rebuilt**.

## R. Lane R — the live render path (owner: lane R)

### R1 — one SVG element

The editor creates the `<svg class="ck-board">` once and keeps it until
  the document is replaced (boot, `ck-board-replace`, reset). No gesture path may replace that
  element; it may write attributes on it (`viewBox`) and update its children in place. The gate
  counts `<svg class="ck-board">` creations during a gesture and requires 0.
### R2 — scene vs chrome, tracked by a dirty flag

The SCENE is what the two renderers draw from
  the document: the grid rect, groups, areas, edges, nodes, notes, lines — in the pinned draw order
  (R5.2). CHROME is editor state the Python renderer never emits: guides, resize handles, vertex and
  waypoint handles, the anchor overlay, the draft preview, the marquee. A document mutation (any
  write to `state`, any selection change that alters a scene element — the selection ring lives
  INSIDE `nodeSvg`/`noteSvg`/`groupSvg`) marks the scene dirty; `render()` rebuilds the scene only
  when it is dirty, and always refreshes chrome. Consequence: the draft-hover, marquee, guide,
  pan and zoom paths cost one chrome refresh, not a scene rebuild.
### R3 — a live drag patches items, it does not rebuild the scene

While a node/note/group drag or
  a handle resize is live, the moved items are updated IN PLACE: a node's `transform="translate(x,y)"`
  attribute, a note's or group's own markup re-emitted inside its existing `<g>` (their geometry is
  baked into their children). Every edge and every line that the move displaces is re-emitted in
  place through the SAME builder (`edgeSvg`/`lineSvg`) and the same `route(...)` the full render
  calls, so the patched picture cannot drift from the drawn one. The scene is still marked dirty:
  the next `render()` rebuilds it and must produce the identical DOM.
### R4 — the drift invariant, gated

After any patched gesture the `#ck-board-host` DOM is
  byte-identical to what a full render of the same model produces. Gate: the editor's own self-test
  step `livepatch` forces a full scene rebuild after the gesture and compares serialised HTML, and
  the harness re-does it from outside (see Z9).
### R5 — chrome is replaced in place, never re-appended after a scene rebuild it did not cause

The chrome elements are editor-owned and held in one list; a chrome refresh removes exactly the
  old chrome nodes and appends the new ones. At idle (no selection of one item, no draft, no
  marquee, no guides) chrome is EMPTY, so the idle DOM — and therefore the parity test and every
  recorded baseline — is unchanged by this lane.
### R6 — pan and zoom write the viewBox, they do not re-render the scene

`wheel`, the pan drag
  and `zoomAt` change `view` and write the `<svg>`'s `viewBox` attribute plus chrome (the zoom
  readout lives outside the host).
### R7 — no new markup

This lane adds NO wrapper group, NO `data-*` attribute and NO element to
  the rendered document: parity with `render_svg` and the `baseline-*.json` hashes are unchanged
  evidence that it did not. A wrapper `<g class="ck-scene">` is FORBIDDEN — it would change every
  pinned baseline and every PNG.

## S. Lane S — the straight line (owner: lane S)

### S1 — the tool button

A fourth control in the toolbar's tool group: `data-tool="straight"`,
  its own 16×16 glyph (a single diagonal stroke with two end dots), `aria-label="draw a straight
  line"`, a `title` naming the gesture and the Shift constraint, and a `pressed` flag like its
  neighbours. Emitted by `boardkit.page()`'s toolbar (the `_BAR_GLYPHS` table + one `bar_button`
  call) AND by the editor's own fallback bar builder (the path that builds `[data-tool]` buttons
  when the page carries none), so the tool set cannot differ between the two pages. The bar must
  still fit ONE row at the pane's 440 px: measured headroom at the v1.7 head is 386.9 px of content
  in a 416 px box, and the new button costs 26 px, so the existing `barfit` step must stay green.
### S2 — the gesture

With `tool === "straight"`, `pointerdown` on the canvas opens a two-point
  draft at that point; `pointermove` moves its free end under S4's axis lock (Shift frees it);
`pointerup` commits it
  through the existing `addLine(points, from, to)` factory, whose two ends snap exactly as the
  `line` tool's do (`snapAnchor`). The committed object IS a `line` — no new document field, no new
  op, no renderer change, no panel change. A stroke shorter than 3 document units commits NOTHING
  (no model write, no undo entry, no selection change) and the mode stays armed for the next one.
### S3 — the preview reuses the draft mechanism

The straight drag writes the SAME `draft` global
  the `line` tool writes (`{points, hover}`) and calls the same `render()`, so it needs no render
  path of its own and inherits lane R's cheap live path (the preview polyline is emitted as chrome).
  With no hover, the draft is not drawn — a press with no move must not flash a line.
### S4 — the drag is locked to 45° steps, Shift frees the angle

The mode draws the lines a diagram is made of: the free end is snapped to the nearest multiple of
**45°** from the anchor — horizontal, vertical, and the two diagonals ("parallel or perpendicular to
the horizon" is in that set, and the user confirmed the 45° steps are fine as well). The LENGTH is the
pointer vector's PROJECTION onto the snapped direction, so the end stays on the ray through the anchor
and the preview cannot disagree with the commit. Holding **Shift** releases the lock and the free end
follows the pointer at any angle — the escape hatch for a line that is not one of the steps. The rule
is ONE pure helper `constrainStep45(ax, ay, bx, by, free) -> [x, y]` with no DOM access, so it can be
asserted directly: a mostly-horizontal offset returns `y === ay`, a mostly-vertical one returns
`x === ax`, a ~40° offset returns `|dy| === |dx|` (rounded), a shallow 5° offset returns a horizontal
end whose length is the x-delta (the projection, not the hypotenuse), and `free === true` returns the
point untouched.
### S5 — Esc cancels, no trace

Esc during a live straight draft drops it: nothing in the
  document, nothing in the undo history, no panel section (the same rule Z4.2 pins for `line`).
### S6 — the mode's way out

`setTool` stays the single writer of the mode and accepts
  `"straight"`; exactly one `[data-tool]` carries `aria-pressed="true"` at all times; the `select`
  button is the way back; the canvas cursor is `crosshair` in the mode and the default outside it;
  leaving the mode drops a live straight draft the same way `line` mode does.
### S7 — the message

Entering the mode says, in `#ck-status`: "straight line: drag for a 45°-step line (horizontal,
vertical or diagonal), hold Shift for any angle" — and leaving it clears the message (the `setTool`
idiom). The button's `title` says the same thing, shorter. This is the whole of the mode's
discoverability: the default is the locked line the user asked for, and the message is where the
escape hatch is announced.
### S8 — docs and version

`README.md` names the tool where it names the `line` tool and lists it
  in the pane's toolbar paragraph; `plugin.yaml` → `version: 1.8.0`; `docs/SPEC-v1.8` is this file.

## Z9. Gates (what "done" is measured by)

1. `tests/test_editor.py` — the whole existing suite stays green, every `baseline-*.json` hash
   still reproduces (R7), and two new checks are added by the ORCHESTRATOR (not by a lane):
   * `gesture_churn` — via `probe_chrome`: capture the top-level scene children and observe
     `#ck-board-host`; drive a 20-move synthetic stroke in line mode and a 20-move node drag;
     require `<svg class="ck-board">` creations `<= 1` across both, every captured scene child still
     `isConnected` to the same parent, host childList mutations `<= 2` per gesture, and — the drift
     check — `host.innerHTML` after the patched drag equal to `host.innerHTML` after forcing a full
     scene rebuild (two grid toggles). With the fix reverted this check fails on all four counts.
   * `straight_tool` — the rendered page at 440 px carries `data-tool="straight"` inside the `.ck-bar`
     (read from the RENDERED DOM, not from the page source), the bar still occupies one row, and the
     editor's `straightdraw` step is present and true.
2. The editor's own self-test: `livepatch` (R4) and `straightdraw` (S1–S6) present and true, asserted
   by name — `report.ok` alone is not enough.
3. The independent verifier card re-runs (1) and (2) on the merged head and, per finding, either
   refutes it by re-running this reproduction or confirms it with the fix removed.
4. The orchestrator records the same probe numbers as W0 but on the merged head, in the MR.

## Z10. Lane map (function ownership — do not touch another lane's functions)

| requirement | file | functions / elements the lane owns |
|---|---|---|
| R1–R7 | `vendor/board_editor.js` | `render`, `schedule`, the new scene/chrome split and its dirty flag, the item-patch helpers, `zoomAt`, `fitView`, the `wheel` handler, the `pointermove` branches for `draft`/`marquee`/`panDrag`/`drag`, `resizeMove`, `pathDragMove`, every `host.innerHTML` write, the `livepatch` self-test step |
| S1, S8 | `vendor/boardkit.py` | `_BAR_GLYPHS`, the single `bar_button("straight", …)` call in `page()`'s toolbar |
| S1–S7 | `vendor/board_editor.js` | the `tool` variable's accepted values, `setTool`, the fallback bar builder, `constrainStep45`, the `straight` gesture (its own branches in `pointerdown`/`pointermove`/`pointerup`), `commitStraight`, the `straightdraw` self-test step |
| S8 | `README.md`, `plugin.yaml` | the pane paragraph, the toolbar list, `version: 1.8.0` |
| Z9, integration, the MR, the vendor→skill copy | orchestrator | `tests/test_editor.py`, `docs/**`, the wave branch, the merge, `scripts/sync_boardkit.py` |

Both lanes edit the three pointer handlers (each its own branch) — one expected conflict there,
resolved by keeping BOTH sides. No other shared function. Neither lane touches the other's files.

## Z11. Non-goals

No change to either renderer's output (R7 is the proof): no new primitive, no new op, no new
document field, no change to `route`/auto-layout, no change to the panel's row map, no CSS change to
`.ck-bar`'s layout beyond the one new button, no change to `desktop/plugin.js`, no new fixture (the
v1.7 fixture is the newest) and no new `baseline-*.json`.

## Z12. Delivery

Lane branches merge into `feat/v1.8-draw-perf`; the gate runs on the merged head; the vendor copy of
`board_editor.js`/`boardkit.py` is copied into the `chat-charts` skill in the SAME command as the
commit that touches `vendor/`; then an MR to `main` (`main` is protected — branch + MR, never a
direct push) with the W0-vs-merged probe table, the gate numbers and the limits in the body.

## Z13. Integration record (orchestrator)

Lanes: R = `weatherstore` on `wt/t_df50812d` (`9cd6a39`, +326/−27, one file), S = `gitlab-developer` on
`wt/t_602283c3` (`8c57ba1`, +257/−13, four files). S's S4/S7 were amended mid-flight (the user's two
clarifications: the locked line is the DEFAULT and Shift frees it; the lock is the 45° step) — landed
as a board comment plus a replaced card body, and the merged head proves the amendment took
(`straightdraw` true, and the gate's own three drags: 45° step, vertical, Shift-free).

One conflict, in `pointerup` (S's straight-commit branch against R's `drawLive()` renames) plus the two
self-test blocks: both sides kept, no function defined twice (`test_editor.py`'s duplicate check).

BEFORE/AFTER, measured INSIDE the page (20 synthetic moves in one burst, the same fixture, the
installed pre-fix page against the merged head; a CDP round trip costs ~16 ms here on its own, which
is why the per-move number from `probe_repaint.py` cannot show a page-side win):

| 20-move burst | before (installed, pre-fix) | after (merged head) |
|---|---|---|
| line stroke, page work | 67.2 ms | **33.6 ms** |
| line stroke, host childList mutations | 21 | **0** |
| node drag, page work | 57.1 ms | **8.2 ms** |
| node drag, host childList mutations | 21 | **0** |
| the `<svg>` element survived | no | **yes** |

The gate's own check was fixed once, by the orchestrator, on the first merged-head run: it compared
`host.innerHTML` including the CHROME, and a synchronous burst of pointer events legitimately leaves
a `requestAnimationFrame` (the chrome refresh) pending, so the resize handles still sat where the item
had been. The chrome is rebuilt from the model on every draw by construction and cannot drift; the
SCENE is what a patch writes by hand, so the scene is what must equal a full rebuild's. That is the
check as shipped (commit `423d85c`) — and the orchestrator's commits are as reviewable as the lanes'.


## Z14. Round 3 — the verifier's findings on the merged head (and their dispositions)

The independent verifier gated `97a5092` (merged to `main` as `a738623`) with real CDP input and scratch
mutants, and returned four findings. Its verdict line: *round 2 — findings 4 (CRITICAL 1, MAJOR 2,
MINOR 1) | new-actionable 4 | repeat 0*. All four are accepted; the routes are below.

### F1 (CRITICAL) — the STRAIGHT tool's drag rebuilds the whole scene on every move

`vendor/board_editor.js`, the straight-mode `pointermove` branch, calls `render()` — and `render()` is
lane R's FULL path (dirty flag + rebuild), not `drawLive()`. Reproduced with real
`Input.dispatchMouseEvent`: during all five real straight drags the preview exists and the `<svg>`
object survives, but `kept=false` — `<defs>` and `.ck-grid` are already disconnected after the FIRST
move, including the 2-unit stroke that cancels. That is W0's visual-shake class, alive in the mode the
user asked for, and it is the other half of "shaking is still here".

Why the Z9 gate missed it: `gesture_churn` drove LINE mode and a node drag, and checked the straight
tool only for the committed geometry AFTER the release. The newest mode's live path was never driven.

**Disposition.** Fix lane `gitlab-developer` (the lane that owns the straight tool): the straight drag
refreshes chrome only (`drawLive()`), the draft preview follows the pointer, and a canceled stroke
leaves the scene untouched. The harness (`tests/test_editor.py`, orchestrator) gains a live straight
drag and a canceled stroke to both the identity and the churn checks — a gate that does not drive the
newest mode is the gate that misses it.

### F2 (MAJOR) — `docs/fixture-v1.7.json` is not pinned in `docs/baseline-v1.7.json`

`tests/test_render.py` asserts that `baseline-v1.7.json` holds only the six PRE-v1.7 fixtures, so the
v1.7 fixture (a shipped document) has no recorded SHA-256 for either half: a future shared change to
its picture passes the pinned-hash check. The current output does match `ae73151` in both halves — the
hole is in the coverage, not the picture. **Disposition: the orchestrator's files** (`docs/**`,
`scripts/baseline_svg.py`, `tests/test_render.py`): pin the pre-wave hashes for every shipped fixture,
and derive the coverage from `docs/fixture-*.json` rather than from a list someone remembers to edit.

### F3 (MAJOR) — the Z9 churn budget is printed, never asserted

Z9 asks for `<= 2` host childList mutations per gesture; the check observes with `subtree: true`
(which counts the deliberate chrome refreshes inside the `<svg>`) and only prints 65/66. A number
nobody asserts is not a gate. **Disposition: the orchestrator's file** — count the HOST's own direct
childList records (no subtree), assert the budget, and keep the identity + drift checks as the sharp
instruments they are.

### F4 (MINOR) — the fallback bar's straight button has no glyph

`vendor/board_editor.js`'s fallback builder emits `<button data-tool="straight">straight</button>`
(text only) where S1 asks for the same 16×16 two-end-dot glyph the page's toolbar carries.
**Disposition: fix lane `gitlab-developer`**, with the rest of the straight tool's surface: assert the
rendered fallback DOM, not the source.

### Non-findings the verifier rederived (recorded so round 3 does not redo them)

Alternate probe's four scene gestures (note drag, group drag, multi-selection drag, resize of a 0×0
auto-sized table) all reported `moved=true`, `svgSame=true`, `kept=true`, `drift=true` **while live**.
The scratch `patchDragged` mutant (a stale anchor in the node patch) reds `livepatch` and the external
drift check. A `constrainStep45` always-free mutant reds `straightdraw` plus two committed-geometry
checks. R7: base vs merged output is byte-identical on all seven fixtures, six pinned hashes matching.
S1/S6/S7 pass at a measured 440 px viewport. Alt-click vertex insertion and the re-anchor drop rebuild
by design (immediate model writes, allowed by R3), and the lane-S 26→24 px icon-width deviation is
necessary at 440 px.
