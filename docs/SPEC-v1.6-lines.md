# SPEC v1.6 — the toolbar, and a line you can draw and re-shape

Status: **draft for the user's approval, then frozen for the v1.6 swarm.** Four lanes build against
this file in parallel; the orchestrator merges, dispositions and gates. Requirement ids (`Z…`) are
cited by the cards, the tests, the commit messages and the verifier. If something here is wrong or
impossible, say so in your lane report — do not silently invent a different field name, enum value
or coordinate.

Base commit: `dd210a1` (content-identical to `origin/main` `f3ad54f`). Round 1 of the gap pass ran on
this file before it was frozen; its ten findings are dispositioned in `## Gaps` at the bottom, and
every one of them is already applied in the text below.

Where this comes from (the user's words, 2026-10-14): *"this panel looks bad, let improve UX"* and
*"now I'm not able to paint a line/multiline — let add this function instead of using connect
function, because now the line after connect function could cross boxes and not possible to change
its path, see an example."* Two screenshots came with it: the editor's toolbar row, and a zoom of the
mini-pc board's edge from the `n12` table into the `n2` box.

## Z0. Evidence (measured, not eyeballed)

* **The toolbar is one undifferentiated row of 12 controls, 664 px of content.** Measured in Chrome
  on the real interactive page: at a 1041 px viewport, one row, the last control ends at x=676; at
  700 px still one row (676 px wide); at 500 px — the narrowest window Chrome's new headless will
  give (`--window-size=440,900` reports `innerWidth` 500) — the bar is **476×61, two rows**, with
  252 px of controls falling to the second. The single-row threshold is therefore ≈688 px of
  content box; the pane declares **440 px** (`desktop/plugin.js`: `width: '440px'`), so in the pane
  the toolbar is a two-row pile. Controls are `+ box | + note | connect | grid | − | + | fit |
  [100%] | undo | copy JSON | download JSON | reset`: three controls open with the glyph `+` (two
  mean "make an object", one means "zoom in"), `100%` is a bare `<span>` rather than a control,
  `reset` (destructive) sits next to `download JSON`, and the transient status text lives inside
  the same flex row.
* **The connector's path is neither box-safe nor editable.** Measured with `boardkit._route` on the
  mini-pc board: edge `n12→n2` runs **25 px inside its own source box and 36 px inside its own
  target box** (the visible artifact in the second screenshot: the line starts on the table's left
  border, travels back *over* the table's first column, then drops), and edge `n5→n2` crosses the
  unrelated box `n10` by **39 px + 33 px**. The document, both renderers (`_route`/`route`) and the
  ops all carry `waypoints` — `bus` writes them (`store.py:1313`) — but **no pane control ever writes
  one**: grep finds `waypoints` only in the two route functions and in `edgeSvg`. The user's
  sentence "not possible to change its path" is exactly this: a capability present in the model and
  absent from the UI.
* The same shapes are baked into the shipped fixtures — v1.2: 8 third-box crossings, v1.3: 7 own-box
  intrusions, v1.5: 1 crossing. Consequence for Z8/Z9: **changing the auto-router re-baselines every
  fixture's rendered output**, so it is its own wave (v1.7), not a corner of this one. This wave ships
  the tool that puts the path in the user's hand, and the handles that let an existing connector be
  fixed by hand.

## Z0.1 Lane map (file ownership — do not touch another lane's files)

The lane map is part of the frozen header (the v1.5 spec's `Y0`); the requirement ids that
follow are the ones the cards cite, and `Z1` is the toolbar.

| Lane | Branch | Assignee (precedent) | Owns (only these) | Features |
|------|--------|---------------------|-------------------|----------|
| **L** lines | `feat/v16-line` | `weatherstore` (the v1.2–v1.5 drawing lane) | `vendor/boardkit.py`: the `line()` factory, the polyline/head helper extracted out of `render_svg`'s edge loop, `_line_svg`, `_line_anchor_rect`, `bounds()`, `verify()`'s counts, the reader-side normalisation of `lines`; `vendor/board_editor.js`: `state.lines` in `normalise()`/`replaceState()`, `lineSvg()`, the same extracted helper, the anchor resolution into `itemById`/`boxOf`; `tests/test_render.py` | Z3 |
| **I** interaction | `feat/v16-interaction` | `gitlab-developer` (the v1.2/v1.3/v1.5 interaction lane) | `vendor/board_editor.js`: the tool machine (`tool`, `setTool`), the line draft and its preview branch in `render()`, vertex/segment dragging, the edge-waypoint drag, `clearWaypoint`, the canvas hit tests, `syncButtons()`, every `[data-act]` / `[data-tool]` branch of the toolbar click listener, the self-test steps `line`, `linesnap`, `lineedit`, `edgepath` | Z2, Z4, Z5 |
| **P** chrome | `feat/v16-chrome` | `default` (the v1.2/v1.3 chrome/ops lane) | `vendor/boardkit.py`: `page()`'s toolbar markup, `_CSS`'s `.ck-bar`/`.ck-sep`/`.ck-ico`/`.ck-msg` rules and the glyph tables, the panel's new `line` section markup; `vendor/board_editor.js`: `panelKind()`, `syncPanel()`, the section-applicability map, the new section's `bind`s and row helpers; the self-test steps `barfit`, `linepanel` | Z1, Z6 |
| **O** ops/docs | `feat/v16-ops` | `design` (the v1.4/v1.5 ops lane) | `store.py`, `tools.py`, `tests/test_tools.py`, `README.md`, `plugin.yaml` | Z7 |

Shared surfaces, so the split is exact:

* `vendor/boardkit.py` — **L** owns every `def _…` drawing function, the vocabulary tuples
  (`TONES`, `HEADS`, …; L may only EXTEND a tuple, never reorder it) and `bounds`/`verify`; **P** owns
  `page()` (toolbar markup and CSS) and the panel markup. If you need a new colour variable or a new
  drawing helper, ask for it in your report instead of editing the other half.
* `vendor/board_editor.js` — **L** owns `normalise`, `replaceState`, `lineSvg`, the extracted polyline
  helper, `itemById`/`boxOf`; **I** owns the pointer handlers, the tool machine, `render()`'s *draft*
  branch only, `syncButtons`, the toolbar click listener and the self-test; **P** owns the panel
  functions and the section map. `render()` and the toolbar click listener are genuinely shared:
  expect one conflict in each and keep BOTH sides — never resolve by dropping a lane's work.
* **One closure, one definition per name.** The editor is a single script scope: a second
  `function foo()` does not shadow, the LAST declaration wins with no error. The pinned names below
  (`setTool`, `polylineSvg`, `lineSvg`, `addLine`, `snapEnd`, `clearWaypoint`) may be DEFINED by
  exactly one lane (L: `polylineSvg`, `lineSvg`; I: `addLine`, `setTool`, `snapEnd`, `clearWaypoint`);
  every other lane only CALLS them. The existing one-definition gate must stay green on the merged
  head.
* The orchestrator (not a lane) owns `docs/SPEC-v1.6-lines.md` (this file), `docs/fixture-v1.6.json`,
  `docs/fixture-v1.6-route.json`, `docs/baseline-v1.6.json` + `scripts/baseline_svg.py` (written
  BEFORE dispatch, at the base commit), `tests/test_editor.py`, `.github/workflows/tests.yml`, the vendor →
  chat-charts sync, the merge, the disposition commit, the demo render and the MR.

Rules for every lane:

* Work only in the worktree you were handed; **commit only** — never `git checkout`, `branch`,
  `merge`, `rebase`, `push`, `stash`, and never write under `~/.hermes/skills/**`.
* `vendor/**` is the only copy you edit. Run `HERMES_HOME=$(mktemp -d) tests/test_vendor.py` so the
  skill drift check skips loudly.
* **A v1.5 document must behave and render byte-identically** when it carries no `lines` key.
  `docs/baseline-v1.6.json` is the pinned hashes of BOTH halves' SVG for the four shipped fixtures
  at the base commit; the merged head must reproduce them (Z9). Cross-half parity cannot prove this
  (both halves can move together), so the baseline is a separate gate.
* No hardcoded colours (`var(--ck-…)` only), no new files, no new dependencies, no `<script src>`,
  no `url(http…` (the static `verify()` refuses external references).
* One commit: `v1.6 <lane>: <what>`, body naming the `Z…` ids.

## Z1. The toolbar and the page chrome (lane P)

Measured today (Z0): 12 controls in one flat row, 664 px of content, which needs a content box of
about 688 px — and the pane declares 440 px, so the bar is a two-row pile. Three controls open with
`+` (two mean "make an object", one means "zoom in"), `100%` is a bare `<span>`, and the transient
status text sits inside the same flex row, so a message reflows the bar.

* Z1.1 Groups, in this DOM order, separated by a `<span class="ck-sep" aria-hidden="true">`: create
  (`+ box`, `+ note`) · tool (`select`, `data-tool="select"`, pressed when no drawing tool is
  active — the way OUT of line mode; `line`, `data-tool="line"`) · view (`grid`, `−`, `#ck-zoom`,
  `+`) ·
  history (`undo`) · file (`copy JSON`, `download JSON`, `reset`). The zoom readout `#ck-zoom` IS the
  fit control: `data-act="fit"`, its label the percentage, its tooltip "click to fit the whole
  board" — there is NO second `fit` button, and the rendered bar carries every `data-act` exactly
  once (the editor's own control-surface gate asserts exactly-once, so a duplicate fails it).
* Z1.2 Every icon-only control carries `aria-label` + `title` and an inline `<svg>` glyph (14–16 px,
  `stroke="currentColor"`); the create pair keeps its word label (two meanings must not be guessed
  from a glyph). No hardcoded colour anywhere.
* Z1.3 The transient message lives in a block element (`<div class="ck-msg" id="ck-status"
  role="status">`) BELOW the bar, never inside it, so a message cannot reflow the toolbar.
* Z1.4 At most ONE accent-marked control: the pressed tool. The `grid` toggle reads as "on" in the
  default tone, never the accent.
* Z1.5 `reset` stays the last control and stays UNDOABLE (it snapshots first); its tooltip says so —
  there is no dialog.
* Z1.6 The `line` tool replaces `connect` (Z4.5): no `data-act="connect"` in the page, no
  `connectFrom` in the editor.
* Z1.7 The bar is ONE row in the pane: `barfit` (Z9) asserts it at 440 px.

## Z2. Three layers, three write policies (apply to every new field)

* **Reading** a document (loader, import, hand-written file) is lenient: an unknown enum value or a
  wrong type becomes the default and the document still renders.
* The **pane's writers** keep the OLD value, mark the row `aria-invalid` and show the reason — an
  invalid value never reaches the model.
* The **ops** raise, naming the field.

Resolved per field (a rule that appears in one layer must appear in the other two, each gated with
the input only that layer sees):

| field | reader | pane writer | ops |
|-------|--------|-------------|-----|
| `points` | not a list, an entry that is not a pair of finite numbers, or fewer than 2 entries → the whole ITEM is dropped (it cannot be drawn) | a drag only ever writes finite coordinates ≥ 0 | raise, naming `points` |
| a coordinate < 0 | clamped to `0` (the canvas origin is the board's corner) | a vertex drag CLAMPS to ≥ 0, so the pane cannot produce one | raise, naming the field |
| `tone`/`style`/`start`/`end` | an unknown value becomes the default (`default`/`solid`/`none`) | the control cannot produce an unknown value | raise, naming the field |
| `from`/`to` | not an object, or no string `ref` → the end stays FREE (the point is used); an unknown `side` → `auto` | — | `add_line`/`update_line` raise when `ref` does not resolve (`null` clears the anchor) |

`points` is stricter than the other fields on purpose: a point list with an unusable entry has no
sane default, and half a polyline is worse than no line.

## Z3. The `line` primitive in the document (lane L)

A board gains one optional top-level list, a sibling of `nodes`/`notes`/`groups`/`edges`:

```json
"lines": [
  {"id": "line-<8 chars>", "points": [[x, y], [x, y], …],
   "tone": "default", "style": "solid", "start": "none", "end": "arrow",
   "from": {"ref": "n3-…", "side": "auto"}, "to": {"ref": "n7-…", "side": "right"}}
]
```

* Z3.1 `points` is a list of at least 2 `[x, y]` pairs, in board units, finite and ≥ 0 (Z2), never
  normalised or re-rounded by the renderer.
* Z3.2 `tone`, `style`, `start`, `end` reuse the edge vocabulary exactly (`TONES`, `"solid" |
  "dashed" | "dotted"`, `HEADS`) and the same CSS variables (`--ck-edge-<tone>`), the same marker ids
  (`_marker_id` / `markerId`) and the same dash arrays. **No new head-size row: a line's heads use
  the `medium` box**; `start`/`end` may be `none`.
* Z3.3 `from`/`to` are OPTIONAL anchors, one per end. When present and resolvable, the resolved
  anchor **replaces** that end's point through the SAME resolver the edges use, called with the
  document's coordinate pair widened into a zero-size rect:

  ```js
  anchors(rect, { x: points[0][0], y: points[0][1], w: 0, h: 0 }, side, "auto")[0]   // `from`
  anchors({ x: p[0], y: p[1], w: 0, h: 0 }, rect, "auto", side)[1]                   // `to`
  ```

  (`board_editor.js`'s resolver reads numeric `x`/`y`, and the document stores pairs — a lane that
  passes `points[0].x` supplies `undefined` and misplaces every `auto` end.) Python mirrors this
  with `boardkit.anchors`. `side` takes `auto|left|right|top|bottom`; an unknown value is `auto`.
  **Do not write a second copy of that rule** — the v1.5 wave lost a day to exactly that.
* Z3.4 **A dangling anchor is not a vanished item** (this is where a line differs from an edge, and
  it must be stated in the code): when `ref` names no node/note, or `from`/`to` is malformed, that end
  falls back to its stored `points[0]`/`points[-1]` and the line is still drawn. (`edgeSvg` today
  returns `""` for an unresolvable edge; that behaviour is untouched — see Z8.)
  A `ref` may name a **node or a note**. The Python half has no node-and-note lookup today (edge
  rendering resolves nodes only, `boardkit.py:903`), so L adds ONE helper, `_line_anchor_rect(ref, b)`,
  that searches `nodes` then `notes` and returns the effective rect (a table through `table_size`,
  everything else through its stored `x/y/w/h`, notes with the renderer's own `300×60` defaults). The
  editor resolves through `itemById` + `boxOf`. Gate both halves with a line anchored to a `notes`
  entry: a mismatch means the pane attaches where the PNG falls back.
* Z3.5 `bounds()` and the editor's own size accounting include every line point and its resolved
  anchors, so a line drawn outside the boxes is never clipped. `bounds()` accounts only max x/y and
  both SVGs start their viewBox at `0 0`, which is why Z2 pins coordinates to ≥ 0 instead of
  introducing min-x/min-y accounting. `verify()` reports `{"nodes": …, "edges": …, "lines": …}`.
* Z3.6 Rendering is one `<polyline fill="none">` per line, with the edge's tone/stroke/dash/marker
  attributes; selection thickness (`1.7` / `3`) matches the edge rule. The Python `render_svg` and
  the editor's `lineSvg` must agree element for element — the parity fixture (Z10) is the gate.
* Z3.7 `normalise()` in the editor is the ONE entry point that installs a document (its boot, its
  `reset`, and the bridge/`replaceState` path an agent update or an import takes). `state.lines` is
  defaulted there and nowhere else: a document that omits the list must not throw on any path.

## Z4. The line tool (lane I) — what replaces `connect` in the toolbar

Z4.1 The toolbar's `line` button (`data-tool="line"`) is a MODE. Exactly one of `[data-tool]` is
pressed; entering line mode sets the canvas cursor to `crosshair`; leaving it returns to the
default. `setTool(name)` is the single writer of the mode.

Z4.2 Creating: `pointerdown` on the canvas opens a draft at that point; each further click on the
canvas appends a point; a rubber preview polyline follows the pointer between clicks. A
**double-click** or **Enter** commits the line; **Esc** cancels it (nothing is written to the
document, the draft is dropped); a draft with fewer than 2 points is dropped without a message. The
draft is never written to `state` until it commits — an aborted or interrupted draft leaves no trace
in the model and no entry in the undo history.

Z4.3 Snapping (`snapEnd(x, y)`): a point that falls inside a node/note rect, or within **8 screen
px** of its border (the tolerance is in screen space, divided by the current zoom), records an anchor
for that end — `{ref: <id>, side: <the nearest side, or "auto">}` — while the point itself is KEPT in
`points` as the fallback position of Z3.4. Only the two ENDS of a stroke snap; interior points never
do (a line may legitimately cross a box — that is the user's annotation arrow).

Z4.4 Committing selects the new line and opens its panel section, exactly as `addNode` does today.
`addLine(points, from, to)` is the single factory: it appends to `state.lines`, snapshots for undo
and returns the id.

Z4.5 The `connect` button is REMOVED from the toolbar (`data-act="connect"` and `connectFrom` are
deleted from the pane), because the line tool does the same job with a path the user owns: start the
stroke on a box edge and Z4.3 anchors that end. `addEdge` stays in the file — the agent still creates
edges through `add_edge`, and their paths become editable by Z5.2.

## Z5. Re-shaping a path (lane I)

Z5.1 A committed line is editable: click selects it; each vertex is a `.ck-vtx` handle; dragging one
moves that point (with Z4.3 snapping on drop, which sets or clears that end's anchor). Alt-click on a
segment inserts a vertex at the clicked point. Alt-click on a vertex (while `points.length > 2`)
removes it; Alt-click on a vertex that carries an anchor DETACHES the anchor and keeps the point.
Deleting the selected line uses the existing delete path. Every one of these is a `snapshot()` before
the write, so `undo` restores the previous path one step at a time.

Z5.2 An EDGE's path becomes editable in the pane, without touching either renderer (both already
honour `waypoints`): dragging a middle segment materialises it as two waypoints
(`waypoints = [[x1,y1],[x2,y2]]` between the neighbouring anchors) and moves them together along the
segment's perpendicular axis; subsequent drags move `waypoints[i]`; Alt-click on a waypoint removes
it; once `waypoints` is non-empty, dragging a segment moves its two waypoints. This is the answer to
"the line after connect could cross boxes and not possible to change its path": any edge the agent
drew can be pushed out of the way by hand, and the new path is written into the document as data the
agent can read back.

Z5.3 **A waypoint is never left inside a box** (`clearWaypoint(x, y, axis, dir)`): a waypoint that a
drag materialises or moves into any node/note rect is pushed out through the boundary the segment
is heading toward, plus 12 units of clearance, before it reaches the model. Without this rule the
materialised pair can sit behind its own anchor, and the resolver then draws the path *backwards
through the source box* — reproduced on a scratch copy in both halves: `waypoints
[[150,130],[350,130]]` with a source box spanning x=100–200 draws `(200,130) → (150,130)`, i.e. 50
units back inside. The rule applies to EDGES only; a `line`'s interior points may cross boxes
deliberately (Z4.3).

Z5.4 Hit testing is screen-space: a click within ~6 screen px of a polyline (line or edge) selects
it, at any zoom. A marquee selects lines whose vertices or segments intersect it. Handles
(resize/guides) draw for a multi-selection or in line mode exactly as they do for a marquee today —
no regressions in the `resize`/`guides`/`marquee` self-test steps.

## Z6. The panel (lane P)

Z6.1 A `line` section appears for a line selection only: the point count with a `+`/`−` stepper and a
live count hint (append a point at the end of the path / remove the last one, never below 2), the
tone swatches, the `style` group, and the start/end head grids — the same controls, glyphs and
`bind` idiom the v1.5 panel already uses. It is HIDDEN, not disabled, for every other selection, and
the applicability map is mechanical: the visible rows of a `line` selection equal the fields whose
change alters the rendered picture (a line has no text, no size, no table: those rows must be absent).
Z6.2 The node/edge sections are hidden for a line selection and the line section is hidden otherwise,
including for the `note`-as-a-node vs `note`-list pair (each is its own kind — give the line its own
kind in `panelKind()`).

## Z7. Agent surface, ops, docs, version (lane O)

* Z7.1 Ops: `add_line {points, tone?, style?, start?, end?, from?, to?}`,
  `update_line {id, points?, tone?, style?, start?, end?, from?, to?}` (an explicit `null` clears an
  anchor), `delete_line {id}`. Validation is IDENTICAL for add and update, and follows the Z2 table
  for the ops column: a non-list `points`, an entry that is not a pair of finite numbers, fewer than
  2 entries, a negative coordinate, an unknown enum value, a `ref` that does not resolve, or a
  malformed `from`/`to` all RAISE, naming the field. `add_line`/`update_line` also refuse a `ref` in a
  document where nothing is named that (the reader stays lenient as Z3.4 requires — the two layers
  are deliberately different).
* Z7.2 `board_get` returns `lines`; `board_apply`'s OPS tuple lists the three ops; `OPS_HELP`
  documents them for the agent's tool schema.
* Z7.3 `diff_boards` reports lines: added / removed / edited (points, tone, style, heads, anchors) /
  moved, and the one-sentence summary says `drew 1 line (3 points)` / `deleted 1 line`. A re-save that
  changed nothing must still produce `{"changed": false}` and no wake.
* Z7.4 `README.md` documents the tool, the primitive and the two ops in the section a user reads; the
  pane paragraph names the line tool where it currently names `connect`.
* Z7.5 `plugin.yaml` → `version: 1.6.0` (the pane's version badge is how the user sees the reload
  picked the change up).
* Z7.6 `add_edge` and `update_edge` accept `waypoints` like the document does (and the
  `add_edge` docs sentence gains it): the model, both renderers and `bus` all carry waypoints today,
  the ops being the one layer that drops the field silently. With Z5.2 this closes the loop — the
  agent can author a hand-routed path, the user can move it in the pane, and the agent reads the new
  path back from the document.

## Z8. Non-goals (state them; do not build them)

No change to the auto-router's geometry (`_route`/`route`): the four shipped fixtures render
byte-identically, defects included — the router needs its own wave with re-baselined fixtures
(`docs/fixture-v1.6-route.json` captures the user's own case for it). No curves/beziers, no freehand
brush, no per-line labels, no z-order controls, no `convert edge → line`, no line grouping with the
items it crosses, no clearance rule for a line's interior points (Z5.3 applies to edges only). No
change to `desktop/plugin.js`'s React chrome; no change to node geometry, padding or default sizes;
no new dependencies or files; no behaviour change for edges with an unresolvable `from`/`to`; no
change to the exported JSON's existing keys; no keyboard-first editing beyond Enter/Esc in Z4.2.

## Z9. Gates

```bash
PY=$(ls -d ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
HERMES_HOME=$(mktemp -d) $PY tests/test_vendor.py      # lanes
node --check vendor/board_editor.js
node tests/pane_smoke.mjs
$PY tests/test_render.py                               # lane L
$PY tests/test_tools.py && $PY tests/test_api.py        # lane O
hermes plugins validate . && hermes plugins doctor .    # orchestrator, locally
```

The lanes' own editor gate asserts the RESULT, not that the run happened — `boardkit.selftest`
returns a report and never raises, so the command must fail on a false verdict (verified: a scratch
mutant with `ok:false` exits 0 under the bare print):

```bash
$PY - <<'PYEOF'
import sys, os, tempfile
sys.path.insert(0, "vendor")
os.environ["HERMES_BOARD_DIR"] = tempfile.mkdtemp(); os.environ["HERMES_BOARD_RENDER_DIR"] = tempfile.mkdtemp()
import boardkit
b = boardkit.load("docs/fixture-v1.6.json")
boardkit.report("/tmp/fx16.html", b, bridge=True)
r = boardkit.selftest("/tmp/fx16.html")
bad = [s for s in r["steps"] if not s[1]]
want = {"line", "linesnap", "lineedit", "edgepath", "barfit", "linepanel"}
have = {s[0] for s in r["steps"] if s[1]}
print("ok:", r["ok"], "false steps:", bad, "missing:", want - have)
assert r["ok"] is True and not bad and not (want - have)
PYEOF
```

The orchestrator runs `tests/test_editor.py` after the merge: cross-renderer parity on five
fixtures, the panel map and section rules, the one-definition check, the new control surface, and —
new in v1.6 — the **byte-identity baseline**: for each of the four shipped fixtures, the merged
head's Python SVG and editor DOM must hash to the values pinned in `docs/baseline-v1.6.json`
(`dd210a1`: v1.2 `024c37e4…`/`e4d7e8e2…`, v1.3 `18c4ed9a…`/`bfffa62d…`, v1.4 `2ff3fb90…`/
`c069c438…`, v1.5 `3688c545…`/`6b74d350…`; regenerate with `python3 scripts/baseline_svg.py --write`,
never by editing the numbers).

Required new self-test step names: **`line`, `linesnap`, `lineedit`, `edgepath`, `barfit`,
`linepanel`** — plus every name from v1.2–v1.5, which must keep passing.

**`barfit` must be measured at 440 px, not at whatever the window happens to be.** Chrome's new
headless clamps a window to 500 px (`--window-size=440,900` reports `innerWidth` 500 — this is how a
440 px claim silently becomes a 500 px measurement), so the harness sets the viewport through CDP
`Emulation.setDeviceMetricsOverride` (440×900) before running the step, and the step itself asserts
`window.innerWidth === 440` and FAILS naming the measured width otherwise. With the width proven,
the step asserts: exactly ONE distinct `top` among the bar's visible children, bar height ≤ 30, and
`document.body.scrollWidth <= window.innerWidth`.

`lineedit` and `edgepath` DRIVE the real controls and pointer events (synthetic
`pointerdown`/`pointermove`/`pointerup`/`dblclick`), then read the model back — never assert that a
handler exists. `edgepath` covers the own-box intrusion of Z5.3 as well as a third-box crossing.
Every new gate earns its place by failing on a mutant (remove the fix in a scratch copy, watch THAT
named check go red).

Two gates must be written for the merged head, not for a lane: the one-definition check over the
merged `vendor/board_editor.js` (names defined twice — `function` declarations in the JS half,
top-level `def`s in the Python half), and the parity + baseline run against the NEWEST fixture, where
cross-lane interactions surface.

**The self-test runs twice on purpose (orchestrator amendment, after lane P).** The functional steps
cannot run at 440 px: the drag threshold in `board_editor.js`'s `pointermove` is 3 DOCUMENT units, so
a 1 px click jitter becomes a drag at 39 % zoom and `clickdrag` goes red. `barfit` therefore runs in
a second page load (`#selftest-pane`) at the pane's 440×900 viewport, and `boardkit.selftest` merges
both pages into the one report it returns. Required: the merged report carries the step names of BOTH
pages, and `barfit` still asserts `window.innerWidth === 440`.

## Z10. The fixtures the orchestrator writes

`docs/fixture-v1.6.json` carries, in one document: (1) a free 3-point line with no anchors and an
off-grid middle vertex; (2) a line anchored at both ends between two boxes and crossing a third
box's rect (the user's annotation arrow) — one of those two ends anchored to a `notes` entry, to
exercise the Z3.4 lookup; (3) an edge with `waypoints` routing around a box, plus an edge whose
materialised waypoints would sit inside their own source box (the Z5.3 reproduction: box x=100–200,
waypoints `[[150,130],[350,130]]`); (4) a line whose `from.ref` names a missing id and a line whose
`from` is a malformed object (Z3.4's two fallbacks); (5) a line with `style: "dashed"`,
`start: "circle"`, `end: "triangle"`, and one with a negative coordinate in an imported document
(clamped, Z2); (6) a table and a note so the panel's applicability map has its other kinds in the
same document.

`docs/fixture-v1.6-route.json` is a copy of the user's `mini-pc` board's edges (`n5→n2` crossing
`n10`; `n12→n2` running inside its own boxes). v1.6 must render it exactly as today; it exists so the
router wave has the reproduction of the reported defect.

## Z11. Delivery

Branch `feat/v1.6-lines` off `origin/main` (`f3ad54f`). Lanes branch off that branch's head; the
orchestrator merges, dispositions the findings, runs the gates on the merged head, and opens an MR to
this repository's `main` (protected — branch + PR, never a direct push).

The bundled library and the live skill must not drift, and the copy is outside the repo's Git root,
so it CANNOT ride inside a repo commit. Order and evidence:

1. lanes and integration with `HERMES_HOME=$(mktemp -d)` (the vendor↔skill drift check skips loudly);
2. the merge commit touching `vendor/**` — its own repo commit, nothing else in it;
3. in the SAME command (never as a later cleanup, or a concurrent reader's gate goes red for a
   reason that is not a defect): `python3 scripts/sync_boardkit.py` — or `cp vendor/boardkit.py
   vendor/board_editor.js ~/.hermes/skills/data-science/chat-charts/scripts/` — plus the chat-charts
   skill's own version bump and its SKILL.md vocabulary line for `lines`;
4. the drift gate, with NO isolation: `python3 tests/test_vendor.py` must print `ok
   vendor/boardkit.py matches the skill copy` AND the same for `board_editor.js` (a real-home run is
   the only place this check is not skipped — `tests/test_vendor.py:91`);
5. record the three sha256 triples (repo `vendor/`, live skill, installed plugin) in the delivery
   note, and run the skill's own `test_boardkit.py` there, so the shared library is proven in both
   homes.

## Gaps

Round 1 — findings 10 (CRITICAL 0, MAJOR 9, MINOR 1) | new-actionable 9 | repeat 0 | trade-off 0 |
noise 0 | contract-misread 1 | verdict: request changes. Every finding from that round was
reproduced by the pass on a scratch copy and is now applied in the text above:

| # | severity | disposition | where it landed |
|---|----------|-------------|-----------------|
| 1 | MAJOR | fixed | Z5.3 — `clearWaypoint`, the own-box intrusion reproduction, and the own-box case added to `edgepath` |
| 2 | MAJOR | fixed | Z3.3 — the resolver call widens `[x,y]` into a zero-size rect (the pinned expression read objects); `auto` now gated on both ends |
| 3 | MAJOR | fixed | Z3.4 — `_line_anchor_rect` (nodes then notes) in Python, `itemById`+`boxOf` in the editor, one parity case anchored to a `notes` entry |
| 4 | MAJOR | fixed | Z2 table — per-field reader/writer/op verdicts, incl. negative coordinates and malformed anchors; op strictness identical for add and update |
| 5 | MAJOR | fixed | Z2/Z3.5 — coordinates are finite and ≥ 0 (op raises, reader clamps, the drag clamps); `bounds()` stays max-only and the viewBox origin is unchanged |
| 6 | MAJOR | fixed | Z9 + `docs/baseline-v1.6.json` + `scripts/baseline_svg.py` — pre-wave hashes of both halves per fixture, written at the base commit |
| 7 | MAJOR | fixed | Z9 — the lane gate asserts `ok is True` and the exact step names; the vacuum is demonstrated by the mutant that exited 0 |
| 8 | MAJOR | fixed | Z9 — `barfit` sets the viewport through CDP and asserts `innerWidth === 440` before measuring geometry |
| 9 | MAJOR | fixed | Z11 — the skill copy is a shell step in the same command as the merge commit (it cannot be inside it), with the both-files drift gate and the hash record |
| 10 | MINOR | fixed | Z0/Z7.6 — the evidence sentence corrected (`bus` writes waypoints, `add_edge` silently dropped the field) and `waypoints` added to `add_edge`/`update_edge` |

Round 1 of the CODE gate (`reviewer`, merged head `685258c`) — 5 findings (0 CRITICAL, 4 MAJOR,
1 MINOR), all reproduced on a scratch copy; the verdict is routed to the fix lane, not dispositioned
here:

| # | severity | finding | routed to |
|---|----------|---------|------------|
| 1 | MAJOR | the editor's self-test assumes a `[data-tool="select"]` button `page()` never emitted, so `if (selectBtn) selectBtn.click()` was a no-op, line mode stayed ON and the next `lineBtn.click()` toggled it OFF — `linesnap` false, `lineedit`/`edgepath` never reported, then a null deref | fix lane: emit the `select` button (Z1.1, amended above) and drive the real control, asserting the OFF state |
| 2 | MAJOR | `linePts`/`hitPolyline` consume raw malformed entries (`points:[[1,2],null]`) although `lineSvg` drops them: a `pointerdown` throws `Cannot read properties of null (reading '0')` and breaks selection | fix lane: the same validated-points policy in every interaction path, with a real pointer-event regression |
| 3 | MAJOR | the readers coerce non-numeric coordinates instead of dropping the whole line (Z2): `[[null,20],[30,40]]` is dropped in Python but drawn from `(0,20)` in the editor, and `["12","20"]`/booleans draw too | fix lane: validate finite NUMERIC types (no bool/null/str) in BOTH readers before clamping, gated on the same document in both halves |
| 4 | MAJOR | `store.py`'s `p.get(field) is not None` swallows explicit nulls: `update_line {points:null}`, `{tone:null}`, `{style:null}`, `{start:null}`, `{end:null}` all succeed unchanged (and `add_line` accepts them) | fix lane: field membership, then validate the value; only a null `from`/`to` clears an anchor |
| 5 | MINOR | `tests/test_editor.py`'s cross-language function-COUNT comparison (`< 60`) failed a correct merge (134 vs 69) | FIXED by the orchestrator before this round was read (`d12b7b0`): the bound is now proportional, the two uniqueness checks stay |

Confirmed by that round, independently reproduced: the baseline provenance and all eight pinned
SHA-256 values on the merged tree; the user's `fixture-v1.6-route.json` renders byte-identically to the
pre-wave renderers; `node --check`, `pane_smoke`, `test_render`, `test_tools`, `test_api` pass; both
halves' one-definition checks pass; a bridge replace with a document omitting `lines` renders without
throwing; `barfit` (440 px) and `linepanel` pass; and a mutant per step name and per op assertion
turns its named check red.

Wave dispositions (the orchestrator's own, from the lanes' reports — they are not gap findings):

| item | disposition |
|---|---|
| lane P: the toolbar carried TWO `fit` controls (`#ck-zoom` and a `fit` glyph) | fixed in the disposition commit — the standalone glyph is dropped, `#ck-zoom` is the single fit control (Z1.1) |
| lane P: `CONTROLS["toolbar"]` in `tests/test_editor.py` still named `data-act="connect"` | fixed before the merge — the entry is dropped (Z1.6) |
| lane P: `barfit` runs in a second page load at 440×900 | accepted and now part of Z9 — the functional steps need the normal viewport |

Boundary — what this wave deliberately does not cover:

* the auto-router's crossing/intrusion geometry (Z8), with the reproduction parked in
  `docs/fixture-v1.6-route.json`;
* the pane's React chrome (`desktop/plugin.js`) beyond the version badge;
* curves, freehand, per-line labels, z-order, `convert edge → line`, grouping a line with a box it
  crosses;
* edge behaviour for unresolvable endpoints, and any clearance rule for a line's interior points;
* keyboard-first line editing (Enter/Esc are the only keys Z4 pins).
