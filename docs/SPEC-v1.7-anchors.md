# SPEC v1.7 — anchor points along a box's border

Status: **draft for the user's approval, then frozen for the v1.7 swarm.** Two lanes build against
this file in parallel; the orchestrator merges, dispositions and gates. Requirement ids (`A…`) are
cited by the cards, the tests, the commit messages and the verifier. If something here is wrong or
impossible, say so in your lane report — do not silently invent a different field name, enum value or
coordinate.

Base commit: `ddee72c` (`origin/main`, the v1.6.1 merge). The pre-wave render baseline for **all six**
shipped documents is pinned in `docs/baseline-v1.7.json`, both halves, at this commit.

Where this comes from (the user's words, 2026-10-14, right after painting his first line in the
shipped pane): *"when I paint a line to a box it is always connected to center of box line. Could we
add connected points? I think we need to have connect point for each quarter of box line. Also if I
paint line close to the box to show all connect points of box."* His two decisions: **12 discrete
points (3 per side at 25 / 50 / 75 %)** and **edges get them too**.

## A0. Evidence (measured, not eyeballed)

* Today a painted end lands at 50 % of a side, wherever the pointer was: `snapEnd` records only
  `{ref, side}` and the resolver takes the side's midpoint — `sideMid(r, side)` in
  `vendor/board_editor.js` and `_side_mid(rect, side)` in `vendor/boardkit.py`. An agent-authored edge
  behaves the same way through `anchors()`/`_route()`.
* The change must not move a single existing picture: a document without the new fields keeps
  choosing 0.5, i.e. exactly today's point. That is a gate, not a promise —
  `docs/baseline-v1.7.json` pins the Python SVG **and** the editor DOM of all six shipped documents
  (fixture-v1.2 … v1.5, fixture-v1.6, fixture-v1.6-route) at `ddee72c`, and
  `scripts/merged_head_check.py` reads every `docs/baseline-v*.json` and must stay `clean`/exit 0.

## A1. Lane map (file ownership — do not touch another lane's files)

| Lane | Branch | Assignee (precedent) | Owns (only these) | Features |
|------|--------|---------------------|-------------------|----------|
| **G** geometry + schema + panel markup | `feat/v17-anchor-geometry` | `weatherstore` (the v1.2–v1.6 drawing lane) | `vendor/boardkit.py`: `_side_mid` → a position-aware midpoint, `anchors`, `_route`, `line()`/`edge()` factories, the line-anchor resolver, the readers' normalisation, `bounds()`, and `page()`'s panel markup for the new position rows (+ `_CSS` if a rule is needed); `vendor/board_editor.js`: `sideMid`, `anchors`, `route`, the line resolver in `lineSvg`, `normalise()`; `store.py`, `tools.py`, `tests/test_render.py`, `tests/test_tools.py` | A2, A3, A6.1 |
| **I** interaction + panel wiring | `feat/v17-anchor-interaction` | `gitlab-developer` (the v1.2–v1.6 interaction lane) | `vendor/board_editor.js`: `snapEnd` → the position-aware snap, the candidate-point overlay and its branch in `render()`, the endpoint/vertex drag paths, the panel functions for the new rows and their `bind`s, the self-test steps `anchorpoints`, `anchorsnap`, `anchordrag` | A4, A5, A6.2–A6.4 |
| orchestrator | — | — | `docs/SPEC-v1.7-anchors.md` (this file), `docs/fixture-v1.7.json`, `docs/baseline-v1.7.json`, `scripts/baseline_svg.py`, `scripts/merged_head_check.py`, `tests/test_editor.py`, `.github/workflows/tests.yml`, the merge, the dispositions, the demo, the MR, the vendor → chat-charts sync | — |

Shared surfaces, so the split is exact:

