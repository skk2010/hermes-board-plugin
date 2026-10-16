# SPEC v1.2 — board primitives (shapes, arrow heads, grid)

Status: **frozen for the v1.2 swarm**. Three lanes build against this file in parallel; the
orchestrator merges and gates. Requirement ids (`R…`) are cited by the verifier, by the tests and
by the commit messages. If something here is wrong or impossible, say so in the lane's report —
do **not** silently invent a different field name, enum value or coordinate.

## R0. Lane map (file ownership — do not touch another lane's files)

| Lane | Branch | Owns (only these files) |
|------|--------|-------------------------|
| A — shapes | `wt/shapes` | `vendor/boardkit.py`, new `tests/test_render.py` |
| B — editor | `wt/editor` | `vendor/board_editor.js` |
| C — ops/docs | `wt/ops` | `store.py`, `tools.py`, `tests/test_tools.py`, `README.md`, `plugin.yaml` |

Orchestrator (not a lane): `docs/SPEC-v1.2-primitives.md` (this file), `docs/fixture-v1.2.json`,
`tests/test_editor.py` (cross-renderer parity + selftest gate), `.github/workflows/tests.yml`, the
`v1.2: round-1 gap dispositions` commit that lands after the lane merge, the `vendor/` →
chat-charts skill sync, the merge, the demo render, the MR. Lanes build against the frozen base
tagged `v1.2-base` (the commit that first carried this file).

Rules for every lane:

* Work only in the worktree you were handed; **commit only** — never `git checkout`, `branch`,
  `merge`, `rebase`, `push`, `stash`, or write to `~/.hermes/skills/**`. One commit, message
  `v1.2 <lane>: <what>`, body naming the `R…` ids implemented.
* `vendor/boardkit.py` and `vendor/board_editor.js` are the **only** copies you edit. The
  chat-charts skill copy under `~/.hermes/skills/…` is stale on purpose (the orchestrator syncs it
  at integration): run the suites with an isolated home —
  `HERMES_HOME=$(mktemp -d) $PY tests/test_vendor.py` — so the vendor/skill drift check skips
  loudly instead of failing on a file you must not touch.
* Keep both renderers byte-for-byte equivalent in *structure*: same elements, same order, same
  attribute values (numbers may differ in the last float digit — that is what
  `tests/test_editor.py` normalises). Never "improve" the geometry of your side alone.
* Existing behaviour must not change: `tests/test_tools.py`, `tests/test_api.py`,
  `tests/test_vendor.py`, `tests/pane_smoke.mjs` stay green. `bidir` keeps working (R6.4).
* No new imports/files beyond the ones listed, no external references, no hardcoded colors: use
  `var(--ck-…)` / `var(--ck-tone-<tone>-fill|line)` / `var(--ck-edge-<tone>)`.
* `text`, `sub`, `tone` behave as today unless this file says otherwise.

## R1. Document schema (new fields; absent key = current behaviour)

```jsonc
{
  "title": "…", "width": 900, "height": 460,
  "grid": 10,                       // spacing, px — unchanged
  "grid_on": false,                 // NEW: draw the background grid
  "grid_style": "lines",            // NEW: "lines" | "dots" (unknown → "lines")
  "snap": true,                     // NEW: editor snaps drags to `grid` when not false
  "nodes": [{
    "id": "n1", "x": 40, "y": 60, "w": 190, "h": 72, "text": "…", "sub": "…",
    "kind": "box",                  // + cloud cylinder document server computer actor area
    "tone": "accent",
    "align": "center",              // NEW: "center" (default) | "left"
    "parts": [{"text": "…", "sub": "…"}],   // NEW: split box compartments
    "split": "rows"                 // NEW: "rows" (default) | "columns"
  }],
  "edges": [{
    "from": "n1", "to": "n2", "label": "…", "style": "solid", "route": "auto",
    "start": "none",                // NEW: head at the source end
    "end": "arrow"                  // NEW: head at the target end
  }]
}
```

