# SPEC v1.4 — panel hygiene, split counts, tables, resize/guides/arrange, text formatting

Status: **frozen for the v1.4 swarm**. Four lanes build against this file in parallel; the
orchestrator merges, dispositions and gates. Requirement ids (`X…`) are cited by the cards, the
tests, the commit messages and the verifier. If something is wrong or impossible, say so in your
lane report — do not silently invent a different field name, enum value or coordinate.

Two of these come straight from the user, and are the highest priority in this wave:

* **"hide not related attributes (which are unavailable to change) on a node"** — the panel shows
  every control for every selection today (some disabled). See X1.
* **"do not see how to input the number — I can only change the split type (row/column)"** — the
  compartment count is the number of lines in a textarea, which is not discoverable. See X2. They
  also chose a real **table** shape for rows × columns: X3.

## X0. Lane map (file ownership — do not touch another lane's files)

| Lane | Branch | Owns (only these) | Features |
|------|--------|-------------------|----------|
| **T** content | `feat/v14-content` | `vendor/boardkit.py` EXCEPT `page()`, `vendor/board_editor.js` for the draw paths only (`tableSvg`, the text-emitting functions), `tests/test_render.py` | the `table` kind and `text_size`/`bold`, mirrored in both renderers (X3, X4) |
| **P** panel | `feat/v14-panel` | `vendor/boardkit.py`'s `page()` (the panel markup only), `vendor/board_editor.js`'s `openPanel`/`syncPanel`/panel `bind`s + the `#selftest` block except lane R's two steps, and its own panel tests | hide irrelevant controls (X1), the split count control (X2), arrange tools (X7), the panel rows for T's new fields (X3/X4) |
| **R** canvas | `feat/v14-canvas` | `vendor/board_editor.js`'s pointer/geometry/overlay regions, plus the two `#selftest` steps it inserts immediately BEFORE the `report.ok = …` line | resize handles (X5), smart guides (X6) |
| **O** ops/docs | `feat/v14-ops` | `store.py`, `tools.py`, `tests/test_tools.py`, `README.md`, `plugin.yaml` | ops/validation/summary/diffs/docs for the new fields, version 1.4.0 (X8) |

`vendor/board_editor.js` is edited by **T** (draw paths), **P** (panel) and **R** (pointer/overlay) —
disjoint functions, and the split is exact:

* **T**: `tableSvg` (new), `nodeSvg`'s dispatch line for `kind === "table"`, the text-emitting
  helpers (`nodeText`/`splitParts`/`captionSvg`/`areaSvg` as they are named today) — read them
  before editing, and change ONLY how `text_size`/`bold`/table cells are emitted.
* **P**: `openPanel`, `syncPanel`, the panel `bind(...)` block, the panel-related self-test steps.
  Lane T adds no panel code; if a control is missing, say so in your report.
* **R**: the pointer handlers, `toSvgPoint`/`svgMetrics`, `render()`'s body EXCEPT the `markers()`/
  `edgeSvg()`/node-drawing calls, and the overlay functions it adds. It must not touch `openPanel`.
* `vendor/boardkit.py`: **T** owns every drawing function and the `KINDS`/`HEAD_SIZES` tuples;
  **P** owns `page()`'s panel block only (the toolbar and `_CSS` are shared: touch them only if a
  new control needs a rule, and say so).

A merge conflict inside `board_editor.js` or `boardkit.py` is expected and is the orchestrator's
job: keep both sides, never resolve by dropping a lane's work.

Orchestrator (not a lane): `docs/SPEC-v1.4-content.md` (this file), `docs/fixture-v1.4.json`,
`tests/test_editor.py`, `.github/workflows/tests.yml`, the vendor → chat-charts sync, the merge, the disposition
commit, the demo render, the MR.

Rules for every lane (unchanged from v1.2/v1.3):

* Work only in the worktree you were handed; **commit only** — never `git checkout`, `branch`,
  `merge`, `rebase`, `push`, `stash`, and never write under `~/.hermes/skills/**`.
