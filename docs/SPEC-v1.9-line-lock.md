# SPEC v1.9 — the line tool draws on 45° steps, and the panel's delete becomes a glyph on the name line

Frozen before the first card exists. ONE lane (`L`), one verifier. Two changes, one wave: both of them
touch `vendor/boardkit.py` and `vendor/board_editor.js`, and the v1.8 wave proved what splitting one
file pair across two lanes costs (a conflict in the pointer handlers plus a re-gate) — so this is one
lane, stated with its mechanism rather than as a preference.

## W0. What the user asked for (verbatim, in order)

> "Let add the same strigh line behaviour to to draw a line as for Stright line I mean that we change
> behaviour of existed line and paint them by default only with 45 degrees step and if Shift pressed
> make it free painted. And may be remove dedicated straight line at all due to it is only from A to B
> which is not so good."

Answered forks (the user chose, in their own words):

* the line tool's gesture: **"Click-per-point only: the line tool places a point per click, exactly as
  today, just with the 45° lock (no drag gesture at all)"**;
* the lock vs the box anchor: **"The lock wins by default, but the ANCHOR wins while you hold Shift"**.

> "Also let change the place of delete button in node attributes popup window. Let move it on the sam
> line as node name but pull it right and replace the text to glyph"

## L. Lane L — the line tool (owner: lane L)

## L1 — the `straight` tool is REMOVED

The toolbar's tool group goes back to `select` + `line`: the `data-tool="straight"` button, its glyph in
`_BAR_GLYPHS`, its entry in the editor's fallback bar builder, the `tool === "straight"` mode, its
pointer branches and its commit path are all deleted. No trace of a second drawing tool survives: a
grep for `straight` in `vendor/` finds the spec's own history and the README's changelog sentence, and
nothing in the running code. `setTool` still accepts only `select` and `line`.

## L2 — the 45° lock is the line tool's default

The point a click PLACES is constrained to the nearest multiple of **45°** from the previous point
(horizontal, vertical or diagonal), with the length taken as the pointer vector's **projection** onto
that direction — the same `constrainStep45` helper v1.8 shipped, now the line tool's own rule. It
applies to every new point, so a multi-point path is made of 45° segments; the FIRST point has nothing
to reference and stays where the click lands.

## L3 — the preview shows the constrained point

While a draft is live, the rubber band's free end is the constrained point, not the raw pointer: what
you see is what the click writes. (The chrome-only live path stays — the v1.8 round-3 fix is what makes
this preview cheap.)

## L4 — Shift frees the angle

Holding Shift while placing a point drops the lock for that point: it lands where the pointer is. The
modifier is read per placement, so Shift on and Shift off mix freely in one path.

## L5 — the box anchor moves under Shift

Default (no Shift): the lock wins, and **no anchoring happens** — an end that lands near a box does not
stick to it. Shift: the angle is free AND the existing box-anchor snap applies to the two ENDS with the
band v1.7 pinned (`snapAnchor`, 12 screen px). Interior points never anchor (unchanged).

* The modifier is read at each point placement for the ANGLE, and at the COMMIT for the two ends'
  anchoring — so a path whose last point was placed with Shift anchors its To end when it is committed
  with Shift held.
* The anchor overlay (`.ck-anchors`, the ring and the candidate dots) is drawn **only while Shift is
  held** with a live draft. In default mode it would advertise a snap that no longer happens, and a
  hint that lies is worse than no hint.
* Anchoring is therefore opt-in per end at drawing time. Everything that anchors an end AFTER the fact
  is untouched: the end-drag re-anchor (v1.7 A5), the panel's from/to side and position controls, and
  the `from`/`to` fields the ops write. Existing documents keep their anchors — this changes the
  drawing default, never the model.

## L6 — the rest of the line tool is unchanged

Click-per-point, the double-click/Enter commit, Esc cancelling with no trace, the ≥2-point rule, the
draft never touching the model before it commits, hit testing, path editing (Alt-click vertex
insert/remove, vertex drags), the panel's line section: all as v1.6/v1.7 shipped. A single straight
line is therefore: click the start, double-click the end (or click it and press Enter).

## D. Lane L — the panel's delete control (owner: lane L)

## D1 — where it moves

Today it is the first control of the panel's LAST row: `<div class="ck-row"><button id="ck-delete">delete</button><button id="ck-close">close</button></div>`.
It moves onto the **name line**: a flex container that holds the name row (`#ck-row-text`, unchanged id
and unchanged hiding) and, beside it, a right-pulled cell holding the delete control. The bottom row
keeps `close` alone.

## D2 — a glyph, not a word

The control is icon-only: an inline 16×16 trash SVG in the toolbar's glyph idiom (`currentColor`,
`stroke-width 1.3`), in a ~24×24 button. Its accessible name is not optional: `aria-label="delete the
selected item"` and `title="delete the selected item (Del)"` — an icon-only control with no name is a
control nobody can find with a screen reader or a tooltip.

## D3 — the id and the wiring do not change