* `vendor/boardkit.py` — **G** owns the whole file this wave (geometry, the readers' normalisation
  and `page()`'s panel markup); lane I never edits it.
* `vendor/board_editor.js` — **G** owns `normalise`, `replaceState`, `sideMid`, `anchors`, `route`,
  `lineSvg`; **I** owns the pointer handlers, `snapEnd`/its successor, the draft and drag paths,
  `render()`'s overlay branch, the panel functions and the self-test. `render()` is shared: expect
  one conflict and keep BOTH sides.
* One closure, one definition per name (the editor is a single script scope — a second
  `function foo()` wins silently). Pinned names, each defined by exactly one lane:
  **G** — `sideMid`, `anchors`, `route`, `lineAnchorPoint`; **I** — `snapAnchor`, `anchorSvg`,
  `applyAnchor`. Every other lane only CALLS them.
* No hardcoded colours (`var(--ck-…)` only), no new files except the orchestrator's fixture, no new
  dependencies, no external references.
* Lanes run with `HERMES_HOME=$(mktemp -d)` so the vendor↔skill drift check skips loudly.
* One commit per lane: `v1.7 <lane>: <what>`, body naming the `A…` ids.

## A2. The model: a position along the side

* The CONVENTION, pinned so a check can falsify it: `at` is measured along the side's increasing
  coordinate — `left`/`right` walk y from the top edge, `top`/`bottom` walk x from the left edge. So
  for a rect `(x, y, w, h)`: `left` at 0.25 → `(x, y + 0.25h)`; `right` 0.75 → `(x + w, y + 0.75h)`;
  `top` 0.25 → `(x + 0.25w, y)`; `bottom` 1 → `(x + w, y + h)`. Both halves must agree, and a swapped
  axis is a gate failure, not a matter of taste.
* **Lines** (`state.lines`): the optional anchor objects gain an optional `at` —
  `{"ref": "<node or note id>", "side": "auto|left|right|top|bottom", "at": 0.5}`. `at` is a fraction
  of the side's length: 0 = the start corner, 1 = the end corner, 0.5 = today's midpoint. The editor
  only ever writes 0.25 / 0.5 / 0.75; the model accepts any fraction so a later wave can snap
  continuously without a schema change.
* **Edges**: `from_at` / `to_at`, siblings of `from_side` / `to_side`, same range and default.
* `at` is ABSENT in every document written before this wave and means 0.5 — which is why the six
  pinned baselines must not move.

## A3. The geometry (lane G)

* `_side_mid(rect, side, at=0.5)` / `sideMid(rect, side, at)`: the point `at` along that side —
  `left`/`right` walk `y`, `top`/`bottom` walk `x`, the same direction for both halves, so a given
  `at` is the same point in the PNG and in the pane. Unknown side → `auto` (existing rule).
* `anchors(...)` and `_route(...)` take the two `at` values (edges: `from_at`/`to_at`; lines: the
  `at` of that end's anchor) and pass them to the side resolver. An `auto` end keeps the
  position-based side rule of v1.2 — only its position along the chosen side becomes selectable.
* The line resolver (`_line_anchor_rect` / the editor's `itemById` + `boxOf` path) passes `at` the
  same way; a dangling or malformed anchor still falls back to the stored point (v1.6 rule).
* Validation, three layers, per the v1.6 policy, resolved per value — "clamped" alone is wrong for
  a value that is not a number at all (measured on a scratch copy: `"0.25"` parses as a string, `true`
  as a bool, `-3` is finite, JSON `1e400` becomes `+inf` in Python, and Python's default decoder
  accepts a literal `NaN` while `JSON.parse` refuses it):

  | offered value | reader | pane writer | ops |
  |---|---|---|---|
  | finite number in [0, 1] | used as-is | written | accepted |
  | finite number outside, e.g. `-3`, `1.4` | clamped to 0 / 1 | refused, old value kept, row marked + reason | raise, naming `at`/`from_at`/`to_at` |
  | `1e400` (JSON → `+inf`) | clamped to 1 | refused | raise |
  | string `"0.25"`, boolean | ignored → the anchor keeps 0.5 | refused | raise |
  | literal `NaN` in the document's JSON | Python's decoder accepts it → `at` defaults to 0.5; the editor's `JSON.parse` refuses the whole document (the reader-leniency contract covers fields, never a malformed JSON text) — both halves are correct, and the divergence is stated, not hidden | — | a NaN cannot be offered by a control |
  | JSON `null` | the field is absent → 0.5 | clears to 0.5 | `null` resets it (documented) |

  The gates exercise the clamp, the ignore, the `1e400` and the `-3` cases in BOTH halves and the
  refusal (not merely the acceptance) in the ops; and after importing `1e400`, export writes 1.
* `bounds()` counts the resolved anchor point (unchanged in shape; the resolved point can now be
  anywhere along the side, which the existing accounting already covers).
* `store.py`/`tools.py`: `at` accepted by `add_line`/`update_line`, `from_at`/`to_at` by
  `add_edge`/`update_edge`; `null` clears (an absent `at` IS 0.5, so `null` is the documented way to
  reset it); `OPS_HELP` documents both; `diff_boards` reports a changed position as an edit of that
  end.
* The `edge()`/`line()` factories and the layout helpers pass the fields through unchanged.

## A4. The affordance (lane I)

* `anchorSvg()` draws, in the editor only, the candidate points of the box the pointer is nearest
  to: 12 dots (3 per side at 0.25/0.5/0.75) plus a ring on the one the pointer would take. ALL of it
  is measured in screen pixels through the ACTUAL rendered scale — `svgMetrics().scale`, i.e.
  `rect.width / viewBox.width`, the same factor the pointer conversion and the v1.6 path hit
  tolerance already use — never `view.k` alone: the SVG is `width="100%"`, so at the pane's 440 px a
  900-unit board has a scale well below 1 and `view.k = 1` is not one screen pixel per board unit.
  Drawn
  while `tool === "line"` with a live draft, or while an endpoint of a line or edge is being
  dragged. Dots are 3 px screen-space squares/dots (`var(--ck-edge-default)`), the active one an
  8 px ring in `var(--ck-accent)` — sizes divided by the zoom so they do not grow with it.
* Exactly ONE box owns the overlay. The search runs over nodes and notes, EXCLUDING `area`-kind
  nodes: they are backdrops drawn behind the content, and a large one would otherwise own the overlay
  instead of the box under the pointer. A box is considered when the pointer is inside it or within
  **24 screen px** of its border. Ties (two eligible boxes at the same distance) are resolved
  deterministically: the SMALLER rect first, then the lowest id — so a tie never depends on document
  order. Nothing is drawn when no box qualifies.
* The overlay lives inside the board SVG (`render()`'s output), so it cannot become panel or toolbar
  chrome: the panel is a separate fixed element.
* Nothing about the overlay reaches the model, the exported JSON or the rendered page.

## A5. Snapping and re-anchoring (lane I)

* `snapAnchor(x, y)` replaces `snapEnd`: it returns `{ref, side, at}` for the nearest candidate point
  of the owning box when the pointer is inside that box or within **12 screen px** of one of its 12
  points (tolerances in screen space, divided by `view.k`); otherwise `null`. It writes the point
  itself into `points` exactly as today (the stored point stays the fallback of the dangling-anchor
  rule).
* Painting: the stroke's first and last point take that anchor; interior points never snap (v1.6
  rule unchanged).
* Re-anchoring: dragging an endpoint that carries an anchor moves it between the 12 points of
  whichever box owns the overlay at the drop position (the overlay is what shows the target); drop
  far from every box and the end becomes free (the anchor is detached, the point stays), exactly as
  v1.6 already does for a free drag.
* An edge's two ends become draggable while the edge is selected, but an edge has NO free end: its
  `from`/`to` are node ids the ops require to resolve. Pinned rules:
  * drop on a candidate point of the SAME box → `from_side`/`from_at` (or `to_*`) change;
  * drop on a candidate point of ANOTHER box → that end is RE-POINTED: `from`/`to` becomes the new
    node id (`update_edge` accepts a new `from`/`to` for this, A3), undoable like every other write;
  * drop far from every box → the edge stays attached where it was (nothing is detached — the
    difference from a line, which falls back to a free point);
  * an end handle answers BEFORE the segment drag of Z5.2 (v1.6): a press within that handle starts a
    re-anchor, not a waypoint materialisation;
  * selection and marquee are unchanged: a marquee that touches an end handle still selects the edge,
    and the handle drag still snapshots for undo.

## A6. The panel (lane G for the markup, lane I for the wiring)

* A6.1 (lane G emits the markup, lane I wires it) For a **line**: the `line` section gains its own
  `from`/`to` sub-rows — a side select (`ck-line-from-side` / `ck-line-to-side`, `auto|left|right|top|
  bottom`) — plus the position group under the existing `from`/`to` rows, a position group
  (`id="ck-line-from-at"` / `ck-line-to-at"`) with three buttons labelled `25`, `50`, `75`
  (`data-at="0.25|0.5|0.75"`), exactly one pressed, plus a live hint naming the fraction.
* A6.2 (lane G emits the markup, lane I wires it) For an **edge**: `ck-from-at` / `ck-to-at`, the
  same control shape, in the edge section of the panel (emitted by `page()`, which lane G owns — a
  control lane I wires but cannot create is a feature that does not exist).
* A6.3 Applicability is mechanical, as in v1.5, and the two ends have DIFFERENT predicates (a
  single "has a ref" rule is unimplementable — an edge's ends are always ids, a line's `ref` can
  dangle):
  * a LINE end's rows are visible iff that end's anchor RESOLVES (the `ref` names an existing node or
    note). A free end and a dangling `ref` both hide them: the renderer ignores such an anchor and
    draws the stored point, so a row there would edit a value with no visible effect;
  * an EDGE end's rows are always visible (its `from`/`to` must resolve to exist at all).
  Hidden, never disabled. The map and the renderer must agree in BOTH directions — a row visible for a
  state the renderer ignores, and a field with no row in a state it does render, are both failures.
* A6.4 The displayed value equals the model's (the v1.5 rule), including the case the presets
  cannot express: the model accepts ANY fraction (A2) while the control offers three. So exactly one
  preset is pressed when `at` equals it, and NONE is pressed otherwise — with the hint naming the
  exact percentage (`40 %`) and the model's value left untouched by merely opening or re-rendering
  the panel. A preset click writes that preset. The gate drives both states: an op-authored
  `at: 0.4` (no preset pressed, the hint says 40 %, the model still 0.4), then a click on `75` (the
  model becomes 0.75 and that button is pressed).

## A7. Non-goals (state them; do not build them)

No corner points (0/1) as separate candidates; no continuous snapping along a side (the model allows
it, the UI does not offer it); no anchor points on `groups` or on `area` backdrops as such; no change
to the auto-router's side choice or its crossing geometry (still its own wave); no automatic
distribution of several ends onto different points; no touch-specific affordance; no change to
nodes' own geometry, padding or default sizes; no change to the exported document's existing keys.

## A8. Gates

```bash
PY=$(ls -d ~/.hermes/installs/*/environments/*/venv/bin/python | head -1)
HERMES_HOME=$(mktemp -d) $PY tests/test_vendor.py      # lanes
node --check vendor/board_editor.js
node tests/pane_smoke.mjs
$PY tests/test_render.py                               # lane G
$PY tests/test_tools.py && $PY tests/test_api.py        # lane G
$PY scripts/merged_head_check.py                        # orchestrator: exit 0, every pinned document clean
$PY tests/test_editor.py                                # orchestrator, on the merged head
```

Required new self-test step names: **`anchorpoints`** (the overlay draws exactly 12 points for the
owning box, one is ringed, one box owns it, nothing else appears), **`anchorsnap`** (a stroke's last
point takes the nearest of the 12 with the right `side`+`at`; a point 12 px away does not snap; inside
the box it takes the nearest), **`anchordrag`** (dragging an endpoint re-anchors it to another
candidate point and the drawn path follows; dropping far from every box detaches the anchor and keeps
the point; an edge's end handle does the same) — plus every name from v1.2–v1.6, which must keep
passing, exactly and truly.

**The six old hashes are the backward-compatibility gate; they cannot catch a wrong fraction**, so
the geometry is gated separately and independently of parity (both halves using 0.5, or both swapping
an axis, would pass parity and the old baseline):

* `tests/test_render.py` (lane G) asserts the RESOLVED point for a rect of known geometry, per side
  and per end, computed from the formula the spec pins (A2) — `left`/`right` at 0.25 and 0.75 by the y
  walk, `top`/`bottom` by the x walk, a line's `from` and `to`, an edge's `from`/`to`, and an `auto`
  side with a NON-midpoint fraction (0.75);
* `tests/test_editor.py` (the orchestrator) runs the same assertions through the editor's own
  resolver and requires the same numbers, plus parity on `docs/fixture-v1.7.json` — a document the
  pinned baseline deliberately does not contain;
* the mutant → check mapping the lane must demonstrate: "always 0.5" → the coordinate check;
  "swapped axis" (y walk for `top`) → the top/bottom check; "0 and 1 reversed" → the 0.75/0.25 pair;
  "auto ignores `at`" → the auto case; "line end ignored" / "edge end ignored" → their own cases.

Every new check earns its place by failing on a mutant (remove the fix in a scratch copy, watch THAT
named check go red, report both runs). Two gates belong to the merged head only: the one-definition
check and the baseline run.

Note on real input: unlike the v1.6.1 double-click (where the browser decided whether an event
existed), the snapping here is our own hit-test arithmetic, so synthetic pointer events are
representative — the gate drives them and reads the model back. The overlay is asserted against the
RENDERED DOM.

## A9. Fixture (the orchestrator writes `docs/fixture-v1.7.json`)

One document carrying: (1) a line anchored to a node's `left` side at `at: 0.25`; (2) a line anchored
to a note's `right` side at `at: 0.75`; (3) a line with `from.side: "auto"` and `at: 0.75` (the side is
chosen as before, the position is not); (4) an edge with `from_at`/`to_at` at 0.25/0.75 including one
`auto` side; (5) a line with `at: 1.4` (clamped to 1 by the readers) and one with `at: "half"`
(ignored → 0.5); (6) a line with `at` and a dangling `ref` (the point fallback); (7) a table and a note so the panel's applicability map has its other kinds in the same document;
(8) a large `area`-kind node BEHIND an ordinary box, so the overlay's ownership rule has something to
refuse. The tie rule (two eligible boxes at the same distance) is constructed by the self-test
itself, not by the fixture. All of it pinned in
`docs/baseline-v1.7.json` (already recorded at the base) for the six pre-existing documents.

## A10. Delivery

Branch `feat/v1.7-anchors` off `ddee72c`; lanes branch off its head; the orchestrator merges,
dispositions, runs the gates on the merged head, copies the merged `vendor/*` into
`~/.hermes/skills/data-science/chat-charts/scripts/` in the same command as the merge commit (bumping
that skill's version and its `lines` vocabulary), re-runs `tests/test_vendor.py` with NO isolation,
and opens a PR against this repository's `main` (protected — branch + PR).
`plugin.yaml` → 1.7.0.

## Gaps

Round 1 — findings 8 (CRITICAL 0, MAJOR 8, MINOR 0) | new-actionable 8 | repeat 0 | trade-off 0 |
noise 0 | contract-misread 0 | verdict: request changes. Every finding was reproduced by the pass on
a scratch copy; all eight are applied in the text above:

| # | severity | disposition | where it landed |
|---|----------|-------------|-----------------|
| 1 | MAJOR | fixed | A1/A6.2 — the edge rows' HTML is `page()`'s, so lane G emits it and lane I only wires it |
| 2 | MAJOR | fixed | A5 — an edge has no free end: same-box drop re-anchors (side/at), another box's point re-points `from`/`to`, a far drop changes nothing; handle priority over the v1.6 segment drag; selection/marquee unchanged; `update_edge` accepts a new `from`/`to` |
| 3 | MAJOR | fixed | A6.3 — two predicates: a line end's rows show iff its anchor RESOLVES; an edge end's always; A6.1 states where the line's own from/to rows sit (they do not exist yet) |
| 4 | MAJOR | fixed | A6.4 — a legal non-preset fraction shows NO pressed preset and a hint with the exact percentage, and the model is not rewritten by opening the panel |
| 5 | MAJOR | fixed | A4/A5 — every dot, ring and threshold goes through `svgMetrics().scale` (rect.width / viewBox.width), never `view.k` alone |
| 6 | MAJOR | fixed | A3 — the per-value table (clamp for out-of-range and `1e400`, ignore a string/boolean, NaN defaults to 0.5 in Python and is a document-level `JSON.parse` refusal in the editor, `null` resets) |
| 7 | MAJOR | fixed | A4 — `area` backdrops are excluded from the owning-box search, ties resolve by smaller rect then lowest id, the overlay stays inside the board SVG |
| 8 | MAJOR | fixed | A8/A9 — the coordinate assertions per side/end/auto in both halves plus parity on the new fixture, with the mutant → check mapping; the six pinned hashes stay as the separate backward-compatibility gate |

Ledger line: `round 1 - findings 8 (CRITICAL 0, MAJOR 8, MINOR 0) | new-actionable 8 | repeat 0 |
trade-off 0 | noise 0 | verdict: request changes`.

Boundary — what this wave deliberately does not cover:

* the auto-router (A7) — its own wave, with `docs/fixture-v1.6-route.json` as the reproduction;
* corner points, continuous snapping, several ends sharing a box, touch affordances;
* an edge that ends free (an edge always names two boxes) — the spec makes a far drop a no-op for an
  edge and a detach for a line, deliberately;
* any change to how a node's own geometry is computed.