* `vendor/**` is the only copy you edit; run `HERMES_HOME=$(mktemp -d) tests/test_vendor.py` so the
  skill drift check skips loudly.
* Behaviour with NONE of the new fields must not change — a v1.3 document renders byte-identically
  (`tests/test_render.py`, `tests/test_editor.py`, `tests/test_tools.py` stay green; the first two
  are the orchestrator's gates for your merge).
* No new imports/files beyond the ones listed; no hardcoded colors (`var(--ck-…)` only).
* One commit: `v1.4 <lane>: <what>`, body naming the `X…` ids.

## X1. The panel shows only what applies (lane P) — user request

Today every control is visible for every selection. Make the panel **selection-driven**:

The rule behind the map — apply it, do not just copy the list: **a control is shown only if its
field changes the rendered output of the primary item.** The list below is the map for today's
vocabulary (`boardkit.KINDS` also carries `caption`, `server`, `computer`, `actor` and `note` as
node kinds, and `kind: "note"` can live in `nodes` as well as in the `notes` list — cover all of
them):

* **any node kind**: text, sub (only where the renderer draws one), tone, kind, align, text size,
  bold, W, H.
* **`box` / `pill` only**: parts + split + the count control (X2). Both renderers read `parts` for
  those two kinds alone (`boardkit.py` guards it), so no other kind may show them.
* **`caption`, `server`, `computer`, `actor`, icon kinds, `kind: "note"` in `nodes`**: everything
  above except parts/split/count. A caption draws text only — still show text/tone/kind/size/bold.
* **a note in the `notes` list** (selection type `note`): text, tone, W, H, text size, bold.
* **edge**: label (or whatever the panel calls the edge label today), tone, style, route, start,
  end, from side, to side, head size.
* **table**: tone, kind, align, rows, cols, cells + its count control, header, W, H.
* **area** (a node of `kind: "area"`): title text, tone, align, text size, bold, W, H.
* **group**: no field editing (as in v1.3) — see the multi-selection rule for arrange.
* **multi-selection**: with 2+ items selected the panel shows the hint line `"N items selected"`
  plus the arrange row (X7) even when the primary is a group (that is the only way to reach
  arrange); field rows follow the primary as above.
* A control that does not apply is **hidden** (`hidden` attribute / `display:none` on its row), not
  merely disabled. Keep the row markup in `page()` (one row per control, each with a stable id) so
  the map is checkable: the orchestrator's gate asserts that selecting a node hides `ck-start` and
  friends.
* A self-test step named exactly `panelhide` proves it: select a node → the edge-only rows are
  hidden and the node rows are not; select an edge → the reverse; select a note → the node-only
  rows are hidden.

## X2. The split count is explicit (lane P) — user request

`parts` stays one compartment per line (the document format does not change), but the pane must make
the count obvious and controllable:

* `split` keeps its `rows|columns` select and its label must read `split` with a hint that it is a
  direction (`rows`), not a count.
* A **stepper**: `<button data-act="part-add">+</button><span id="ck-parts-count">3 parts</span>
  <button data-act="part-del">−</button>` beside the textarea. `+` appends a line `part <N>` to the
  textarea value (the model drops blank parts, so a placeholder is what makes the compartment
  exist), `−` removes the last line; both then behave exactly as typing in the field does.
* A live hint under the field: `<span id="ck-parts-hint">one part per line · 3 parts</span>`,
  refreshed as the field changes and whenever the panel opens.
* **Lossless by index.** The existing typing handler keeps a part's `sub` only when the line COUNT is
  unchanged, so a count change silently erases every subtitle (`board_editor.js` rebuilds parts as
  `{text}`). That is a document-data bug, not just a stepper bug: an edit that changes the count must
  preserve `{text, sub}` at every unchanged index, `+` appends one part, `−` drops the last, and
  undo restores what was there before. Subtitles are rendered data (`_split_svg`) — losing them
  silently is not acceptable.
* A self-test step named exactly `splitstep`: a split box with 3 parts shows `3 parts`, `+` makes it
  4 parts in both the field and the model, `−` returns it to 3.

## X3. A table shape (lanes T and P; mirrored — parity matters)

**X3.1** New node kind `"table"` with:
`rows` (int ≥ 1, default **2**), `cols` (int ≥ 1, default **2**), `cells` (a flat row-major list of
strings, normalised to exactly `rows * cols` entries: missing ones become `""`, extras are dropped),
`header` (bool, default false — the FIRST row is the header: its cells are drawn bold and a divider
separates it from the body, so the header is visually distinct without a second fill rect).
`kind: "table"` joins `boardkit.KINDS` (the panel's picker iterates it, so the shape becomes
selectable by construction).

**X3.2** Empty cells are LEGAL and drawn as an empty compartment (this is the difference from split
box `parts`, where blank entries are dropped — do not "fix" that rule). The table's own `text` is
NOT drawn (the header row carries that role); `sub` is not drawn either.

**X3.3** Geometry, pinned. **One effective size, every reader**: a table's `w`/`h` that are absent
OR not positive (the fixture writes `w: 0, h: 0` to exercise exactly this) mean "auto", and the
resolved size must come from a SINGLE helper per side (`table_size`/`nodeSize`) used by *every*
consumer — drawing, edge anchors, hit tests, marquee bounds, resize handles, guides and the undo
geometry. Auto-sizing only inside the table's own draw function would leave the visible table
disagreeing with its anchors, handles and guides (the fixture's `tbl` renders 180×100 while its
stored w/h are 0, so a raw-dimension reader would anchor an edge at its top-left corner). Ops and
panel writers write resolved numbers, never 0.
* auto-size: `w = 90 * cols`, `h = (header ? 28 : 0) + 24 * rows`; `header_h = header ? 28 : 0`,
  `cell_w = w / cols`, `cell_h = (h - header_h) / rows`, and cell (r, c) occupies
  `x = c * cell_w … (c+1) * cell_w`, `y = header_h + r * cell_h … header_h + (r+1) * cell_h` — row 0
  is the header band when `header` is true and the first body row otherwise (never a blank band);
* dividers: horizontal lines at `y = header_h + i * cell_h` for `i` in 1..rows-1 **plus the header
  boundary at `y = header_h` when `header` is true** (a 3×2 header table therefore draws three
  horizontal lines: 28, 52, 76), and `cols - 1` vertical lines at `x = j * cell_w` (spanning the full
  height, header included), 1px, `stroke="var(--ck-tone-<tone>-line)"` — the same style split boxes
  use;
* cell text: font 12, weight 600 (700 for the header row), drawn inside its own band as pinned
  above, `fill="var(--ck-fg)"`, wrapped at
  `cell_w - 12` (the same greedy `wrap` both sides already use), centred horizontally in the cell
  (`text-anchor="middle"`) and vertically within its row; the cell text starts at the same y as a
  split-box part would (pin: `cell_top + (cell_h - block) / 2 + 12`, `block = lines * 14` — read the
  split-box code and mirror its constants instead of inventing new ones, and say in your report
  which constants you used);
* `text_size` and `bold` do NOT apply to table cells (a table is its own thing).

**X3.4** Panel (P): rows/cols steppers (`+`/`−` for each, ids `ck-rows`, `ck-cols` as number inputs
plus `data-act="rows-add"|"rows-del"|"cols-add"|"cols-del"`), a `cells` textarea (one cell per line,
row-major, EMPTY LINES ALLOWED — do not filter them here), a `header` checkbox, and a live hint
`"4 cells (2 × 2) · one per line"`. Changing `rows`/`cols` resizes `cells` without losing text
(pad with `""` / truncate the tail), which is the same normalisation X3.1 requires.

**X3.5** `tests/test_render.py` (lane T): assert the divider count and positions for a 3×2 table with
a header, that an empty cell emits no `<text>`, that the header row is bold over the tone fill, and
that a table without `rows`/`cols` uses 2×2. Plus a table in `docs/fixture-v1.4.json` (the
orchestrator writes the fixture; render it, do not edit it).

## X4. Text size and bold (lane T; mirrored)

**X4.1** New optional node fields: `text_size` ∈ `"small" | "default" | "large"` (absent =
`"default"`) and `bold` (bool, absent = false). They apply to the node's own `text` only — including
a split box's HEADER text, an `area`'s title and an icon kind's caption — never to `sub`, part or
cell texts.

**X4.2** Pinned sizes: `small` = **10**, `default` = **the size that function uses today**,
`large` = **17**. `default` and absent must be byte-identical to v1.3 output, so read the current
constant and keep it (do not "tidy" it). Wrapping must use the effective size on BOTH sides (a
larger font wraps earlier — that is the point), so the wrap call takes the size as an argument.

**X4.3** `bold: true` must be an override, not a rewrite of the common case: it emits
`font-weight="700"` for the node's own text **whatever weight that path would otherwise emit** — the
600 of a box title, the 400 of an icon caption (`boardkit.py`/`board_editor.js` emit 400 there), and
the caption kind that emits no weight attribute at all (add it then). Absent/false emits exactly
what v1.3 emits, byte for byte. A literal "turn 600 into 700" rule would make `bold` a silent no-op
on icons and captions.

**X4.4** An unknown `text_size` is treated as `default` and never rendered raw.

## X5. Resize handles (lane R, editor only — the document only ever gets new x/y/w/h)

* A **single** selected node/note/group draws 8 handles (`class="ck-handle"`,
  `data-handle="nw|n|ne|e|se|s|sw|w"`, 8×8 px, `fill="var(--ck-1)"`, drawn above everything except
  the marquee). A multi-selection draws none.
* Dragging a handle resizes that item: the opposite edge stays put, the minimum size is **40 × 28**
  (never smaller), the delta is grid-snapped when `snap` is not false (snap the resulting edge
  position), and a resize writes one undo snapshot (start, not every move).
* Handles are hidden while the marquee is active and during connect mode.
* Cursor affordance: `cursor: nwse-resize | nesw-resize | ns-resize | ew-resize` per handle.
* Self-test step named exactly `resize`: drag the `se` handle of a known node and assert its w/h grew
  by the dragged delta (snap off for the step), that x/y did not move, and that dragging `nw` moves
  x/y and shrinks w/h while the opposite corner stays fixed.

## X6. Smart guides (lane R, editor only)

* While dragging (a single item or a multi-selection — use the primary), compare its
  left/right/h-centre against every other node, note and group, and its top/bottom/v-centre
  likewise. A match is within **6 document px**.
* For each axis take the smallest matching delta. Draw a thin dashed `var(--ck-1)` line for each
  match (`<line class="ck-guide" …>`, 1px, `stroke-dasharray="4 3"`), spanning the two aligned
  items, drawn above the board but below the marquee. Clear them on pointerup.
* When `snap` is not false, the match **wins over the grid rounding on that axis**: nudge the
  dragged position by the matched delta instead of rounding it to the grid. With `snap: false` the
  guides still draw but nothing is nudged.
* A guide that matches an item to itself, or to an item already inside the dragged selection, is not
  a guide.
* Self-test step named exactly `guides`, in two halves that cannot contradict each other:
  * **snap OFF**, dragging to within ~2 px of another node's left edge: a `.ck-guide` line exists
    mid-drag, the position after the drop is **unchanged** (a near miss must not be nudged — no
    nudging with snap off), and the guides are gone after the drop;
  * **snap ON**, dragged to the same deliberately off-grid near miss: the two left edges are then
    **exactly equal** after the drop, which is what proves the guide beat the grid rounding.

## X7. Arrange tools (lane P, editor only)

* **Eligibility counts geometric items only** — nodes, notes and groups, the things that have a box.
  Selected edges never count and are never moved (they follow their endpoints); a selection of two
  edges alone shows no arrange row. With 2+ geometric items the panel shows the `arrange` row
  (`data-act="align-left"|"align-h-centre"|"align-right"|"align-top"|"align-v-middle"|"align-bottom"`),
  with 3+ also `data-act="spread-h"|"spread-v"`. Because a group primary normally means "no panel",
  the arrange row must still be reachable then (X1's multi-selection rule).
* Reference per operation, and what moves:
  * `align-left` → target `x = min(x)`; every item whose `x` differs moves; `align-right` →
    `max(x + w)`; `align-h-centre` → the centre of the selection's bounding box;
  * `align-top`/`align-bottom`/`align-v-middle` are the same on `y`/`h`;
  * spread sorts by the axis, keeps the first and the last exactly where they are and equalises the
    **gaps** between consecutive boxes (3+ items, no overlap check).
* Every arrange action is one undo snapshot, and edges follow their endpoints as usual.
* Self-test step named exactly `arrange`: three nodes with unequal `x`/`y` — `align-top` makes all
  three `y` equal and moves only those that differed; `align-left` on unequal `x` makes the leftmost
  stay and the others come to it; `spread-h` equalises the two gaps within 1 px; a selection that
  includes an edge aligns the boxes and leaves the edge following its endpoints.

## X8. Ops, tool surface and docs (lane O)

* `add_node`/`update_node` accept `rows`, `cols`, `cells`, `header`, `text_size`, `bold`;
  `kind: "table"` is accepted by the plugin's own kind vocabulary in `store.py`.
  Define the enums there (`TEXT_SIZES = ("small", "default", "large")`), never import them from
  `boardkit`; write the fields onto the dict the library's builders return.
* **A table needs no `text`**: `store.py` requires non-empty `text` unless `parts` are present, so
  `add_node(kind="table", rows=3, cols=2)` and the same with `text: ""` both raise today, and the box
  builder would answer 190×72. Establish the table's own defaults (rows/cols/cells/header, and the
  X3.3 effective size, not the box defaults) BEFORE the text requirement and before normalisation,
  and exercise it through `board_apply` — a textless, headerless table must be writable.
* Validation: `rows`/`cols` are integers ≥ 1 (`ValueError` otherwise, naming the limit),
  `text_size` must be in `TEXT_SIZES`, `header`/`bold` must be booleans, `cells` is normalised to
  exactly `rows * cols` strings (pad `""` / truncate) — the same rule as X3.1, so a document written
  by the tool half and one written by the pane agree.
* `OPS_HELP`, `_summary` (rows/cols/header/text_size/bold per node) and `diff_boards`
  (`table 3x2 cells changed`, `text size large`, `bold on`) as usual, still emitting NOTHING for a
  re-save that changed nothing.
* `README.md`: a `## Content and layout (v1.4)` section — the panel hygiene, the split count, the
  table, text size/bold, resize, guides, arrange — plus what the new gate covers and the
  limitations. Do not rewrite existing sections.
* `plugin.yaml`: `version: 1.4.0`.

## X9. Non-goals (state them; do not build them)

Images/asset nodes, viewport persistence, per-cell/part/sub text formatting, rotation, lock/hide,
merged/spanning table cells, cell-level tone or alignment, column resizing, marquee resize,
multi-column table sorting, touch gestures, cross-tab clipboard, a second toolbar row.

## X10. Gates

```bash
PY=$(ls -d ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
HERMES_HOME=$(mktemp -d) $PY tests/test_vendor.py     # lanes
node --check vendor/board_editor.js
node tests/pane_smoke.mjs
$PY tests/test_render.py                              # lane T
$PY tests/test_tools.py && $PY tests/test_api.py       # lane O
# lane P / lane R: the editor self-test on the v1.4 fixture (Chrome is on PATH)
$PY - <<'PYEOF'
import sys, os, tempfile
sys.path.insert(0, "vendor")
os.environ["HERMES_BOARD_DIR"] = tempfile.mkdtemp(); os.environ["HERMES_BOARD_RENDER_DIR"] = tempfile.mkdtemp()
import boardkit
b = boardkit.load("docs/fixture-v1.4.json")
boardkit.report("/tmp/fx14.html", b, bridge=True)
print(boardkit.selftest("/tmp/fx14.html"))
PYEOF
```
The orchestrator runs `tests/test_editor.py` (cross-renderer parity on both fixtures plus the panel
map, the table and text-format rules it cannot see) after the merge, and syncs `vendor/` into the
chat-charts skill.

## Dispositions from the v1.4 reviewer gate (round 1: 4 MAJOR + 1 MINOR, none dismissed)

| # | Finding | Disposition |
|---|---------|-------------|
| 14 | MAJOR: a 3×2 header table drew row 0 **under** an empty 28px band (row-0 text at y=45, height 100) and the spec pinned invented font numbers (12/14) where both renderers use their own (12.5/15) | Fixed here, and the pin restated: `rows` counts EVERY row and row 0 IS the header band, so `h = (header ? 28 : 24) + 24 * (rows - 1)` — 76 for a 3×2 header table, with interior boundaries at the cumulative row ends (28, 52) and no empty band. Both renderers, `store.table_size`, `tests/test_render.py`, `tests/test_tools.py` and the orchestrator's gate follow; the gate now asserts the header text at y = 19 in row 0's own band, the 76 height, and `font-size="12.5"` read from the code rather than retyped. |
| 15 | MAJOR: `ck-board-replace` normalised nothing, so an agent update or an import that omitted a list crashed the pane and left the stale picture on screen | Fixed here: every replacement goes through one `replaceState()` that normalises the four lists, materialises tables and resets the transient drag state. A new self-test step `replace` replaces the board with a document that omits `notes`/`groups`/`edges` and asserts it rendered — and a mutation that skips the normalise makes exactly that step fail. |
| 16 | MINOR: `store.apply_ops` accepted `header: "yes"`, `header: 1`, `bold: "true"` and coerced them | Fixed here: `_norm_bool` takes real booleans only and names the field in the error; a spelling that a human meant but the format does not define is refused rather than silently applied. |
| 17 | MAJOR (two findings): the panel map disagreed with the renderer for `kind: "note"` in `nodes` (and offered a table `align` that changed nothing), and a note in `notes` could not use the `text_size`/`bold` X4 promised it | **Deferred to v1.5 by design**, where the panel and the note paths are rewritten: v1.5's Y1.5 makes the map mechanical (a field is visible for a kind exactly when changing it changes the rendered SVG, enforced by a `panelfields` self-test step) and Y4.7 makes both note paths render the formatting. Recorded here so the v1.4 head is not claimed to be fully gated. |

## Dispositions from the v1.5 gap pass (round 1) that turned out to be v1.4 defects

| # | Finding | Disposition |
|---|---------|-------------|
| 18 | MAJOR: `boxOf()` kept its own copy of the table auto-size formula, drifted to the pre-fix numbers, so a table with `w`/`h` = 0 was selected, dragged, marqueed and handled by a box 24 units too tall — and the editor's `resize` step asserted the same stale 100, so the gate agreed with the bug | Fixed here: `boxOf` now resolves through the one `nodeSize` helper (the draw path's), and the `resize` step asserts 76 and the handles sitting on the drawn box. This is the "one size helper per side, used by every reader" rule, which v1.4 had broken in the JS. |
| 19 | MAJOR: a table given an explicitly taller `h` left an orphan region below its last row — the bands were fixed at 28/24/24 whatever the box said | Fixed here: the bands FILL the effective height — row `r` is `h * nominal[r] / sum(nominal)` with `nominal` 28 for the header row and 24 otherwise. At the auto height the scale is 1 and the bands are exactly 28/24/24, so no existing picture changes. Gated on an explicit 152-tall 3×2 header table (boundaries at 56 and 104). |

