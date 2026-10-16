# SPEC v1.5 — panel control surface, table v2 (widths, alignment, colour)

Status: **frozen for the v1.5 swarm**. Three lanes build against this file in parallel; the
orchestrator merges, dispositions and gates. Requirement ids (`Y…`) are cited by the cards, the
tests, the commit messages and the verifier. If something here is wrong or impossible, say so in
your lane report — do not silently invent a different field name, enum value or coordinate.

Where this comes from (the user's words): *"improve tool panel, make it more user friendly and
better UX"*, *"some switchers we could replace by buttons group with icons"*, *"the color select
could be replaced to inline buttons colored"*, and for tables *"per-column widths and per-cell
alignment"* … *"color for entire row/column and cell"*.

## Y0. Lane map (file ownership — do not touch another lane's files)

| Lane | Branch | Owns (only these) | Features |
|------|--------|-------------------|----------|
| **P** panel | `feat/v15-panel` | `vendor/boardkit.py`'s `page()` (the panel markup and its CSS), and in `vendor/board_editor.js` the panel functions (`openPanel`/`syncPanel`/the panel `bind`s and click handlers) plus the `#selftest` steps it adds | sections (Y1), inline controls (Y2), hints and validation (Y3), the table v2 controls (Y5) |
| **T** table | `feat/v15-table` | `vendor/boardkit.py`'s drawing functions and vocabulary tuples, `vendor/board_editor.js`'s draw paths (`tableSvg` and the helpers it calls), `tests/test_render.py` | table v2 rendering (Y4) |
| **O** ops/docs | `feat/v15-ops` | `store.py`, `tools.py`, `tests/test_tools.py`, `README.md`, `plugin.yaml` | table v2 through the ops and the agent surface, docs, version 1.5.0 (Y6) |

Shared surfaces, so the split is exact:

* `vendor/boardkit.py`: **T** owns every `def _…` drawing function plus the vocabulary tuples
  (`KINDS`, `TONES` and friends — T may only EXTEND a tuple, never reorder it); **P** owns `page()`
  and may add CSS rules there. If you need a new colour variable or a new drawing helper, ask for it
  in your report instead of editing the other half.
* `vendor/board_editor.js`: **T** owns `tableSvg` and its callees; **P** owns the panel. **R's
  regions from v1.4 (pointer handlers, `render()`'s body, resize/guides) are owned by nobody this
  wave — read them, do not change their behaviour.** A merge conflict is expected and is the
  orchestrator's job: keep both sides, never resolve by dropping a lane's work.
* The orchestrator (not a lane) owns `docs/SPEC-v1.5-panel.md` (this file),
  `docs/fixture-v1.5.json`, `tests/test_editor.py`, `.github/workflows/tests.yml`, the vendor → chat-charts sync,
  the merge, the disposition commit, the demo render and the MR.

Rules for every lane:

* Work only in the worktree you were handed; **commit only** — never `git checkout`, `branch`,
  `merge`, `rebase`, `push`, `stash`, and never write under `~/.hermes/skills/**`.
* `vendor/**` is the only copy you edit. Run `HERMES_HOME=$(mktemp -d) tests/test_vendor.py` so the
  skill drift check skips loudly.
* **A v1.4 document must behave and render byte-identically** when none of the new fields are used.
  `tests/test_render.py`, `tests/test_tools.py` and the orchestrator's `tests/test_editor.py` are the
  gates; `node --check vendor/board_editor.js` and `node tests/pane_smoke.mjs` must pass.
* No hardcoded colours (`var(--ck-…)` only), no new files, no new dependencies.
* Every top-level function name in either renderer must stay UNIQUE — a second definition of the
  same name does not shadow in JS, it wins (this cost us a wave; there is now a gate).
* One commit: `v1.5 <lane>: <what>`, body naming the `Y…` ids.

## Y1. The panel is a set of collapsible sections (lane P)

* The rows are grouped into `<details class="ck-sec" data-section="…">` with a `<summary>` heading,
  in this order: `content` (text, sub, size, bold), `shape` (kind, tone), `layout` (w, h, align),
  `parts` (parts, split, the count control), `table` (the Y5 rows), `edge` (the edge rows),
  `arrange` (the Y7 row), `cell` (the Y5 cell rows).
* Default state: every section `open`. Collapsing is a session-only editor state — **never written
  to the document** and never persisted.
* A section is hidden entirely when it has no visible row for the primary selection (so X1's
  selection-driven visibility still decides what exists, and Y1 only groups it). A section that is
  hidden must not be reachable by keyboard either.
* Self-test step named exactly `sections`: with a node selected, `content`/`shape`/`layout` exist and
  `edge`/`table`/`cell` are hidden; with an edge selected, `edge` exists and `content`/`shape` are
  hidden; collapsing `shape` hides its swatch row and expanding it brings it back.

## Y2. Inline controls instead of dropdowns (lane P)

Replace these `<select>`s with inline button groups. The GROUP carries the id the old select had
(so `tests/test_editor.py`'s control surface keeps meaning) and every button carries a stable
`data-` attribute:

| row | control | buttons |
|-----|---------|---------|
| tone | `<div class="ck-swrow" id="ck-tone" role="group">` | one `<button class="ck-sw" data-tone="<tone>">` per tone in `boardkit.TONES`, filled with that tone's own variable, `aria-pressed` on the current one, `aria-label="<tone>"` and `title="<tone>"` |
| kind | `<div class="ck-kindgrid" id="ck-kind" role="group">` | one `<button class="ck-shape" data-kind="<kind>">` per kind in `boardkit.KINDS`, each with a small (≤16×16) inline SVG glyph that is distinguishable from the others, `aria-label`/`title` = the kind name, `aria-pressed` on the current one |
| align | `<div class="ck-group" id="ck-align">` | `data-align="left"`/`"center"` — exactly the values the renderer honours for a node — plus `"right"` for a table cell and, in the **cell** section only, `data-align="inherit"` (a dash glyph) which is a member of the group like any other: pressing it clears the cell's own align so the cell follows the table's (Y4.5). Clearing must be reachable, or a cell could never go back to inheriting from a table whose own align is `left` |
| split | `<div class="ck-group" id="ck-split">` | `data-split="rows"`/`"columns"` |
| text size | `<div class="ck-group" id="ck-text-size">` | `data-size="small"`/`"default"`/`"large"` |
| head size | `<div class="ck-group" id="ck-head-size">` | `data-head="small"`/`"medium"`/`"large"` |
| start / end | `<div class="ck-headgrid" id="ck-start">` / `id="ck-end"` | `data-end="<head>"` per head the renderer knows, each drawn as its own marker glyph |

Keep real `<input>`/`<textarea>`/`<input type="number">` for free text and numbers (text, sub, parts,
cells, w, h, rows, cols, col widths), and keep a `<select>` ONLY where a lane can justify it in its
report. Clicking a button applies the value exactly as the old select did (one undo snapshot, the
same model field), and `syncPanel` must show the current value by setting `aria-pressed` on exactly
one button per group.

## Y3. Hints and validation (lane P)

* Every editable control has a hint element `<span class="ck-hint" data-hint-for="<control id>">`
  saying in a few words what the field does (`w` → `width in board units`, `cells` → `one cell per
  line, empty lines kept`, `parts` → `one part per line · 3 parts`). The hint is visible whenever the
  control is.
* An invalid value does not reach the model. The row is marked `aria-invalid="true"`, the hint
  element is replaced by the reason (`rows` → `1 or more`; `w`/`h` → `40 or more` / `28 or more`,
  matching the resize minimum; a bad number → `a number, please`), and the previous model value
  stays. A valid value clears the mark and restores the hint.
* Self-test step named exactly `validate`: set `rows` to `0` → the model's `rows` is unchanged, the
  row is `aria-invalid`, and the hint shows a reason; then set `3` → the model has 3 rows, no
  `aria-invalid`, and the normal hint is back.

## Y4. Table v2 — widths, per-cell alignment and colour (lane T, mirrored; parity matters)

A v1.4 table (`cells` as strings, no new fields) must render byte-identically. On top of that:

**Y4.1 `cells` entries may be objects.** A string is exactly what it is today. An object is
`{text, tone?, align?}` where `text` may be `""` (an empty cell) — the object's keys are normalised
(surplus keys dropped, `text` stringified, `tone` must be a known tone else ignored, `align` ∈
`left|center|right` else ignored). A cell's own values win over the row/column/table defaults.

**Y4.1a Three layers, three policies — the same input must not be both normalised and rejected.**
The v1.5 gap pass found the spec asking a lenient reader, a careful pane and a strict op to agree on
invalid input, which they cannot. Pin them separately:

* **Reading a document** (the loaders, an import, a board written by hand or by an older version) is
  **lenient**: an unknown `tone`, a bad `align` or a wrong list type is treated as absent, a
  non-positive, non-numeric, non-finite or too-large-for-a-double `col_widths` entry becomes `1`, and a
  conversion that *raises* (`OverflowError` on a 310-digit JSON integer) is that same `1`. A document
  must always render.
* **The pane's writers** validate and **keep the old value** (Y3's hint + `aria-invalid`); an invalid
  value never reaches the model.
* **The ops** (`add_table`, `update_node`) **reject** with a `ValueError` naming the field — a writer
  fails loudly. So `add_table` does *not* normalise an invalid value away
  (`cells:[{text:"x",tone:"bogus"}]` is refused, not silently repaired), and the two ops give the same
  verdict for the same input. Equivalent valid input must produce the same normalised document through
  either, and through the pane.

**Y4.2 `col_widths`** — optional list of positive numbers, normalised to exactly `cols` entries
(missing/short → `1`; extra dropped; non-positive, non-numeric, **non-finite**, or **too large to be a
double at all** → `1` on read, refused by a writer per Y4.1a). Column width =
`col_widths[j] / sum(col_widths) * w`, and the vertical dividers
sit at the cumulative sums (so `[2, 1]` on a 180-wide table gives dividers at 120, and `[1, 2]` at 60) —
**computed overflow-safely**: the shares are divided by the largest of them before they are summed, so
every scaled share is ≤ 1 and the sum is ≤ `cols`. A plain `sum()` is wrong here, not merely fragile: a
pair of *finite* shares such as `[1e308, 1e308]` sums to `inf`, the ratio collapses to `0`/NaN and the
divider lands at `x = 0` (v1.5 verifier round 2, CRITICAL). Both renderers do it that way and both are
gated; for the all-1 shares of a v1.4-shaped table the scaling is a no-op, bit for bit. The reader's
normalisation must also survive a share it cannot *convert*, not only one it cannot *sum*: a JSON number
with 310 digits is a finite, positive integer, but it is not a double — Python's `float()` raises
`OverflowError` (which used to escape the reader and crash `render_svg`), and the editor's `JSON.parse`
reads that same literal as `Infinity`, so `1` is the only value both renderers can agree on (v1.5
verifier round 3, follow-up on the same CRITICAL).

**Y4.3 `row_tones` / `col_tones`** — optional lists of tone names, normalised to exactly `rows` /
`cols` entries (`null`/unknown → no tone there). A cell's background tone is
`cell.tone || row_tones[r] || col_tones[c] || the table's own tone` — that precedence, in that
order, is the spec.

**Y4.4 Painting.** The base rect **is** a tone fill — of the table's *own* tone, over the whole box, as
it has been since v1.4 (an earlier sentence here said the opposite and was simply wrong; the verifier
measured the base as `fill="var(--ck-tone-default-fill)"`). A painted-cell check therefore identifies
the base by its geometry (it covers the whole box) and compares the **overlays** by their own
row/column boxes — a swapped precedence paints the same four names, so a multiset of names cannot
falsify it — and asserts the draw order: base, overlays, dividers, text.
Every cell that ends up with a *different* tone than the table's own draws one
plain `<rect>` covering its own cell box, filled with that tone's fill variable, drawn after the
base rect and before the dividers/text. Because a squared cell fill would poke out of the base
rect's rounded corner, a table that paints any cell/row/column tone uses `rx="0"` for its base rect
(a table with no per-cell colour keeps `rx="9"`, exactly as v1.4).

**Y4.5 Cell text.** Font `FONT`, weight 600 (700 in the header row), wrapped at the cell's own width
minus 12 — **read `FONT`/`LINE` from the code (12.5 and 15 today); never retype a number into this
spec** (the first pin said 12 against the renderers' 12.5). The cell's own `align` (else the table's
`align`, else `center`) sets the anchor and the x. With `x0[c] = sum(widths[:c])` and
`widths[j] = col_widths[j] / sum(col_widths) * w`:

* a cell that still cannot fit its text — the wrap has a floor of six characters, so an extreme share
  such as `[0.1, 100]` on a 180-wide table leaves a 0.18-wide cell — **clips** it to its own box with a
  per-cell `<clipPath id="tclip-<node>-<index>">` and `clip-path` on its `<text>` elements. Emitted only
  where the text does not fit, so every table that fits is byte-identical (v1.5 verifier, MAJOR: the
  text used to draw straight through the neighbouring column). The `clip-path` is a **sibling
  attribute** of the `<text>` element, appended after a *closed* `font-weight="…"`: concatenated
  inside that quote it is not an attribute at all, Chrome computes a clip of `none` and the text
  spills — which is exactly what the v1.5 verifier's round 2 measured in the pane (MAJOR);
* `left` → `x0[c] + 6`, `text-anchor="start"`;
* `center` → `x0[c] + widths[c] / 2`, `middle`;
* `right` → `x0[c] + widths[c] - 6`, `end`.

So with `[2, 1]` on a 180-wide table the second column's **centre is 150 and its left inset is 126** —
not 135 and 96 (the equal-width mistake the v1.5 gap pass caught; the right edge, 174, agrees under
both formulas and cannot distinguish them). Equal-width columns reduce to v1.4's `cell_w * (c + 0.5)`,
so an absent `col_widths` stays byte-identical, and the vertical placement stays exactly as v1.4
computes it (Y4.6's bands).

**Y4.5a The table's own alignment is a writer field.** `align` ∈ `left|center|right` is accepted by
`add_table` (default `center`) and by `update_node` on a table, and cleared by an absent key; it is the
fallback for every cell that has none (Y4.5). `_norm_align` therefore accepts `right` for a table and a
note too, not only for a cell, and a table's `align` write must not be skipped by the early return the
v1.5 gap pass found in the table branch. Gate a table with no cell alignment under **each** of the three
table defaults.

**Y4.6 `rows` counts EVERY row, and row 0 IS the header band.** (Corrected after the v1.4 gate found
the first pin self-contradictory: it asked for a 100-tall 3-row header table *and* row 0 in the header
band, which puts a cell row under a 28px band that belongs to nobody.)

* row heights: row 0 is `28` when `header` is true, else `24`; rows 1.. are `24` each;
* `h = (header ? 28 : 24) + 24 * (rows - 1)` — so a 3×2 header table is **76** tall, a 1-row header
  table is **28**, a headerless 3-row table is 72. `w = 90 * cols` unchanged;
* the interior boundaries are the cumulative row ends (a 3-row header table: `y = 28` and `y = 52`),
  which is one divider between each pair of neighbouring rows — there is no separate "header
  boundary" line, and no band is left empty;
* cell text is centred in its own row's band with the renderer's own constants — **read `FONT`/`LINE`
  from the code (today 12.5 and 15); never retype a number into this spec**. The header row's cells
  are weight 700, the body's 600, wrapped at `cell_w - 12`;
* `table_size(rows, cols, header)` in `store.py` and the auto-size in both renderers use this same
  formula — the tool half and the pane must not disagree about the height of a table;
* that formula is the **auto** height. An explicit positive `h` is the effective height and the bands
  **fill it**: row `r` is `h * nominal[r] / sum(nominal)` tall, where `nominal` is 28 for the header row
  and 24 otherwise. At the auto height the scale is 1 and the bands are exactly 28/24/24 (so every
  v1.4-shaped table stays byte-identical); a taller explicit `h` stretches them and **leaves no orphan
  region below the last row**, which the first cut of the v1.4 fix did leave. Gate both renderers on an
  explicit 152-tall 3×2 header table: boundaries at 56 and 104, the last row ending at the box bottom.
`tests/test_render.py` gains the Y4 cases (unequal widths, each precedence step, the `rx` rule, the
three alignments, an object cell with an empty text, and the v1.4 byte-identity).

**Y4.7 A note in `notes` gets its text formatting too** — and this is the one place Y0's file split
does not name an owner: the JS `noteSvg` (a `notes` entry) and its Python mirror `_note_svg` belong to
lane T with the other rendering, and the notes-list panel writes belong to lane P. If T's run has
ended, the orchestrator owns the renderer change at integration. X4 promised `text_size`/`bold` for a note and
the v1.4 gate found only the `nodes` path honoured them (both note renderers ignored the fields and the
panel hid them). Both note paths render them now — at the same sizes and weights as a node's own text,
byte-identical when absent — and the panel exposes and writes them for a `notes` selection.

## Y1.5 The panel map must follow the renderer (lane P, with lane T)

The v1.4 gate found the map and the renderer out of step in both directions: a node of `kind: "note"`
draws `sub`/`align`/size/weight but showed only text/tone/W/H, while a table offered `align`, which
changed nothing in v1.4 (it does matter in v1.5, see Y4.5 — that is the only reason it stays).

* The rule is mechanical **per state, not per kind**: a field is visible exactly when, from the state
  the panel is showing, changing it changes the rendered SVG. (A field whose value the renderer reads
  but which already holds the discriminating value still counts — the step sets a different one.)
* Some rows are **state-dependent** and the map must follow the state: a split/pill with no `parts`
  draws its `sub` and does not draw a split, so in that state `sub` is visible and `split` is not; with
  `parts` it is the other way round. (The v1.5 verifier measured exactly that, and the first wording of
  this bullet had the split half backwards — the renderer and the step are right, the prose was not.)
* A self-test step named exactly `panelfields` enforces it: for every kind in `boardkit.KINDS`, and for
  both note representations, it probes a **discriminating state per field** and compares the set of
  visible panel rows against the set of fields whose change alters the rendered SVG, failing on any
  mismatch and naming the representation, the kind and the field.
* **Declared exceptions** — controls that are visible without changing the picture, which the step
  asserts are exactly these: `ck-cell-row`/`ck-cell-col` (they change which cell the cell section
  edits, not the SVG). The `col widths` inputs are **not** an exception: the pane writes them at once,
  so they change the picture — the step proved it the moment lane T's renderer landed next to lane P's
  panel, and being an exception had hidden that. Any other exception must be named in a lane's report
  and added to this list.
* A node of `kind:"note"` in `nodes` and a note in `notes` are **two representations with two
  matrices**: the first draws `sub`, `align`, `text_size`, `bold` and a shape (`kind`), the second has
  no `kind`/`sub`/`w`/`h` and, after Y4.7, does have `text_size`/`bold`. The step walks both.

## Y5. Panel controls for table v2 (lane P)

* `col widths`: one small number input per column (`id="ck-colw-<j>"`, `min="0.1"`, `step="any"` — an
  anchored `step="0.5"` makes the browser reject the default `1` as a step mismatch),
  rebuilt when `cols` changes, with a hint (`1 by default; a column is its share of the width`).
  Writing it normalises to exactly `cols` positive numbers.
* `cells`: the DISPLAY is unchanged (one cell per line, empty lines kept) and there is **no** text
  syntax (never `text | tone | align` — cell tone/align come from the cell section below). The WRITE is
  **index-preserving**: editing line `i` replaces that cell's `text` and leaves every other line exactly
  as it was, object cells included. A line that was a string stays a string; a line that was an object
  keeps its own `tone`/`align` unless the cell section clears them. Changing `rows`/`cols` remaps
  index-preserving too: a cell that survives (`row < rows && col < cols`) keeps its own object fields.
  The naive "join the whole list and write the strings back" behaviour is a defect — the v1.5 gap pass
  showed it erasing cell 1's tone when cell 2's text was edited.
* Self-test step named exactly `celledit`: give cell 2/1 a tone and an align, type an unrelated text
  edit into another line → cell 2/1 still has them; then add a column → the survivor still has them.
* `cell` section: `<input type="number" id="ck-cell-row" min="1">` and `id="ck-cell-col"` pick the
  cell being edited (clamped to the table, first cell `1/1` by default), then
  `<input id="ck-cell-text">` for its text, a `data-cell-align` group (`left|center|right`) and a
  `data-cell-tone` swatch row (including a `data-tone=""` swatch meaning "no own tone").
  Writing to the cell rewrites that `cells` entry as an object only when it needs one (tone or align
  set), and back to a plain string when both are cleared — a v1.4-shaped document must stay
  v1.4-shaped. The `data-cell-align` group is `inherit|left|center|right` with **`inherit` first and
  pressed whenever the cell has no own align** (Y2's "exactly one pressed" holds: inheritance is one of
  the choices). Gate it: on a table whose own align is `left`, give a cell `right`, then press
  `inherit` → the cell's own key is gone and its text is left-aligned.
* `row tones` / `col tones`: one swatch row each (`data-row-tone="<r>"`, `data-col-tone="<c>"`,
  including a "no tone" swatch), rebuilt when `rows`/`cols` change.
* Self-test step named exactly `cellcontrols`: on a 2×2 table, pick cell 2/1, give it tone `warn` and
  align `right` → the document's `cells[1]` is now an object with those two keys and the table's
  other cells are still plain strings; clear both → it is a plain string again; set column 1's tone
  → `col_tones[0]` is that tone.

## Y6. Table v2 through the ops and the agent surface (lane O)

* A new op **`add_table`**: `{op, id?, x?, y?, rows?, cols?, cells?, header?, col_widths?,
  row_tones?, col_tones?, tone?}` — table defaults applied before normalisation, a resolved `w`/`h`
  (never 0), and the same normalisation Y4 pins (so an agent and the pane write the same document).
  `add_node` keeps accepting `kind: "table"` with the same fields.
* `update_node` accepts every new field on a table node (cells as strings or objects, `col_widths`,
  `row_tones`, `col_tones`), validating what Y4 normalises and raising `ValueError` for a wrong TYPE
  (`col_widths` not a list, a tone not in `TONES`, an align outside the tri-state, `cells` not a
  list).
* A Python builder for direct use: `boardkit.table(id, x, y, rows, cols, cells, header=…,
  col_widths=…, row_tones=…, col_tones=…, tone=…)` returning the node dict the renderer expects.
* Summary and diff lines: `added 3x2 table n1 at (40, 40)` (as today), plus
  `table n1: 3x2 header, 4/6 cells filled, widths 2:1, 1 coloured row` and diff lines
  `column widths 2:1`, `row 2 tone warn`, `cell 2/1 align right`, `cell 2/1 tone warn` — and NOTHING
  for a re-save that changed nothing.
* The board description the tools hand the model gains one line per table:
  `table n1 3x2 header=yes filled=4/6` (find the existing description helper and extend it).
* **Booleans are booleans** (v1.4 gate finding 5): a `header`/`bold` that is not a real boolean raises
  `ValueError`, for `add_table`, `add_node` and `update_node` — `"yes"`, `"true"` and `1` are refused,
  not silently coerced. Test both the add and the update path.
* `tests/test_tools.py` covers each of the above (accepted, rejected, normalised, summary/diff, no-op
  re-save). `README.md` gains `## Panel and tables (v1.5)`; do not rewrite existing sections.
  `plugin.yaml` → `version: 1.5.0`.

## Y9. Dispositions — the v1.4 gate's findings, and where each one is fixed

The v1.4 head (`98d629f`) was gated by an independent reviewer: **fail, 4 MAJOR + 1 MINOR**. Nothing
in that report is dismissed; three of the five are fixed on the v1.4 branch itself and two are fixed
in this wave, because that is where the code they touch lives.

| # | v1.4 gate finding | Fixed where |
|---|-------------------|-------------|
| 1 | The table's header row was drawn under an empty 28px band and cells one band low (row-0 text at y=45, height 100), and the spec pinned invented font numbers (12/14) instead of the renderer's own (12.5/15) | **v1.4, now**: Y4.6 restates the geometry, both renderers and `store.table_size` use it, the gate asserts y=19 in row 0's own band, the interior boundaries at 28/52, the 76 height and `font-size="12.5"` from the code. `tests/test_render.py` and `tests/test_tools.py` updated. |
| 2 | The panel map disagreed with the renderer for `kind: "note"` in `nodes` (hiding fields that change the picture) and offered a table `align` that changed nothing | **This wave**: Y1.5's mechanical rule plus the `panelfields` self-test step, which the gate requires by name. |
| 3 | A note in `notes` could not use `text_size`/`bold` although X4 promised them: both note renderers ignored the fields and the panel hid them | **This wave**: Y4.7 (rendering) and Y5 (panel). |
| 4 | `ck-board-replace` normalised nothing, so an agent update or import omitting a list crashed the pane and left the stale picture | **v1.4, now**: every replacement goes through one `replaceState()`, and a new self-test step `replace` proves a replacement without `notes`/`groups`/`edges` renders (mutation: skipping the normalise makes exactly that step fail). |
| 5 | Ops coerced `header: "yes"`, `header: 1`, `bold: "true"` instead of rejecting them | **v1.4, now**: `_norm_bool` accepts only real booleans; Y6 pins it for the v1.5 fields too, with tests on both the add and the update path. |

The reviewer also noted `tests/test_vendor.py` skipped its drift check in its tree because the
chat-charts skill was not resolvable there; the orchestrator syncs `vendor/` into the skill and runs
that assertion with the real HOME before pushing, and it passes.

## Y10. Dispositions — the v1.5 gap pass, round 1 (14 findings: 12 MAJOR, 2 MINOR)

The frozen spec was reviewed before the lanes' work was read as a verdict. **All 14 findings are
accepted**; none is dismissed. They are spec-and-gate defects, not lane bugs, and `Y0`–`Y8` above now
carry the corrections.

| # | Finding | Disposition |
|---|---------|-------------|
| 1 | Y4.5 positioned cell text with `cell_w * c` while Y4.2 allows unequal widths → 135/96 instead of 150/126 in the narrow column, and the gate's right-edge check (174) agreed under both | Fixed in Y4.5: `x0[c] = sum(widths[:c])`, left `x0+6`, centre `x0+widths[c]/2`, right `x0+widths[c]-6`; the gate probes an aligned cell in the narrow column on both renderers |
| 2 | Y4.5 said font 12 while both renderers emit 12.5, and byte identity cannot hold at 12 | Fixed in Y4.5: read `FONT`/`LINE` from the code; the gate asserts the code's number |
| 3 | The fixed 24/28 bands accounted for the auto height but left an orphan region under an explicitly taller table | Fixed in Y4.6: the bands fill the effective height (`h * nominal[r] / sum(nominal)`), scale 1 at the auto height; gated on an explicit 152-tall table. The v1.4 branch carries the same fix in both renderers |
| 4 | "Visible exactly when changing the field changes the SVG" is not a per-kind predicate: `sub`/`split` are state-dependent, `kind` changes a note's shape, and the row/col pickers change selection, not SVG | Fixed in Y1.5: the rule is per state, the step probes a discriminating state per field, the pickers are declared exceptions, and the two note representations are walked separately |
| 5 | A cell's align could be set but never cleared, and `center` is not `inherit` when the table's own align is `left` | Fixed in Y2/Y5: `data-align="inherit"` is a member of the cell group, pressed when the cell has no own align; gated |
| 6 | The unchanged textarea write joined the whole cell list, so editing one line erased another cell's tone/align | Fixed in Y5: index-preserving writes and row/column remapping; new `celledit` step |
| 7 | The same invalid input was both normalised and rejected (`add_table` vs `update_node` vs the pane) | Fixed by Y4.1a: three layers, three policies — lenient reader, keeping pane, rejecting ops; the accepted-write equivalence is stated |
| 8 | `add_table` omitted `align`, `_norm_align` only wrote left/center, and the table update path returned before writing align | Fixed by Y4.5a: the table's own align is a writer field with the full tri-state, gated under each default |
| 9 | No lane owned the JS `noteSvg` / Python `_note_svg` that Y4.7 needs | Fixed in Y4.7: lane T (or the orchestrator at integration) for the renderers, lane P for the notes-list panel writes |
| 10 | The v1.5 overlay check captured the base rect too and compared a multiset of tone names | Fixed in Y4.4 + Y8: the base rect is not a tone fill; overlays are located by geometry with the draw order asserted |
| 11 | The gate itself demanded four divider paths and a 100-tall auto height, and the editor's `resize` step agreed with a drifted second auto-size helper | Fixed on both branches: three paths and 76, `boxOf` delegates to the single `nodeSize` helper, and the stale assertions are corrected before the gate is read as a verdict |
| 12 | Replacing the selects necessarily failed the gate's own select-based assertions | Fixed in Y8: the assertions count the button groups and their pressed state; no assertion requires a replaced select |
| 13 | The "empty object cell draws nothing" assertion was vacuous (its cell is a plain string) | Fixed in Y8: a real `{text:""}` probe cell, plus geometry-located overlays |
| 14 | `min="0.1"` with `step="0.5"` makes the browser reject the field's own default of 1 | Fixed in Y5: `step="any"` |

## Y11. Dispositions — the v1.5 verifier, round 1 (1 CRITICAL + 6 MAJOR, all accepted)

An independent reviewer gated `46c77ac` with its own probes (Chrome + Python, plus scratch mutations),
found the seven gates green, and failed the wave on these. **All seven are fixed**, each with a gate
that would catch it again; the two prose clarifications are folded into Y1.5 and Y4.4 above.

| # | Finding | Disposition |
|---|---------|-------------|
| 1 | CRITICAL: `col_widths` accepted `float("inf")` — it passes `> 0`, makes the column sum infinite, every column resolves to NaN, and the renderer dies with "cannot convert float NaN to integer" on a document the op had just written | Both layers fixed: a **writer** refuses a non-finite/non-positive/non-numeric entry with the index named, and a **reader** (`_norm_col_widths`, both renderers) turns it into `1`. Gated in `tests/test_tools.py` (inf, NaN, 0, -1, `"wide"`) and by a read-side assertion |
| 2 | MAJOR: a table's own `align: "right"` was reduced to `center` on add and ignored on update, and an invalid value was accepted | `_norm_align(value, allow_right=True)` and the table's add/update paths persist the full tri-state; an invalid value raises naming the field. Gated for all three values plus a rejection |
| 3 | MAJOR: the `boardkit.table(...)` builder Y6 promises did not exist | Added in `vendor/boardkit.py` (it runs the same `_table_grid` normalisation and writes an object cell only where the cell needs one) and gated by building a table and rendering it |
| 4 | MAJOR: the panel could not set a table's `right` default and displayed an existing one as `center` | The align group carries a `right` button that the panel shows only for a table, and the sync presses the value the renderer honours. Gated by the new `panelinputs` self-test step |
| 5 | MAJOR: `parseInt` truncated a fractional `rows`/`cols` into the model with no complaint | `integerCount()` refuses a non-integral count with `aria-invalid` and "a whole number, please", leaving the grid alone. Gated by `panelinputs` |
| 6 | MAJOR: a legal extreme share (`[0.1, 100]`) left a 0.18-unit cell whose text drew through the next column (the wrap has a floor of six characters) | A cell that cannot fit its text now **clips** it to its own box (a per-cell `clipPath` + `clip-path` on its `<text>` elements), emitted only where needed, so every table that fits stays byte-identical. Gated in `tests/test_render.py` |
| 7 | MAJOR: a node of `kind: "note"` draws its `sub`, but the field showed empty — editing it would have replaced a subtitle the user could not see | The sync fills `#ck-sub` for that kind too. Gated by `panelinputs` |

The reviewer also confirmed, with its own probes, the band scaling (14/26, 28/52, 56/104 ending exactly
at `h`), the tone precedence, six note-format combinations, v1.4 table byte-identity, and that four
scratch mutations made their intended gates fail.

## Y12. Dispositions — the v1.5 verifier, round 2 (1 CRITICAL + 4 MAJOR, all accepted)

A second independent review gated `b6fd469` with its own probes (`review-v15-r2.txt`, its Chrome +
Python probes and a mutation harness). It **refuted** the round-1 fix on the read side and on the JS
clip, and found three gates that passed when the fix they claimed to cover was removed. Its standard is
this wave's standard: **a gate that passes when the fix is removed is not a gate.** All five are fixed
and each fix now has a gate that fails on a scratch mutant of it. A third pass over that fix (the same
reviewer, `review_extra_probe.py`) then found **one more CRITICAL on the same reader** — a share whose
*conversion* overflows, not merely one whose sum does — recorded as row 6; the five above are
otherwise confirmed working.

| # | Round-2 finding | Disposition |
|---|-----------------|-------------|
| 1 | **CRITICAL** — the store/op writer was fixed, but the *renderers' own readers* were not: `boardkit._table_col_widths` and `board_editor.tableColWidths` accepted a non-finite share (an imported `"Infinity"` died with "cannot convert float NaN to integer"), and **finite** shares can overflow their sum — `[1e308, 1e308]` made the divider collapse to `M 0,0 V 48` / `M NaN,0 V 48` | Both readers normalise a non-finite, non-positive or non-numeric share to `1`; both ratios divide the shares by the largest of them before summing, so the scaled sum is ≤ `cols` and no finite input can overflow. Gated in `tests/test_render.py` (both documents, through `render_svg` **and** `boardkit.table`), by the `width_probe()` parity case and its Chrome assertions in `tests/test_editor.py`, and by the editor's `badwidth` step. Y4.2's prose corrected to state the overflow-safe form. Mutants `1a`–`1e` fail. |
| 2 | **MAJOR** — the update-align gate proved nothing: `tests/test_tools.py` asserted only `add_table`, so deleting the update path's alignment write kept it green | `tests/test_tools.py` updates an initially **unaligned** table to each of `left`/`center`/`right`, asserting the stored key **and** the rendered anchor (`start`/`middle`/`end`) on all four cells, that `center` stores nothing (the default stays absent), that `bogus` is refused naming the field, and that the refused write left the value alone. Mutant "2 update-only alignment removed" fails. |
| 3 | **MAJOR** — the right-align control's action was not proven: `panelinputs` started on a table that was already `right` and clicked `right` again, so a dead click handler passed | The step starts from `center` and separately from `left`, clicks `right`, and asserts that the model, the rendered anchor **and** the pressed button all changed; the table-only visibility of the control is asserted for a table *and* for a note. Mutant "4 align click write removed" fails. |
| 4 | **MAJOR** — the fractional-count gate was rows-only: restoring `parseInt` for `ck-cols` alone kept the suite green | `panelinputs` exercises **both** counts: a fractional value is refused with `aria-invalid` and "a whole number, please", the model *and* the grid are unchanged, and a valid value afterwards recovers and clears the mark. Mutant "5 columns validation removed" fails. |
| 5 | **MAJOR** — the JS clip was malformed: `clip-path` was concatenated *inside* the open `font-weight="` quote, so Chrome computed `clip: "none"` and the text still drew into the neighbouring cell | The weight attribute's quote is closed before the clip is appended. Gated on the **JS** side: the editor's `clipjs` step and `tests/test_editor.py`'s Chrome probe assert the *computed* clip is this cell's `clipPath`, that the clip rect is the cell's own box, and that the text's geometry really does extend past it (so the assertion is not vacuous); the narrow case is in the cross-renderer parity board, and a table that fits still carries no clip bytes. Mutant "6 js clip inside the weight quote" fails. |
| 6 | **CRITICAL (follow-up, round 3)** — that same reader still crashed on a share it could not *convert*: `float()` raised `OverflowError` on a JSON number with 310 digits (which round-trips through `json.dumps`/`loads` as a valid integer), so `render_svg` died instead of normalising. The reviewer's `review_extra_probe.py` reproduced it | `vendor/boardkit.py:560` (`_table_col_widths`) treats a conversion overflow as the same unusable share: the `except` clause is `(TypeError, ValueError, OverflowError)` and the value becomes `1`. Gated in `tests/test_render.py:541` (the 310-digit width in the reader loop, plus a real `json.dumps`/`json.loads` round trip at `:552`) and in `tests/test_editor.py`'s `width_probe()`/`width_checks()` (**both** renderers: the `digit` node's divider at 90 and texts at 45/135, the whole board compared for parity). Mutants "1f python conversion-overflow guard removed" (fails `test_render.py` **and**, through the parity gate, `test_editor.py`) fail. |

The reviewer's other results were confirmed and left alone: the `boardkit.table(...)` builder (finding 3
of round 1), the note `sub` sync (finding 7), the band scaling, and the v1.4 table byte-identity in both
renderers (`Python table bytes equal True counts 2 2`, `Chrome table bytes equal True counts 2 2`).

## Y7. Non-goals (state them; do not build them)

No change to node geometry, padding or default sizes (the user's "compact" request was about the
controls, and node density would churn every existing board's picture); no keyboard-first field
navigation (not chosen); no cell picking by clicking the canvas (the panel's row/col inputs are the
picker); no column drag-resize on the canvas; no merged/spanning cells; no per-cell text size or
weight; no images; no viewport persistence; no table sorting; no text syntax for cell colours.

## Y8. Gates

```bash
PY=$(ls -d ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
HERMES_HOME=$(mktemp -d) $PY tests/test_vendor.py      # lanes
node --check vendor/board_editor.js
node tests/pane_smoke.mjs
$PY tests/test_render.py                               # lane T
$PY tests/test_tools.py && $PY tests/test_api.py        # lane O
# lanes P and T: the editor self-test on the v1.5 fixture (Chrome is on PATH)
$PY - <<'PYEOF'
import sys, os, tempfile
sys.path.insert(0, "vendor")
os.environ["HERMES_BOARD_DIR"] = tempfile.mkdtemp(); os.environ["HERMES_BOARD_RENDER_DIR"] = tempfile.mkdtemp()
import boardkit
b = boardkit.load("docs/fixture-v1.5.json")
boardkit.report("/tmp/fx15.html", b, bridge=True)
print(boardkit.selftest("/tmp/fx15.html"))
PYEOF
```

The orchestrator runs `tests/test_editor.py` after the merge: cross-renderer parity on four fixtures,
the panel map and section rules, the new control surface (every tone has a swatch, every kind a glyph,
every group exactly one pressed button, every control a handler, every input a hint), the table v2
rules above, the round-2/round-3 width/clip probe (`width_checks()`: an imported `"Infinity"` string, a
finite `[1e308, 1e308]` pair and a 310-digit JSON integer whose *conversion* overflows, each asserted at
the divider/text positions in both renderers, plus an extreme legal share and the computed clip in
Chrome), and the one-definition check. Required self-test
step names: `sections`, `validate`, `cellcontrols`, `panelfields`, `celledit`, `panelinputs`,
`badwidth`, `clipjs` (plus every name
from v1.2–v1.4, which must keep passing).

**The gate itself had to be corrected first, or it would have rejected a correct implementation**
(the v1.5 gap pass found all of these in the orchestrator's own files):

* `tests/test_editor.py` still demanded **four** divider paths for the v1.4 fixture's table and a
  **100**-tall auto height in the editor's `resize` step, both stale since the header accounting was
  fixed to three paths and 76 (the `resize` step asserted the number produced by a *second*, drifted
  auto-size helper in `boxOf`, so the two wrongs agreed — `boxOf` now calls the one `nodeSize` helper);
* `tests/test_editor.py`'s control-surface assertions and `tests/test_render.py`'s picker assertions
  still parsed the replaced `<select>` elements (`ck-kind`, `ck-end`, …) and demanded every
  `KINDS`/`HEADS` value as an `<option>`. They must count the **button groups** instead — one button
  per kind/head with its `data-` attribute and exactly one `aria-pressed` per group — and keep no
  assertion that a replaced select still exists;
* the "object cell with an empty text" assertion was vacuous: the fixture cell it pointed at is a
  plain string, so a renderer that stringified `{text:""}` wrongly still passed. Probe a real
  `{text:""}` object cell instead (the fixture deliberately keeps its own cell as a string), and
  locate the painted overlays by geometry (Y4.4) rather than by a multiset of tone names.