`parts` entries may also be a bare string (`"A"` ≡ `{"text": "A"}`); renderers normalise when
reading, never when writing. `bidir` (legacy) is still honoured (R6.4).

## R2. Shape kinds — geometry in node-local coordinates (translate(x,y) applied by the caller)

Every shape is filled `var(--ck-tone-<tone>-fill)` and stroked `var(--ck-tone-<tone>-line)`,
`stroke-width="1.4"`, exactly like `box` today. `w`,`h` below are the node's size; expressions are
Python-style (`0.5*w` = `0.5 * w`). Emit the computed numbers as-is (no rounding, no unit).

`box`, `pill`, `diamond`, `note`, `caption` are unchanged. New kinds:

* **`cloud`** — one `<path>`:
  `M 0.30w,h C 0.14w,h 0.04w,0.86h 0.12w,0.70h C 0.02w,0.50h 0.16w,0.30h 0.34w,0.36h
  C 0.42w,0.10h 0.72w,0.08h 0.78w,0.34h C 0.94w,0.32h 1.02w,0.56h 0.88w,0.72h
  C 0.96w,0.88h 0.84w,h 0.70w,h Z`
* **`cylinder`** — two elements, in this order:
  `<path d="M 0,0.12h A 0.5w,0.12h 0 0 1 w,0.12h V 0.88h A 0.5w,0.12h 0 0 1 0,0.88h Z"/>`
  then `<ellipse cx="0.5w" cy="0.12h" rx="0.5w" ry="0.12h" fill="none"/>` with the same stroke.
* **`document`** — one `<path>`:
  `M 0,0 H w V 0.84h C 0.75w,0.70h 0.50w,1.00h 0.25w,0.86h C 0.12w,0.78h 0.06w,0.92h 0,0.84h Z`
* **`hexagon`** — `<polygon points="0.18w,0 0.82w,0 w,0.5h 0.82w,h 0.18w,h 0,0.5h"/>`
* **`parallelogram`** — `<polygon points="0.20w,0 w,0 0.80w,h 0,h"/>`
`server`**, **`computer`**, **`actor`** — *icon kinds*: a pictogram plus the node's `text` as a
caption line **below** it. `label_h = LINE + 8` (= 23 at `LINE = 15`), pictogram region
`ph = h - label_h`. The caption is ONE line — the **first** wrapped line of `text` at width
`w - 12` with font `FONT` (the same wrap helper the rest of the file uses, exactly as an `area`
title is drawn) — emitted as
`<text x="0.5w" y="h - 6" text-anchor="middle" font-size="FONT" font-weight="400"
fill="var(--ck-fg)">…</text>`.
`sub` is **not** drawn for icon kinds. (Both renderers must wrap here — an unwrapped caption
drifts as soon as a label is longer than the node.)
  * `server`: `<rect x="0.18w" y="4" width="0.64w" height="ph-8" rx="6"/>` (filled+stroked);
    three slots `i = 0,1,2`: `<rect x="0.18w+0.10*(0.64w)" y="4+(ph-8)*(0.10+0.30i)"
    width="0.62*(0.64w)" height="0.20*(ph-8)" rx="2" fill="none" stroke-width="1"/>`, and
    `<circle cx="0.18w+0.88*(0.64w)" cy="4+(ph-8)*(0.20+0.30i)" r="min(3, 0.06*(ph-8))"
    fill="var(--ck-tone-<tone>-line)" stroke="none"/>`.
  * `computer`: monitor `<rect x="0.15w" y="4" width="0.70w" height="ph-18" rx="4"/>` (filled+stroked);
    stand `<path d="M 0.5w,ph-14 V ph-4" fill="none" stroke-width="1.2"/>`;
    base `<path d="M 0.36w,ph-4 H 0.64w" fill="none" stroke-width="1.4"/>`.
  * `actor`: stroke-only stick figure (`fill="none"`, `stroke-width="1.4"`, no node fill):
    `r = min(0.14*ph, 0.13*w)`; `<circle cx="0.5w" cy="6+r" r="r"/>`;
    `shoulder_y = 6 + 2r + 2`; `torso_bottom = shoulder_y + (ph - 6 - shoulder_y) * 0.68`;
    `<path d="M 0.5w,shoulder_y V torso_bottom"/>`;
    `<path d="M 0.26w,shoulder_y+(torso_bottom-shoulder_y)*0.30 H 0.74w"/>`;
    `<path d="M 0.5w,torso_bottom L 0.30w,ph-2 M 0.5w,torso_bottom L 0.70w,ph-2"/>`.