## Dispositions after the merge (found by the orchestrator's own gate)

| # | Finding | Disposition |
|---|---------|-------------|
| 12 | The v1.2 `dragEvents` self-test asserted "both axes are grid multiples" after a drop, which X6 makes false: an axis that matches a guide lands exactly on that alignment, deliberately off-grid. Each lane passed alone (on the v1.3 fixture the match happens to be exact, delta 0) and the step only failed on the **v1.4** fixture, where the dragged table's bottom anchor is 2px from another table's — a cross-wave contradiction, not a lane defect. | Fixed on the integration branch: the step now accepts an axis that is grid-aligned **or** exactly on a matched neighbour anchor (`|delta| < 0.001`), which is what both rules together promise. Verified by mutation (disabling snapping on the move and drop paths makes `attach`, `groupmove` and `guides` fail) and the step passes on all three fixtures. |
| 13 | The gate could not tell a lane's own pass from a cross-lane interaction: P and R each passed `dragEvents` alone but failed it merged. | Not a code fix: recorded so the next wave's merge runs the editor self-test **after** every merge, on the newest fixture, not just per lane. A per-lane green is not evidence about the merged tree. |

## Dispositions — independent gap pass round 1 (8 MAJOR, all accepted)

| # | Finding | Disposition |
|---|---------|-------------|
| 1 | Table size had three meanings (absent / 0 / literal), and only the draw function would have auto-sized | Accepted: X3.3 pins "absent or non-positive = auto" and ONE effective-size helper per side used by every reader (drawing, anchors, hit tests, marquee, handles, guides); ops/panel writers write resolved numbers. The gate asserts the fixture's `tbl` (stored 0×0) renders 180×100 AND that an edge anchored to its bottom side starts at the effective bottom midpoint, which a raw-dimension reader cannot produce. |
| 2 | `add_node(kind="table")` raises `missing text` and would have inherited the box builder's 190×72 | Accepted, lane O: textless tables are legal, table defaults and the effective size apply before the text requirement and normalisation, and a test drives it through `board_apply` (not just a hand-written fixture). |
| 3 | The header band's geometry was ambiguous (blank 28px band with no divider under it) | Accepted: X3.1/X3.3 now pin `header_h`/`cell_w`/`cell_h`, that row 0 IS the header band (never a blank band), and a divider at the header boundary — so the 3×2 header table draws three horizontal lines (28, 52, 76), which is what the gate asserts. |
| 4 | The panel map missed `caption`/`server`/`computer`/`actor`/`note`, and offered parts for kinds that never draw them | Accepted: X1 states the rule (a control is shown only if its field changes the rendered output), parts/split/count are `box`/`pill` only, both note representations are covered, and `panelhide` must include a diamond, both note representations and an icon/caption kind. |
| 5 | `bold` was specified so it could never affect icon captions (weight 400) or the caption kind (no weight) | Accepted: X4.3 makes bold an override (400→700, 600→700, and adds the attribute where a path emits none) while absent/false stays byte-identical. Tested true/false/absent on an icon and a caption in both renderers. |
| 6 | The split stepper (and any count-changing edit) silently deleted every part's `sub` | Accepted, lane P: the count change must be index-preserving and lossless, with a test that `+`, `−` and undo keep the `sub` of every surviving part. This is a document-data bug the existing handler has always had. |
| 7 | The `guides` step demanded exact edge equality in the same breath as snap-off, which forbids nudging | Accepted: the step now splits — snap off asserts the near miss is unchanged and the guide clears; snap on (deliberately off-grid) asserts exact equality, which proves guide-over-grid precedence. |
| 8 | Arrange was promised for selections that have no boxes (two edges) or no panel (group primary) | Accepted: eligibility counts geometric items only (edges follow endpoints and never count), the multi-selection panel gives a group primary a hint + arrange row so arrange is reachable, and each operation's reference and the set of items that move are pinned, with tests for a group-primary mix, an edge-containing selection and `align-left` on unequal `x`. |