The button keeps `id="ck-delete"`, so `board_editor.js`'s binding (`getElementById("ck-delete")` →
`removeSelected`) is untouched, and so is the `deleteNode` self-test step (which calls `removeSelected`
directly). No new handler, no new data-act.

## D4 — it is visible for EVERY selection kind

The name row is hidden for the kinds that have no name (`ck-row-text`'s list in `PANEL_ROWS` excludes
`table`, `edge` and every line). The delete control must NOT inherit that hiding: it lives in its own
cell, its id is **not** a `ck-row-*` id and it is **not** in `PANEL_ROWS` — the same precedent the
anchor rows set (A6.3: the map's per-kind check must not see rows whose visibility is not per-kind).
The result is that the trash sits at the panel's top-right for a line, an edge and a table too, where
the name row is gone.

## D5 — right-pulled, on the name line

The delete cell is `flex: 0 0 auto` with the name row taking the remaining width, so the control is
pulled to the panel's right edge and shares the name line's row: its vertical position is the label's
own line (the "node name" caption), not the bottom of the row. No change to `.ck-panel`'s width, its
padding or its fixed position.

## Z9. Gates (what "done" is measured by)

1. **The harness** (`tests/test_editor.py`, the orchestrator's file — NOT a lane's). The v1.8
   straight-tool checks are replaced by line-tool ones, all read from the rendered page:
   * `line45`: in line mode, click a start, move to a ~40° offset, click, commit → the committed
     polyline's second point is on the step (`|dy| === |dx|`), and the first point is where it was
     clicked (nothing constrains the first point);
   * `lineshift`: the same drag with Shift at the placing click commits the pointer's own point (free),
     and with Shift at the commit the end anchors — read back through the panel's
     `#ck-line-from-side` / the anchor rows' visibility, never from the helper;
   * `lineoverlay`: with a live draft the anchor overlay is absent by default and present while Shift
     is held with the pointer inside a node's rect;
   * `nodrag`: a press-drag-release in line mode places exactly ONE point (no drag gesture) and the
     mode stays armed;
   * the live-stroke identity/churn checks stay as they are (the v1.8 R-series contract is not weakened
     by this wave) — `kept`, `svgSame` and the host budget for a line-mode stroke;
   * `delcontrol`: the delete button shares the name field's line (its box overlaps the name row's
     vertical span and sits to the RIGHT of the field), it is glyph-only (`textContent.trim() === ""`
     and it contains an `<svg>`), it carries an aria-label and a title, it is NOT hidden for a table,
     an edge or a line selection (while `#ck-row-text` IS hidden for those), and it is still the
     element that reaches `removeSelected` (click it and watch the selection go).
2. **The editor's own self-test**: the v1.8 step `straightdraw` is gone, the new step (name it
   `line45`) is present and TRUE, and every other pre-existing step name is still present and TRUE —
   including `barfit` (the bar is one row again, with one control fewer).
3. `tests/test_render.py` still passes with its panel assertions, and `tests/test_tools.py`,
   `tests/test_api.py`, `tests/test_vendor.py`, `node tests/pane_smoke.mjs`,
   `node --check vendor/board_editor.js`, `hermes plugins validate .` are green.
4. The verifier re-derives, with real input where it matters, and tries to BREAK each new gate with a
   mutant: the lock removed (a click must then land off the step and red `line45`), the overlay drawn
   unconditionally (`lineoverlay` red), the delete control put back inside the name row
   (`delcontrol`'s per-kind visibility red).

## Z10. Lane map (function ownership — one lane owns all of it)

| requirement | file | functions / elements |
|---|---|---|
| L1–L6 | `vendor/board_editor.js` | the `tool` values and `setTool`, the line tool's pointer branches (`pointerdown` / `pointermove`), `constrainStep45`, `commitDraft`, `anchorSvg`'s gate condition, the `shiftDown` tracker, the fallback bar builder, the `line45` self-test step |
| L1 | `vendor/boardkit.py` | `_BAR_GLYPHS["straight"]` and the one `bar_button("straight", …)` call in `page()`'s toolbar |
| D1–D5 | `vendor/boardkit.py` | the panel markup: the name row's wrapper, the delete cell, the trash glyph, and the `.ck-panel` CSS rules for both |
| D3 | `vendor/board_editor.js` | nothing — verified by inspection, not changed |
| the harness, docs, version, integration, the MR, the vendor→skill copy | orchestrator | `tests/test_editor.py`, `docs/**`, `README.md`, `plugin.yaml` 1.9.0, `scripts/sync_boardkit.py` |

The lane does NOT touch `tests/**`, `scripts/**`, `docs/**` or `desktop/plugin.js`. The orchestrator
does NOT edit `vendor/**` except to integrate the lane's committed work.

## Z11. Non-goals

No change to the `line` primitive, the ops, either renderer's output, the panel's row map (the delete
control is deliberately outside it), the pane (`desktop/plugin.js`), the fixture set or the baselines.
No drag gesture for the line tool (the user chose click-per-point). No new document field. No curves.