* **`area`** — a titled backdrop region, drawn **behind** everything else (R5.2):
  `<rect x="0" y="0" width="w" height="h" rx="10" fill="var(--ck-tone-<tone>-fill)"
  stroke="var(--ck-tone-<tone>-line)" stroke-width="1.4"/>`,
  then the header rule `<path d="M 0,24 H w" fill="none" stroke-width="1"/>` with the same stroke
  color, then `<text x="12" y="17" font-size="11" font-weight="600"
  fill="var(--ck-tone-<tone>-line)">` with the **first** wrapped line of `text` (wrap width
  `w - 24`, font `11`). `sub` and `align` are not used by `area`.

## R3. Text placement per kind

The generic block (wrap `text` to `w - 2·inset`, `font-size = FONT`, `font-weight="600"`,
`text-anchor="middle"`, `fill=var(--ck-fg)`, centred vertically including the optional `sub`
block) is unchanged; only `inset` and the vertical offset change:

| kind | inset | vertical offset |
|------|-------|-----------------|
| box, pill | 12 | 0 |
| diamond | `0.45h` | 0 |
| cloud | `0.16w` | `+0.02h` |
| cylinder | `0.14w` | `+0.06h` |
| document | `0.14w` | `-0.02h` |
| hexagon | `0.22w` | 0 |
| parallelogram | `0.24w` | 0 |
| icon kinds (server/computer/actor) | — (caption rule, R2) | — |

`align: "left"` replaces `text-anchor="middle"` with `start`, keeps the same `inset` (the anchor `x`
becomes the left edge of the text column), keeps the same wrap width and vertical centring.
`caption` and `area` keep their own rule (left aligned already) and ignore `align`.

## R4. Split boxes (`parts`)

Honoured for kinds `box` and `pill` only; on any other kind `parts` is ignored (no error).
Normalise each part (R1) first; ignore parts with empty text; if nothing is left render the node
normally for its kind — a `pill` stays a pill — with no split content at all.

* `head_h = 28` when the node has a non-empty `text`, else `0`. The header text is centred in
  `[0, head_h]` (inset 12).
* rows: `cell_h = (h - head_h) / n`; divider `i = 1…n-1` is
  `<path d="M 0,head_h+i*cell_h H w" fill="none" stroke="var(--ck-tone-<tone>-line)" stroke-width="1"/>`;
  part `i`'s block is centred vertically in `[head_h+i*cell_h, head_h+(i+1)*cell_h]`, wrapped to
  `w - 16`, `sub` (if present) drawn under the part's text in `SUB_FONT`/`var(--ck-muted)` like the
  node `sub` is.
* columns: `cell_w = w / n`; divider `i = 1…n-1` is
  `<path d="M i*cell_w,head_h V h" …/>` (same stroke); part `i` wrapped to `cell_w - 12` and
  centred both ways in `[i*cell_w, (i+1)*cell_w] × [head_h, h]`.
* `align: "left"` applies to header and part texts too (anchor `start` at the block's left inset).
* The node's shape (R2) is drawn first, then the header text, then the dividers, then the parts —
  in that element order. `sub` of the node itself is not drawn when `parts` render.

## R5. Grid

**R5.1** Fields per R1. `grid_on` defaults to **false** when absent (existing boards, PNG renders
and chat artifacts must not change). `grid` spacing uses the existing default (10).

**R5.2** Draw order inside the `<svg>` (both renderers, same order): `<defs>` (markers + grid
pattern) → grid rect → groups → areas → edges → nodes → notes.

**R5.3** The pattern is defined in `<defs>` only when `grid_on` is true:

```
<pattern id="ck-grid" width="{g}" height="{g}" patternUnits="userSpaceOnUse">
  lines: <path d="M 0,0 H {g} M 0,0 V {g}" fill="none" stroke="var(--ck-grid)" stroke-width="1"/>
  dots:  <circle cx="0.5" cy="0.5" r="1" fill="var(--ck-grid)"/>
</pattern>
```

and used as the first drawing element:
`<rect class="ck-grid" x="0" y="0" width="{canvas_w}" height="{canvas_h}" fill="url(#ck-grid)"/>`
(`canvas_w/h` are the same numbers the `<svg>` `viewBox` uses).

**R5.4** A lane-A CSS addition (`.ck-grid` needs no rule; `--ck-grid` already exists) is not
required. Do not add CSS.

**R5.5** The editor (lane B) must expose a **grid** toggle in the toolbar (R7) and must only snap
a dropped node to the grid when `state.snap !== false` (default: snap).

## R6. Edge heads

**R6.1** `HEADS = ("none", "arrow", "open", "triangle", "circle", "diamond", "bar")` — `arrow` is
the current filled triangle; `triangle` is its hollow twin; `open`: "open" (a two-stroke chevron, no
fill); `circle` a filled dot; `diamond` a filled rhombus; `bar` a perpendicular tick.
Effective heads: `end = "arrow"` when the field is missing, null or not in `HEADS`, else the
field; `start = "none"` unless the field holds a value in `HEADS`, in which case that value, or —
when the field is missing, null or unknown — `"arrow"` if `e.get("bidir")` else `"none"`. Never
build a marker id out of a null or unknown value (no `ck-head-default-None`).

**R6.2** Marker defs, one per (tone, type), type ≠ `none`, replacing today's single `ck-arrow-*`:

```
<marker id="ck-head-{tone}-{type}" viewBox="0 0 10 10" refX="10" refY="5"
        markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse"
        orient="auto-start-reverse">
  arrow:    <path d="M 0,0 L 10,5 L 0,10 Z" fill="var(--ck-edge-{tone})"/>
  triangle: <path d="M 0,0 L 10,5 L 0,10 Z" fill="none" stroke="var(--ck-edge-{tone})" stroke-width="1.2"/>
  open:     <path d="M 0,0 L 10,5 L 0,10" fill="none" stroke="var(--ck-edge-{tone})" stroke-width="1.6"/>
  circle:   <circle cx="6" cy="5" r="4" fill="var(--ck-edge-{tone})"/>
  diamond:  <path d="M 2,5 L 6,1 L 10,5 L 6,9 Z" fill="var(--ck-edge-{tone})"/>
  bar:      <path d="M 8,1 V 9" fill="none" stroke="var(--ck-edge-{tone})" stroke-width="2"/>
</marker>
```

Attribute order inside the marker element is free; the element order (tone-major, then type in
`HEADS` order) must match between the two renderers.

**R6.3** Edge drawing: `marker-end="url(#ck-head-{tone}-{end})"` when `end != "none"`, and
`marker-start="url(#ck-head-{tone}-{start})"` when `start != "none"` — attribute in that order,
after `stroke-width` and the dash attribute.

**R6.4** Back-compat: a document with `bidir: true` and no `start` renders an arrow at both ends
(today's behaviour) and must not be rewritten by a reader. `boardkit.edge(…)` keeps its positional
signature and gains `start=None, end=None` at the end; it writes `start`/`end` into the returned
dict **only when a non-null value was supplied** (never `"start": null`), and keeps
`"bidir": True` when `bidir` was passed true. `boardkit.box(…)` likewise never writes an empty
`parts`/`split` or `align: "center"`.

## R7. Editor UI (lane A emits the HTML, lane B wires it up)

Toolbar (one row, `page()`'s `.ck-bar`): add after `connect`
`<button data-act="grid" aria-pressed="false">grid</button>`. Behaviour (lane B): toggles
`state.grid_on`, re-renders, `say("grid on"|"grid off")`, keeps every `aria-pressed` in sync at
boot (extend `syncButtons`).

Panel — new controls, in this order, after the existing `shape` select; ids and option values
exactly as written:

* `<label>align</label><select id="ck-align"><option value="center">center</option><option value="left">left</option></select>`
* `<label>parts</label><textarea id="ck-parts" rows="4"></textarea>` (one part per line)
* `<label>split</label><select id="ck-split"><option value="rows">rows</option><option value="columns">columns</option></select>`
* `<label>start</label><select id="ck-start">…HEADS…</select>` and
  `<label>end</label><select id="ck-end">…HEADS…</select>` (`HEADS` order, values as in R6.1)

Behaviour (lane B): `ck-align`/`ck-split` set `item.align` / `item.split`;
`ck-parts` splits on `\n`, drops blank lines, and sets `item.parts` to `[{text: line}, …]` —
merging by index: when the new line count equals the previous part count, each line keeps the
previous part's `sub` at that index (so editing a part's text does not silently discard the
subtitles of the parts around it); with a different count the parts are rebuilt from the lines
alone. An empty textarea **deletes** `item.parts`; `ck-start`/`ck-end` set the
edge's head fields. Disabled (like today's `sub` field) when the selection is not a node, resp.
not an edge; `openPanel()` fills all of them, using the effective defaults from R6.1 for edges.

## R8. Ops and tool surface (lane C)

* `add_node` accepts `parts`, `split`, `align` and writes them onto the node dict. **Do not pass
  them as kwargs to `boardkit.box(…)`)**: `box()` only gains those kwargs in lane A and your suite
  must pass on the library as it is today. Call `box(...)` with the fields it already takes, then
  set `node["align"]`, `node["parts"]`, `node["split"]` on the returned dict. Normalise `parts` at
  write time into `[{"text": …}]` dicts (a bare string per entry means a part with no `sub`; a
  dict keeps its `sub`), drop parts with empty text, and never write an empty `parts`/`split` or
  `align: "center"` — absent means the default. `text` may be empty **when normalised `parts` is
  non-empty** (a headerless split box): `_require` must accept that case, and a test must build one
  through `board_apply`.
  When `parts` is a non-empty list and `h` was not given:
  `h = (28 if text else 0) + 24 * len(parts) + 16`. `w` default stays 190.
* `update_node` accepts `parts`, `split`, `align`; `parts: []` clears the split (the whitelist
  must not treat `[]` as "not given").
* `add_edge`/`update_edge` accept `start`, `end`; a value outside the plugin's own
  `HEADS = ("none", "arrow", "open", "triangle", "circle", "diamond", "bar")` (defined in
  `store.py`, **not** imported from boardkit) raises `ValueError` naming the valid list. Set the
  head fields on the dict returned by `boardkit.edge(...)` — again, do not pass them as kwargs.
  `bidir: true` with no `start` stores `start: "arrow"`.
* New op `set_grid {size?, on?, style?, snap?}` — sets `grid` (positive number, else
  `ValueError`), `grid_on` (bool), `grid_style` (`"lines"|"dots"`), `snap` (bool); returns a
  human line like `grid: on, 20px, dots`.
* `boardkit.box(…)` gains `align=None`, `parts=None`, `split=None` as **keyword** arguments after
  the existing ones (lane A implements it for hand-authored documents — your ops set the fields
  instead, see above). Your suite must additionally assert `store.HEADS == boardkit.HEADS` when the
  resolved library defines `HEADS`, and print a loud skip when it does not yet (lane A lands in
  parallel) — that is the cross-check that the two enums agree.
* `OPS_HELP` (tools.py) documents every new field and `set_grid`; `_summary` (tools.py) reports
  `grid: {on, size, style}` and, per node, `align`/`parts` count; per edge, `start`/`end`.
* `diff_boards` (store.py) describes the new edits in plain language (`split into 3 parts`,
  `align left`, `head open -> diamond`, `grid on, 20px, dots`) and still returns **no** event for a
  re-save that changed nothing (the pane saves on every drag).
* `README.md`: a `## Primitives (v1.2)` section — the shape list, `parts`/`split`, heads, grid, the
  parity gate, and the `vendor/` ↔ skill sync direction. Insert it; do **not** rewrite the
  `## The pane` section (another branch is editing it).
* `plugin.yaml`: `version: 1.2.0`.

## R9. Lane gates (all three, before committing)

```bash
PY=$(ls -d ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
$PY tests/test_tools.py            # lane C
$PY tests/test_api.py              # lane C (FastAPI + ASGI; the Hermes venv has it)
HERMES_HOME=$(mktemp -d) $PY tests/test_vendor.py   # every lane
# the orchestrator's integration gate (after the merge, R11): $PY tests/test_editor.py
node tests/pane_smoke.mjs          # every lane (stubbed pane render, unchanged)
# lane A additionally: $PY tests/test_render.py   (its own new suite)
# lane B's own evidence — run this from the repo root, exactly as written:
#   $PY - <<'PYEOF'
#   import sys
#   sys.path.insert(0, "vendor")
#   import boardkit
#   b = boardkit.load("docs/fixture-v1.2.json")
#   boardkit.report("/tmp/fx-parity.html", b, bridge=True)
#   print(boardkit.selftest("/tmp/fx-parity.html"))   # ok:true + the new shapes/heads/grid steps
#   PYEOF

```
Lane B's own evidence: render `docs/fixture-v1.2.json` through `boardkit.page(bridge=True)` and
run `boardkit.selftest()` — the report's `ok` must be true and it must contain the new
`shapes`/`heads`/`grid` steps it added to the `#selftest` block.

## R10. Non-goals (Wave 2 — do not start these)

Pan/zoom, multi-select and marquee, copy/paste, connector attach points, per-part tones,
image/asset nodes, free drawing, `start_label`/`end_label`, arrow-head size control, swimlanes.

## R11. CI wiring (orchestrator)

`.github/workflows/tests.yml` runs the two new gates, because a gate that CI never invokes is not a gate:

* `python_tests` (chromium is installed and asserted there already) additionally runs
  `python tests/test_editor.py` — the cross-renderer parity + editor self-test suite;
* `pane_smoke` additionally runs `node --check vendor/board_editor.js`, so a syntax error in the
  inlined editor fails the pipeline instead of only the pane.

`tests/test_editor.py` must pass locally on the merged tree before the branch is pushed, and the
pipeline must be read back (not assumed) before the MR is reported as ready.

## Round-1 dispositions (independent gap pass, `docs/SPEC-v1.2-primitives.gaps-r1.md`)

The lanes started from the pre-disposition text; the code-side dispositions are applied by the
orchestrator **after** the lane merge, in one commit, and the verifier checks the merged head:

| Gap | Disposition |
|-----|-------------|
| 1 MAJOR `start` null vs legacy `bidir` | R6.1/R6.4 amended: null/unknown is absent, the factory never writes a null head; a factory `bidir` edge and a factory default edge must render as before (verifier: render both, both renderers). |
| 2 MAJOR headerless split via ops | R8 amended: `text` may be empty when `parts` is non-empty; covered by a `board_apply` test. |
| 3 MAJOR textarea edit drops part `sub` | R7 amended (index merge) and R8 amended (ops keep a submitted `sub`); covered by a parts edit test. |
| 4 MAJOR parity gate not in CI | R11 added: `tests/test_editor.py` joins the `python_tests` job; the parity check is written to discriminate (it fails on any asymmetric change). |
| 5 MINOR lane-B recipe not runnable | R9 recipe corrected to real code. |
| 6 MINOR `parts: []` on a pill | R4 amended: no parts left ⇒ render the node normally for its kind. |
| 7 MAJOR icon caption wrap (found by the orchestrator in lane B's log, mid-flight) | R2 amended: the caption is the FIRST wrapped line at `w - 12`, `text-anchor="middle"` — an unwrapped caption on one side and a wrapped one on the other is exactly the drift `tests/test_editor.py` exists to catch. |

