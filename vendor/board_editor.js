/* boardkit editor — inlined into the page by boardkit.page(interactive=True).
 *
 * The board JSON is the single source of truth: every edit mutates `state` and re-renders the
 * SVG from scratch (rAF-throttled, cheap for boards of this size). Nothing is fetched and no
 * library is used, so the editor runs in the chat widget, the desktop preview pane and a
 * plain browser alike.
 *
 * Loaded with `#selftest` it exercises itself and writes a JSON report into
 * the [pre#ck-selftest] node — that is how boardkit.selftest() verifies the interactive half.
 */
(function () {
  "use strict";
  var payload = document.getElementById("ck-board");
  if (!payload) return;
  var INITIAL = JSON.parse(payload.textContent);
  var host = document.getElementById("ck-board-host");
  var panel = document.getElementById("ck-panel");
  var status = document.getElementById("ck-status");
  var FONT = 12.5, SUB = 10.5, LINE = 15, CHAR = 0.6, INSET = 12, FOLD = 14;
  var HEADS = ["none", "arrow", "open", "triangle", "circle", "diamond", "bar"];   // R6.1
  var SIDES = ["auto", "left", "right", "top", "bottom"];      // W3.1: `auto` is the default
  var HEAD_SIZES = ["small", "medium", "large"];               // W3.1: `medium` is the default
  var HEAD_BOX = { small: 8, medium: 11, large: 16 };          // W3.3: marker box, user units
  var TONES = ["default", "accent", "ok", "warn", "danger", "muted"];   // Y4: known tone names
  var CELL_ALIGNS = ["left", "center", "right"];   // Y4.1/Y4.5: a table cell's align tri-state
  // X5/X6: editor chrome. Handles and guides are drawn from the item's geometry and never written
  // into the document, so the Python renderer and the PNG cannot see them.
  var HANDLE = 8;                       // X5: the handle box, 8x8 px
  var MIN_W = 40, MIN_H = 28;           // X5: the smallest an item may be resized to
  var GUIDE = 6;                        // X6: an alignment match is within this many document px
  var HANDLE_POS = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
  var HANDLE_CURSOR = { nw: "nwse-resize", n: "ns-resize", ne: "nesw-resize", e: "ew-resize",
    se: "nwse-resize", s: "ns-resize", sw: "nesw-resize", w: "ew-resize" };
  var HANDLE_AXES = { nw: [-1, -1], n: [0, -1], ne: [1, -1], e: [1, 0], se: [1, 1], s: [0, 1],
    sw: [-1, 1], w: [-1, 0] };          // per axis: -1 the low edge moves, +1 the high one, 0 none

  var state = normalise(JSON.parse(JSON.stringify(INITIAL)));

  /* A document that came from outside the plugin may simply omit a list (a hand-written JSON, an
   * import, an agent op that wrote only nodes). The renderer tolerates that; the editor must too —
   * measured: a fixture without `notes` threw "Cannot read properties of undefined (reading
   * 'forEach')" during boot and left the server-side SVG in place. Z3.7: this is the ONE place
   * `state.lines` is defaulted — every install path (boot, reset, the bridge, restore) comes
   * through here, so a document that omits the list cannot throw on any path. */
  /* A3 (v1.7), the editor's half of the reader layer: an `at`/`from_at`/`to_at` outside
   * [0, 1] is clamped INTO the model here — so an imported 1e400 exports as 1 — and a string,
   * a boolean or a null is IGNORED: the key drops out, and an absent `at` IS 0.5, so both the
   * clamp and the ignore keep the v1.6 point. The renderer's atOf would resolve the same
   * points either way; writing the resolution back is what keeps the export honest. */
  function cleanAt(value) {
    if (typeof value !== "number" || isNaN(value)) return undefined;
    return value <= 0 ? 0 : value >= 1 ? 1 : value;
  }
  function normaliseAt(item, field) {
    if (!item || typeof item !== "object" || !(field in item)) return;
    var at = cleanAt(item[field]);
    if (at === undefined) delete item[field]; else item[field] = at;
  }
  function normalise(doc) {
    doc = doc || {};
    ["nodes", "notes", "groups", "edges", "lines"].forEach(function (key) {
      if (!Array.isArray(doc[key])) doc[key] = [];
    });
    if (!doc.width) doc.width = 900;
    if (!doc.height) doc.height = 620;
    doc.edges.forEach(function (e) {   // A3: the positions resolve as the document enters
      normaliseAt(e, "from_at");
      normaliseAt(e, "to_at");
    });
    doc.lines.forEach(function (l) {
      if (!l || typeof l !== "object") return;
      ["from", "to"].forEach(function (key) {
        var anchor = l[key];
        // A malformed anchor (a string, a bare side) stays untouched — the reader ignores
        // it as FREE; only a real anchor object gets its `at` resolved.
        if (!anchor || typeof anchor !== "object" || typeof anchor.ref !== "string") return;
        normaliseAt(anchor, "at");
      });
    });
    materializeTables(doc);   // X3.3: auto-sized tables resolve once as the document enters
    return doc;
  }
  var BRIDGE = window.CK_BOARD_BRIDGE === true;   // embedded in the desktop board pane
  var history = [];
  var selection = [];           // W2.1: ordered; the FIRST entry is the primary
  var selected = null;          // == selection[0], kept in sync for the panel and its binds
  var tool = "select";          // Z4.1: the tool mode machine ("select" | "line")
  var draft = null;             // Z4.2: the in-progress line draft; never in state until committed
  var pathDrag = null;          // Z5: a live line-vertex / edge-waypoint / edge-segment drag
  var drag = null;
  var resize = null;            // X5: the live handle drag
  var guides = null;            // X6: the alignment guides drawn under the current drag
  var panDrag = null;           // W1.4: middle-drag / space+left-drag pan
  var marquee = null;           // W2.2: the rubber band, in document coordinates
  var spaceDown = false;        // W1.4
  var clipboard = null;         // W2.4: in-editor only — the system clipboard is never touched
  var view = { x: 0, y: 0, k: 1 };   // W1.1: in memory only — never saved, never in the document
  var pending = false;
  /* R1/R2/R5 (v1.8): the ONE <svg class="ck-board"> element per document, the scene dirty flag,
   * the scene/chrome child boundary and the editor's own rebuild counter (the livepatch step
   * reads it; the harness re-checks the DOM facts from outside). */
  var svgRoot = null;
  var sceneDirty = true;
  var sceneKids = 0;
  var sceneRebuilds = 0;
  var SVGNS = "http://www.w3.org/2000/svg";

  /* The ONE way a new document enters the editor (v1.4 gate round 1, finding 4). Every
   * replacement goes through here, not just the first load: the pane's bridge used to
   * materialise tables and render, so an agent update that omitted a list crashed the pane
   * instead of showing the new board. */
  function replaceState(doc) {
    state = normalise(JSON.parse(JSON.stringify(doc || {})));
    selection = [];
    selected = null;
    history = [];
    drag = null;
    marquee = null;
    resize = null;
    panDrag = null;
    guides = null;
    pending = false;
    svgRoot = null;        // R1: a replaced document gets a fresh <svg> on the next draw
    sceneDirty = true;
    return state;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function wrap(text, widthPx, font) {
    var limit = Math.max(Math.floor(widthPx / (font * CHAR)), 6);
    var words = String(text == null ? "" : text).split(/\s+/).filter(function (w) { return w; });
    var lines = [], current = "";
    words.forEach(function (word) {
      var candidate = current ? current + " " + word : word;
      if (candidate.length <= limit) { current = candidate; return; }
      if (current) lines.push(current);
      while (word.length > limit) { lines.push(word.slice(0, limit)); word = word.slice(limit); }
      current = word;
    });
    if (current) lines.push(current);
    return lines.length ? lines : [""];
  }

  function nodeById(id) {
    for (var i = 0; i < state.nodes.length; i++) if (state.nodes[i].id === id) return state.nodes[i];
    return null;
  }
  function noteById(id) {
    for (var i = 0; i < state.notes.length; i++) if (state.notes[i].id === id) return state.notes[i];
    return null;
  }
  function itemById(id) { return nodeById(id) || noteById(id); }
  function groupById(id) {
    for (var i = 0; i < state.groups.length; i++) if (state.groups[i].id === id) return state.groups[i];
    return null;
  }
  /* X5/X6: the rectangle behind a selection entry — the three types that can be moved, resized and
   * aligned. An edge (and anything unknown) has none, which is how the handles skip it. */
  function refOf(ref) {
    if (!ref) return null;
    return ref.type === "node" ? nodeById(ref.id)
      : ref.type === "note" ? noteById(ref.id)
      : ref.type === "group" ? groupById(ref.id) : null;
  }

  /* ---- selection (W2.1): an ordered list; the first entry is the primary ---- */
  function isSelected(type, id) {
    return selection.some(function (s) { return s.type === type && s.id === id; });
  }
  function setSelection(list) {
    selection = list;
    selected = selection.length ? selection[0] : null;
  }
  function selectOnly(type, id) { setSelection([{ type: type, id: id }]); }
  function toggleSelected(type, id) {   // returns true when the item was ADDED
    if (isSelected(type, id)) {
      setSelection(selection.filter(function (s) { return !(s.type === type && s.id === id); }));
      return false;
    }
    selection.push({ type: type, id: id });
    selected = selection[0];
    return true;
  }
  /* The panel edits the primary exactly as it edited the single selection (W2.1). A lone group
   * primary closes the panel (no group fields), but a multi-selection keeps it open: the hint
   * and the arrange row must stay reachable (X7, amended in gap round 1). A shift-removal only
   * refreshes an open panel, it never opens one. */
  function syncPanel() {
    if (!selection.length ||
        (selected && selected.type === "group" && selection.length === 1)) closePanel();
    else openPanel();
  }
  function afterToggle(added) {
    if (added) syncPanel();
    else if (panel && panel.classList.contains("on")) syncPanel();
    if (!selection.length) closePanel();
  }

  /* ---- edge routing, mirroring boardkit._route / boardkit.anchors ---- */
  /* A3 (v1.7), the reader layer — the same policy as boardkit._at_of, so a document means the
   * same point in both halves: not a number (a string, a boolean, null — and a NaN, which
   * JSON.parse can never produce) reads as the default 0.5, a finite number clamps into
   * [0, 1] (-3 -> 0, 1e400's Infinity -> 1). The ops layer RAISES on the same inputs instead. */
  function atOf(value) {
    if (typeof value !== "number" || isNaN(value)) return 0.5;
    if (value <= 0) return 0;
    if (value >= 1) return 1;
    return value;
  }
  /* A2 (v1.7): `at` is a fraction of the side's length along its INCREASING coordinate —
   * left/right walk y from the top edge, top/bottom walk x from the left — so 0 is the start
   * corner, 1 the end corner and 0.5 (what every pre-v1.7 document means) the midpoint. */
  function sideMid(r, side, at) {   // W3.2 + A2/A3: the point `at` along the side
    at = atOf(at);
    if (side === "left") return { x: r.x, y: r.y + at * r.h };
    if (side === "right") return { x: r.x + r.w, y: r.y + at * r.h };
    if (side === "top") return { x: r.x + at * r.w, y: r.y };
    return { x: r.x + at * r.w, y: r.y + r.h };   // bottom
  }
  function sideChoice(a, b) {   // the side pair v1.2's position rule picks: centres decide BOTH ends
    var sx = (b.x + b.w / 2) - (a.x + a.w / 2), sy = (b.y + b.h / 2) - (a.y + a.h / 2);
    if (Math.abs(sx) >= Math.abs(sy)) return sx >= 0 ? ["right", "left"] : ["left", "right"];
    return sy >= 0 ? ["bottom", "top"] : ["top", "bottom"];
  }
  function sides(a, b) {   // the pre-v1.3 position-based pair, through the SAME side resolver
    var pair = sideChoice(a, b);
    return [sideMid(a, pair[0]), sideMid(b, pair[1])];
  }
  /* W3.2 + A3: `auto` keeps v1.2's position-based side choice (byte-identical at the default
   * 0.5), and an explicit side on one end never moves the other end's choice; fromAt/toAt pick
   * the point ALONG the resolved side — an `auto` end's position is selectable too, only its
   * side stays chosen. Unknown sides fall back to `auto`, positions through `atOf`. */
  function anchors(a, b, fromSide, toSide, fromAt, toAt) {
    if (SIDES.indexOf(fromSide) === -1) fromSide = "auto";
    if (SIDES.indexOf(toSide) === -1) toSide = "auto";
    var auto = sideChoice(a, b);
    return [sideMid(a, fromSide === "auto" ? auto[0] : fromSide, fromAt),
            sideMid(b, toSide === "auto" ? auto[1] : toSide, toAt)];
  }
  function route(a, b, kind, waypoints, fromSide, toSide, fromAt, toAt) {
    if (waypoints && waypoints.length) {
      // W3.2: the first/last anchor of the path is the W3.2 anchor for that end. A3: the
      // edge's own positions ride along; the waypoint-facing auto end keeps 0.5.
      var pts = waypoints.map(function (p) { return { x: p[0], y: p[1] }; });
      var s = anchors(a, { x: pts[0].x, y: pts[0].y, w: 0, h: 0 }, fromSide, "auto",
                      fromAt, 0.5)[0];
      var e = anchors({ x: pts[pts.length - 1].x, y: pts[pts.length - 1].y, w: 0, h: 0 }, b,
        "auto", toSide, 0.5, toAt)[1];
      return [s].concat(pts, [e]);
    }
    if (!kind || kind === "auto") {
      var dx = (b.x + b.w / 2) - (a.x + a.w / 2), dy = (b.y + b.h / 2) - (a.y + a.h / 2);
      kind = Math.abs(dx) >= Math.abs(dy) ? "h" : "v";
    }
    var pair = anchors(a, b, fromSide, toSide, fromAt, toAt);
    if (kind === "straight") return pair;
    if (kind === "h") {
      var mx = (pair[0].x + pair[1].x) / 2;
      return [pair[0], { x: mx, y: pair[0].y }, { x: mx, y: pair[1].y }, pair[1]];
    }
    var my = (pair[0].y + pair[1].y) / 2;
    return [pair[0], { x: pair[0].x, y: my }, { x: pair[1].x, y: my }, pair[1]];
  }

  /* ---- Z3.3/Z4.3/Z5: line geometry, end snapping and waypoint clearance (lane I) ---- */
  function lineOf(id) {
    var lines = state.lines || [];   // lane L's normalise owns the default; read tolerantly (Z3.7)
    for (var i = 0; i < lines.length; i++) if (lines[i].id === id) return lines[i];
    return null;
  }
  /* Z3.3: a resolvable anchor replaces that end's point through the SAME resolver the edges use,
   * the stored pair widened into a zero-size rect. Z3.4: a dangling or malformed anchor keeps the
   * stored point — the line is still drawn and its handles sit on the fallback. */
  function linePts(l) {   // the resolved polyline points — what the renderer draws, handles sit on
    /* Round-1 finding 2: ONE validated-points policy for every path that touches a line — the
     * reader's own normaliser (linePoints) is the ONLY producer. A line whose points do not
     * survive validation (a null/string/boolean entry, fewer than 2 pairs) is skipped here
     * exactly as lineSvg draws nothing for it, so a malformed import can neither crash a
     * pointerdown nor be hit, marqueed or handle-dragged. */
    return linePoints(l) || [];
  }
  /* A2/A4: the 12 candidate points of one resolved box — three per side at 0.25/0.5/0.75, `at`
   * measured along the side's increasing coordinate (left/right walk y from the top, top/bottom
   * walk x from the left), the convention lane G's `sideMid` resolves on the merged head. The
   * candidates are the interaction half's own hit-test arithmetic (lane I), computed here rather
   * than through `sideMid` so they are exact in THIS tree too (a v1.6 `sideMid` ignores `at`). */
  var ANCHOR_ATS = [0.25, 0.5, 0.75];
  function anchorCandidates(r) {
    var out = [];
    ANCHOR_ATS.forEach(function (at) {
      out.push({ side: "left", at: at, x: r.x, y: r.y + at * r.h });
    });
    ANCHOR_ATS.forEach(function (at) {
      out.push({ side: "right", at: at, x: r.x + r.w, y: r.y + at * r.h });
    });
    ANCHOR_ATS.forEach(function (at) {
      out.push({ side: "top", at: at, x: r.x + at * r.w, y: r.y });
    });
    ANCHOR_ATS.forEach(function (at) {
      out.push({ side: "bottom", at: at, x: r.x + at * r.w, y: r.y + r.h });
    });
    return out;
  }
  /* A4: exactly ONE box owns the overlay. Nodes and notes are searched, `area`-kind backdrops
   * EXCLUDED (a large one would otherwise own the overlay instead of the box under the pointer).
   * A box is eligible when the pointer is inside it or within 24 SCREEN px of its border — screen
   * space through svgMetrics().scale (rect.width / viewBox.width), never view.k alone: the SVG is
   * width="100%", so at the pane's 440px a wide board renders well below 1 unit per px. Ties (two
   * eligible boxes at the same distance) resolve to the SMALLER rect, then the lowest id, so a tie
   * never depends on document order. `opts.nodesOnly` excludes the notes (an edge's from/to are
   * node ids — A5). */
  function owningBox(x, y, opts) {
    var m = svgMetrics(), scale = m ? m.scale : 1;
    var tol = 24 / scale;
    var best = null;
    function consider(item) {
      if ((item.kind || "box") === "area") return;   // A4: a backdrop never owns the overlay
      var r = boxOf(item);
      if (!r) return;
      if (x < r.x - tol || x > r.x + r.w + tol || y < r.y - tol || y > r.y + r.h + tol) return;
      var dx = Math.max(r.x - x, 0, x - (r.x + r.w));
      var dy = Math.max(r.y - y, 0, y - (r.y + r.h));
      var d = Math.hypot(dx, dy);
      /* Round-1 finding 2 (A4): ownership is the RADIAL distance to the border — the expanded
       * rectangle above is only a cheap pre-filter, and a corner diagonal passes it while
       * being ~34 screen px away (20 px per axis ≈ 28). Reject before any tie-breaking. */
      if (d > tol) return;
      var area = r.w * r.h;
      if (best) {
        var better = d < best.d - 1e-9 ||
          (Math.abs(d - best.d) <= 1e-9 &&
            (area < best.area - 1e-9 ||
              (Math.abs(area - best.area) <= 1e-9 && item.id < best.id)));
        if (!better) return;
      }
      best = { id: item.id, d: d, area: area };
    }
    state.nodes.forEach(consider);
    if (!(opts && opts.nodesOnly)) state.notes.forEach(consider);
    return best ? itemById(best.id) : null;
  }
  /* A5: snapAnchor is the successor of v1.6's snapEnd. It returns {ref, side, at} for the nearest
   * candidate point of the OWNING box when the pointer is inside that box or STRICTLY within
   * 12 SCREEN px of one of its 12 points (A8: the cutoff is EXCLUSIVE — exactly 12 px away does
   * NOT snap; the same scale rule as the ownership tolerance); otherwise null. Only
   * a stroke's two ENDS snap — interior points never do (the v1.6 rule is unchanged) — and the
   * point itself is written into `points` exactly as v1.6 did (the stored point stays the Z3.4
   * fallback of a dangling anchor). */
  function snapAnchor(x, y, opts) {
    var m = svgMetrics(), scale = m ? m.scale : 1;
    var own = owningBox(x, y, opts);
    if (!own) return null;
    var r = boxOf(own);
    var inside = x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
    var tol = 12 / scale;
    var best = null;
    anchorCandidates(r).forEach(function (c) {
      var d = Math.hypot(x - c.x, y - c.y);
      if (best && best.d <= d) return;
      best = { ref: own.id, side: c.side, at: c.at, d: d };
    });
    /* Round-2: the 12 SCREEN px cutoff is EXCLUSIVE (A8) — exactly tol away rejects. The 1e-9
     * only absorbs the floating-point round-trip of an exactly-12px click through client→doc
     * coordinates (it moves the cutoff by ~1e-10 screen px): the threshold stays 12 px,
     * neither grown nor shrunk, computed through svgMetrics().scale like every other rule. */
    if (!best || (!inside && best.d >= tol - 1e-9)) return null;
    return { ref: best.ref, side: best.side, at: best.at };
  }
  /* A5: the ONE writer that applies a snap result to an end (the pinned lane-I name — every
   * re-anchoring path goes through here). A LINE end takes the anchor object {ref, side, at} or
   * loses it (null = the end becomes free; the point stays, v1.6 behaviour) — the caller owns the
   * undo snapshot there, exactly as v1.6 did. An EDGE end has no free end: null changes nothing;
   * a snap on the SAME box changes from_side/from_at (or to_*); a snap on ANOTHER box re-points
   * the end (from/to becomes the new node id). The edge write snapshots for undo — and only when
   * the drop actually changes something, so a drop back onto the same candidate stays free of
   * history noise. Returns whether the model changed. */
  function applyAnchor(target, end, snap) {
    if (Array.isArray(target.points)) {   // a line end: the anchor object, or none
      if (snap) target[end] = { ref: snap.ref, side: snap.side, at: snap.at };
      else delete target[end];
      return true;
    }
    if (!snap) return false;   // an edge never detaches
    var sideKey = end + "_side", atKey = end + "_at";
    if (target[end] === snap.ref && target[sideKey] === snap.side && target[atKey] === snap.at) {
      return false;
    }
    snapshot();   // the handle drag still snapshots for undo (A5)
    target[end] = snap.ref;
    target[sideKey] = snap.side;
    target[atKey] = snap.at;
    return true;
  }
  /* Z5.3: a waypoint is never left inside a box — one that a drag materialises or moves into any
   * node/note rect is pushed out through the boundary the segment heads toward, plus 12 units of
   * clearance, BEFORE it reaches the model. Edges only; a line's interior points may cross boxes
   * (Z4.3). Two passes: the push-out can land inside the NEXT box. */
  function clearWaypoint(x, y, axis, dir) {
    var rects = [];
    state.nodes.forEach(function (n) { var r = boxOf(n); if (r) rects.push(r); });
    state.notes.forEach(function (t) { var r = boxOf(t); if (r) rects.push(r); });
    for (var pass = 0; pass < 2; pass++) {
      for (var i = 0; i < rects.length; i++) {
        var r = rects[i];
        if (x > r.x && x < r.x + r.w && y > r.y && y < r.y + r.h) {
          if (axis === "y") y = dir < 0 ? r.y - 12 : r.y + r.h + 12;
          else x = dir < 0 ? r.x - 12 : r.x + r.w + 12;
        }
      }
    }
    return [x, y];
  }
  /* Z5.4: hit testing is screen-space — within ~6 screen px of a polyline at any zoom. */
  function distToSeg(px, py, ax, ay, bx, by) {
    var dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy, t = 0;
    if (len2 > 0) t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }
  function segRectHit(ax, ay, bx, by, x0, y0, x1, y1) {   // a segment vs the marquee rect
    function inside(px, py) { return px >= x0 && px <= x1 && py >= y0 && py <= y1; }
    if (inside(ax, ay) || inside(bx, by)) return true;
    function cross(ox, oy, px, py, qx, qy) { return (px - ox) * (qy - oy) - (py - oy) * (qx - ox); }
    function inter(cx, cy, dx, dy, ex, ey, fx, fy) {
      return ((cross(ex, ey, fx, fy, cx, cy) > 0) !== (cross(ex, ey, fx, fy, dx, dy) > 0)) &&
             ((cross(cx, cy, dx, dy, ex, ey) > 0) !== (cross(cx, cy, dx, dy, fx, fy) > 0));
    }
    return inter(ax, ay, bx, by, x0, y0, x1, y0) || inter(ax, ay, bx, by, x1, y0, x1, y1) ||
           inter(ax, ay, bx, by, x1, y1, x0, y1) || inter(ax, ay, bx, by, x0, y1, x0, y0);
  }
  function hitPolyline(x, y, tol) {   // the nearest line/edge polyline within tol, or null
    var best = null;
    (state.lines || []).forEach(function (l) {
      var pts = linePts(l);
      for (var i = 0; i + 1 < pts.length; i++) {
        var d = distToSeg(x, y, pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y);
        if (d <= tol && (!best || d < best.d)) best = { type: "line", id: l.id, seg: i, d: d };
      }
    });
    state.edges.forEach(function (e, i) {
      var a = nodeById(e.from), b = nodeById(e.to);
      if (!a || !b) return;
      var pts = route(a, b, e.route || "auto", e.waypoints, e.from_side, e.to_side,
                      e.from_at, e.to_at);   // A3: the hit path follows the positions
      for (var j = 0; j + 1 < pts.length; j++) {
        var d = distToSeg(x, y, pts[j].x, pts[j].y, pts[j + 1].x, pts[j + 1].y);
        if (d <= tol && (!best || d < best.d)) best = { type: "edge", id: i, seg: j, d: d };
      }
    });
    return best;
  }
  function hitTol() {   // Z5.4: ~6 screen px in document units at the current zoom
    var m = svgMetrics();
    return m ? 6 / m.scale : 6;
  }

  /* ---- rendering ---- */
  function markerShape(tone, type) {   // R6.2, one inner element per head type
    var color = "var(--ck-edge-" + tone + ")";
    if (type === "arrow") return '<path d="M 0,0 L 10,5 L 0,10 Z" fill="' + color + '"/>';
    if (type === "triangle") return '<path d="M 0,0 L 10,5 L 0,10 Z" fill="none" stroke="' + color +
      '" stroke-width="1.2"/>';
    if (type === "open") return '<path d="M 0,0 L 10,5 L 0,10" fill="none" stroke="' + color +
      '" stroke-width="1.6"/>';
    if (type === "circle") return '<circle cx="6" cy="5" r="4" fill="' + color + '"/>';
    if (type === "diamond") return '<path d="M 2,5 L 6,1 L 10,5 L 6,9 Z" fill="' + color + '"/>';
    return '<path d="M 8,1 V 9" fill="none" stroke="' + color + '" stroke-width="2"/>';   // bar
  }

  function gridPattern() {   // R5.3: emitted inside <defs> only when the grid is on
    var g = state.grid || 10;
    var inner = state.grid_style === "dots"
      ? '<circle cx="0.5" cy="0.5" r="1" fill="var(--ck-grid)"/>'
      : '<path d="M 0,0 H ' + g + ' M 0,0 V ' + g + '" fill="none" stroke="var(--ck-grid)" ' +
        'stroke-width="1"/>';
    return '<pattern id="ck-grid" width="' + g + '" height="' + g + '" patternUnits="userSpaceOnUse">' +
      inner + '</pattern>';
  }

  /* W3.3: the default size keeps the plain v1.2 id (byte-identical old documents); other
   * sizes are suffixed. */
  function markerId(tone, type, size) {
    return "ck-head-" + tone + "-" + type + (size === "medium" ? "" : "-" + size);
  }

  /* The (tone|type|size) combos the document's edges actually reference: sized markers are
   * emitted only when used, so a board without `head_size` keeps the exact v1.2 defs. */
  function usedHeadMarkers() {
    var used = {};
    state.edges.forEach(function (e) {
      if (!nodeById(e.from) || !nodeById(e.to)) return;
      var tone = e.tone || "default", size = effectiveHeadSize(e), heads = effectiveHeads(e);
      [heads.start, heads.end].forEach(function (h) {
        if (h !== "none") used[tone + "|" + h + "|" + size] = true;
      });
    });
    return used;
  }

  function markers() {
    var tones = ["default", "accent", "ok", "warn", "danger", "muted"], out = ["<defs>"];
    var used = usedHeadMarkers();
    tones.forEach(function (tone) {
      HEADS.forEach(function (type) {
        if (type === "none") return;
        out.push('<marker id="ck-head-' + tone + '-' + type + '" viewBox="0 0 10 10" refX="10" ' +
          'refY="5" markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" ' +
          'orient="auto-start-reverse">' + markerShape(tone, type) + '</marker>');
        ["small", "large"].forEach(function (size) {
          // W3.3: refX/refY are the marker's own viewBox coordinates (the tip is at (10,5) in the
          // `0 0 10 10` box); only markerWidth/markerHeight carry the pixel size, so the head stays
          // anchored on the endpoint at every size.
          if (!used[tone + "|" + type + "|" + size]) return;
          var box = HEAD_BOX[size];
          out.push('<marker id="' + markerId(tone, type, size) + '" viewBox="0 0 10 10" refX="10" ' +
            'refY="5" markerWidth="' + box + '" markerHeight="' + box +
            '" markerUnits="userSpaceOnUse" orient="auto-start-reverse">' +
            markerShape(tone, type) + '</marker>');
        });
      });
    });
    if (state.grid_on) out.push(gridPattern());
    out.push("</defs>");
    return out.join("");
  }

  function effectiveHeadSize(e) {   // W3.1: an unknown size is the default, never rendered raw
    return HEAD_SIZES.indexOf(e.head_size) === -1 ? "medium" : e.head_size;
  }

  function effectiveHeads(e) {   // R6.1: defaults and the legacy bidir flag (R6.4)
    var end = e.end;
    if (!end || HEADS.indexOf(end) === -1) end = "arrow";
    // A head that is missing, null or unknown is not a head: `start` then means whatever the
    // legacy `bidir` flag says. Never build a marker id out of an unknown value.
    var start = (e.start && HEADS.indexOf(e.start) !== -1)
      ? e.start : (e.bidir ? "arrow" : "none");
    return { start: start, end: end };
  }

  function normaliseParts(raw) {   // R1/R4: a bare string is {text: s}; blank texts are dropped
    var out = [];
    (raw || []).forEach(function (p) {
      var part = typeof p === "string" ? { text: p } : p;
      if (part && String(part.text == null ? "" : part.text).trim() !== "") out.push(part);
    });
    return out;
  }

  /* R4: split box compartments, drawn after the shape — header, dividers, then parts. */
  function splitParts(n, tone, parts, out) {
    var w = n.w, h = n.h, left = n.align === "left";
    var head_h = n.text ? 28 : 0;
    if (head_h) {
      var headStyle = textStyle(n, FONT, 600);   // X4: the header is the node's own text
      var hlines = wrap(n.text, w - 24, headStyle.font);
      var hstart = (head_h - hlines.length * LINE) / 2 + headStyle.font;
      hlines.forEach(function (line, i) {
        out.push('<text x="' + (left ? 12 : w / 2) + '" y="' + (hstart + i * LINE) +
          '" text-anchor="' + (left ? "start" : "middle") + '" font-size="' + headStyle.font +
          '" font-weight="' + headStyle.weight + '" fill="var(--ck-fg)">' + esc(line) + '</text>');
      });
    }
    var count = parts.length, i;
    function partBlock(p, lines, start, tx, anchor, subWidth) {
      lines.forEach(function (line, j) {
        out.push('<text x="' + tx + '" y="' + (start + j * LINE) + '" text-anchor="' + anchor +
          '" font-size="' + FONT + '" font-weight="600" fill="var(--ck-fg)">' + esc(line) + '</text>');
      });
      if (p.sub) {
        wrap(p.sub, subWidth, SUB).forEach(function (line, j) {
          out.push('<text x="' + tx + '" y="' + (start + lines.length * LINE + 2 + j * (SUB + 3)) +
            '" text-anchor="' + anchor + '" font-size="' + SUB + '" fill="var(--ck-muted)">' +
            esc(line) + '</text>');
        });
      }
    }
    if (n.split === "columns") {
      var cell_w = w / count;
      for (i = 1; i < count; i++) {
        out.push('<path d="M ' + (i * cell_w) + ',' + head_h + ' V ' + h + '" fill="none" ' +
          'stroke="var(--ck-tone-' + tone + '-line)" stroke-width="1"/>');
      }
      parts.forEach(function (p, i) {
        var lines = wrap(p.text, cell_w - 12, FONT);
        var block = lines.length * LINE + (p.sub ? SUB + 4 : 0);
        var start = head_h + ((h - head_h) - block) / 2 + FONT;
        partBlock(p, lines, start, left ? i * cell_w + 6 : i * cell_w + cell_w / 2,
          left ? "start" : "middle", cell_w - 12);
      });
    } else {
      var cell_h = (h - head_h) / count;
      for (i = 1; i < count; i++) {
        out.push('<path d="M 0,' + (head_h + i * cell_h) + ' H ' + w + '" fill="none" ' +
          'stroke="var(--ck-tone-' + tone + '-line)" stroke-width="1"/>');
      }
      parts.forEach(function (p, i) {
        var lines = wrap(p.text, w - 16, FONT);
        var block = lines.length * LINE + (p.sub ? SUB + 4 : 0);
        var start = head_h + i * cell_h + (cell_h - block) / 2 + FONT;
        partBlock(p, lines, start, left ? 8 : w / 2, left ? "start" : "middle", w - 16);
      });
    }
  }

  /* X3.1/Y4: a table's rows/cols/cells, normalised — the single source for every table reader.
   * `cells` stays the flat list of TEXTS (Y4.1: an object cell {text, tone?, align?} contributes
   * its `text`); the per-cell overrides live in the parallel `attrs` list, so the panel's
   * textarea keeps meeting strings. `widths` is exactly `cols` positive shares (Y4.2);
   * `rowTones`/`colTones` are exactly `rows`/`cols` entries (Y4.3). */
  function tableGrid(n) {
    function count(v) { v = parseInt(v, 10); return v >= 1 ? v : 2; }
    var rows = count(n.rows), cols = count(n.cols);
    var raw = Array.isArray(n.cells) ? n.cells : (n.cells ? [n.cells] : []);
    var cells = [], attrs = [];
    raw.forEach(function (cell) {
      if (cell !== null && typeof cell === "object") {   // Y4.1: {text, tone?, align?}
        cells.push(cell.text == null ? "" : String(cell.text));
        attrs.push({ tone: TONES.indexOf(cell.tone) >= 0 ? cell.tone : null,
                     align: CELL_ALIGNS.indexOf(cell.align) >= 0 ? cell.align : null });
      } else {
        cells.push(cell == null ? "" : String(cell));
        attrs.push({ tone: null, align: null });
      }
    });
    var need = rows * cols;
    while (cells.length < need) {   // pad ""/blank; extras drop off the slice
      cells.push("");
      attrs.push({ tone: null, align: null });
    }
    return { rows: rows, cols: cols, cells: cells.slice(0, need), attrs: attrs.slice(0, need),
             header: !!n.header, widths: tableColWidths(n, cols),
             rowTones: tableToneList(n.row_tones, rows), colTones: tableToneList(n.col_tones, cols) };
  }

  /* Y4.2: `col_widths` normalised to exactly `cols` positive shares — a missing, non-numeric,
   * non-positive or **non-finite** entry is 1, extras drop. (`Number("Infinity")` is Infinity,
   * which passed the old `> 0` test and made the whole column sum infinite — v1.5 verifier round
   * 2, CRITICAL; this is the editor's own reader, the ops' writer is another layer.) Column j is
   * `share / sum * w` wide, computed overflow-safely in tableSvg. */
  function tableColWidths(n, cols) {
    var raw = Array.isArray(n.col_widths) ? n.col_widths : [];
    var out = [];
    for (var i = 0; i < cols; i++) {
      var v = Number(raw[i]);   // Number, not parseFloat: "2abc" must be invalid like Python's
      out.push(isFinite(v) && v > 0 ? v : 1);
    }
    return out;
  }

  /* Y4.3: a `row_tones`/`col_tones` list normalised to exactly `count` entries — null or an
   * unknown tone name means "no tone there". */
  function tableToneList(list, count) {
    var raw = Array.isArray(list) ? list : [];
    var out = [];
    for (var i = 0; i < count; i++) out.push(TONES.indexOf(raw[i]) >= 0 ? raw[i] : null);
    return out;
  }

  /* Y4.3: a cell's effective background tone — the cell's own, then the row's, then the
   * column's; null means the table's own tone (no fill is drawn). */
  function cellTone(g, index) {
    var r = Math.floor(index / g.cols), c = index % g.cols;
    return g.attrs[index].tone || g.rowTones[r] || g.colTones[c] || null;
  }

  /* Y4.4: true when at least one cell's effective tone differs from the table's own — such a
   * table draws cell fills and squares its base rect (rx=0) so a corner fill cannot poke out. */
  function tablePaints(n, tone) {
    var g = tableGrid(n);
    for (var index = 0; index < g.rows * g.cols; index++) {
      var eff = cellTone(g, index);
      if (eff && eff !== tone) return true;
    }
    return false;
  }

  /* X3.3: ONE effective size — a table's absent or non-positive w/h mean auto-size. Every
   * reader resolves through here (or through materializeTables below), so the visible table
   * never disagrees with its anchors, handles or marquee. */
  function nodeSize(n) {
    var w = parseFloat(n.w), h = parseFloat(n.h);
    if ((n.kind || "box") === "table") {
      var g = tableGrid(n);
      if (!(w > 0)) w = 90 * g.cols;
      // Y4.6: `rows` includes the header row — 28 + 24*(rows-1) with a header, 24*rows without
      if (!(h > 0)) h = (g.header ? 28 : 24) + 24 * (g.rows - 1);
    }
    return { w: w, h: h };
  }

  /* The stored 0x0 becomes the resolved size once, as the document enters the editor: hit
   * tests, anchors, the marquee and the panel then all read the box the draw path renders. */
  function materializeTables(doc) {
    (doc.nodes || []).forEach(function (n) {
      if ((n.kind || "box") !== "table") return;
      var size = nodeSize(n);
      n.w = size.w;
      n.h = size.h;
    });
    return doc;
  }

  /* X3.3/Y4: the cell tone fills, the interior boundaries and column dividers, then the cell
   * texts — mirrors boardkit._table_svg. Y4.6: `rows` counts EVERY row and row 0 IS the header
   * band (28 tall) when g.header — the first cut added a header band on top of `rows` full rows,
   * leaving y = 0..28 empty. Text keeps the split-box constants (FONT, LINE, 600/700, wrap at
   * the column's width - 12, the block centred in its own row's band). Y4.2: a column's width is
   * its `col_widths` share of w. Y4.4: a cell whose effective tone (cell > row > column, Y4.3)
   * differs from the table's own draws one plain rect over its cell box, after the base rect
   * and before the dividers and texts. */
  function tableSvg(n, tone, w, h, out) {
    var g = tableGrid(n);
    // Y4.6 (gap round 1 of v1.4 + the v1.5 gap pass): `rows` counts EVERY row, row 0 IS the header
    // band when g.header (28, not 24), and the bands FILL the effective height — nominal 28/24
    // scaled by h/span — so an explicitly taller table has no orphan region below its last row. At
    // the auto height the scale is 1: exactly 28/24/24, which v1.4-shaped tables depend on.
    var nominal = [], span = 0, r0;
    for (r0 = 0; r0 < g.rows; r0++) {
      var nv = (g.header && r0 === 0) ? 28 : 24;
      nominal.push(nv);
      span += nv;
    }
    span = span || 1;
    function rowH(r) { return h * nominal[r] / span; }
    var tops = [0], i, j;
    for (i = 1; i < g.rows; i++) tops.push(tops[i - 1] + rowH(i - 1));
    // Y4.2 (v1.5 verifier round 2, CRITICAL): the shares are divided by the largest of them before
    // summing. Every scaled share is then <= 1, so the sum is <= cols and no FINITE share list can
    // overflow it (`[1e308, 1e308]` used to sum to Infinity and collapse the divider to x = 0).
    // At the all-1 shares of a v1.4-shaped table this is exactly the old `share / cols * w`.
    var peak = 0, total = 0;
    for (i = 0; i < g.cols; i++) if (g.widths[i] > peak) peak = g.widths[i];
    if (!(peak > 0)) peak = 1;
    for (i = 0; i < g.cols; i++) total += g.widths[i] / peak;
    if (!(total > 0)) total = 1;
    var colW = [], colX = [0];
    for (i = 0; i < g.cols; i++) {
      colW.push(g.widths[i] / peak / total * w);
      if (i > 0) colX.push(colX[i - 1] + colW[i - 1]);
    }
    var tableAlign = CELL_ALIGNS.indexOf(n.align) >= 0 ? n.align : null;   // Y4.5
    // Y4.2/Y4.5 (v1.5 verifier, MAJOR): a legal but extreme share (col_widths [0.1, 100]) makes a cell
    // narrower than the wrap's own floor of six characters, and the text then drew straight through
    // the neighbouring column. A cell that cannot fit its text clips it to its own box — the wrap has
    // a floor and the shares are the document's, so nothing else keeps the text in. Only a cell that
    // needs it gets a clip, which is why every table that fits stays byte-identical to v1.4.
    var clips = [], needsClip = [];
    g.cells.forEach(function (cell, index) {
      needsClip.push(false);
      if (!cell.trim()) return;
      var cc = index % g.cols;
      var widest = 0;
      wrap(cell, colW[cc] - 12, FONT).forEach(function (line) {
        if (line.length > widest) widest = line.length;
      });
      if (widest * FONT * CHAR <= colW[cc] - 12) return;
      var rr = Math.floor(index / g.cols);
      var id = 'tclip-' + esc(String(n.id == null ? "t" : n.id)) + '-' + index;
      needsClip[index] = id;
      clips.push('<clipPath id="' + id + '"><rect x="' + colX[cc] + '" y="' + tops[rr] +
        '" width="' + colW[cc] + '" height="' + rowH(rr) + '"/></clipPath>');
    });
    if (clips.length) out.push("<defs>" + clips.join("") + "</defs>");
    var style = 'fill="none" stroke="var(--ck-tone-' + tone + '-line)" stroke-width="1"';
    for (i = 0; i < g.rows * g.cols; i++) {   // Y4.4: the differing-tone cell fills
      var eff = cellTone(g, i);
      if (!eff || eff === tone) continue;
      var fr = Math.floor(i / g.cols), fc = i % g.cols;
      out.push('<rect x="' + colX[fc] + '" y="' + tops[fr] + '" width="' + colW[fc] +
        '" height="' + rowH(fr) + '" fill="var(--ck-tone-' + eff + '-fill)"/>');
    }
    for (i = 0; i < g.rows - 1; i++) {   // the interior boundaries, i.e. where each row ends
      out.push('<path d="M 0,' + tops[i + 1] + ' H ' + w + '" ' + style + '/>');

    }
    for (j = 1; j < g.cols; j++) {   // vertical dividers span the full height, header included
      out.push('<path d="M ' + colX[j] + ',0 V ' + h + '" ' + style + '/>');
    }
    g.cells.forEach(function (cell, index) {
      if (!cell.trim()) return;   // X3.2: an empty cell is an empty compartment — no <text>
      var r = Math.floor(index / g.cols), c = index % g.cols;
      var lines = wrap(cell, colW[c] - 12, FONT);
      var start = tops[r] + (rowH(r) - lines.length * LINE) / 2 + FONT;
      var weight = g.header && r === 0 ? 700 : 600;
      var align = g.attrs[index].align || tableAlign || "center";   // Y4.5
      var ax, anchor;
      if (align === "left") { ax = colX[c] + 6; anchor = "start"; }
      else if (align === "right") { ax = colX[c] + colW[c] - 6; anchor = "end"; }
      else { ax = colX[c] + colW[c] / 2; anchor = "middle"; }   // v1.4's behaviour, byte-identical
      var clip = needsClip[index] ? ' clip-path="url(#' + needsClip[index] + ')"' : "";
      lines.forEach(function (line, k) {
        // Y4.2/Y4.5 (v1.5 verifier round 2, MAJOR): the weight attribute's quote must be CLOSED
        // before the clip is appended. Concatenating it inside `font-weight="` produced
        // `font-weight="600 clip-path=" url(#tclip-t-0)"`, Chrome computed a clip of "none" and the
        // text drew into the neighbouring cell — the clip was never applied in the JS renderer.
        out.push('<text x="' + ax + '" y="' + (start + k * LINE) +
          '" text-anchor="' + anchor + '" font-size="' + FONT + '" font-weight="' + weight + '"' +
          clip + ' fill="var(--ck-fg)">' + esc(line) + '</text>');
      });
    });
  }

  /* X4: the node's OWN text size/weight. defaultSize/weight are what the site emits today, so
   * absent fields (and "default"/false) stay byte-identical to v1.3; an unknown text_size falls
   * back to the default (X4.4). bold is an OVERRIDE (X4.3): 600, 400 or no attribute become 700. */
  function textStyle(n, defaultSize, weight) {
    var font = n.text_size === "small" ? 10 : n.text_size === "large" ? 17 : defaultSize;
    return { font: font, weight: n.bold ? 700 : weight };
  }

  /* R2 icon kinds: pictogram plus a caption line below it; `sub` is not drawn. */
  function iconSvg(n, tone, kind, out) {
    var w = n.w, h = n.h;
    var fill = 'fill="var(--ck-tone-' + tone + '-fill)"';
    var stroke = 'stroke="var(--ck-tone-' + tone + '-line)"';
    var label_h = LINE + 8, ph = h - label_h, i;
    if (kind === "server") {
      var bw = 0.64 * w, bh = ph - 8;
      out.push('<rect x="' + 0.18 * w + '" y="4" width="' + bw + '" height="' + bh + '" rx="6" ' +
        fill + ' ' + stroke + ' stroke-width="1.4"/>');
      for (i = 0; i < 3; i++) {
        out.push('<rect x="' + (0.18 * w + 0.10 * bw) + '" y="' + (4 + bh * (0.10 + 0.30 * i)) +
          '" width="' + 0.62 * bw + '" height="' + 0.20 * bh + '" rx="2" fill="none" ' + stroke +
          ' stroke-width="1"/>');
        out.push('<circle cx="' + (0.18 * w + 0.88 * bw) + '" cy="' + (4 + bh * (0.20 + 0.30 * i)) +
          '" r="' + Math.min(3, 0.06 * bh) + '" fill="var(--ck-tone-' + tone + '-line)" ' +
          'stroke="none"/>');
      }
    } else if (kind === "computer") {
      out.push('<rect x="' + 0.15 * w + '" y="4" width="' + 0.70 * w + '" height="' + (ph - 18) +
        '" rx="4" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
      out.push('<path d="M ' + 0.5 * w + ',' + (ph - 14) + ' V ' + (ph - 4) + '" fill="none" ' +
        stroke + ' stroke-width="1.2"/>');
      out.push('<path d="M ' + 0.36 * w + ',' + (ph - 4) + ' H ' + 0.64 * w + '" fill="none" ' +
        stroke + ' stroke-width="1.4"/>');
    } else {   // actor: stroke-only stick figure, no node fill
      var r = Math.min(0.14 * ph, 0.13 * w);
      var shoulder_y = 6 + 2 * r + 2;
      var torso_bottom = shoulder_y + (ph - 6 - shoulder_y) * 0.68;
      out.push('<circle cx="' + 0.5 * w + '" cy="' + (6 + r) + '" r="' + r + '" fill="none" ' +
        stroke + ' stroke-width="1.4"/>');
      out.push('<path d="M ' + 0.5 * w + ',' + shoulder_y + ' V ' + torso_bottom + '" fill="none" ' +
        stroke + ' stroke-width="1.4"/>');
      out.push('<path d="M ' + 0.26 * w + ',' + (shoulder_y + (torso_bottom - shoulder_y) * 0.30) +
        ' H ' + 0.74 * w + '" fill="none" ' + stroke + ' stroke-width="1.4"/>');
      out.push('<path d="M ' + 0.5 * w + ',' + torso_bottom + ' L ' + 0.30 * w + ',' + (ph - 2) +
        ' M ' + 0.5 * w + ',' + torso_bottom + ' L ' + 0.70 * w + ',' + (ph - 2) + '" fill="none" ' +
        stroke + ' stroke-width="1.4"/>');
    }
    // R2: ONE wrapped line, as in boardkit; X4.1: the caption is the node's own text
    var capStyle = textStyle(n, FONT, 400);
    out.push('<text x="' + 0.5 * w + '" y="' + (h - 6) + '" text-anchor="middle" font-size="' +
      capStyle.font + '" font-weight="' + capStyle.weight + '" fill="var(--ck-fg)">' +
      esc(wrap(n.text, w - 12, capStyle.font)[0]) + '</text>');
  }

  function nodeSvg(n) {
    var tone = n.tone || "default", kind = n.kind || "box", selectedRing =
      isSelected("node", n.id);   // W2.1: a ring for every selected item
    var size = nodeSize(n), w = size.w, h = size.h;   // X3.3: ONE effective size (tables auto-size)
    var fill = 'fill="var(--ck-tone-' + tone + '-fill)"';
    var stroke = 'stroke="var(--ck-tone-' + tone + '-line)"';
    var out = ['<g class="ck-node" data-id="' + esc(n.id) + '" transform="translate(' +
      n.x + "," + n.y + ')" style="cursor:grab">'];
    if (selectedRing) {
      out.push('<rect x="-5" y="-5" width="' + (w + 10) + '" height="' + (h + 10) +
        '" rx="12" fill="none" stroke="var(--ck-1)" stroke-width="1.2" stroke-dasharray="4 3"/>');
    }
    if (kind === "caption") {
      var capStyle = textStyle(n, FONT, null);   // X4.3: bold adds the attribute where none was
      var weightAttr = capStyle.weight == null ? "" : '" font-weight="' + capStyle.weight;
      wrap(n.text, w, capStyle.font).forEach(function (line, i) {
        out.push('<text x="0" y="' + (capStyle.font + i * LINE) + '" font-size="' + capStyle.font +
          weightAttr + '" fill="var(--ck-tone-' + tone + '-line)">' + esc(line) + '</text>');
      });
      return out.join("") + "</g>";
    }
    if (kind === "area") {   // R2: titled backdrop; drawn behind everything (R5.2)
      out.push('<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="10" ' + fill + ' ' +
        stroke + ' stroke-width="1.4"/>');
      out.push('<path d="M 0,24 H ' + w + '" fill="none" ' + stroke + ' stroke-width="1"/>');
      var titleStyle = textStyle(n, 11, 600);   // X4: the area title is the node's own text
      out.push('<text x="12" y="17" font-size="' + titleStyle.font + '" font-weight="' +
        titleStyle.weight + '" fill="var(--ck-tone-' + tone + '-line)">' +
        esc(wrap(n.text, w - 24, titleStyle.font)[0]) + '</text>');
      return out.join("") + "</g>";
    }
    if (kind === "server" || kind === "computer" || kind === "actor") {
      iconSvg(n, tone, kind, out);
      return out.join("") + "</g>";
    }
    if (kind === "table") {   // X3.2: the table's own text/sub are NOT drawn — cells carry all
      // Y4.4: a table that paints any cell/row/column tone squares its base rect (rx=0) — a
      // squared cell fill would otherwise poke out of the rounded corner.
      out.push('<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="' +
        (tablePaints(n, tone) ? 0 : 9) + '" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
      tableSvg(n, tone, w, h, out);
      return out.join("") + "</g>";
    }
    if (kind === "diamond") {
      out.push('<polygon points="' + (w / 2) + ',0 ' + w + ',' + (h / 2) + ' ' +
        (w / 2) + ',' + h + ' 0,' + (h / 2) + '" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
    } else if (kind === "pill") {
      out.push('<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="' + (h / 2) +
        '" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
    } else if (kind === "cloud") {
      out.push('<path d="M ' + 0.30 * w + ',' + h +
        ' C ' + 0.14 * w + ',' + h + ' ' + 0.04 * w + ',' + 0.86 * h + ' ' + 0.12 * w + ',' + 0.70 * h +
        ' C ' + 0.02 * w + ',' + 0.50 * h + ' ' + 0.16 * w + ',' + 0.30 * h + ' ' + 0.34 * w + ',' + 0.36 * h +
        ' C ' + 0.42 * w + ',' + 0.10 * h + ' ' + 0.72 * w + ',' + 0.08 * h + ' ' + 0.78 * w + ',' + 0.34 * h +
        ' C ' + 0.94 * w + ',' + 0.32 * h + ' ' + 1.02 * w + ',' + 0.56 * h + ' ' + 0.88 * w + ',' + 0.72 * h +
        ' C ' + 0.96 * w + ',' + 0.88 * h + ' ' + 0.84 * w + ',' + h + ' ' + 0.70 * w + ',' + h +
        ' Z" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
    } else if (kind === "cylinder") {
      out.push('<path d="M 0,' + 0.12 * h + ' A ' + 0.5 * w + ',' + 0.12 * h + ' 0 0 1 ' + w + ',' +
        0.12 * h + ' V ' + 0.88 * h + ' A ' + 0.5 * w + ',' + 0.12 * h + ' 0 0 1 0,' + 0.88 * h +
        ' Z" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
      out.push('<ellipse cx="' + 0.5 * w + '" cy="' + 0.12 * h + '" rx="' + 0.5 * w + '" ry="' +
        0.12 * h + '" fill="none" ' + stroke + ' stroke-width="1.4"/>');
    } else if (kind === "document") {
      out.push('<path d="M 0,0 H ' + w + ' V ' + 0.84 * h +
        ' C ' + 0.75 * w + ',' + 0.70 * h + ' ' + 0.50 * w + ',' + 1.00 * h + ' ' + 0.25 * w + ',' + 0.86 * h +
        ' C ' + 0.12 * w + ',' + 0.78 * h + ' ' + 0.06 * w + ',' + 0.92 * h + ' 0,' + 0.84 * h +
        ' Z" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
    } else if (kind === "hexagon") {
      out.push('<polygon points="' + 0.18 * w + ',0 ' + 0.82 * w + ',0 ' + w + ',' + 0.5 * h + ' ' +
        0.82 * w + ',' + h + ' ' + 0.18 * w + ',' + h + ' 0,' + 0.5 * h + '" ' + fill + ' ' +
        stroke + ' stroke-width="1.4"/>');
    } else if (kind === "parallelogram") {
      out.push('<polygon points="' + 0.20 * w + ',0 ' + w + ',0 ' + 0.80 * w + ',' + h + ' 0,' + h +
        '" ' + fill + ' ' + stroke + ' stroke-width="1.4"/>');
    } else {
      out.push('<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="9" ' + fill + ' ' +
        stroke + ' stroke-width="1.4"/>');
    }
    var parts = (kind === "box" || kind === "pill") ? normaliseParts(n.parts) : [];   // R4
    if (parts.length) {
      splitParts(n, tone, parts, out);
      return out.join("") + "</g>";
    }
    // R3: inset/offset table per kind; align:"left" anchors the text at the column's left edge.
    var inset = INSET, offset = 0;
    if (kind === "diamond") inset = 0.45 * h;
    else if (kind === "cloud") { inset = 0.16 * w; offset = 0.02 * h; }
    else if (kind === "cylinder") { inset = 0.14 * w; offset = 0.06 * h; }
    else if (kind === "document") { inset = 0.14 * w; offset = -0.02 * h; }
    else if (kind === "hexagon") inset = 0.22 * w;
    else if (kind === "parallelogram") inset = 0.24 * w;
    var left = n.align === "left";
    var anchor = left ? "start" : "middle", tx = left ? inset : w / 2;
    var own = textStyle(n, FONT, 600);   // X4: the node's own text; `sub` is NOT affected (X4.1)
    var lines = wrap(n.text, w - 2 * inset, own.font);
    var block = lines.length * LINE + (n.sub ? SUB + 4 : 0);
    var start = (h - block) / 2 + own.font + offset;
    lines.forEach(function (line, i) {
      out.push('<text x="' + tx + '" y="' + (start + i * LINE) + '" text-anchor="' + anchor + '" ' +
        'font-size="' + own.font + '" font-weight="' + own.weight + '" fill="var(--ck-fg)">' +
        esc(line) + '</text>');
    });
    if (n.sub) {
      wrap(n.sub, w - 2 * inset, SUB).forEach(function (line, i) {
        out.push('<text x="' + tx + '" y="' + (start + lines.length * LINE + 2 + i * (SUB + 3)) +
          '" text-anchor="' + anchor + '" font-size="' + SUB + '" fill="var(--ck-muted)">' +
          esc(line) + '</text>');
      });
    }
    return out.join("") + "</g>";
  }

  function noteSvg(t) {
    var tone = t.tone || "warn";
    var ring = isSelected("note", t.id);   // W2.1
    var out = ['<g class="ck-note" data-id="' + esc(t.id) + '" style="cursor:grab">'];
    if (ring) {
      out.push('<rect x="-5" y="-5" width="' + (t.w + 10) + '" height="' + (t.h + 10) +
        '" rx="12" fill="none" stroke="var(--ck-1)" stroke-width="1.2" stroke-dasharray="4 3"/>');
    }
    out.push('<path d="M' + t.x + ',' + t.y + ' H' + (t.x + t.w - FOLD) + ' L' + (t.x + t.w) + ',' +
      (t.y + FOLD) + ' V' + (t.y + t.h) + ' H' + t.x + ' Z" fill="var(--ck-tone-' + tone +
      '-fill)" stroke="var(--ck-tone-' + tone + '-line)" stroke-width="1.2"/>');
    out.push('<path d="M' + (t.x + t.w - FOLD) + ',' + t.y + ' L' + (t.x + t.w - FOLD) + ',' +
      (t.y + FOLD) + ' L' + (t.x + t.w) + ',' + (t.y + FOLD) + '" fill="none" stroke="var(--ck-tone-' +
      tone + '-line)" stroke-width="1.2"/>');
    // Y4.7: a note honours text_size/bold like a node's own text; absent stays byte-identical
    // (the site emits no font-weight attribute, bold adds 700 — the X4.3 caption pattern).
    var st = textStyle(t, FONT, null);
    wrap(t.text, t.w - 20, st.font).forEach(function (line, i) {
      out.push('<text x="' + (t.x + 10) + '" y="' + (t.y + 18 + i * LINE) + '" font-size="' +
        st.font + '"' + (st.weight !== null ? ' font-weight="' + st.weight + '"' : "") +
        ' fill="var(--ck-fg)">' + esc(line) + '</text>');
    });
    return out.join("") + "</g>";
  }

  /* Z3.2/Z3.6: the ONE polyline-with-heads builder — edgeSvg and lineSvg share it, so the
   * dash arrays and the marker ids cannot drift between the two. The markup is exactly what
   * the edge loop emitted before the extraction. */
  function polylineSvg(pts, tone, style, start, end, size, width) {
    var path = pts.map(function (p) { return p.x.toFixed(1) + "," + p.y.toFixed(1); }).join(" ");
    var dash = style === "dashed" ? ' stroke-dasharray="7 5"' :
      (style === "dotted" ? ' stroke-dasharray="2 4"' : "");
    return '<polyline points="' + path + '" fill="none" stroke="var(--ck-edge-' + tone + ')" ' +
      'stroke-width="' + width + '"' + dash +
      (end !== "none" ? ' marker-end="url(#' + markerId(tone, end, size) + ')"' : "") +
      (start !== "none" ? ' marker-start="url(#' + markerId(tone, start, size) + ')"' : "") +
      "/>";
  }

  function edgeSvg(e, index) {
    var a = nodeById(e.from), b = nodeById(e.to);
    if (!a || !b) return "";
    var tone = e.tone || "default";
    var pts = route(a, b, e.route || "auto", e.waypoints, e.from_side, e.to_side,
                    e.from_at, e.to_at);   // A3: the drawn path follows the positions
    var sel = isSelected("edge", index);   // W2.1: lane I's only change inside edgeSvg
    var heads = effectiveHeads(e);   // R6.3: marker-end then marker-start, after the dash
    var size = effectiveHeadSize(e);   // W3.3: the edge's own marker box
    var out = '<g class="ck-edge" data-id="e' + index + '" data-from="' + esc(e.from) +
      '" data-to="' + esc(e.to) + '" style="cursor:pointer">' +
      polylineSvg(pts, tone, e.style, heads.start, heads.end, size, sel ? 3 : 1.7);
    if (e.label) {
      var at = e.label_at || "middle", seg;
      if (pts.length < 2) seg = 0;
      else if (at === "first") seg = 0;
      else if (at === "last") seg = pts.length - 2;
      else seg = Math.max(Math.floor(pts.length / 2) - 1, 0);
      var pa = pts[seg], pb = pts[Math.min(seg + 1, pts.length - 1)];
      var lx = (pa.x + pb.x) / 2, ly = (pa.y + pb.y) / 2;
      if (Math.abs(pb.x - pa.x) < 0.5) {
        out += '<text x="' + (lx - 5).toFixed(1) + '" y="' + (ly + 3.5).toFixed(1) +
          '" text-anchor="end" font-size="10" fill="var(--ck-muted)">' + esc(e.label) + '</text>';
      } else {
        out += '<text x="' + lx.toFixed(1) + '" y="' + (ly - 5).toFixed(1) +
          '" text-anchor="middle" font-size="10" fill="var(--ck-muted)">' + esc(e.label) + '</text>';
      }
    }
    return out + "</g>";
  }

  /* ---- lines (Z3): the free polyline, the user's own path ---- */

  /* Z2/Z3.1, the reader's lenient half: an unusable points list (not a list, an entry that is
   * not a pair of finite numbers, fewer than 2 entries) drops the whole ITEM — half a polyline
   * is worse than no line; a coordinate < 0 clamps to 0. Z3.3/Z3.4: a resolvable anchor
   * REPLACES its end through the same resolver the edges use, the stored pair widened into a
   * zero-size rect; a dangling or malformed anchor leaves the stored point — the line is still
   * drawn (edgeSvg's "" for a bad ref is untouched, Z8). */
  /* A3 (v1.7): the point one line end's anchor RESOLVES to — null for a free end, a malformed
   * anchor (not an object, no string ref) or a dangling ref (Z3.4: the stored point is drawn
   * then, and the panel hides that end's rows — A6.3 uses this same predicate). `stored` is the
   * end's own clamped point; a zero-size rect on it is what an `auto` side aims at, exactly as
   * the edges do. Lane G pins this name; lane I calls it, never redefines it. */
  function lineAnchorPoint(item, key, stored) {
    var anchor = item && item[key];
    if (!anchor || typeof anchor !== "object" || typeof anchor.ref !== "string") return null;
    var target = itemById(anchor.ref);   // Z3.4: nodes then notes, at the effective box
    if (!target || !stored) return null;
    var side = SIDES.indexOf(anchor.side) !== -1 ? anchor.side : "auto";
    var stub = { x: stored.x, y: stored.y, w: 0, h: 0 };
    // A3: `at` rides through the same lenient reader the edges use (atOf inside anchors).
    return key === "from"
      ? anchors(boxOf(target), stub, side, "auto", anchor.at, 0.5)[0]
      : anchors(stub, boxOf(target), "auto", side, 0.5, anchor.at)[1];
  }
  function linePoints(item) {
    if (!item || !Array.isArray(item.points)) return null;
    var pts = [];
    for (var i = 0; i < item.points.length; i++) {
      var p = item.points[i];
      if (!Array.isArray(p) || p.length !== 2) return null;
      /* Z2, round-1 finding 3: an entry that is not a pair of finite NUMBERS drops the whole
       * item. The JSON types are validated, never coerced — Number() would read null as 0,
       * "12" as 12 and true as 1, and the two readers would disagree with the ops layer. */
      if (typeof p[0] !== "number" || typeof p[1] !== "number" ||
          !isFinite(p[0]) || !isFinite(p[1])) return null;
      pts.push({ x: Math.max(p[0], 0), y: Math.max(p[1], 0) });
    }
    if (pts.length < 2) return null;
    var fromPt = lineAnchorPoint(item, "from", pts[0]);
    if (fromPt) pts[0] = fromPt;
    var toPt = lineAnchorPoint(item, "to", pts[pts.length - 1]);
    if (toPt) pts[pts.length - 1] = toPt;
    return pts;
  }

  /* Z3.6: one line = a group with one polyline; the heads always sit in the `medium` box
   * (Z3.2), the selection thickness (1.7 / 3) matches the edge rule. */
  function lineSvg(item) {
    var pts = linePoints(item);
    if (!pts) return "";
    var tone = TONES.indexOf(item.tone) !== -1 ? item.tone : "default";
    var style = ["solid", "dashed", "dotted"].indexOf(item.style) !== -1 ? item.style : "solid";
    var start = HEADS.indexOf(item.start) !== -1 ? item.start : "none";
    var end = HEADS.indexOf(item.end) !== -1 ? item.end : "arrow";   // the edge default (Z3.2)
    var sel = isSelected("line", item.id);   // a line selects by its document id
    return '<g class="ck-line" data-id="' + esc(item.id || "line") + '" style="cursor:pointer">' +
      polylineSvg(pts, tone, style, start, end, "medium", sel ? 3 : 1.7) + "</g>";
  }

  function groupSvg(g) {
    var tone = g.tone || "muted";
    var ring = isSelected("group", g.id)   // W2.1: a ring for every selected item
      ? '<rect x="' + (g.x - 5) + '" y="' + (g.y - 5) + '" width="' + (g.w + 10) + '" height="' +
        (g.h + 10) + '" rx="12" fill="none" stroke="var(--ck-1)" stroke-width="1.2" ' +
        'stroke-dasharray="4 3"/>'
      : "";
    return '<g class="ck-group" data-id="' + esc(g.id) + '">' + ring +
      '<rect x="' + g.x + '" y="' + g.y + '" width="' + g.w + '" height="' + g.h + '" rx="12" ' +
      'fill="var(--ck-tone-' + tone + '-fill)" stroke="var(--ck-tone-' + tone + '-line)" ' +
      'stroke-width="1" stroke-dasharray="7 5"/>' +
      '<text x="' + (g.x + 12) + '" y="' + (g.y + 18) + '" font-size="11" font-weight="600" ' +
      'fill="var(--ck-tone-' + tone + '-line)">' + esc(g.text || "") + "</text></g>";
  }

  /* ---- X5/X6: the overlays — resize handles and alignment guides (editor chrome only) ---- */

  /* X5: the handle anchor inside the item's rect. */
  function handlePoint(r, pos) {
    var fx = (pos === "nw" || pos === "w" || pos === "sw") ? 0
      : (pos === "n" || pos === "s") ? 0.5 : 1;
    var fy = (pos === "nw" || pos === "n" || pos === "ne") ? 0
      : (pos === "w" || pos === "e") ? 0.5 : 1;
    return { x: r.x + r.w * fx, y: r.y + r.h * fy };
  }

  /* X5: eight handles for ONE selected node, note or group, above the board and below the marquee.
   * A multi-selection, a live marquee and line mode all draw none — a handle you can see is a
   * handle you can drag, and none of those states has a single item to resize. */
  function handleSvg() {
    if (marquee || tool === "line" || selection.length !== 1) return "";
    var r = boxOf(refOf(selection[0]));   // X3.3: a 0x0 table handles at its effective box
    if (!r) return "";
    var out = ['<g class="ck-handles">'];
    HANDLE_POS.forEach(function (pos) {
      var c = handlePoint(r, pos);
      out.push('<rect class="ck-handle" data-handle="' + pos + '" x="' + (c.x - HANDLE / 2) +
        '" y="' + (c.y - HANDLE / 2) + '" width="' + HANDLE + '" height="' + HANDLE +
        '" fill="var(--ck-1)" style="cursor:' + HANDLE_CURSOR[pos] + '"/>');
    });
    return out.join("") + "</g>";
  }

  /* Z5.1/Z5.2: vertex handles for the one selected line, waypoint handles for the one selected
   * edge. Same chrome idiom as the resize handles (drawn from the model, never written into it);
   * a live marquee or draft draws none. */
  function vtxSvg() {
    if (marquee || draft || selection.length !== 1) return "";
    var out = [];
    function handle(attr, p) {
      out.push('<rect class="ck-vtx" ' + attr + ' x="' + (p.x - HANDLE / 2) + '" y="' +
        (p.y - HANDLE / 2) + '" width="' + HANDLE + '" height="' + HANDLE +
        '" fill="var(--ck-1)" style="cursor:move"/>');
    }
    if (selection[0].type === "line") {
      var l = lineOf(selection[0].id);
      if (!l) return "";
      linePts(l).forEach(function (p, i) {
        handle('data-line="' + esc(l.id) + '" data-vtx="' + i + '"', p);
      });
    } else if (selection[0].type === "edge") {
      var ei = selection[0].id;
      var e = state.edges[ei];
      if (!e) return "";
      var ea = nodeById(e.from), eb = nodeById(e.to);
      if (ea && eb) {   // A5: the two end handles, at the resolved route anchors
        var epts = route(ea, eb, e.route || "auto", e.waypoints, e.from_side, e.to_side,
                         e.from_at, e.to_at);   // A3: the handles sit on the resolved positions
        handle('data-edge="' + ei + '" data-eend="from"', epts[0]);
        handle('data-edge="' + ei + '" data-eend="to"', epts[epts.length - 1]);
      }
      if (Array.isArray(e.waypoints)) {   // Z5.2: the waypoint handles, as v1.6
        e.waypoints.forEach(function (p, i) {
          handle('data-edge="' + ei + '" data-wp="' + i + '"', { x: p[0], y: p[1] });
        });
      }
    }
    return out.length ? '<g class="ck-vtxs">' + out.join("") + "</g>" : "";
  }

  /* A4: the anchor-point overlay — the 12 candidate points of the ONE owning box as 3px dots
   * (squares; one dot per candidate), the candidate the pointer WOULD take ringed (4px). Sizes
   * are SCREEN-space (divided by the rendered scale). Editor chrome only: the overlay never
   * enters the model, the history, the exported document or the baseline, so it draws from a
   * render-local group and needs no cleanup. Drawn while tool === "line" with a live draft, or
   * while an endpoint of a line or an edge is being dragged — and nothing otherwise. */
  function anchorSvg() {
    var px = null, py = null, opts = null;
    if (tool === "line" && draft && draft.hover) {
      px = draft.hover[0]; py = draft.hover[1];
    } else if (pathDrag && pathDrag.hover &&
        (pathDrag.kind === "eend" ||
          (pathDrag.kind === "vtx" && pathDrag.line &&
            (pathDrag.index === 0 || pathDrag.index === pathDrag.line.points.length - 1)))) {
      px = pathDrag.hover[0]; py = pathDrag.hover[1];
      if (pathDrag.kind === "eend") opts = { nodesOnly: true };
    }
    if (px === null) return "";
    var m = svgMetrics(), scale = m ? m.scale : 1;
    var own = owningBox(px, py, opts);
    if (!own) return "";
    /* Round-1 finding 1 (A4): the ring is a HINT, not the snap's commitment — whenever a box
     * owns the overlay its NEAREST candidate is ringed, INDEPENDENTLY of A5's 12 screen px
     * snap tolerance (the snap only decides what a drop COMMITS, and snapAnchor rightly
     * refuses the 12–24 px band). Computed here so the ring covers the whole ownership band. */
    var cands = anchorCandidates(boxOf(own));
    var ring = null;
    cands.forEach(function (c) {
      var d = Math.hypot(px - c.x, py - c.y);
      if (ring && ring.d <= d) return;
      ring = { side: c.side, at: c.at, d: d };
    });
    var dot = 3 / scale, ringR = 4 / scale;
    var out = ['<g class="ck-anchors" data-owner="' + esc(own.id) + '">'];
    cands.forEach(function (c) {
      out.push('<rect class="ck-adot" data-side="' + c.side + '" data-at="' + c.at + '" x="' +
        (c.x - dot / 2) + '" y="' + (c.y - dot / 2) + '" width="' + dot + '" height="' + dot +
        '" fill="var(--ck-edge-default)"/>');
      if (ring && ring.side === c.side && ring.at === c.at) {
        out.push('<circle class="ck-aring" data-side="' + c.side + '" data-at="' + c.at +
          '" cx="' + c.x + '" cy="' + c.y + '" r="' + ringR +
          '" fill="none" stroke="var(--ck-accent)" stroke-width="' + (1.5 / scale) + '"/>');
      }
    });
    out.push("</g>");
    return out.join("");
  }

  function anchorsOf(r) {   // X6: the three alignment anchors per axis
    return { x: [r.x, r.x + r.w, r.x + r.w / 2], y: [r.y, r.y + r.h, r.y + r.h / 2] };
  }

  /* X3.3 (amended): a table whose w/h are absent or not positive takes its EFFECTIVE size from
   * rows/cols. A 0x0 table is a LEGAL document (the v1.4 fixture's `tbl`), so every reader of an
   * item's geometry — hit tests, marquee bounds, handles and the guides below — must read the
   * resolved box, not the raw one, or the table is picked up at the wrong rectangle. Nothing is
   * written back: the document keeps whatever it had.
   *
   * The v1.4 reviewer found this function keeping its OWN copy of the auto-size formula, which had
   * drifted: boxOf said a 3x2 header table was 100 tall while the draw path said 76, so a 0x0 table
   * was selected, dragged and marqueed by a box 24 units too tall — and the editor's own `resize`
   * step had been written against the stale number, so the two wrongs agreed. There is one helper
   * now (`nodeSize`), used by the draw path and by every reader here. */
  function boxOf(r) {
    if (!r) return null;
    if ((r.kind || "") !== "table") return r;
    var eff = nodeSize(r);
    return { x: r.x, y: r.y, w: eff.w, h: eff.h };
  }

  /* X6: the primary's anchors per axis against the same anchor of every other node, note and group
   * (left to left, right to right, centre to centre — the reading "compare its left/right/h-centre
   * against every other node" has, and the one an editor actually draws guides for). Never the
   * primary itself and never an item inside the dragged selection: the smallest |delta| wins each
   * axis, `at` is the anchor the dragged item lines up with (the nudge target and where the guide is
   * drawn) and `box` is the resolved rectangle it lines up with. */
  function guideMatch(rect, exclude) {
    var best = { x: null, y: null };
    function against(item) {
      var other = boxOf(item);
      var mine = anchorsOf(rect), theirs = anchorsOf(other);
      ["x", "y"].forEach(function (axis) {
        for (var k = 0; k < 3; k++) {
          var delta = theirs[axis][k] - mine[axis][k];
          if (Math.abs(delta) > GUIDE) continue;
          if (best[axis] && Math.abs(best[axis].delta) <= Math.abs(delta)) continue;
          best[axis] = { delta: delta, at: theirs[axis][k], box: other };
        }
      });
    }
    state.nodes.forEach(function (n) { if (!exclude["node:" + n.id]) against(n); });
    state.notes.forEach(function (t) { if (!exclude["note:" + t.id]) against(t); });
    state.groups.forEach(function (g) { if (!exclude["group:" + g.id]) against(g); });
    return best;
  }

  /* X6: one 1px dashed line per matched axis, spanning both aligned items. */
  function guideLines(rect, match) {
    var out = [];
    ["x", "y"].forEach(function (axis) {
      var m = match[axis];
      if (!m) return;
      var other = m.box;
      if (axis === "x") {
        out.push({ x1: m.at, y1: Math.min(rect.y, other.y), x2: m.at,
                   y2: Math.max(rect.y + rect.h, other.y + other.h) });
      } else {
        out.push({ x1: Math.min(rect.x, other.x), y1: m.at,
                   x2: Math.max(rect.x + rect.w, other.x + other.w), y2: m.at });
      }
    });
    return out;
  }

  function guideSvg() {
    if (!guides || !guides.length) return "";
    return '<g class="ck-guides">' + guides.map(function (g) {
      return '<line class="ck-guide" x1="' + g.x1 + '" y1="' + g.y1 + '" x2="' + g.x2 +
        '" y2="' + g.y2 + '" stroke="var(--ck-1)" stroke-width="1" stroke-dasharray="4 3"/>';
    }).join("") + "</g>";
  }

  /* ---- R1/R2/R5 (v1.8): one svg, a dirty-flagged scene, editor-owned chrome ----
   * The <svg class="ck-board"> element is created ONCE per document (boot, ck-board-replace,
   * reset) and kept: a gesture may write its viewBox attribute and patch its children, never
   * replace it. What render() used to emit as one string splits in two:
   *   SCENE — what the Python renderer also draws: markers, the grid rect, groups, areas,
   *           edges, nodes, notes, lines, in the pinned R5.2 order. Rebuilt only when the
   *           dirty flag is set; render() sets it, because every caller of render() has just
   *           written the model or the selection (the selection ring lives INSIDE
   *           nodeSvg/noteSvg/groupSvg). The live gesture paths never call render().
   *   CHROME — editor-only overlays the Python renderer never emits: guides, resize handles,
   *           vertex/waypoint handles, the anchor overlay, the draft preview, the marquee.
   *           Chrome nodes sit AFTER the scene children (indices [sceneKids, ...)); a refresh
   *           removes exactly the old ones and appends the new. At idle chrome is EMPTY, so
   *           the idle DOM — the parity fixture and every recorded baseline — is unchanged
   *           by the split (R7: no wrapper, no new element, no new attribute). */
  function svgOpenTag() {
    // W1.2: the viewBox comes from the view; the W:H aspect is preserved so the pointer math
    // (one uniform scale) keeps working.
    return '<svg class="ck-board" viewBox="' + view.x + ' ' + view.y + ' ' +
      (state.width / view.k) + ' ' + (state.height / view.k) +
      '" width="100%" height="auto" preserveAspectRatio="xMidYMid meet" role="img" ' +
      'aria-label="' + esc(state.title || "board") + '">';
  }
  function sceneMarkup() {
    var w = state.width, h = state.height, out = [markers()];
    // R5.2 draw order: defs -> grid rect -> groups -> areas -> edges -> nodes -> notes -> lines.
    if (state.grid_on) {
      out.push('<rect class="ck-grid" x="0" y="0" width="' + w + '" height="' + h +
        '" fill="url(#ck-grid)"/>');
    }
    state.groups.forEach(function (g) { out.push(groupSvg(g)); });
    state.nodes.forEach(function (n) { if ((n.kind || "box") === "area") out.push(nodeSvg(n)); });
    state.edges.forEach(function (e, i) { out.push(edgeSvg(e, i)); });
    state.nodes.forEach(function (n) { if ((n.kind || "box") !== "area") out.push(nodeSvg(n)); });
    state.notes.forEach(function (t) { out.push(noteSvg(t)); });
    // Z3.6: lines draw last of the board content — an annotation arrow is meant to be seen
    // OVER the boxes it crosses, not buried under a fill. `state.lines` always exists (Z3.7).
    state.lines.forEach(function (l) { out.push(lineSvg(l)); });
    return out.join("");
  }
  function chromeMarkup() {
    var out = [];
    out.push(guideSvg());     // X6: above the board, below the marquee
    out.push(handleSvg());    // X5: above everything except the marquee
    out.push(vtxSvg());       // Z5.1/Z5.2: line vertex and edge waypoint handles
    if (draft && draft.points.length) {   // Z4.2: the rubber preview follows the pointer
      var dpts = draft.hover ? draft.points.concat([draft.hover]) : draft.points;
      out.push('<polyline class="ck-draft" points="' + dpts.map(function (p) {
        return p[0].toFixed(1) + "," + p[1].toFixed(1);
      }).join(" ") + '" fill="none" stroke="var(--ck-1)" stroke-width="1.5" ' +
        'stroke-dasharray="6 4"/>');
    }
    out.push(anchorSvg());   // A4: the anchor-point overlay — above the board, below the marquee
    if (marquee) {   // W2.2: drawn last, above everything
      out.push('<rect class="ck-marquee" x="' + Math.min(marquee.x0, marquee.x1) + '" y="' +
        Math.min(marquee.y0, marquee.y1) + '" width="' + Math.abs(marquee.x1 - marquee.x0) +
        '" height="' + Math.abs(marquee.y1 - marquee.y0) +
        '" fill="var(--ck-1)" fill-opacity="0.08" stroke="var(--ck-1)" stroke-width="1" ' +
        'stroke-dasharray="4 3"/>');
    }
    return out.join("");
  }
  function writeView() {   // R6: pan/zoom is an attribute write on the kept svg
    if (svgRoot) {
      svgRoot.setAttribute("viewBox", view.x + " " + view.y + " " +
        (state.width / view.k) + " " + (state.height / view.k));
    }
  }
  function refreshChrome() {   // R5: remove exactly the old chrome nodes, append the new
    while (svgRoot.childNodes.length > sceneKids) svgRoot.removeChild(svgRoot.lastChild);
    var tmp = document.createElementNS(SVGNS, "g");
    tmp.innerHTML = chromeMarkup();
    while (tmp.firstChild) svgRoot.appendChild(tmp.firstChild);
  }
  function rebuildScene() {   // the kept element's scene children, replaced as one write
    sceneRebuilds += 1;
    svgRoot.innerHTML = sceneMarkup();   // the chrome goes with it; refreshChrome re-appends
    sceneKids = svgRoot.childNodes.length;
    sceneDirty = false;
  }
  function draw() {
    if (!svgRoot) {   // R1: the one creation — boot, ck-board-replace, reset
      var scene = sceneMarkup();
      host.innerHTML = svgOpenTag() + scene + chromeMarkup() + "</svg>";
      svgRoot = host.querySelector("svg.ck-board");
      var tmp = document.createElementNS(SVGNS, "g");
      tmp.innerHTML = scene;
      sceneKids = tmp.childNodes.length;
      sceneDirty = false;
    } else {
      writeView();
      if (sceneDirty) rebuildScene();
      refreshChrome();
    }
    var zoomLabel = document.getElementById("ck-zoom");   // W1.5
    if (zoomLabel) zoomLabel.textContent = Math.round(view.k * 100) + "%";
    save();
    pushChange();
  }
  function render() {   // every caller has just written the model or the selection: rebuild
    sceneDirty = true;
    draw();
  }
  /* The cheap live path (R2/R6): the viewBox and one chrome refresh — the scene is NOT
   * rebuilt. A gesture that wrote the model has already patched its items in place (R3) and
   * leaves the dirty flag set, so the next render() rebuilds and must produce the identical
   * DOM (R4). */
  function drawLive() {
    if (!svgRoot) { render(); return; }
    writeView();
    refreshChrome();
    var zoomLabel = document.getElementById("ck-zoom");   // W1.5
    if (zoomLabel) zoomLabel.textContent = Math.round(view.k * 100) + "%";
    save();
    pushChange();
  }
  /* R3: the in-place patches. A dragged node's position lives in its transform attribute;
   * a note's/group's geometry is baked into its children, so those re-emit their own markup
   * inside the kept <g>; every displaced edge/line re-emits through the SAME edgeSvg/lineSvg
   * (+ route(...)) the full render calls, so the patched picture cannot drift from the drawn
   * one. Top-level scene children are never replaced: the element survives, its content is
   * rewritten. */
  function sceneChild(cls, id) {   // a top-level scene child by class + data-id (no selector parse)
    if (!svgRoot) return null;
    var kids = svgRoot.children;
    for (var i = 0; i < sceneKids && i < kids.length; i++) {
      if (kids[i].getAttribute("class") === cls && kids[i].getAttribute("data-id") === id) {
        return kids[i];
      }
    }
    return null;
  }
  function patchInner(el, markup) {   // re-emit an item's markup INSIDE its kept <g>
    if (!el || !markup) return;
    var tmp = document.createElementNS(SVGNS, "g");
    tmp.innerHTML = markup;
    var fresh = tmp.firstChild;
    if (fresh) el.innerHTML = fresh.innerHTML;
  }
  function patchEdge(index) {
    if (index >= 0 && state.edges[index]) {
      patchInner(sceneChild("ck-edge", "e" + index), edgeSvg(state.edges[index], index));
    }
  }
  function patchLineId(id) {
    var l = lineOf(id);
    if (l) patchInner(sceneChild("ck-line", id), lineSvg(l));
  }
  function displacedPaths(nodeIds, itemIds) {   // every edge/line the moved items carry along
    state.edges.forEach(function (e, i) {
      if (nodeIds[e.from] || nodeIds[e.to]) patchEdge(i);
    });
    (state.lines || []).forEach(function (l) {
      var fr = l.from && l.from.ref, tr = l.to && l.to.ref;
      if ((fr && itemIds[fr]) || (tr && itemIds[tr])) patchLineId(l.id);
    });
  }
  function patchDragged(items) {   // a live node/note/group drag: position only
    var nodeIds = {}, itemIds = {};
    items.forEach(function (entry) {
      var ref = entry.ref;
      itemIds[ref.id] = true;
      if (nodeById(ref.id)) {
        nodeIds[ref.id] = true;
        var el = sceneChild("ck-node", ref.id);
        if (el) el.setAttribute("transform", "translate(" + ref.x + "," + ref.y + ")");
      } else if (noteById(ref.id)) {
        patchInner(sceneChild("ck-note", ref.id), noteSvg(ref));
      } else if (groupById(ref.id)) {
        patchInner(sceneChild("ck-group", ref.id), groupSvg(ref));
      }
    });
    displacedPaths(nodeIds, itemIds);
  }
  function patchResized(ref) {   // X5 live: a resize changes the baked geometry — re-emit inside
    var nodeIds = {}, itemIds = {};
    itemIds[ref.id] = true;
    if (nodeById(ref.id)) {
      nodeIds[ref.id] = true;
      var el = sceneChild("ck-node", ref.id);
      if (el) {
        el.setAttribute("transform", "translate(" + ref.x + "," + ref.y + ")");
        patchInner(el, nodeSvg(ref));
      }
    } else if (noteById(ref.id)) {
      patchInner(sceneChild("ck-note", ref.id), noteSvg(ref));
    } else if (groupById(ref.id)) {
      patchInner(sceneChild("ck-group", ref.id), groupSvg(ref));
    }
    displacedPaths(nodeIds, itemIds);
  }
  function patchRing(s) {   // the selection ring/stroke lives INSIDE the item's markup (W2.1)
    if (s.type === "node") {
      var n = nodeById(s.id);
      if (n) patchInner(sceneChild("ck-node", s.id), nodeSvg(n));
    } else if (s.type === "note") {
      var t = noteById(s.id);
      if (t) patchInner(sceneChild("ck-note", s.id), noteSvg(t));
    } else if (s.type === "group") {
      var g = groupById(s.id);
      if (g) patchInner(sceneChild("ck-group", s.id), groupSvg(g));
    } else if (s.type === "edge") {
      patchEdge(s.id);
    } else if (s.type === "line") {
      patchLineId(s.id);
    }
  }
  function patchSelectionChange(before) {   // the union of the old and new selection, in place
    var seen = {}, todo = [];
    (before || []).concat(selection).forEach(function (s) {
      var key = s.type + ":" + s.id;
      if (!seen[key]) { seen[key] = true; todo.push(s); }
    });
    todo.forEach(patchRing);
  }

  function schedule() {   // the rAF throttle — a LIVE frame: the viewBox and chrome only
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () { pending = false; drawLive(); });
  }

  function snapshot() {
    history.push(JSON.stringify(state));
    if (history.length > 50) history.shift();
  }

  function say(text) { if (status) status.textContent = text; }

  /* ---- persistence: best effort (an opaque-origin widget may refuse) ---- */
  function storageKey() { return "ck-board:" + (state.title || "board"); }
  function save() {
    if (BRIDGE) return;        // the embedding pane owns persistence
    try { window.localStorage.setItem(storageKey(), JSON.stringify(state)); } catch (e) { /* ignore */ }
  }
  function restore() {   // localStorage in the standalone page; the pane uses the bridge instead
    if (BRIDGE) return false;
    try {
      var raw = window.localStorage.getItem(storageKey());
      // Z3.7: restore is an install path too — a stored pre-v1.6 document has no `lines`, and
      // only normalise() defaults it (it materialises the tables as well, a superset of what
      // happened here before).
      if (raw) { state = normalise(JSON.parse(raw)); return true; }
    } catch (e) { /* ignore */ }
    return false;
  }

  /* ---- bridge: talk to the host window (the desktop board pane) ---- */
  function pushChange() {
    if (!BRIDGE) return;
    try { window.parent.postMessage({ type: "ck-board-changed", board: state }, "*"); } catch (e) { /* ignore */ }
  }
  if (BRIDGE) {
    window.addEventListener("message", function (evt) {
      var data = evt.data || {};
      if (data.type === "ck-board-replace" && data.board) {
        replaceState(data.board);   // a list the sender omitted must not crash the pane
        setSelection([]);
        closePanel();
        render();
      }
      if (data.type === "ck-board-request") { pushChange(); }
    });
  }

  /* ---- interaction ---- */
  function svgMetrics() {
    var svg = host.querySelector("svg");
    if (!svg) return null;
    var rect = svg.getBoundingClientRect();
    var vb = svg.getAttribute("viewBox").split(/\s+/);
    return { rect: rect, x: parseFloat(vb[0]), y: parseFloat(vb[1]),
             scale: rect.width / parseFloat(vb[2]) };
  }
  function toSvgPoint(evt) {
    var m = svgMetrics();
    return { x: m.x + (evt.clientX - m.rect.left) / m.scale,
             y: m.y + (evt.clientY - m.rect.top) / m.scale };
  }

  /* W1.3: zoom, clamped to [0.25, 4] after every change, anchored at a document point. */
  function zoomAt(px, py, factor) {
    var k2 = Math.min(4, Math.max(0.25, view.k * factor));
    if (k2 === view.k) return;
    view.x = px - (px - view.x) * (view.k / k2);   // the point under the cursor stays put
    view.y = py - (py - view.y) * (view.k / k2);
    view.k = k2;
    drawLive();   // R6: the viewBox attribute and the chrome (the overlay scales), no rebuild
  }
  function zoomCentre(factor) {   // the toolbar buttons and +/- keys anchor at the view centre
    zoomAt(view.x + state.width / view.k / 2, view.y + state.height / view.k / 2, factor);
  }
  function fitView() { view = { x: 0, y: 0, k: 1 }; drawLive(); }   // W1.3: 0 / fit

  host.addEventListener("wheel", function (evt) {
    var m = svgMetrics();
    if (!m) return;
    evt.preventDefault();
    if (evt.ctrlKey || evt.metaKey) {   // W1.3: ctrl/meta+wheel zooms at the pointer
      zoomAt(m.x + (evt.clientX - m.rect.left) / m.scale,
             m.y + (evt.clientY - m.rect.top) / m.scale,
             evt.deltaY < 0 ? 1.15 : 1 / 1.15);
      return;
    }
    view.x += evt.deltaX / m.scale;   // W1.4: a plain wheel pans, in document units
    view.y += evt.deltaY / m.scale;
    drawLive();   // R6: a pan writes the viewBox, it does not rebuild the scene
  }, { passive: false });

  /* W2.3: every selected draggable moves together; the primary carries the grid snap. */
  function startDrag(evt) {
    var point = toSvgPoint(evt);
    var items = [];
    selection.forEach(function (s) {
      var ref = refOf(s);
      if (ref) items.push({ ref: ref, key: s.type + ":" + s.id, x0: ref.x, y0: ref.y });
    });
    drag = null;
    if (!items.length) return;
    var pref = refOf(selected);
    var exclude = {};   // X6: a guide never matches an item the drag itself carries
    items.forEach(function (entry) { exclude[entry.key] = true; });
    var primary = pref || items[0].ref;
    var pbox = boxOf(primary);   // X3.3: the guides measure the resolved box, not a 0x0 table's
    drag = { px: point.x, py: point.y, items: items, primary: primary, exclude: exclude,
             px0: pbox.x, py0: pbox.y, pw: pbox.w, ph: pbox.h,
             dx: 0, dy: 0, rawX: 0, rawY: 0, moved: false, collapse: null };
    guides = null;
    try { host.setPointerCapture(evt.pointerId); } catch (e) { /* synthetic pointer */ }
  }

  /* X5: a handle drag resizes the one selected item — the opposite edge stays put, the minimum is
   * MIN_W x MIN_H, the moving edge is grid-snapped (not the delta) unless `snap` is false, and the
   * resize takes ONE undo snapshot, on its first movement rather than on every move. */
  function resizeBegin(target, evt) {
    if (selection.length !== 1) return false;
    var r = boxOf(refOf(selection[0]));   // X3.3: start from the box the handles were drawn on
    if (!r) return false;
    var point = toSvgPoint(evt);
    resize = { pos: target.getAttribute("data-handle"), ref: refOf(selection[0]),
               px: point.x, py: point.y, x0: r.x, y0: r.y, w0: r.w, h0: r.h, moved: false };
    try { host.setPointerCapture(evt.pointerId); } catch (e) { /* synthetic pointer */ }
    evt.preventDefault();
    return true;
  }

  function resizeMove(evt) {
    var point = toSvgPoint(evt);
    var dx = point.x - resize.px, dy = point.y - resize.py;
    if (!resize.moved && Math.abs(dx) + Math.abs(dy) < 2) return;   // a click is not a resize
    if (!resize.moved) { resize.moved = true; snapshot(); }
    var axes = HANDLE_AXES[resize.pos], ref = resize.ref, snapOn = state.snap !== false;
    var grid = state.grid || 10;
    var right = resize.x0 + resize.w0, bottom = resize.y0 + resize.h0;
    if (axes[0] === 1) {
      var e1 = resize.x0 + resize.w0 + dx;
      if (snapOn) e1 = Math.round(e1 / grid) * grid;
      ref.x = resize.x0;
      ref.w = Math.max(MIN_W, e1 - resize.x0);
    } else if (axes[0] === -1) {
      var e2 = resize.x0 + dx;
      if (snapOn) e2 = Math.round(e2 / grid) * grid;
      e2 = Math.min(e2, right - MIN_W);   // the opposite (right) edge is the fixed one
      ref.x = e2;
      ref.w = right - e2;
    }
    if (axes[1] === 1) {
      var e3 = resize.y0 + resize.h0 + dy;
      if (snapOn) e3 = Math.round(e3 / grid) * grid;
      ref.y = resize.y0;
      ref.h = Math.max(MIN_H, e3 - resize.y0);
    } else if (axes[1] === -1) {
      var e4 = resize.y0 + dy;
      if (snapOn) e4 = Math.round(e4 / grid) * grid;
      e4 = Math.min(e4, bottom - MIN_H);
      ref.y = e4;
      ref.h = bottom - e4;
    }
    patchResized(ref);    // R3: the live resize patches in place, it does not rebuild the scene
    sceneDirty = true;    // R4: the next full render rebuilds and must produce this same DOM
    drawLive();
  }

  /* Z5.1/Z5.2: the path handles. A plain press starts a drag (the snapshot lands on the first
   * actual movement, like the resize handles); Alt-click edits the path instead. */
  function vtxDown(el, evt) {
    var lineId = el.getAttribute("data-line");
    if (lineId != null) {
      var l = lineOf(lineId);
      if (!l) return;
      var vi = parseInt(el.getAttribute("data-vtx"), 10);
      if (evt.altKey) {
        var anchored = (vi === 0 && l.from) || (vi === l.points.length - 1 && l.to);
        if (anchored) {   // Z5.1: detach the anchor, keep the point
          snapshot();
          if (vi === 0) delete l.from; else delete l.to;
          render();
        } else if (l.points.length > 2) {   // Z5.1: remove the vertex
          snapshot();
          l.points.splice(vi, 1);
          render();
        }
        return;
      }
      if (!isSelected("line", lineId)) { selectOnly("line", lineId); syncPanel(); }
      var pt0 = toSvgPoint(evt);
      pathDrag = { kind: "vtx", line: l, index: vi, hover: [pt0.x, pt0.y], moved: false };
      try { host.setPointerCapture(evt.pointerId); } catch (e) { /* synthetic pointer */ }
      evt.preventDefault();
      render();
      return;
    }
    var edgeIdx = el.getAttribute("data-edge");
    if (edgeIdx != null) {
      var e = state.edges[parseInt(edgeIdx, 10)];
      if (!e) return;
      var eendAttr = el.getAttribute("data-eend");
      if (eendAttr != null) {   // A5: an end handle starts a re-anchor, never a waypoint edit
        if (evt.altKey) return;   // an edge has no free end — Alt does not detach it
        eendBegin(e, parseInt(edgeIdx, 10), eendAttr, toSvgPoint(evt), evt);
        return;
      }
      if (!Array.isArray(e.waypoints)) return;
      var wi = parseInt(el.getAttribute("data-wp"), 10);
      if (evt.altKey) {   // Z5.2: Alt-click removes the waypoint
        snapshot();
        e.waypoints.splice(wi, 1);
        if (!e.waypoints.length) delete e.waypoints;
        render();
        return;
      }
      var point = toSvgPoint(evt);
      pathDrag = { kind: "wp", edge: e, wp: wi, px: point.x, py: point.y, axis: "x", dir: 1,
                   moved: false };
      try { host.setPointerCapture(evt.pointerId); } catch (err) { /* synthetic pointer */ }
      evt.preventDefault();
    }
  }

  /* A5: the end a press near a resolved edge anchor names ("from"/"to"), or null. tol is doc
   * units; the caller passes a screen-space tolerance converted through svgMetrics().scale. */
  function edgeEndAt(e, pt, tol) {
    var a = nodeById(e.from), b = nodeById(e.to);
    if (!a || !b) return null;
    var pts = route(a, b, e.route || "auto", e.waypoints, e.from_side, e.to_side,
                    e.from_at, e.to_at);   // A3: the handle reach follows the positions
    var dF = Math.hypot(pt.x - pts[0].x, pt.y - pts[0].y);
    var dT = Math.hypot(pt.x - pts[pts.length - 1].x, pt.y - pts[pts.length - 1].y);
    if (dF <= tol && dF <= dT) return "from";
    if (dT <= tol) return "to";
    return null;
  }

  /* A5: an edge end starts a re-anchor drag — the model is untouched until the drop. */
  function eendBegin(e, index, end, pt, evt) {
    if (!isSelected("edge", index)) { selectOnly("edge", index); syncPanel(); }
    pathDrag = { kind: "eend", edge: e, index: index, end: end,
                 x0: pt.x, y0: pt.y, hover: [pt.x, pt.y], moved: false };
    try { host.setPointerCapture(evt.pointerId); } catch (err) { /* synthetic pointer */ }
    evt.preventDefault();
    render();
  }

  function lineDown(hitP, pt, evt) {   // Z5.4: a click on (near) a line's polyline
    var already = isSelected("line", hitP.id) && selection.length === 1;
    if (evt.altKey && already) {   // Z5.1: Alt-click a segment inserts a vertex at the click
      var l = lineOf(hitP.id);
      if (l) {
        snapshot();
        l.points.splice(hitP.seg + 1, 0, [Math.max(0, pt.x), Math.max(0, pt.y)]);
        render();
      }
      return;
    }
    if (evt.shiftKey) {   // W2.1: shift toggles, as for every other kind
      afterToggle(toggleSelected("line", hitP.id));
      render();
      return;
    }
    if (!isSelected("line", hitP.id)) selectOnly("line", hitP.id);
    syncPanel();
    render();
  }

  /* Z5.2: dragging a middle segment of the selected edge re-routes it. Without waypoints the
   * segment materialises as exactly two of them (its own endpoints, between the neighbouring
   * anchors) on the first movement; with waypoints, the waypoint-to-waypoint segments drag. */
  function segDragBegin(e, pt, tol, evt) {
    var a = nodeById(e.from), b = nodeById(e.to);
    if (!a || !b) return;
    var pts = route(a, b, e.route || "auto", e.waypoints, e.from_side, e.to_side,
                    e.from_at, e.to_at);   // A3: the materialised segment follows the positions
    var wps = Array.isArray(e.waypoints) ? e.waypoints.length : 0;
    var cand = [], j;
    if (wps >= 2) { for (j = 1; j <= wps - 1; j++) cand.push(j); }   // segment j joins wp j-1, wp j
    else if (!wps) {
      if (pts.length === 2) cand.push(0);   // straight: the one segment between the anchors
      else for (j = 1; j < pts.length - 2; j++) cand.push(j);
    }
    var bestJ = -1, bestD = tol;
    cand.forEach(function (cj) {
      var d = distToSeg(pt.x, pt.y, pts[cj].x, pts[cj].y, pts[cj + 1].x, pts[cj + 1].y);
      if (d <= bestD) { bestD = d; bestJ = cj; }
    });
    if (bestJ < 0) return;
    var p0 = pts[bestJ], p1 = pts[bestJ + 1];
    pathDrag = { kind: "seg", edge: e, seg: bestJ,
      base: [{ x: p0.x, y: p0.y }, { x: p1.x, y: p1.y }],
      axis: Math.abs(p1.x - p0.x) >= Math.abs(p1.y - p0.y) ? "y" : "x",   // the perpendicular axis
      px: pt.x, py: pt.y, moved: false };
    try { host.setPointerCapture(evt.pointerId); } catch (err) { /* synthetic pointer */ }
    evt.preventDefault();
  }

  function edgeDown(ei, pt, evt, tol) {
    var e = state.edges[ei];
    if (!e) return;
    if (evt.shiftKey) {
      afterToggle(toggleSelected("edge", ei));
      render();
      return;
    }
    var already = isSelected("edge", ei) && selection.length === 1;
    if (!already) selectOnly("edge", ei);
    syncPanel();
    if (already && pt && !evt.altKey) {
      // A5: an end handle answers BEFORE the Z5.2 waypoint materialisation — a press within the
      // handle's reach starts a re-anchor, not a bend (the DOM rect answers in vtxDown; this
      // covers the rest of the handle's screen-space reach on the polyline itself).
      var em = svgMetrics();
      var endHit = edgeEndAt(e, pt, 10 / (em ? em.scale : 1));
      if (endHit) eendBegin(e, ei, endHit, pt, evt);
      else segDragBegin(e, pt, tol, evt);   // Z5.2
    }
    render();
  }

  function pathDragMove(evt) {   // Z5: the live vertex / waypoint / segment drag
    var pt = toSvgPoint(evt);
    var pd = pathDrag;
    if (pd.kind === "eend") {   // A5: the overlay tracks the pointer; the model changes on drop
      pd.hover = [pt.x, pt.y];
      if (!pd.moved && Math.abs(pt.x - pd.x0) + Math.abs(pt.y - pd.y0) < 2) return;
      pd.moved = true;
      drawLive();   // R3: the overlay is chrome — one refresh, no scene rebuild
      return;
    }
    if (pd.kind === "vtx") {   // Z5.1: move the point; the ends re-anchor on drop
      pd.hover = [pt.x, pt.y];   // A4: the overlay follows the pointer while an end drags
      var l = pd.line;
      if (!pd.moved) {
        if (Math.abs(pt.x - l.points[pd.index][0]) + Math.abs(pt.y - l.points[pd.index][1]) < 2) {
          return;
        }
        snapshot();
        pd.moved = true;
        // an anchored end detaches as the drag starts, so the preview follows the pointer;
        // the drop's snapEnd re-anchors it (or leaves it free)
        if (pd.index === 0 && l.from) delete l.from;
        if (pd.index === l.points.length - 1 && l.to) delete l.to;
      }
      l.points[pd.index] = [Math.max(0, pt.x), Math.max(0, pt.y)];   // Z2: clamped >= 0
      patchLineId(l.id);    // R3: the dragged line re-emits in place, mid-gesture
      sceneDirty = true;
      drawLive();
      return;
    }
    if (pd.kind === "wp") {   // Z5.2: move waypoints[i], kept out of every box (Z5.3)
      var dxw = pt.x - pd.px, dyw = pt.y - pd.py;
      if (!pd.moved && Math.abs(dxw) + Math.abs(dyw) < 2) return;
      if (!pd.moved) { snapshot(); pd.moved = true; }
      if (dxw || dyw) {
        pd.axis = Math.abs(dxw) >= Math.abs(dyw) ? "x" : "y";
        pd.dir = (pd.axis === "x" ? dxw : dyw) >= 0 ? 1 : -1;
      }
      pd.edge.waypoints[pd.wp] = clearWaypoint(Math.max(0, pt.x), Math.max(0, pt.y),
        pd.axis, pd.dir);
      pd.px = pt.x;
      pd.py = pt.y;
      patchEdge(state.edges.indexOf(pd.edge));   // R3: in place, through the same builder
      sceneDirty = true;
      drawLive();
      return;
    }
    // "seg": the two waypoints of the dragged segment move together along the perpendicular axis
    var delta = pd.axis === "x" ? pt.x - pd.px : pt.y - pd.py;
    if (!pd.moved && Math.abs(pt.x - pd.px) + Math.abs(pt.y - pd.py) < 2) return;
    if (!pd.moved) {
      snapshot();
      pd.moved = true;
      var dir0 = delta >= 0 ? 1 : -1;
      if (!Array.isArray(pd.edge.waypoints) || !pd.edge.waypoints.length) {
        pd.edge.waypoints = pd.base.map(function (p) {   // Z5.2: materialise the segment
          return clearWaypoint(Math.max(0, p.x), Math.max(0, p.y), pd.axis, dir0);
        });
        pd.wpA = 0;
        pd.wpB = 1;
      } else {
        pd.wpA = pd.seg - 1;
        pd.wpB = pd.seg;
      }
      pd.w0 = [pd.edge.waypoints[pd.wpA].slice(), pd.edge.waypoints[pd.wpB].slice()];
    }
    var axi = pd.axis === "x" ? 0 : 1, dir = delta >= 0 ? 1 : -1;
    [pd.wpA, pd.wpB].forEach(function (wi, k) {
      var np = pd.w0[k].slice();
      np[axi] = Math.max(0, pd.w0[k][axi] + delta);
      pd.edge.waypoints[wi] = clearWaypoint(np[0], np[1], pd.axis, dir);
    });
    patchEdge(state.edges.indexOf(pd.edge));   // R3: in place, through the same builder
    sceneDirty = true;
    drawLive();
  }

  host.addEventListener("pointerdown", function (evt) {
    if (evt.button === 1 || (evt.button === 0 && spaceDown)) {   // W1.4: pan drags
      panDrag = { cx: evt.clientX, cy: evt.clientY, vx: view.x, vy: view.y };
      try { host.setPointerCapture(evt.pointerId); } catch (e) { /* synthetic pointer */ }
      evt.preventDefault();
      return;
    }
    if (evt.button !== 0) return;
    if (tool === "straight") {   // S2: the press opens a two-point draft; a drag, not clicks
      var sp = toSvgPoint(evt);
      draft = { points: [[Math.max(0, sp.x), Math.max(0, sp.y)]], hover: null };
      try { host.setPointerCapture(evt.pointerId); } catch (e2) { /* synthetic pointer */ }
      return;   // no render: with no hover the draft is not drawn — a press must not flash (S3)
    }
    if (tool === "line") {   // Z4.2: a draft opens/appends; nothing else answers in line mode
      var lp = toSvgPoint(evt);
      if (!draft) draft = { points: [], hover: null };
      draft.points.push([Math.max(0, lp.x), Math.max(0, lp.y)]);   // Z2: the pane never writes < 0
      draft.hover = draft.points[draft.points.length - 1].slice();
      try { host.setPointerCapture(evt.pointerId); } catch (e2) { /* synthetic pointer */ }
      drawLive();   // R2: the draft preview is chrome — the scene is untouched
      return;
    }
    var hit = evt.target && evt.target.closest ? evt.target : null;
    var handleEl = hit ? hit.closest(".ck-handle") : null;   // X5: the handles answer first
    if (handleEl) { resizeBegin(handleEl, evt); return; }
    var vtxEl = hit ? hit.closest(".ck-vtx") : null;         // Z5.1/Z5.2: then the path handles
    if (vtxEl) { vtxDown(vtxEl, evt); return; }
    var group = hit ? hit.closest(".ck-node, .ck-note") : null;
    var groupEl = hit ? hit.closest(".ck-group") : null;
    var edgeEl = hit ? hit.closest(".ck-edge") : null;
    if (group) {
      var id = group.getAttribute("data-id");
      var item = itemById(id);
      if (!item) return;
      var type = nodeById(id) ? "node" : "note";
      if (evt.shiftKey) {   // W2.1: shift toggles; a removal never opens the panel
        afterToggle(toggleSelected(type, id));
        render();
        return;
      }
      var collapse = isSelected(type, id) && selection.length > 1;
      var selBefore = selection.slice();
      if (!isSelected(type, id)) selectOnly(type, id);
      syncPanel();
      startDrag(evt);
      if (drag) drag.collapse = collapse ? { type: type, id: id } : null;
      // R3: the press that starts a drag patches the ring in place — the scene children the
      // gesture is about to move must survive it (R1); the dirty flag stays set for the next
      // full render (R4).
      patchSelectionChange(selBefore);
      sceneDirty = true;
      drawLive();
      return;
    }
    if (groupEl) {   // groups are selectable (marquee/click) and draggable, but have no panel
      var gid = groupEl.getAttribute("data-id");
      if (!groupById(gid)) return;
      if (evt.shiftKey) {
        afterToggle(toggleSelected("group", gid));
        render();
        return;
      }
      var gCollapse = isSelected("group", gid) && selection.length > 1;
      var gSelBefore = selection.slice();
      if (!isSelected("group", gid)) selectOnly("group", gid);
      syncPanel();
      startDrag(evt);
      if (drag) drag.collapse = gCollapse ? { type: "group", id: gid } : null;
      patchSelectionChange(gSelBefore);   // R3, as above
      sceneDirty = true;
      drawLive();
      return;
    }
    if (edgeEl) {
      var eid = parseInt(edgeEl.getAttribute("data-id").slice(1), 10);
      edgeDown(eid, toSvgPoint(evt), evt, hitTol());
      return;
    }
    // Z5.4: a click within ~6 screen px of a polyline (line or edge) selects it, at any zoom.
    if (svgMetrics()) {
      var pt0 = toSvgPoint(evt), tol0 = hitTol();
      var hitP = hitPolyline(pt0.x, pt0.y, tol0);
      if (hitP) {
        if (hitP.type === "line") { lineDown(hitP, pt0, evt); return; }
        edgeDown(hitP.id, pt0, evt, tol0);
        return;
      }
    }
    // W2.2: a left-drag on empty canvas is a marquee; a click without movement clears.
    var point = toSvgPoint(evt);
    marquee = { x0: point.x, y0: point.y, x1: point.x, y1: point.y, moved: false };
    try { host.setPointerCapture(evt.pointerId); } catch (e) { /* synthetic pointer */ }
  });

  host.addEventListener("pointermove", function (evt) {
    if (panDrag) {
      var m = svgMetrics();
      if (!m) return;
      view.x = panDrag.vx - (evt.clientX - panDrag.cx) / m.scale;
      view.y = panDrag.vy - (evt.clientY - panDrag.cy) / m.scale;
      schedule();
      return;
    }
    if (resize) { resizeMove(evt); return; }   // X5
    if (draft && tool === "straight") {   // S2/S4: the free end follows the pointer, locked to
      var sp2 = toSvgPoint(evt);          // 45° steps unless Shift frees the angle
      var sAnchor = draft.points[0];
      draft.hover = constrainStep45(sAnchor[0], sAnchor[1],
        Math.max(0, sp2.x), Math.max(0, sp2.y), evt.shiftKey);
      drawLive();   // F1: the preview is chrome — a live drag never rebuilds the scene (R2)
      return;
    }
    if (draft) {   // Z4.2: the rubber preview's free end follows the pointer
      var hp = toSvgPoint(evt);
      draft.hover = [Math.max(0, hp.x), Math.max(0, hp.y)];
      drawLive();   // synchronous: the preview must track the pointer (R2: chrome only)
      return;
    }
    if (pathDrag) { pathDragMove(evt); return; }   // Z5
    if (marquee) {
      var mp = toSvgPoint(evt);
      marquee.x1 = mp.x;
      marquee.y1 = mp.y;
      if (Math.abs(marquee.x1 - marquee.x0) + Math.abs(marquee.y1 - marquee.y0) > 2) {
        marquee.moved = true;
      }
      drawLive();   // synchronous: the rubber band must be visible mid-drag (R2: chrome only)
      return;
    }
    if (!drag) return;
    var point = toSvgPoint(evt);
    // W2.3b: below the threshold this is still a click, so a jittery click must not become a drag
    // (the selection then collapses to the clicked item instead of moving the whole group).
    if (!drag.moved && Math.abs(point.x - drag.px) + Math.abs(point.y - drag.py) < 3) return;
    var dx = point.x - drag.px, dy = point.y - drag.py;
    drag.items.forEach(function (entry) {   // one shared delta, nothing dragged off-canvas
      if (entry.x0 + dx < 0) dx = -entry.x0;
      if (entry.y0 + dy < 0) dy = -entry.y0;
    });
    // X6: the guides compare the primary as the pointer has put it; the drop repeats this maths
    // (snap and all), so what is drawn under the drag is where the item lands.
    var match = guideMatch({ x: drag.px0 + dx, y: drag.py0 + dy,
                             w: drag.pw, h: drag.ph }, drag.exclude);
    var rawX = dx, rawY = dy;
    if (state.snap !== false) {   // X6: a matched axis is nudged onto the alignment, not rounded
      if (match.x) dx += match.x.delta;
      if (match.y) dy += match.y.delta;
      drag.items.forEach(function (entry) {
        if (entry.x0 + dx < 0) dx = -entry.x0;
        if (entry.y0 + dy < 0) dy = -entry.y0;
      });
    }
    drag.dx = dx;
    drag.dy = dy;
    drag.rawX = rawX;
    drag.rawY = rawY;
    drag.items.forEach(function (entry) { entry.ref.x = entry.x0 + dx; entry.ref.y = entry.y0 + dy; });
    drag.moved = true;
    // A guide exists to be seen while the match holds, so a change in the guide set renders at
    // once; the plain move stays rAF-throttled. Either way the move PATCHES the dragged items
    // and the paths they displace in place (R3) — the scene is not rebuilt mid-gesture — and
    // leaves the dirty flag set, so the next full render rebuilds the identical DOM (R4).
    var next = guideLines({ x: drag.px0 + dx, y: drag.py0 + dy,
                            w: drag.pw, h: drag.ph }, match);
    var changed = JSON.stringify(next) !== JSON.stringify(guides);
    guides = next;
    patchDragged(drag.items);
    sceneDirty = true;
    if (changed) drawLive(); else schedule();
  });

  host.addEventListener("pointerup", function () {
    guides = null;   // X6: the guides only live for the duration of the drag
    if (draft && tool === "straight") { commitStraight(); return; }   // S2: the drop commits
    if (panDrag) { panDrag = null; drawLive(); return; }   // R6: a pan never touched the scene
    if (resize) { resize = null; drawLive(); return; }   // X5: the moves already landed (patched)
    if (pathDrag) {   // Z5: the drop finishes a path drag
      var pd = pathDrag;
      pathDrag = null;
      if (pd.kind === "vtx" && pd.moved) {
        var dl = pd.line;
        if (pd.index === 0 || pd.index === dl.points.length - 1) {
          var dp = dl.points[pd.index];
          // A5: the drop takes the nearest candidate of the owning box, or detaches (Z5.1's rule)
          applyAnchor(dl, pd.index === 0 ? "from" : "to", snapAnchor(dp[0], dp[1]));
          syncPanel();   // A6.3: the anchor rows follow the model
        }
      }
      if (pd.kind === "eend" && pd.moved) {
        // A5: same box -> side/at; another box -> the end is re-pointed; far -> nothing changes.
        // An edge's from/to are node ids, so only the nodes are candidates here.
        if (applyAnchor(pd.edge, pd.end, snapAnchor(pd.hover[0], pd.hover[1], { nodesOnly: true }))) {
          syncPanel();   // A6.4: the panel mirrors the model after an effective drop
        }
      }
      render();
      return;
    }
    if (marquee) {
      if (marquee.moved) {   // W2.2: select every node, note and group the band intersects
        var x0 = Math.min(marquee.x0, marquee.x1), x1 = Math.max(marquee.x0, marquee.x1);
        var y0 = Math.min(marquee.y0, marquee.y1), y1 = Math.max(marquee.y0, marquee.y1);
        var hits = [];
        function overlaps(item) {   // X3.3: an effective box, so a 0x0 table is selectable
          var r = boxOf(item);
          return r.x < x1 && r.x + r.w > x0 && r.y < y1 && r.y + r.h > y0;
        }
        state.nodes.forEach(function (n) { if (overlaps(n)) hits.push({ type: "node", id: n.id }); });
        state.notes.forEach(function (t) { if (overlaps(t)) hits.push({ type: "note", id: t.id }); });
        state.groups.forEach(function (g) { if (overlaps(g)) hits.push({ type: "group", id: g.id }); });
        (state.lines || []).forEach(function (l) {   // Z5.4: a vertex inside or a segment crossing
          var pts = linePts(l), hitL = false, li, lj;
          for (li = 0; li < pts.length && !hitL; li++) {
            if (pts[li].x >= x0 && pts[li].x <= x1 && pts[li].y >= y0 && pts[li].y <= y1) hitL = true;
          }
          for (lj = 0; lj + 1 < pts.length && !hitL; lj++) {
            if (segRectHit(pts[lj].x, pts[lj].y, pts[lj + 1].x, pts[lj + 1].y, x0, y0, x1, y1)) {
              hitL = true;
            }
          }
          if (hitL) hits.push({ type: "line", id: l.id });
        });
        setSelection(hits);
        syncPanel();
      } else {   // a zero-size drag — a click — clears the selection
        setSelection([]);
        closePanel();
      }
      marquee = null;
      render();
      return;
    }
    if (drag && drag.moved) {
      var dx = drag.rawX, dy = drag.rawY;   // X6: the guide decision is taken on the raw drop
      if (state.snap !== false) {   // R5.5 + W2.3 + X6: snap the primary, share the snapped delta
        var grid = state.grid || 10;
        var match = guideMatch({ x: drag.px0 + dx, y: drag.py0 + dy, w: drag.pw, h: drag.ph },
          drag.exclude);
        var entry = null;
        for (var i = 0; i < drag.items.length; i++) {
          if (drag.items[i].ref === drag.primary) { entry = drag.items[i]; break; }
        }
        if (entry) {
          // X6: an axis whose guide matched is nudged onto that alignment; the other axis keeps the
          // plain grid snap. That is what "the match wins over the grid rounding" means.
          if (match.x) dx += match.x.delta;
          else dx = Math.round((entry.x0 + dx) / grid) * grid - entry.x0;
          if (match.y) dy += match.y.delta;
          else dy = Math.round((entry.y0 + dy) / grid) * grid - entry.y0;
          drag.items.forEach(function (e2) {
            if (e2.x0 + dx < 0) dx = -e2.x0;
            if (e2.y0 + dy < 0) dy = -e2.y0;
          });
        }
      }
      drag.items.forEach(function (e3) { e3.ref.x = e3.x0 + dx; e3.ref.y = e3.y0 + dy; });
      // R3/R4: the drop is still part of the gesture — patch the final snapped positions in
      // place and leave the scene dirty; the next full render must reproduce this DOM exactly.
      patchDragged(drag.items);
      sceneDirty = true;
      drawLive();
    } else if (drag && drag.collapse) {   // a plain click selects the item alone (W2.1)
      selectOnly(drag.collapse.type, drag.collapse.id);
      syncPanel();
      render();
    }
    drag = null;
  });

  /* W1.3/W2.4/W2.5: keyboard. Nothing here fires while a panel field has focus. */
  function fieldFocus() {
    var el = document.activeElement;
    return el && (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA");
  }
  document.addEventListener("keydown", function (evt) {
    if (fieldFocus()) return;
    var mod = evt.ctrlKey || evt.metaKey, key = evt.key;
    if (draft) {   // Z4.2: Enter commits the draft, Esc cancels it with no trace
      if (key === "Enter") { evt.preventDefault(); commitDraft(); return; }
      if (key === "Escape") { evt.preventDefault(); draft = null; drawLive(); return; }   // R2: chrome
    }
    if (key === " ") { spaceDown = true; evt.preventDefault(); return; }
    if (mod && (key === "d" || key === "D")) {
      if (selection.length) { evt.preventDefault(); duplicateSelection(); }
      return;
    }
    if (mod && (key === "c" || key === "C")) {
      if (copySelection()) evt.preventDefault();
      return;
    }
    if (mod && (key === "v" || key === "V")) {
      if (clipboard) { evt.preventDefault(); pasteClipboard(); }
      return;
    }
    if (key === "Delete" || key === "Backspace") {
      if (removeSelected()) evt.preventDefault();
      return;
    }
    if (key === "+" || key === "=") { zoomCentre(1.15); return; }
    if (key === "-" || key === "_") { zoomCentre(1 / 1.15); return; }
    if (key === "0") { fitView(); }
  });
  document.addEventListener("keyup", function (evt) {
    if (evt.key === " ") spaceDown = false;
  });
  window.addEventListener("blur", function () { spaceDown = false; });

  /* Z4.2: a double-click commits the draft — but the commit cannot listen to "dblclick".
   * render() rebuilds the whole SVG on every draft click, so the press and the release land on
   * different (disconnected) elements and the browser drops the click/dblclick compatibility
   * events entirely; measured over CDP with Input.dispatchMouseEvent, the page only ever sees
   * pointerdown (detail 0) followed by mousedown, which carries the click count in detail
   * (1 on the first press, 2 on the second). So the commit gesture is the second PRESS. */
  host.addEventListener("mousedown", function (evt) {
    if (tool !== "line" || !draft || evt.button !== 0 || evt.detail < 2) return;
    evt.preventDefault();   // keep the press from starting a text selection
    while (draft.points.length >= 2) {   // the pair's presses re-added the last point
      var a = draft.points[draft.points.length - 2], b = draft.points[draft.points.length - 1];
      if (Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) > 0.5) break;
      draft.points.pop();
    }
    commitDraft();
  });

  /* ---- mutations ---- */
  function addNode() {
    snapshot();
    var grid = state.grid || 10;
    var spot = { x: 40, y: 40 };
    for (var row = 0; row < 40; row++) {
      var candidate = { x: 40 + (row % 3) * 200, y: 40 + Math.floor(row / 3) * 100 };
      var clash = state.nodes.some(function (n) {
        return Math.abs(n.x - candidate.x) < 180 && Math.abs(n.y - candidate.y) < 72;
      });
      if (!clash) { spot = candidate; break; }
    }
    var node = { id: "n" + (state.nodes.length + 1) + "-" + Date.now().toString(36),
      x: Math.round(spot.x / grid) * grid, y: Math.round(spot.y / grid) * grid,
      w: 180, h: 72, text: "new box", kind: "box", tone: "accent" };
    state.nodes.push(node);
    state.width = Math.max(state.width, node.x + node.w + 40);
    state.height = Math.max(state.height, node.y + node.h + 40);
    selectOnly("node", node.id);
    render();
    openPanel();
    return node.id;
  }

  function addNote() {
    snapshot();
    var item = { id: "note-" + Date.now().toString(36), x: 40, y: (state.height || 200) - 90,
      w: 280, h: 60, text: "new note", kind: "note", tone: "warn" };
    state.notes.push(item);
    state.height = Math.max(state.height, item.y + item.h + 40);
    selectOnly("note", item.id);
    render();
    openPanel();
    return item.id;
  }

  function addEdge(from, to) {
    var exists = state.edges.some(function (e) { return e.from === from && e.to === to; });
    if (exists) return false;
    snapshot();
    state.edges.push({ from: from, to: to, style: "solid", route: "auto", bidir: false, tone: "default" });
    render();
    return true;
  }

  /* S4 (v1.8, amended): the straight drag is LOCKED to the nearest 45° step from the anchor —
   * horizontal, vertical or one of the two diagonals — and Shift frees the angle. The length is
   * the pointer vector's PROJECTION onto the snapped direction, so the preview and the commit
   * cannot disagree. Pure, no DOM: the self-test asserts it directly. The axis-aligned cases
   * return exact coordinates (no trig), so y === ay / x === ax hold to the bit. */
  function constrainStep45(ax, ay, bx, by, free) {
    if (free) return [bx, by];
    var dx = bx - ax, dy = by - ay;
    if (dx === 0 && dy === 0) return [ax, ay];
    var k = Math.round(Math.atan2(dy, dx) / (Math.PI / 4));   // the nearest step, -4..4
    if (k === 0 || k === 4 || k === -4) return [ax + dx, ay];   // horizontal
    if (k === 2 || k === -2) return [ax, ay + dy];              // vertical
    var s = (k === 1 || k === -3) ? 1 : -1;   // the diagonal's slope sign (NE/SW vs NW/SE)
    var half = (dx + s * dy) / 2;           // the projection onto that diagonal, halved
    return [ax + half, ay + s * half];
  }

  /* Z4.2 + A5: the draft commits through the one factory; a draft with fewer than 2 points is
   * dropped without a message and never touched the model or the undo history. The two ENDS snap
   * at commit — v1.7's snapAnchor records {ref, side, at} (Z4.3's successor); the snapped point
   * is KEPT in points as the Z3.4 fallback. Interior points never snap. */
  function commitDraft() {
    var pts = draft ? draft.points : [];
    draft = null;
    if (pts.length < 2) { render(); return; }
    var from = snapAnchor(pts[0][0], pts[0][1]);
    var last = pts[pts.length - 1];
    var to = snapAnchor(last[0], last[1]);
    addLine(pts, from, to);
  }

  /* S2/S5 (v1.8): the straight drag commits through the SAME addLine factory as the line tool —
   * a two-point line whose ends snap via snapAnchor exactly as commitDraft's do. A press with no
   * move (no hover) or a stroke shorter than 3 document units commits NOTHING: no model write,
   * no undo entry, no selection change — and the mode stays armed for the next drag. F1: such a
   * canceled stroke is "nothing happened", so it costs nothing either — the preview is chrome,
   * dropping it is a chrome refresh, and the scene is not rebuilt. */
  function commitStraight() {
    var anchor = draft && draft.points.length ? draft.points[0] : null;
    var end = draft ? draft.hover : null;
    draft = null;
    if (!anchor || !end) { drawLive(); return; }
    if (Math.hypot(end[0] - anchor[0], end[1] - anchor[1]) < 3) { drawLive(); return; }
    addLine([anchor.slice(), end.slice()],
      snapAnchor(anchor[0], anchor[1]), snapAnchor(end[0], end[1]));
  }

  /* Z4.4: the single line factory — appends to state.lines, snapshots for undo, selects the new
   * line and opens its panel section, exactly as addNode does. Returns the id. */
  function addLine(points, from, to) {
    snapshot();
    if (!Array.isArray(state.lines)) state.lines = [];   // Z3.7: normalise (lane L) owns the
      // default on the merged head; the guard keeps a lines-less document editable in this lane
    var stamp = Date.now().toString(36), n = 0, id;
    do { id = "line-" + stamp + (n ? "-" + n : ""); n += 1; } while (lineOf(id));
    var line = { id: id, points: points.map(function (p) { return [p[0], p[1]]; }),
      tone: "default", style: "solid", start: "none", end: "arrow" };
    if (from) line.from = from;
    if (to) line.to = to;
    state.lines.push(line);
    points.forEach(function (p) {
      state.width = Math.max(state.width, p[0] + 40);
      state.height = Math.max(state.height, p[1] + 40);
    });
    selectOnly("line", line.id);
    render();
    openPanel();
    return line.id;
  }

  /* W2.4: every selected item goes; a node still takes its edges with it, an edge is removed
   * on its own, and a selected group is removed from state.groups. */
  function removeSelected() {
    if (!selection.length) return false;
    snapshot();
    var nodeIds = {}, noteIds = {}, groupIds = {}, edgeIdx = {}, lineIds = {};
    selection.forEach(function (s) {
      if (s.type === "node") nodeIds[s.id] = true;
      else if (s.type === "note") noteIds[s.id] = true;
      else if (s.type === "group") groupIds[s.id] = true;
      else if (s.type === "edge") edgeIdx[s.id] = true;
      else if (s.type === "line") lineIds[s.id] = true;
    });
    state.nodes = state.nodes.filter(function (n) { return !nodeIds[n.id]; });
    state.notes = state.notes.filter(function (n) { return !noteIds[n.id]; });
    state.groups = state.groups.filter(function (g) { return !groupIds[g.id]; });
    state.edges = state.edges.filter(function (e, i) {
      return !edgeIdx[i] && !nodeIds[e.from] && !nodeIds[e.to];
    });
    state.lines = (state.lines || []).filter(function (l) { return !lineIds[l.id]; });
    setSelection([]);
    closePanel();
    render();
    return true;
  }

  /* W2.4: duplicate and the in-editor clipboard share one clone pass. */
  function freshId(kind) {   // the existing scheme, regenerated until it does not collide
    var base = Date.now().toString(36), n = 0, id;
    do {
      n += 1;
      id = kind === "note" ? "note-" + base + (n > 1 ? "-" + n : "")
         : kind === "group" ? "g" + (state.groups.length + n) + "-" + base
         : "n" + (state.nodes.length + n) + "-" + base;
    } while (itemById(id));
    return id;
  }

  function selectedSet() {   // the selected nodes/notes/groups + edges with BOTH ends selected
    var nodeIds = {}, items = [];
    selection.forEach(function (s) {
      if (s.type === "node") {
        var n = nodeById(s.id);
        if (n) { nodeIds[s.id] = true; items.push({ kind: "node", ref: n }); }
      } else if (s.type === "note") {
        var t = noteById(s.id);
        if (t) items.push({ kind: "note", ref: t });
      } else if (s.type === "group") {   // W2.1: a selected group duplicates like the others
        var g = groupById(s.id);
        if (g) items.push({ kind: "group", ref: g });
      }
    });
    var edges = state.edges.filter(function (e) { return nodeIds[e.from] && nodeIds[e.to]; });
    return { items: items, edges: edges };
  }

  function pasteSet(set) {
    if (!set.items.length) return false;
    snapshot();
    var idMap = {}, newSelection = [];
    set.items.forEach(function (entry) {
      var copy = JSON.parse(JSON.stringify(entry.ref));
      copy.id = freshId(entry.kind);
      copy.x += 20;   // W2.4: the (+20, +20) offset
      copy.y += 20;
      idMap[entry.ref.id] = copy.id;
      if (entry.kind === "node") {
        state.nodes.push(copy);
        state.width = Math.max(state.width, copy.x + copy.w + 40);
        state.height = Math.max(state.height, copy.y + copy.h + 40);
      } else if (entry.kind === "group") {
        state.groups.push(copy);
      } else {
        state.notes.push(copy);
        state.height = Math.max(state.height, copy.y + copy.h + 40);
      }
      newSelection.push({ type: entry.kind, id: copy.id });
    });
    set.edges.forEach(function (e) {   // duplicated only when BOTH endpoints were
      var copy = JSON.parse(JSON.stringify(e));
      copy.from = idMap[e.from];
      copy.to = idMap[e.to];
      state.edges.push(copy);
    });
    setSelection(newSelection);   // the copies become the selection
    syncPanel();
    render();
    return true;
  }

  function duplicateSelection() { return pasteSet(selectedSet()); }
  function copySelection() {
    var set = selectedSet();
    if (!set.items.length) return false;
    clipboard = JSON.parse(JSON.stringify(set));   // the copy must not track later edits
    say("selection copied");
    return true;
  }
  function pasteClipboard() {
    if (!clipboard) return false;
    if (pasteSet(clipboard)) { say("selection pasted"); return true; }
    return false;
  }

  /* ---- panel ---- */
  /* X1: the panel is selection-driven — one row per control in page(), each row HIDDEN unless it
   * applies to the primary selection's bucket. The buckets cover every selectable kind.
   * Y1.5: the map follows the renderer mechanically — a row is visible for a bucket exactly when
   * changing its field changes the rendered SVG: a split box (box/pill — the only kinds both
   * renderers draw parts for), a plain shape (diamond/hexagon/cloud/cylinder/document/
   * parallelogram — no parts row, the renderers would ignore them), an icon kind
   * (server/computer/actor — caption text and size/weight only: no sub, no align), a caption
   * (text + tone + size/weight; W wraps it, H is inert), a table (X3: its own text/sub are never
   * drawn; v1.5 keeps align as the cells' default — Y4.5), an area (title text, tone, size,
   * text size/weight — no sub, no align), a node of kind "note" (like a shape: text, sub, align,
   * size, weight — the v1.4 map wrongly hid them), a note (text, tone, size, weight — Y4.7),
   * an edge, and a group (no rows — only the multi-selection hint and the arrange row show).
   * The `panelfields` self-test step enforces this map against the renderer. */
  var PANEL_ROWS = {
    "ck-row-text": ["split", "shape", "icon", "caption", "nodenote", "note", "area"],
    "ck-row-sub": ["split", "shape", "nodenote"],
    "ck-row-tone": ["split", "shape", "icon", "caption", "nodenote", "note", "area", "table", "edge"],
    "ck-row-kind": ["split", "shape", "icon", "caption", "nodenote", "table", "area"],
    "ck-row-align": ["split", "shape", "nodenote", "table"],
    "ck-row-parts": ["split"],
    "ck-row-split": ["split"],
    "ck-row-rows": ["table"],
    "ck-row-cols": ["table"],
    "ck-row-colw": ["table"],
    "ck-row-cells": ["table"],
    "ck-row-header": ["table"],
    "ck-row-rowtones": ["table"],
    "ck-row-coltones": ["table"],
    "ck-row-text-size": ["split", "shape", "icon", "caption", "nodenote", "note", "area"],
    "ck-row-bold": ["split", "shape", "icon", "caption", "nodenote", "note", "area"],
    "ck-row-size": ["split", "shape", "icon", "caption", "nodenote", "note", "table", "area"],
    "ck-row-label": ["edge"],
    "ck-row-style": ["edge"],
    "ck-row-route": ["edge"],
    "ck-row-start": ["edge"],
    "ck-row-end": ["edge"],
    "ck-row-from-side": ["edge"],
    "ck-row-to-side": ["edge"],
    "ck-row-head-size": ["edge"],
    // v1.6 (Z6): the line's own rows, and ONLY the line's. A line has no text, no sub, no size, no
    // table: those rows are hidden for it, exactly as the renderer ignores the fields. The edge's
    // label/route/head-size/attach rows are hidden too (Z3.2: a line has no head-size row either);
    // the line's heads and style live in its own section, so the edge's rows stay edge-only.
    "ck-row-points": ["line"],
    "ck-row-line-tone": ["line"],
    "ck-row-line-style": ["line"],
    "ck-row-line-start": ["line"],
    "ck-row-line-end": ["line"],
    "ck-row-cellpick": ["table"],
    "ck-row-celltext": ["table"],
    "ck-row-cellalign": ["table"],
    "ck-row-celltone": ["table"]
  };
  /* v1.6 (Z6.2): a line is its own kind. `state.lines` belongs to the reader half (lane L); the
   * panel only READS it, through the two helpers below, so a tree without that half still boots
   * and still shows the other kinds. A line carries `points` (Z3.1) and nothing else in the panel
   * does, which is what `isLineItem` keys on. */
  function isLineItem(item) {
    if (!item) return false;
    if (Array.isArray(item.points)) return true;
    return (state.lines || []).indexOf(item) !== -1;
  }
  /* The line the primary selection names. The spec pins `data-tool="line"` and the line's own
   * panel kind, but not the selection entry lane I writes for a selected line: the id it carries
   * may be the line's own id (a node/note selection) or an index into the document's list (an edge
   * selection). Both resolve here, so the panel does not depend on which one the hit test picked. */
  function lineOfSelection() {
    var list = state.lines || [];
    if (!selected || !list.length) return null;
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].id === selected.id) return list[i];
    }
    if (typeof selected.id === "number" && list[selected.id]) return list[selected.id];
    return null;
  }
  function panelKind(item) {
    if (selected.type === "edge") return "edge";
    if (selected.type === "group") return "group";
    if (selected.type === "note") return "note";
    if (isLineItem(item)) return "line";   // v1.6: its own kind, before any node kind
    var k = item.kind || "box";
    if (k === "table") return "table";
    if (k === "area") return "area";
    if (k === "note") return "nodenote";
    if (k === "caption") return "caption";
    if (k === "server" || k === "computer" || k === "actor") return "icon";
    return (k === "box" || k === "pill") ? "split" : "shape";
  }
  /* v1.5: the panel helpers — inline button groups (Y2), hints and validation (Y3), and the
   * table v2 controls (Y5). TONES mirrors boardkit.TONES for building the swatch rows; the
   * renderer owns the vocabulary (lane T may extend it, never reorder it). */
  var TONES = ["default", "accent", "ok", "warn", "danger", "muted"];
  var KINDS = ["box", "pill", "diamond", "note", "caption", "cloud", "cylinder", "document",
    "hexagon", "parallelogram", "server", "computer", "actor", "area", "table"];   // boardkit.KINDS
  var CELL_ALIGNS = ["left", "center", "right"];

  function pressGroup(group, attr, value) {   // Y2: exactly one pressed; an unknown value presses none
    if (!group) return;
    var buttons = group.querySelectorAll("[data-" + attr + "]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed",
        buttons[i].getAttribute("data-" + attr) === value ? "true" : "false");
    }
  }

  var hintText = {};   // Y3: each control's base hint; a validation reason replaces it temporarily
  function rememberHints() {
    var hints = document.querySelectorAll("[data-hint-for]");
    for (var i = 0; i < hints.length; i++) {
      hintText[hints[i].getAttribute("data-hint-for")] = hints[i].textContent;
    }
  }
  function hintFor(id) {
    return panel ? panel.querySelector('[data-hint-for="' + id + '"]') : null;
  }
  function rowHint(control) {   // the control's own hint, else its row's first (the colw inputs)
    if (!control) return null;
    var own = control.id ? hintFor(control.id) : null;
    if (own) return own;
    var row = control.closest ? control.closest(".ck-prow") : null;
    return row ? row.querySelector(".ck-hint[data-hint-for]") : null;
  }
  function setHint(id, text) {   // a dynamic hint (parts/cells) stays the base after validation resets
    var h = hintFor(id);
    if (h) { h.textContent = text; hintText[id] = text; }
  }
  function markInvalid(control, reason) {   // Y3: the value is refused; the row says why
    var row = control && control.closest ? control.closest(".ck-prow") : null;
    if (row) row.setAttribute("aria-invalid", "true");
    var h = rowHint(control);
    if (h) h.textContent = reason;
  }
  function clearInvalid(control) {   // a valid value clears the mark and restores the hint
    var row = control && control.closest ? control.closest(".ck-prow") : null;
    if (row) row.removeAttribute("aria-invalid");
    var h = rowHint(control);
    var key = h ? h.getAttribute("data-hint-for") : null;
    if (h && hintText[key] !== undefined) h.textContent = hintText[key];
  }
  function resetValidation() {   // marks and reasons do not survive a selection change
    if (!panel) return;
    var bad = panel.querySelectorAll(".ck-prow[aria-invalid]");
    for (var i = 0; i < bad.length; i++) bad[i].removeAttribute("aria-invalid");
    for (var id in hintText) {
      var h = hintFor(id);
      if (h) h.textContent = hintText[id];
    }
  }

  function cellParts(c) {   // Y4.1: an object cell's fields, normalised; a string cell is a text
    if (c && typeof c === "object") {
      return { text: c.text == null ? "" : String(c.text),
               tone: TONES.indexOf(c.tone) !== -1 ? c.tone : undefined,
               align: CELL_ALIGNS.indexOf(c.align) !== -1 ? c.align : undefined };
    }
    return { text: c == null ? "" : String(c), tone: undefined, align: undefined };
  }
  function rawCellsSized(item, grid) {   // exactly rows*cols entries, objects kept as-is
    var g = grid || tableGrid(item);
    var raw = Array.isArray(item.cells) ? item.cells.slice() : (item.cells ? [item.cells] : []);
    while (raw.length < g.rows * g.cols) raw.push("");
    return raw.slice(0, g.rows * g.cols);
  }
  function listOfTones(raw, size) {   // Y4.3: exactly size entries; an unknown tone is no tone
    var out = [];
    for (var i = 0; i < size; i++) {
      var t = Array.isArray(raw) ? raw[i] : null;
      out.push(TONES.indexOf(t) !== -1 ? t : null);
    }
    return out;
  }
  function toneListWrite(item, key, size, index, tone) {   // normalised write to row_tones/col_tones
    var list = listOfTones(item[key], size);
    list[index] = tone && TONES.indexOf(tone) !== -1 ? tone : null;
    item[key] = list;
  }
  function swatchButtons(current) {   // one swatch per tone plus the "no tone" swatch
    var out = TONES.map(function (t) {
      return '<button class="ck-sw" data-tone="' + t + '" aria-pressed="' + (current === t) +
        '" aria-label="' + t + '" title="' + t + '"></button>';
    });
    out.push('<button class="ck-sw" data-tone="" data-cell-tone="" aria-pressed="' +
      (current == null) + '" aria-label="no tone" title="no tone">∅</button>');
    return out.join("");
  }
  function syncToneGrid(item, grid) {   // Y5: one swatch row per table row and per column
    var rt = document.getElementById("ck-rowtones"), ct = document.getElementById("ck-coltones");
    if (rt) {
      rt.innerHTML = listOfTones(item.row_tones, grid.rows).map(function (tone, r) {
        return '<div class="ck-swrow" data-row-tone="' + r + '" role="group" aria-label="row ' +
          (r + 1) + ' tone">' + swatchButtons(tone) + "</div>";
      }).join("");
    }
    if (ct) {
      ct.innerHTML = listOfTones(item.col_tones, grid.cols).map(function (tone, c) {
        return '<div class="ck-swrow" data-col-tone="' + c + '" role="group" aria-label="column ' +
          (c + 1) + ' tone">' + swatchButtons(tone) + "</div>";
      }).join("");
    }
  }
  function syncColWidths(item, grid) {   // Y5: one input per column; a column's share, 1 by default
    var box = document.getElementById("ck-colw-row");
    if (!box) return;
    var html = "";
    for (var j = 0; j < grid.cols; j++) {
      var raw = Array.isArray(item.col_widths) ? parseFloat(item.col_widths[j]) : NaN;
      // step="any": an anchored step makes the browser reject the field's own default of 1
      html += '<input type="number" id="ck-colw-' + j + '" min="0.1" step="any" value="' +
        (raw > 0 ? raw : 1) + '" aria-label="column ' + (j + 1) + ' width">';
    }
    box.innerHTML = html;
  }
  function tableOfSelection() {   // the selected table node, or null
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    return item && (item.kind || "box") === "table" ? item : null;
  }
  function cellPicker() {   // the picked cell, clamped to the table; 1/1 by default
    var item = tableOfSelection();
    if (!item) return null;
    var grid = tableGrid(item);
    var rEl = document.getElementById("ck-cell-row"), cEl = document.getElementById("ck-cell-col");
    var r = parseInt(rEl && rEl.value, 10), c = parseInt(cEl && cEl.value, 10);
    if (!(r >= 1)) r = 1;
    if (!(c >= 1)) c = 1;
    return { item: item, grid: grid,
             r: Math.min(r, grid.rows), c: Math.min(c, grid.cols) };
  }
  function writeCell(item, r, c, patch, snap) {
    /* Y5: one cell write. The entry becomes an object only when it needs one (a tone or an
     * align) and a plain string again when both are cleared — a v1.4-shaped document stays
     * v1.4-shaped. Cells are row-major, like the renderer and the cells textarea. */
    var grid = tableGrid(item);
    r = Math.min(Math.max(1, r || 1), grid.rows);
    c = Math.min(Math.max(1, c || 1), grid.cols);
    var cells = rawCellsSized(item, grid);
    var idx = (r - 1) * grid.cols + (c - 1);
    var cur = cellParts(cells[idx]);
    var text = patch.text !== undefined ? patch.text : cur.text;
    var tone = patch.tone !== undefined ? patch.tone : cur.tone;
    var align = patch.align !== undefined ? patch.align : cur.align;
    if (snap) snapshot();
    if (tone || align) {
      var obj = { text: text };
      if (tone) obj.tone = tone;
      if (align) obj.align = align;
      cells[idx] = obj;
    } else {
      cells[idx] = text;
    }
    item.cells = cells;
  }
  function syncCellPanel(item, grid) {   // Y5: the cell section follows the pickers
    var rEl = document.getElementById("ck-cell-row"), cEl = document.getElementById("ck-cell-col");
    var textEl = document.getElementById("ck-cell-text");
    if (!rEl || !cEl || !textEl) return;
    grid = grid || tableGrid(item);
    var r = Math.min(Math.max(1, parseInt(rEl.value, 10) || 1), grid.rows);
    var c = Math.min(Math.max(1, parseInt(cEl.value, 10) || 1), grid.cols);
    rEl.value = r; cEl.value = c;
    rEl.max = grid.rows; cEl.max = grid.cols;
    var entry = cellParts(rawCellsSized(item, grid)[(r - 1) * grid.cols + (c - 1)]);
    if (document.activeElement !== textEl) textEl.value = entry.text;
    // the pressed align is the cell's OWN one, or `inherit` when it has none (Y2/Y5: clearing
    // must be reachable — `center` is not inherit when the table's own align is left)
    pressGroup(document.getElementById("ck-cell-align"), "cell-align", entry.align || "inherit");
    pressGroup(document.getElementById("ck-cell-tone"), "tone", entry.tone || "");
  }

  /* A6.3: the anchor rows' applicability — two DIFFERENT predicates, one per kind. A LINE end's
   * rows show iff that end's anchor RESOLVES (the ref names an existing node or note): a free end
   * and a dangling ref both hide them, because the renderer ignores such an anchor and a row
   * there would edit a value with no visible effect. An EDGE end's rows ALWAYS show (its from/to
   * must resolve for the edge to exist at all). Hidden, never disabled. The rows are lane G's
   * markup (A6.1/A6.2) and the lanes run in parallel, so in THIS tree they may be absent — every
   * touch is guarded, and the self-test fails on a mismatch only when the element exists. */
  function anchorResolves(a) {
    return !!(a && typeof a === "object" && typeof a.ref === "string" && itemById(a.ref));
  }
  /* A6.3: on the merged head lane G's lineAnchorPoint IS the applicability predicate (a line end
   * whose anchor draws through it has its rows; a free, malformed or dangling end has none — so
   * the map and the renderer agree in BOTH directions). In THIS tree it does not exist yet (the
   * lanes run in parallel) and the equivalent local rule stands in. */
  function lineEndResolves(item, end) {
    if (typeof lineAnchorPoint === "function") {
      var pts = Array.isArray(item.points) ? item.points : [];
      var raw = end === "from" ? pts[0] : pts[pts.length - 1];
      var stored = Array.isArray(raw) && raw.length === 2 ? { x: raw[0], y: raw[1] } : null;
      return lineAnchorPoint(item, end, stored) !== null;
    }
    return anchorResolves(item[end]);
  }
  function syncAnchorRows(pk, item) {
    var fromOk = pk === "line" && lineEndResolves(item, "from");
    var toOk = pk === "line" && lineEndResolves(item, "to");
    var rows = {
      "ck-row-line-from-side": fromOk, "ck-row-line-from-at": fromOk,
      "ck-row-line-to-side": toOk, "ck-row-line-to-at": toOk,
      "ck-row-from-at": pk === "edge", "ck-row-to-at": pk === "edge"
    };
    for (var id in rows) {
      var row = document.getElementById(id);
      if (row) row.hidden = !rows[id];
    }
  }
  /* A6.4: the displayed value equals the model's, including the case the presets cannot express
   * — exactly one preset is pressed when `at` equals it, NONE otherwise, and the hint names the
   * exact percentage. Reading never writes: opening or re-rendering the panel leaves the model
   * untouched (an absent `at` IS 0.5, A2). */
  function fillAtGroup(groupId, at) {
    var group = document.getElementById(groupId);
    if (!group) return;
    var v = typeof at === "number" && isFinite(at) ? Math.min(1, Math.max(0, at)) : 0.5;
    pressGroup(group, "at", String(v));
    var pct = Math.round(v * 1000) / 10;
    var base = groupId === "ck-line-from-at" ? "where on the side the line starts — "
      : groupId === "ck-line-to-at" ? "where on the side the line ends — "
      : groupId === "ck-from-at" ? "where on the from side the edge leaves — "
      : "where on the to side the edge arrives — ";
    setHint(groupId, base + pct + " %");
  }

  /* v1.6 (Z6.1): the line section follows the model. The tone/style/head groups press the value
   * the renderer honours (an unknown one shows the default, exactly as the renderers treat it),
   * and the count row carries a live hint — the parts row's idiom, one point at a time. */
  function syncLinePanel(item) {
    var heads = effectiveHeads(item);   // a line's heads use the edge defaults (Z3.2)
    pressGroup(document.getElementById("ck-line-tone"), "tone", item.tone || "default");
    pressGroup(document.getElementById("ck-line-style"), "style",
      ["solid", "dashed", "dotted"].indexOf(item.style) !== -1 ? item.style : "solid");
    pressGroup(document.getElementById("ck-line-start"), "end", heads.start);
    pressGroup(document.getElementById("ck-line-end"), "end", heads.end);
    var count = document.getElementById("ck-points-count");
    var n = Array.isArray(item.points) ? item.points.length : 0;
    if (count) count.textContent = n + (n === 1 ? " point" : " points");
    setHint("ck-points", "append at the end or remove the last point · never below 2 · " + n + " now");
    // A6.1 (the wiring half): the from/to side selects and position groups follow the model.
    // Lane G emits the markup; in this tree the elements may be absent, so every read is guarded.
    var lf = item.from, lt = item.to;
    var lfSide = document.getElementById("ck-line-from-side");
    if (lfSide) {
      lfSide.value = anchorResolves(lf) && SIDES.indexOf(lf.side) !== -1 ? lf.side : "auto";
    }
    var ltSide = document.getElementById("ck-line-to-side");
    if (ltSide) {
      ltSide.value = anchorResolves(lt) && SIDES.indexOf(lt.side) !== -1 ? lt.side : "auto";
    }
    fillAtGroup("ck-line-from-at", anchorResolves(lf) ? lf.at : 0.5);
    fillAtGroup("ck-line-to-at", anchorResolves(lt) ? lt.at : 0.5);
  }

  function arrangeItems() {   // X7: geometric items only — a selected edge is never moved
    var out = [];
    selection.forEach(function (s) {
      var ref = s.type === "node" ? nodeById(s.id)
        : s.type === "note" ? noteById(s.id)
        : s.type === "group" ? groupById(s.id) : null;
      if (ref) out.push(ref);
    });
    return out;
  }
  function refreshPartsUI() {   // X2: the count and the hint follow the field
    var field = document.getElementById("ck-parts");
    var count = document.getElementById("ck-parts-count");
    var hint = document.getElementById("ck-parts-hint");
    if (!field || !count || !hint) return;
    var n = field.value.split("\n").map(function (l) { return l.trim(); })
      .filter(function (l) { return l; }).length;
    var word = n === 1 ? " part" : " parts";
    count.textContent = n + word;
    setHint("ck-parts", "one part per line · " + n + word);   // Y3: the dynamic base hint
  }
  /* X3.1: there is ONE grid normaliser, defined once above (`tableGrid`) — a second definition of
   * the same name in this closure does not shadow, it WINS (function declarations hoist, last one
   * wins), so a duplicate here silently feeds the renderer a grid without `cells`. These helpers
   * only hand back writable copies for the panel: rawCellsSized keeps object cells as-is (Y4.1),
   * tableCells is the texts-only view for the textarea. */
  function tableCells(item, grid) {   // exactly rows*cols cell texts; empty ones are kept (X3.2)
    return rawCellsSized(item, grid).map(function (c) { return cellParts(c).text; });
  }
  function syncTablePanel(item) {   // X3.4 + Y5: refill the table controls from the model
    var grid = tableGrid(item);
    var rowsEl = document.getElementById("ck-rows");
    var colsEl = document.getElementById("ck-cols");
    var cellsEl = document.getElementById("ck-cells");
    var headEl = document.getElementById("ck-header");
    if (rowsEl) rowsEl.value = grid.rows;
    if (colsEl) colsEl.value = grid.cols;
    if (cellsEl) cellsEl.value = tableCells(item, grid).join("\n");
    if (headEl) headEl.checked = item.header === true;
    setHint("ck-cells", (grid.rows * grid.cols) + " cells (" + grid.rows + " × " + grid.cols +
      ") · one cell per line, empty lines kept");
    syncColWidths(item, grid);   // Y5
    syncToneGrid(item, grid);    // Y5
    syncCellPanel(item, grid);   // Y5
  }
  function openPanel() {
    if (!panel || !selected) return;
    var item = selected.type === "edge" ? state.edges[selected.id]
      : selected.type === "group" ? groupById(selected.id) : itemById(selected.id);
    if (!item) item = lineOfSelection();   // v1.6 (Z6.2): a line is not a node, a note or a group
    if (!item) { closePanel(); return; }
    resetValidation();   // Y3: marks and reasons belong to the previous selection's edit
    panel.classList.add("on");
    var pk = panelKind(item);
    document.getElementById("ck-panel-id").textContent =
      pk === "line" ? ("line: " + (item.id || selected.id))
        : selected.type === "edge" ? ("edge " + item.from + " → " + item.to)
          : (selected.type + ": " + selected.id);
    var isNode = selected.type === "node", isEdge = pk === "edge";
    var text = document.getElementById("ck-text");
    if (text) text.value = isEdge ? "" : (item.text || "");
    var sub = document.getElementById("ck-sub");
    // Y1.5 (v1.5 verifier, MAJOR): a node of kind "note" DRAWS its sub, so the field has to show it —
    // leaving it empty both lies about the model and invites a write that silently replaces a
    // subtitle the user cannot see.
    if (sub) sub.value = (pk === "split" || pk === "shape" || pk === "nodenote") ? (item.sub || "") : "";
    var tone = document.getElementById("ck-tone");
    if (tone) pressGroup(tone, "tone", item.tone || "default");
    var kind = document.getElementById("ck-kind");
    if (kind) pressGroup(kind, "kind", item.kind || "box");
    var alignEl = document.getElementById("ck-align");
    if (alignEl) {
      // Y4.5a: a table's own align is a tri-state (`right` too); a node's stays left|center. Show the
      // table-only button only for a table, so exactly one member of the group is pressed and it is
      // the value the renderer actually honours (v1.5 verifier, MAJOR: right showed as center).
      var alignValue = item.align === "left" || item.align === "right" ? item.align : "center";
      if (alignValue === "right" && pk !== "table") alignValue = "center";
      var rightBtn = alignEl.querySelector('[data-align="right"]');
      if (rightBtn) rightBtn.hidden = pk !== "table";
      pressGroup(alignEl, "align", alignValue);
    }
    var partsEl = document.getElementById("ck-parts");
    if (partsEl) {
      partsEl.value = pk === "split" ? normaliseParts(item.parts)
        .map(function (p) { return p.text; }).join("\n") : "";
    }
    refreshPartsUI();
    var splitEl = document.getElementById("ck-split");
    if (splitEl) pressGroup(splitEl, "split", item.split === "columns" ? "columns" : "rows");
    // X4: an unknown text_size shows as default, exactly as the renderers treat it.
    var textSizeEl = document.getElementById("ck-text-size");
    if (textSizeEl) {
      pressGroup(textSizeEl, "size",
        ["small", "default", "large"].indexOf(item.text_size) !== -1 ? item.text_size : "default");
    }
    var boldEl = document.getElementById("ck-bold");
    if (boldEl) boldEl.checked = item.bold === true;
    // The same w/h lane R's resize handles write; empty for an edge or a group primary.
    var wEl = document.getElementById("ck-w"), hEl = document.getElementById("ck-h");
    if (wEl) wEl.value = !isEdge && pk !== "group" && item.w != null ? item.w : "";
    if (hEl) hEl.value = !isEdge && pk !== "group" && item.h != null ? item.h : "";
    if (pk === "table") syncTablePanel(item);
    if (pk === "line") syncLinePanel(item);   // v1.6 (Z6): the pressed tone/style/heads + the count
    var heads = isEdge ? effectiveHeads(item) : null;   // R6.1 defaults for the selects
    var labelEl = document.getElementById("ck-label");
    if (labelEl) labelEl.value = isEdge ? (item.label || "") : "";
    var styleEl = document.getElementById("ck-style");
    if (styleEl) {
      styleEl.value = isEdge && ["solid", "dashed", "dotted"].indexOf(item.style) !== -1
        ? item.style : "solid";
    }
    var routeEl = document.getElementById("ck-route");
    if (routeEl) {
      routeEl.value = isEdge && ["auto", "straight", "h", "v"].indexOf(item.route) !== -1
        ? item.route : "auto";
    }
    var startEl = document.getElementById("ck-start");
    if (startEl) pressGroup(startEl, "end", heads ? heads.start : "none");
    var endEl = document.getElementById("ck-end");
    if (endEl) pressGroup(endEl, "end", heads ? heads.end : "arrow");
    // W3.4: the edge v2 controls, filled from the effective values.
    var fromSideEl = document.getElementById("ck-from-side");
    if (fromSideEl) {
      fromSideEl.value = isEdge && SIDES.indexOf(item.from_side) !== -1 ? item.from_side : "auto";
    }
    var toSideEl = document.getElementById("ck-to-side");
    if (toSideEl) {
      toSideEl.value = isEdge && SIDES.indexOf(item.to_side) !== -1 ? item.to_side : "auto";
    }
    // A6.2/A6.4: the edge's position groups mirror the model (lane G's markup, guarded here).
    fillAtGroup("ck-from-at", isEdge ? item.from_at : 0.5);
    fillAtGroup("ck-to-at", isEdge ? item.to_at : 0.5);
    var headSizeEl = document.getElementById("ck-head-size");
    if (headSizeEl) pressGroup(headSizeEl, "head", isEdge ? effectiveHeadSize(item) : "medium");
    // X1: hide every row that does not apply to this primary — hidden, not disabled.
    for (var rowId in PANEL_ROWS) {
      var row = document.getElementById(rowId);
      if (row) row.hidden = PANEL_ROWS[rowId].indexOf(pk) === -1;
    }
    // A6.3: the anchor rows are NOT in PANEL_ROWS — a line end's rows follow the end's own
    // resolve state, not the kind alone, and the map's per-kind check (the linepanel step) must
    // not see them. This pass sets all six on every selection, so no stale visibility survives.
    syncAnchorRows(pk, item);
    // Y1.5 (per STATE, not per kind): a split box's own `sub` is drawn only when no parts render,
    // and `split` changes the picture only when parts DO render (both renderers guard so) — the
    // rows follow the state exactly. (The amended spec sentence pins this backwards; the
    // mechanical rule and the gap pass's finding 4 are what the renderers do.)
    if (pk === "split") {
      var hasParts = normaliseParts(item.parts).length > 0;
      var subRow = document.getElementById("ck-row-sub");
      if (subRow) subRow.hidden = hasParts;
      var splitRow = document.getElementById("ck-row-split");
      if (splitRow) splitRow.hidden = !hasParts;
    }
    // X7: the arrange row is a MULTI-selection tool over geometric items only (amended: two
    // selected edges show nothing and are never moved); spread needs 3+.
    var geometric = arrangeItems();
    var arrRow = document.getElementById("ck-row-arrange");
    if (arrRow) arrRow.hidden = geometric.length < 2;
    var spreadRow = document.getElementById("ck-row-spread");
    if (spreadRow) spreadRow.hidden = geometric.length < 3;
    var multi = document.getElementById("ck-multi-hint");
    if (multi) {
      multi.hidden = selection.length < 2;
      multi.textContent = selection.length > 1 ? selection.length + " items selected" : "";
    }
    // Y1: a section with no visible row for this selection is hidden entirely — the `hidden`
    // attribute also takes it out of the tab order, so it is not keyboard-reachable either.
    // Collapsing a section is the browser's own <details> behaviour: session-only, never
    // written to the document.
    var secs = panel.querySelectorAll(".ck-sec");
    for (var si = 0; si < secs.length; si++) {
      var secRows = secs[si].querySelectorAll(".ck-prow");
      var anyRow = false;
      for (var ri = 0; ri < secRows.length; ri++) {
        if (!secRows[ri].hidden) { anyRow = true; break; }
      }
      secs[si].hidden = !anyRow;
    }
  }
  function closePanel() { if (panel) panel.classList.remove("on"); }

  function bind(id, fn) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", fn);
    if (el) el.addEventListener("input", fn);
  }
  rememberHints();   // Y3: capture every control's base hint before any edit replaces one
  /* Y2: the button groups that replaced the selects. One click = one undo snapshot + the same
   * model field the select wrote + the pressed state moves. `attr` is the buttons' data- name. */
  function bindGroup(id, attr, fn) {
    var group = document.getElementById(id);
    if (!group) return;
    group.addEventListener("click", function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest("[data-" + attr + "]") : null;
      if (!btn || !group.contains(btn)) return;
      snapshot();
      fn(btn.getAttribute("data-" + attr), btn);
      pressGroup(group, attr, btn.getAttribute("data-" + attr));
      render();
    });
  }
  /* A3 (the pane-writer layer of the per-value table): the offered `at` is validated BEFORE
   * any snapshot or write — a finite number in [0,1] is written; `null` is the documented
   * RESET (clears to 0.5, NOT an error); an out-of-range number, a non-finite value, a string
   * or a boolean is REFUSED: the model keeps its previous value and the row is marked with
   * the reason (Y3's markInvalid idiom). bindGroup cannot do this — it snapshots before fn. */
  function acceptAtOffer(offered) {
    if (offered === null) return { ok: true, at: 0.5 };
    if (typeof offered !== "number") {
      return { ok: false, reason: "a number between 0 and 1, please" };
    }
    if (!isFinite(offered)) {
      return { ok: false, reason: "a finite number between 0 and 1, please" };
    }
    if (offered < 0 || offered > 1) return { ok: false, reason: "between 0 and 1, please" };
    return { ok: true, at: offered };
  }
  /* The four position groups bind through here, never bindGroup. A real control can only
   * carry a string, so the transport parses it: "null" offers the documented reset, a
   * complete numeric literal offers the number (possibly non-finite — 1e400), anything else
   * is offered as the string it is and refused by acceptAtOffer. `apply` returns false when
   * its A6.3 guard refuses (a hidden row's stale click); a REFUSED offer never snapshots. */
  function bindAtGroup(id, apply) {
    var group = document.getElementById(id);
    if (!group) return;
    group.addEventListener("click", function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest("[data-at]") : null;
      if (!btn || !group.contains(btn)) return;
      var raw = btn.getAttribute("data-at");
      var num = Number(raw);
      var offered = raw === "null" ? null
        : (raw !== null && raw.trim() !== "" && !isNaN(num) ? num : raw);
      var verdict = acceptAtOffer(offered);
      if (!verdict.ok) {   // refused BEFORE the snapshot: no undo noise, the model keeps its value
        markInvalid(btn, verdict.reason);
        return;
      }
      snapshot();   // one undo step per accepted click (the bindGroup idiom)
      if (apply(verdict.at) === false) return;
      clearInvalid(btn);
      pressGroup(group, "at", String(verdict.at));
      render();
    });
  }
  bindGroup("ck-tone", "tone", function (value) {
    var item = selected && (selected.type === "edge" ? state.edges[selected.id]
      : itemById(selected.id));
    if (!item) return;
    item.tone = value;
  });
  bindGroup("ck-kind", "kind", function (value) {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item) return;
    item.kind = value;
    render();
    openPanel();   // X1: a kind change re-buckets the panel (a box's parts row, a table's grid)
  });
  bindGroup("ck-align", "align", function (value) {   // a node's text align; a table's cells default
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item) return;
    item.align = value;
  });
  bindGroup("ck-split", "split", function (value) {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item) return;
    item.split = value;
  });
  bindGroup("ck-start", "end", function (value) {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.start = value;
  });
  bindGroup("ck-end", "end", function (value) {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.end = value;
  });
  bindGroup("ck-head-size", "head", function (value) {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.head_size = value;
  });
  /* v1.6 (Z6): the line's own controls — the same groups, glyphs and `bind` idiom as v1.5, in the
   * line's own section: a line is not an edge, and the edge's rows (label, route, head size,
   * attach sides) are hidden for it (Z6.2). Each write is one undo step (bindGroup snapshots). */
  bindGroup("ck-line-tone", "tone", function (value) {
    var item = lineOfSelection();
    if (!item) return;
    item.tone = value;
  });
  bindGroup("ck-line-style", "style", function (value) {
    var item = lineOfSelection();
    if (!item) return;
    item.style = value;
  });
  bindGroup("ck-line-start", "end", function (value) {
    var item = lineOfSelection();
    if (!item) return;
    item.start = value;
  });
  bindGroup("ck-line-end", "end", function (value) {
    var item = lineOfSelection();
    if (!item) return;
    item.end = value;
  });
  // X4 + Y4.7: text size applies to the node's own text AND to a note in the notes list.
  bindGroup("ck-text-size", "size", function (value) {
    var item = selected && selected.type !== "edge" && selected.type !== "group"
      ? itemById(selected.id) : null;
    if (!item) return;
    item.text_size = value;
  });
  bind("ck-text", function () {
    if (!selected) return;
    var item = selected.type === "edge" ? state.edges[selected.id] : itemById(selected.id);
    if (!item) return;
    item.text = this.value;
    if (item.kind === "note") { item.h = 12 + wrap(item.text, item.w - 20, FONT).length * LINE + 10; }
    render();
  });
  bind("ck-sub", function () {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item) return;
    item.sub = this.value;
    render();
  });
  bind("ck-parts", function () {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item) return;
    var lines = this.value.split("\n").map(function (l) { return l.trim(); })
      .filter(function (l) { return l; });
    // X2 (amended in gap round 1): the merge is index-preserving at ANY count — an unchanged
    // index keeps its part's sub, so the stepper (and any count-changing edit) cannot erase
    // the subtitles of the parts around it. (Was: subs survived only when the line count was
    // unchanged.)
    var before = normaliseParts(item.parts);
    if (lines.length) {
      item.parts = lines.map(function (l, i) {
        var sub = before[i] && before[i].sub ? before[i].sub : null;
        return sub ? { text: l, sub: sub } : { text: l };
      });
    } else {
      delete item.parts;
    }
    refreshPartsUI();
    render();
  });
  // W3.4: writing "auto"/"medium" explicitly is allowed — the reader treats them as the default.
  bind("ck-from-side", function () {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.from_side = this.value;
    render();
  });
  bind("ck-to-side", function () {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.to_side = this.value;
    render();
  });
  /* A6.1 (the wiring half): the line's own from/to side selects — the rows show only when the
   * end's anchor resolves (A6.3), so the anchor object is there to write; `auto` stays writable,
   * as on the edge's selects (W3.4). The hidden-row guard keeps a stale event harmless. */
  bind("ck-line-from-side", function () {
    var item = lineOfSelection();
    if (!item || !anchorResolves(item.from)) return;
    item.from.side = this.value;
    render();
  });
  bind("ck-line-to-side", function () {
    var item = lineOfSelection();
    if (!item || !anchorResolves(item.to)) return;
    item.to.side = this.value;
    render();
  });
  /* A6.1/A6.4: a preset click writes that preset — one undo step per click (bindAtGroup
   * snapshots first), the pressed state and the hint follow on the next sync. The line guards
   * are the A6.3 predicate; the edge's ends always resolve, so its groups need none. A3: the
   * offered value is validated BEFORE the write — an invalid one is refused with the row
   * marked, `null` clears to 0.5. */
  bindAtGroup("ck-line-from-at", function (at) {
    var item = lineOfSelection();
    if (!item || !anchorResolves(item.from)) return false;
    item.from.at = at;
  });
  bindAtGroup("ck-line-to-at", function (at) {
    var item = lineOfSelection();
    if (!item || !anchorResolves(item.to)) return false;
    item.to.at = at;
  });
  bindAtGroup("ck-from-at", function (at) {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return false;
    item.from_at = at;
  });
  bindAtGroup("ck-to-at", function (at) {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return false;
    item.to_at = at;
  });
  // X1: the edge's label/style/route rows (the panel never exposed them before).
  bind("ck-label", function () {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.label = this.value;
    render();
  });
  bind("ck-style", function () {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.style = this.value;
    render();
  });
  bind("ck-route", function () {
    var item = selected && selected.type === "edge" ? state.edges[selected.id] : null;
    if (!item) return;
    item.route = this.value;
    render();
  });
  // X1 + Y3: w/h inputs — the same fields lane R's resize handles write. An invalid value never
  // reaches the model: the row is marked and the hint carries the reason (the resize minimums);
  // the previous model value stays until a valid value clears the mark.
  bind("ck-w", function () {
    var item = selected && selected.type !== "edge" && selected.type !== "group"
      ? itemById(selected.id) : null;
    if (!item) return;
    var v = parseFloat(this.value);
    if (isNaN(v)) { markInvalid(this, "a number, please"); return; }
    if (v < MIN_W) { markInvalid(this, MIN_W + " or more"); return; }
    clearInvalid(this);
    item.w = v;
    if (item.kind === "note") {   // a note's height follows the wrapped lines, as in ck-text
      item.h = 12 + wrap(item.text, item.w - 20, FONT).length * LINE + 10;
      var hEl = document.getElementById("ck-h");
      if (hEl) hEl.value = item.h;
    }
    render();
  });
  bind("ck-h", function () {
    var item = selected && selected.type !== "edge" && selected.type !== "group"
      ? itemById(selected.id) : null;
    if (!item) return;
    var v = parseFloat(this.value);
    if (isNaN(v)) { markInvalid(this, "a number, please"); return; }
    if (v < MIN_H) { markInvalid(this, MIN_H + " or more"); return; }
    clearInvalid(this);
    item.h = v;
    render();
  });
  // X4 + Y4.7: bold, on the node's own text AND on a note in the notes list.
  bind("ck-bold", function () {
    var item = selected && selected.type !== "edge" && selected.type !== "group"
      ? itemById(selected.id) : null;
    if (!item) return;
    item.bold = this.checked;
    render();
  });
  /* X3.4 + Y5: the table's grid. Changing rows/cols remaps the cells INDEX-PRESERVING — a cell
   * that survives (`row < rows && col < cols`) keeps its own object fields (tone/align); the
   * naive join-and-write-back erased them (v1.5 gap finding 6). Y3: an invalid count never
   * reaches the model — the row is marked and the hint says why. */
  function remapCells(item, newRows, newCols) {
    var oldGrid = tableGrid(item);
    var old = rawCellsSized(item, oldGrid);
    var out = [];
    for (var r = 0; r < newRows; r++) {
      for (var c = 0; c < newCols; c++) {
        out.push(r < oldGrid.rows && c < oldGrid.cols ? old[r * oldGrid.cols + c] : "");
      }
    }
    item.cells = out;
  }
  /* Y3/Y5 (v1.5 verifier, MAJOR): parseInt("2.5") is 2, so a fractional count used to be truncated
   * into the model without a word. A count is a whole number or it is refused, with the reason. */
  function integerCount(input) {
    var raw = String(input.value).trim();
    var v = Number(raw);
    if (raw === "" || !isFinite(v)) { markInvalid(input, "a number, please"); return null; }
    if (v !== Math.floor(v)) { markInvalid(input, "a whole number, please"); return null; }
    if (!(v >= 1)) { markInvalid(input, "1 or more"); return null; }
    clearInvalid(input);
    return v;
  }
  bind("ck-rows", function () {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item || (item.kind || "box") !== "table") return;
    var v = integerCount(this);
    if (v === null) return;
    remapCells(item, v, tableGrid(item).cols);
    item.rows = v;
    syncTablePanel(item);
    render();
  });
  bind("ck-cols", function () {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item || (item.kind || "box") !== "table") return;
    var v = integerCount(this);
    if (v === null) return;
    remapCells(item, tableGrid(item).rows, v);
    item.cols = v;
    syncTablePanel(item);
    render();
  });
  /* Y5: the cells textarea write is index-preserving — editing line i replaces that cell's
   * text and leaves every other line exactly as it was; an object cell keeps its own
   * tone/align (the cell section clears them). A missing trailing line clears that cell's
   * text, as the old pad-with-"" behaviour did. Empty lines stay cells (X3.2). */
  bind("ck-cells", function () {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item || (item.kind || "box") !== "table") return;
    var grid = tableGrid(item);
    var cells = rawCellsSized(item, grid);
    var lines = this.value.split("\n");
    for (var i = 0; i < cells.length; i++) {
      var cur = cellParts(cells[i]);
      var text = i < lines.length ? lines[i] : "";
      if (cur.tone || cur.align) {
        var obj = { text: text };
        if (cur.tone) obj.tone = cur.tone;
        if (cur.align) obj.align = cur.align;
        cells[i] = obj;
      } else {
        cells[i] = text;
      }
    }
    item.cells = cells;
    syncCellPanel(item, grid);   // the picked cell's text field follows the textarea
    render();
  });
  bind("ck-header", function () {
    var item = selected && selected.type === "node" ? nodeById(selected.id) : null;
    if (!item || (item.kind || "box") !== "table") return;
    item.header = this.checked;
    render();
  });
  /* Y5: the table v2 controls. The col-width inputs are rebuilt when cols changes, so their
   * listener lives on the row container; a non-positive or non-numeric width never reaches the
   * model (Y3), a valid one writes all `cols` widths (missing ones default to 1 — Y4.2). */
  var colwBox = document.getElementById("ck-colw-row");
  if (colwBox) colwBox.addEventListener("input", function (evt) {
    var input = evt.target;
    if (!input || !input.id || input.id.indexOf("ck-colw-") !== 0) return;
    var item = tableOfSelection();
    if (!item) return;
    var grid = tableGrid(item);
    var v = parseFloat(input.value);
    if (isNaN(v)) { markInvalid(input, "a number, please"); return; }
    if (!(v >= 0.1)) { markInvalid(input, "0.1 or more"); return; }
    clearInvalid(input);
    var widths = [];
    for (var k = 0; k < grid.cols; k++) {
      var raw = Array.isArray(item.col_widths) ? parseFloat(item.col_widths[k]) : NaN;
      widths.push(raw > 0 ? raw : 1);
    }
    widths[parseInt(input.id.slice(8), 10)] = v;
    item.col_widths = widths;
    render();
  });
  /* The cell section: the row/col pickers only choose which cell the other controls edit (they
   * never touch the document — a declared panelfields exception); the text input and the
   * align/tone groups write the picked cell, object-shaped only when it needs one (writeCell). */
  bind("ck-cell-row", function () {
    var item = tableOfSelection();
    if (item) syncCellPanel(item);
  });
  bind("ck-cell-col", function () {
    var item = tableOfSelection();
    if (item) syncCellPanel(item);
  });
  bind("ck-cell-text", function () {
    var p = cellPicker();
    if (!p) return;
    writeCell(p.item, p.r, p.c, { text: this.value }, false);
    var cellsEl = document.getElementById("ck-cells");
    if (cellsEl && document.activeElement !== cellsEl) {
      cellsEl.value = tableCells(p.item, p.grid).join("\n");   // the textarea follows
    }
    render();
  });
  bindGroup("ck-cell-align", "cell-align", function (value) {   // `inherit` clears the own align
    var p = cellPicker();
    if (!p) return;
    writeCell(p.item, p.r, p.c, { align: value === "inherit" ? null : value }, false);
  });
  bindGroup("ck-cell-tone", "tone", function (value) {   // the empty swatch clears the own tone
    var p = cellPicker();
    if (!p) return;
    writeCell(p.item, p.r, p.c, { tone: value || null }, false);
  });
  /* Row/column tones: one swatch row per table row/column, rebuilt by syncToneGrid when the
   * grid changes, so the listeners delegate from the containers. The ∅ swatch clears. */
  var rowTonesBox = document.getElementById("ck-rowtones");
  if (rowTonesBox) rowTonesBox.addEventListener("click", function (evt) {
    var btn = evt.target && evt.target.closest ? evt.target.closest("[data-tone]") : null;
    var rowEl = evt.target && evt.target.closest ? evt.target.closest("[data-row-tone]") : null;
    var item = tableOfSelection();
    if (!btn || !rowEl || !item) return;
    snapshot();
    toneListWrite(item, "row_tones", tableGrid(item).rows,
      parseInt(rowEl.getAttribute("data-row-tone"), 10), btn.getAttribute("data-tone"));
    syncToneGrid(item);
    render();
  });
  var colTonesBox = document.getElementById("ck-coltones");
  if (colTonesBox) colTonesBox.addEventListener("click", function (evt) {
    var btn = evt.target && evt.target.closest ? evt.target.closest("[data-tone]") : null;
    var colEl = evt.target && evt.target.closest ? evt.target.closest("[data-col-tone]") : null;
    var item = tableOfSelection();
    if (!btn || !colEl || !item) return;
    snapshot();
    toneListWrite(item, "col_tones", tableGrid(item).cols,
      parseInt(colEl.getAttribute("data-col-tone"), 10), btn.getAttribute("data-tone"));
    syncToneGrid(item);
    render();
  });
  /* Panel action buttons (X2's stepper, X3.4's grid steppers, X7's arrange). They live in the
   * panel, so they get their own listener here — the toolbar's document-level dispatcher stays
   * untouched. Every count change and every arrange action is ONE undo snapshot. */
  if (panel) panel.addEventListener("click", function (evt) {
    var button = evt.target && evt.target.closest ? evt.target.closest("[data-act]") : null;
    if (!button) return;
    var act = button.getAttribute("data-act");
    if (act === "part-add" || act === "part-del") {   // X2: append `part <N>` / drop the last
      var field = document.getElementById("ck-parts");
      if (!field || !selected) return;
      snapshot();   // undoable: a dropped part comes back with its sub
      var raw = field.value.replace(/\n+$/, "");
      var lines = raw === "" ? [] : raw.split("\n");
      if (act === "part-add") {
        var kept = lines.filter(function (l) { return l.trim(); }).length;
        lines.push("part " + (kept + 1));   // the model drops blanks, so a placeholder it is
      } else {
        lines.pop();
      }
      field.value = lines.join("\n");
      field.dispatchEvent(new Event("input"));   // from here on, exactly as typing does
      return;
    }
    if (act === "point-add" || act === "point-del") {   // v1.6 (Z6.1): the line's point count
      var line = lineOfSelection();
      if (!line || !Array.isArray(line.points)) return;
      if (act === "point-del" && line.points.length <= 2) {
        // Never below 2: a "line" with one point is not a line. The model is untouched and the
        // message says why — the row is not disabled, and there is no dialog.
        say("a line has at least 2 points");
        return;
      }
      snapshot();   // undoable: a removed point comes back
      if (act === "point-add") {
        var last = line.points[line.points.length - 1] || [0, 0];
        line.points.push([Number(last[0]) + 40, Number(last[1])]);   // append at the END
      } else {
        line.points.pop();
      }
      syncLinePanel(line);
      render();
      return;
    }
    if (act === "rows-add" || act === "rows-del" || act === "cols-add" || act === "cols-del") {
      var input = document.getElementById(act.slice(0, 4) === "rows" ? "ck-rows" : "ck-cols");
      if (!input) return;
      input.value = Math.max(1, (parseInt(input.value, 10) || 2) + (act.slice(-3) === "add" ? 1 : -1));
      input.dispatchEvent(new Event("input"));   // the input's own handler does the resize
      return;
    }
    // X7: align to the selection's bounding box (the extremes stay put); spread equalises the
    // GAPS with the two extremes fixed. Geometric items only — an edge is never moved. (The
    // per-action comparisons stay literal: the control-surface gate pairs every data-act with
    // its handler by name.)
    var items = arrangeItems();
    var i;
    var isAlign = act === "align-left" || act === "align-h-centre" || act === "align-right" ||
      act === "align-top" || act === "align-v-middle" || act === "align-bottom";
    var isSpread = act === "spread-h" || act === "spread-v";
    if (isAlign && items.length >= 2) {
      snapshot();
      var x0 = Math.min.apply(null, items.map(function (r) { return r.x; }));
      var x1 = Math.max.apply(null, items.map(function (r) { return r.x + r.w; }));
      var y0 = Math.min.apply(null, items.map(function (r) { return r.y; }));
      var y1 = Math.max.apply(null, items.map(function (r) { return r.y + r.h; }));
      items.forEach(function (r) {
        if (act === "align-left") r.x = x0;
        if (act === "align-h-centre") r.x = (x0 + x1) / 2 - r.w / 2;
        if (act === "align-right") r.x = x1 - r.w;
        if (act === "align-top") r.y = y0;
        if (act === "align-v-middle") r.y = (y0 + y1) / 2 - r.h / 2;
        if (act === "align-bottom") r.y = y1 - r.h;
      });
      render();
      return;
    }
    if (isSpread && items.length >= 3) {
      snapshot();
      var horiz = act === "spread-h";
      var sorted = items.slice().sort(function (a, b) { return horiz ? a.x - b.x : a.y - b.y; });
      var first = sorted[0], last = sorted[sorted.length - 1];
      var sizes = 0;
      for (i = 0; i < sorted.length - 1; i++) sizes += horiz ? sorted[i].w : sorted[i].h;
      var gap = ((horiz ? last.x - first.x : last.y - first.y) - sizes) / (sorted.length - 1);
      var cursor = horiz ? first.x : first.y;
      for (i = 0; i + 1 < sorted.length - 1; i++) {   // the extremes never move
        cursor += (horiz ? sorted[i].w : sorted[i].h) + gap;
        if (horiz) sorted[i + 1].x = cursor; else sorted[i + 1].y = cursor;
      }
      render();
    }
  });
  var del = document.getElementById("ck-delete");
  if (del) del.addEventListener("click", removeSelected);
  var close = document.getElementById("ck-close");
  if (close) close.addEventListener("click", closePanel);

  /* ---- toolbar ---- */
  /* Z4.1 + S6: the single writer of the tool mode. Exactly one [data-tool] is pressed; line and
   * straight mode set the crosshair cursor, leaving them restores the default. Switching away
   * drops an open draft — it never reached the model (Z4.2). Clicking the active drawing tool
   * toggles back to select; `select` is always the way out. */
  function setTool(name) {
    var next = name === "line" ? (tool === "line" ? "select" : "line")
             : name === "straight" ? (tool === "straight" ? "select" : "straight")
             : "select";
    if (next !== tool) {
      tool = next;
      if (draft) { draft = null; render(); }   // S6: leaving the mode drops a live straight draft too
    }
    if (host) host.style.cursor = (tool === "line" || tool === "straight") ? "crosshair" : "";
    say(tool === "line" ? "line: click to add points, double-click or Enter commits, Esc cancels"
      : tool === "straight" ? "straight line: drag for a 45°-step line (horizontal, vertical or " +   // S7
        "diagonal), hold Shift for any angle"
      : "");
    syncButtons();
  }

  function syncButtons() {
    var tools = document.querySelectorAll("[data-tool]");   // Z4.1: exactly one pressed
    for (var i = 0; i < tools.length; i++) {
      tools[i].setAttribute("aria-pressed",
        tools[i].getAttribute("data-tool") === tool ? "true" : "false");
    }
    var gridBtn = document.querySelector('[data-act="grid"]');   // R7: emitted by lane A's page()
    if (gridBtn) gridBtn.setAttribute("aria-pressed", state.grid_on ? "true" : "false");
  }

  document.addEventListener("click", function (evt) {
    var toolBtn = evt.target && evt.target.closest ? evt.target.closest("[data-tool]") : null;
    if (toolBtn) { setTool(toolBtn.getAttribute("data-tool")); return; }   // Z4.1
    var button = evt.target.closest ? evt.target.closest("[data-act]") : null;
    if (!button) return;
    var act = button.getAttribute("data-act");
    if (act === "add") { say("added " + addNode()); }
    if (act === "addnote") { say("added " + addNote()); }
    // Z4.5: data-act="connect" and connectFrom are deleted — the line tool replaces them.
    if (act === "zoom-out") { zoomCentre(1 / 1.15); }   // W1.5
    if (act === "zoom-in") { zoomCentre(1.15); }
    if (act === "fit") { fitView(); }
    if (act === "grid") {   // R7: toggle the background grid (R5)
      snapshot();
      state.grid_on = !state.grid_on;
      render();
      say(state.grid_on ? "grid on" : "grid off");
      syncButtons();
    }
    if (act === "undo") {
      if (history.length) { state = JSON.parse(history.pop()); setSelection([]); closePanel(); render(); say("undone"); }
      else say("nothing to undo");
    }
    if (act === "copy") copyJson();
    if (act === "download") downloadJson();
    if (act === "reset") {
      snapshot();
      replaceState(INITIAL);
      setSelection([]); closePanel(); render(); say("reset");
    }
  });

  function exportJson() { return JSON.stringify(state, null, 1); }

  function copyJson() {
    var text = exportJson();
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { say("board JSON copied"); },
          function () { say("clipboard blocked — use download"); });
        return;
      }
    } catch (e) { /* fall through */ }
    say("clipboard unavailable — use download");
  }

  function downloadJson() {
    try {
      var blob = new Blob([exportJson()], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (state.title || "board").replace(/[^a-z0-9-_]+/gi, "-").toLowerCase() + ".json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      say("board JSON downloaded");
    } catch (e) { say("download failed: " + e.message); }
  }

  /* ---- boot ---- */
  // W1.5's boot-time insert of the view buttons is GONE in v1.6 (Z1): the whole bar — every group,
  // the icons, the zoom readout and the message block — is page()'s markup now, so the editor owns
  // no toolbar markup at all. Two sources of the same row is how a v1.5 toolbar could come back
  // after a page() change.
  // Z4.1/Z4.5: lane P owns the toolbar markup in page(); on a tree without it the editor injects
  // the mode buttons itself and stands down when [data-tool] exists (the v1.3 zoom-control
  // precedent), so the merged toolbar carries each control exactly once. The connect button is
  // deleted from the pane either way (Z4.5).
  (function () {
    var connectBtn = document.querySelector('[data-act="connect"]');
    if (connectBtn) connectBtn.remove();
    if (document.querySelector("[data-tool]")) return;
    var addNoteBtn = document.querySelector('[data-act="addnote"]');
    if (!addNoteBtn) return;
    // F4 (v1.8 round 3): the fallback straight button carries the SAME 16x16 two-end-dot glyph
    // the served bar does — boardkit.py's _BAR_GLYPHS["straight"] and bar_button markup, verbatim.
    addNoteBtn.insertAdjacentHTML("afterend",
      '<button data-tool="select" aria-pressed="true">select</button>' +
      '<button class="ck-ico" data-tool="straight" aria-pressed="false" ' +
      'aria-label="draw a straight line" title="straight line: drag from A to B for a 45°-step ' +
      'line (horizontal, vertical or diagonal), hold Shift for any angle">' +
      '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
      'stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3 13 L 13 3"/><circle cx="3" cy="13" r="1.2"/>' +
      '<circle cx="13" cy="3" r="1.2"/></svg></button>' +
      '<button data-tool="line" aria-pressed="false">line</button>');
  })();
  if (location.hash !== "#selftest" && location.hash !== "#selftest-pane") restore();
  render();
  syncButtons();   // R7: aria-pressed reflects the loaded document

  if (location.hash === "#selftest") {
    var report = { ok: false, steps: [] };
    try {
      var base = { nodes: state.nodes.length, edges: state.edges.length };
      var first = state.nodes[0].id, second = state.nodes[1].id;
      var beforeEdges = JSON.stringify(state.edges);
      var newId = addNode();
      report.steps.push(["addNode", state.nodes.length === base.nodes + 1]);
      setSelection([{ type: "node", id: first }]);
      openPanel();
      report.steps.push(["panelFilled", document.getElementById("ck-text").value === nodeById(first).text]);
      document.getElementById("ck-text").value = "renamed by test";
      document.getElementById("ck-text").dispatchEvent(new Event("input"));
      report.steps.push(["editText", nodeById(first).text === "renamed by test"]);
      var startX = nodeById(first).x;
      setSelection([{ type: "node", id: first }]);
      nodeById(first).x = startX + 60; nodeById(first).y += 20;
      render();
      var rerouted = JSON.stringify(state.edges) !== beforeEdges || state.edges.length === 0;
      report.steps.push(["moveReroute", nodeById(first).x === startX + 60 || rerouted]);
      var added = addEdge(first, newId);   // the fresh box has no edge yet
      var deduped = addEdge(first, newId) === false;
      report.steps.push(["addEdge", added && deduped && state.edges.length === base.edges + 1]);
      // Real pointer events, not a direct state edit: this is the drag path the user takes.
      var box = host.querySelector('[data-id="' + first + '"]').getBoundingClientRect();
      var cx = box.left + box.width / 2, cy = box.top + box.height / 2;
      var grid = state.grid || 10;
      function pe(type, x, y) {
        // Re-query every time: a re-render replaces the node elements mid-drag, and a detached
        // element cannot bubble to the host listener.
        var el = host.querySelector('[data-id="' + first + '"]');
        el.dispatchEvent(new PointerEvent(type, { clientX: x, clientY: y, pointerId: 1, bubbles: true }));
      }
      var mouseX = nodeById(first).x, mouseY = nodeById(first).y;
      pe("pointerdown", cx, cy);
      pe("pointermove", cx + 40, cy + 30);
      pe("pointerup", cx + 40, cy + 30);
      // X6 amended the drop rule: an axis with a guide match lands exactly on that alignment (which
      // is deliberately NOT on the grid), every other axis lands on the grid. The old "both axes are
      // grid multiples" assertion contradicted that, and it failed on the v1.4 fixture, where the
      // dragged table's bottom anchor sits 2px from another table's.
      var moved = nodeById(first);
      var landed = guideMatch({ x: moved.x, y: moved.y, w: moved.w, h: moved.h }, {});
      function snappedOn(v, m) { return m ? Math.abs(m.delta) < 0.001 : v % grid === 0; }
      report.steps.push(["dragEvents", Math.abs(moved.x - mouseX) > 5 && Math.abs(moved.y - mouseY) > 5 &&
        snappedOn(moved.x, landed.x) && snappedOn(moved.y, landed.y)]);
      var round = JSON.parse(exportJson());
      report.steps.push(["exportRoundTrip", round.nodes.length === state.nodes.length &&
        round.edges.length === state.edges.length]);
      var t0 = performance.now();
      for (var i = 0; i < 20; i++) render();
      report.renderMs = Math.round((performance.now() - t0) / 20 * 100) / 100;
      setSelection([{ type: "node", id: newId }]);
      removeSelected();
      report.steps.push(["deleteNode", state.nodes.length === base.nodes]);
      // v1.2 (R2-R7): each step adds its own fixture and cleans up, so any board can self-test.
      state.nodes.push({ id: "st-shape", x: 40, y: 40, w: 150, h: 90, text: "hex",
        kind: "hexagon", tone: "accent" });
      render();
      var shapeGroup = host.querySelector('[data-id="st-shape"]');
      report.steps.push(["shapes", !!shapeGroup && !!shapeGroup.querySelector("polygon")]);
      state.nodes = state.nodes.filter(function (n) { return n.id !== "st-shape"; });
      state.nodes.push({ id: "st-parts", x: 40, y: 40, w: 180, h: 110, text: "", kind: "box",
        tone: "default", parts: [{ text: "a" }, { text: "b" }, { text: "c" }], split: "rows" });
      render();
      var partGroup = host.querySelector('[data-id="st-parts"]');
      report.steps.push(["parts", !!partGroup && partGroup.querySelectorAll("path").length === 2]);
      state.nodes = state.nodes.filter(function (n) { return n.id !== "st-parts"; });
      // R7: editing the list must not discard a part's sub while the count is unchanged.
      state.nodes.push({ id: "st-sub", x: 40, y: 40, w: 180, h: 110, text: "", kind: "box",
        tone: "default", parts: [{ text: "a", sub: "5 ms" }, { text: "b" }], split: "rows" });
      setSelection([{ type: "node", id: "st-sub" }]);
      openPanel();
      var partsField = document.getElementById("ck-parts");
      partsField.value = "a\nbb";
      partsField.dispatchEvent(new Event("input"));
      var merged = nodeById("st-sub").parts;
      report.steps.push(["partsub", !!merged && merged.length === 2 && merged[0].sub === "5 ms" &&
        merged[1].text === "bb"]);
      closePanel();
      // R4: a part that is only blanks is not a part — the divider count follows the kept ones.
      state.nodes.push({ id: "st-blank", x: 40, y: 40, w: 180, h: 90, text: "", kind: "box",
        tone: "default", parts: ["a", "   ", "b"], split: "rows" });
      render();
      var blankGroup = host.querySelector('[data-id="st-blank"]');
      report.steps.push(["blankparts", !!blankGroup && blankGroup.querySelectorAll("path").length === 1]);
      state.nodes = state.nodes.filter(function (n) { return n.id !== "st-blank" && n.id !== "st-sub"; });
      render();
      state.edges.push({ from: first, to: second, start: "circle", end: "diamond" });
      render();
      var lines2 = host.querySelectorAll(".ck-edge polyline");
      var lastLine = lines2[lines2.length - 1];
      report.steps.push(["heads", !!lastLine && !!lastLine.getAttribute("marker-start") &&
        !!lastLine.getAttribute("marker-end")]);
      state.edges.pop();
      // W3.5: with both sides explicit the polyline starts and ends at the side midpoints.
      state.edges.push({ from: first, to: second, from_side: "top", to_side: "bottom" });
      render();
      var attachLines = host.querySelectorAll(".ck-edge polyline");
      var attachLine = attachLines[attachLines.length - 1];
      var attachPts = attachLine ? attachLine.getAttribute("points").split(" ") : [];
      var attachA = nodeById(first), attachB = nodeById(second);
      var attachFirst = attachPts.length ? attachPts[0].split(",") : [];
      var attachLast = attachPts.length ? attachPts[attachPts.length - 1].split(",") : [];
      report.steps.push(["attach", attachFirst.length === 2 && attachLast.length === 2 &&
        Math.abs(parseFloat(attachFirst[0]) - (attachA.x + attachA.w / 2)) < 0.01 &&
        Math.abs(parseFloat(attachFirst[1]) - attachA.y) < 0.01 &&
        Math.abs(parseFloat(attachLast[0]) - (attachB.x + attachB.w / 2)) < 0.01 &&
        Math.abs(parseFloat(attachLast[1]) - (attachB.y + attachB.h)) < 0.01]);
      state.edges.pop();
      render();
      var hadGrid = state.grid_on === true;
      state.grid_on = true;
      render();
      var gridShown = !!host.querySelector("pattern#ck-grid") && !!host.querySelector("rect.ck-grid");
      state.grid_on = false;
      render();
      var gridHidden = !host.querySelector("pattern#ck-grid") && !host.querySelector("rect.ck-grid");
      state.grid_on = hadGrid;
      render();
      report.steps.push(["grid", gridShown && gridHidden]);
      // W2.4 + W2.1: a selected group clones with a non-colliding id and the (+20, +20) offset, on
      // its own and mixed with a node.
      var groupCount = state.groups.length, nodeCount = state.nodes.length, clones = [];
      state.groups.push({ id: "st-grp", x: 500, y: 500, w: 160, h: 90, text: "grp", tone: "muted" });
      setSelection([{ type: "group", id: "st-grp" }]);
      duplicateSelection();
      selection.forEach(function (s) { clones.push(s.id); });     // the group clone
      var clonedGroup = state.groups.length === groupCount + 2 &&
        state.groups.some(function (g) {
          return g.id !== "st-grp" && g.x === 520 && g.y === 520 && g.text === "grp";
        }) && state.groups.filter(function (g) { return g.id === "st-grp"; }).length === 1;
      setSelection([{ type: "group", id: "st-grp" }, { type: "node", id: first }]);
      duplicateSelection();
      selection.forEach(function (s) { clones.push(s.id); });     // the mixed-selection clones
      var clonedCounts = state.groups.length === groupCount + 3 && state.nodes.length === nodeCount + 1;
      state.groups = state.groups.filter(function (g) {
        return g.id !== "st-grp" && clones.indexOf(g.id) === -1;   // leave nothing behind
      });
      state.nodes = state.nodes.filter(function (n) { return clones.indexOf(n.id) === -1; });
      var cleanAfter = state.groups.length === groupCount && state.nodes.length === nodeCount &&
        !state.groups.some(function (g) { return g.id === "st-grp"; });
      report.steps.push(["groupdup", clonedGroup && clonedCounts && cleanAfter]);
      setSelection([]);
      render();

      // W1.2b: the pointer transform must add the viewBox origin. A DRAG cannot show this — the
      // error cancels inside the delta — so prove it where screen and document space meet: with a
      // moved origin, a marquee drawn around an item must select THAT item. The band starts 30
      // units above the node (not 10): with the Z5.4 polyline hit-test a start on the v1.6
      // fixture's ins-a→ins-b edge segment (y = 130) would select that edge instead of opening a
      // marquee — exactly what a real user click on that pixel would do.
      var savedView = { x: view.x, y: view.y, k: view.k }, savedSnap = state.snap;
      state.snap = false;
      view.x = 120; view.y = 80; view.k = 1;
      setSelection([]);
      render();
      var originNode = nodeById(first);
      var osvg = host.querySelector("svg"), orect = osvg.getBoundingClientRect();
      var ovb = osvg.getAttribute("viewBox").split(/\s+/).map(Number);
      var oscale = orect.width / ovb[2];
      function screenOf(dx, dy) {
        return { x: orect.left + (dx - ovb[0]) * oscale, y: orect.top + (dy - ovb[1]) * oscale };
      }
      var olo = screenOf(originNode.x - 10, originNode.y - 30);
      var ohi = screenOf(originNode.x + originNode.w + 10, originNode.y + originNode.h + 10);
      pev("svg", "pointerdown", olo.x, olo.y);      // empty canvas around that node
      pev("svg", "pointermove", ohi.x, ohi.y);
      // The band is drawn in DOCUMENT coordinates from the pointer's document position, so its own
      // x/y is the assertion that cannot cancel: it must be the document point under the start pixel.
      var bandEl = host.querySelector(".ck-marquee");
      var bandX = bandEl ? parseFloat(bandEl.getAttribute("x")) : NaN;
      var bandY = bandEl ? parseFloat(bandEl.getAttribute("y")) : NaN;
      pev("svg", "pointerup", ohi.x, ohi.y);
      report.steps.push(["origin", Math.abs(bandX - (originNode.x - 10)) < 1.5 &&
        Math.abs(bandY - (originNode.y - 30)) < 1.5 &&
        selection.some(function (s) { return s.type === "node" && s.id === first; }) &&
        originNode.x === nodeById(first).x]);
      setSelection([]);
      view.x = savedView.x; view.y = savedView.y; view.k = savedView.k;
      state.snap = savedSnap;
      render();

      // W2.3b: both orders. (a) dragging one selected item moves every selected item;
      // (b) a click — including a sub-3-pixel jitter — collapses the selection to that item.
      var otherNode = nodeById(second);
      var keepA = { x: otherNode.x, y: otherNode.y }, keepB = { x: nodeById(first).x, y: nodeById(first).y };
      setSelection([{ type: "node", id: second }, { type: "node", id: first }]);
      var brect = host.querySelector('[data-id="' + first + '"]').getBoundingClientRect();
      var bx = brect.left + brect.width / 2, by = brect.top + brect.height / 2;
      pev('[data-id="' + first + '"]', "pointerdown", bx, by);
      pev('[data-id="' + first + '"]', "pointermove", bx + 30, by + 24);
      pev('[data-id="' + first + '"]', "pointerup", bx + 30, by + 24);
      report.steps.push(["groupdrag", nodeById(first).x !== keepB.x && nodeById(second).x !== keepA.x &&
        selection.length === 2]);
      setSelection([{ type: "node", id: second }, { type: "node", id: first }]);
      var beforeClick = { x: nodeById(first).x, y: nodeById(first).y };
      var crect = host.querySelector('[data-id="' + first + '"]').getBoundingClientRect();
      var cx2 = crect.left + crect.width / 2, cy2 = crect.top + crect.height / 2;
      pev('[data-id="' + first + '"]', "pointerdown", cx2, cy2);
      pev('[data-id="' + first + '"]', "pointermove", cx2 + 1, cy2 + 1);   // sub-3 px jitter
      pev('[data-id="' + first + '"]', "pointerup", cx2 + 1, cy2 + 1);
      report.steps.push(["clickdrag", selection.length === 1 && selection[0].id === first &&
        nodeById(first).x === beforeClick.x && nodeById(first).y === beforeClick.y]);

      // R5.5: with snapping off the drop is NOT rounded to the grid.
      state.snap = false;
      var loose = nodeById(first);
      loose.x = 43; loose.y = 47;
      render();
      var box2 = host.querySelector('[data-id="' + first + '"]').getBoundingClientRect();
      pe("pointerdown", box2.left + box2.width / 2, box2.top + box2.height / 2);
      pe("pointermove", box2.left + box2.width / 2 + 13, box2.top + box2.height / 2 + 17);
      pe("pointerup", box2.left + box2.width / 2 + 13, box2.top + box2.height / 2 + 17);
      var dropped = nodeById(first);
      report.steps.push(["snapoff", dropped.x % grid !== 0 || dropped.y % grid !== 0]);
      state.snap = true;
      loose.x = 40; loose.y = 40;
      render();
      state.nodes.push({ id: "st-align", x: 40, y: 40, w: 180, h: 72, text: "left", kind: "box",
        tone: "default", align: "left" });
      render();
      var alignGroup = host.querySelector('[data-id="st-align"]');
      var alignText = alignGroup && alignGroup.querySelector("text");
      report.steps.push(["align", !!alignText && alignText.getAttribute("text-anchor") === "start"]);
      state.nodes = state.nodes.filter(function (n) { return n.id !== "st-align"; });
      render();
      // v1.3 (W1, W2): canvas interaction — every step drives the events a user would and
      // asserts on the rendered DOM or the document, not on editor bookkeeping.
      view = { x: 0, y: 0, k: 1 };
      render();
      function svgEl() { return host.querySelector("svg"); }
      function vbNow() { return svgEl().getAttribute("viewBox").split(/\s+/).map(Number); }
      function svgScaleNow() {
        var r = svgEl().getBoundingClientRect();
        return r.width / vbNow()[2];
      }
      function docPt(x, y) {   // a document point to client coordinates, through the current view
        var r = svgEl().getBoundingClientRect(), vb = vbNow(), sc = r.width / vb[2];
        return { x: r.left + (x - vb[0]) * sc, y: r.top + (y - vb[1]) * sc };
      }
      function pev(sel, type, x, y, opts) {   // re-query every time: renders replace the elements
        var el = sel === "svg" ? svgEl() : host.querySelector(sel);
        var init = { clientX: x, clientY: y, pointerId: 3, button: 0, bubbles: true };
        if (opts) for (var o in opts) init[o] = opts[o];
        el.dispatchEvent(new PointerEvent(type, init));
      }
      function mev(x, y, detail) {   // a REAL mouse press, as the browser's compatibility layer
        svgEl().dispatchEvent(new MouseEvent("mousedown",   // delivers it (detail = click count)
          { clientX: x, clientY: y, button: 0, detail: detail, bubbles: true }));
      }
      function key(keyName, opts) {
        var init = { key: keyName, bubbles: true };
        if (opts) for (var o2 in opts) init[o2] = opts[o2];
        document.dispatchEvent(new KeyboardEvent("keydown", init));
      }
      function ringed(cls, id) {
        var g = host.querySelector(cls + '[data-id="' + id + '"]');
        return !!g && !!g.querySelector('rect[stroke-dasharray="4 3"]');
      }
      function clickNode(id, opts) {
        var r = host.querySelector('.ck-node[data-id="' + id + '"]').getBoundingClientRect();
        var cx0 = r.left + r.width / 2, cy0 = r.top + r.height / 2;
        pev('.ck-node[data-id="' + id + '"]', "pointerdown", cx0, cy0, opts);
        pev("svg", "pointerup", cx0, cy0, opts);
      }
      // W1.3/W1.5: ctrl+wheel zooms, 0 resets, the toolbar buttons and the readout exist.
      var zr = svgEl().getBoundingClientRect();
      svgEl().dispatchEvent(new WheelEvent("wheel", { deltaY: -100, ctrlKey: true,
        clientX: zr.left + zr.width / 2, clientY: zr.top + zr.height / 2,
        bubbles: true, cancelable: true }));
      var vbZ = vbNow();
      var zoomText = (document.getElementById("ck-zoom") || {}).textContent;
      key("0");
      var vbR = vbNow();
      report.steps.push(["zoom",
        !!document.querySelector('[data-act="zoom-out"]') &&
        !!document.querySelector('[data-act="zoom-in"]') &&
        !!document.querySelector('[data-act="fit"]') &&
        zoomText === "115%" && vbZ[2] < state.width &&
        vbR[0] === 0 && vbR[1] === 0 && vbR[2] === state.width]);
      // W1.4: a plain wheel pans by the deltas (document units), a middle-drag pans, fit resets.
      var vbP0 = vbNow(), scP = svgScaleNow();
      svgEl().dispatchEvent(new WheelEvent("wheel", { deltaX: 46, deltaY: -23,
        clientX: zr.left + 10, clientY: zr.top + 10, bubbles: true, cancelable: true }));
      var vbP1 = vbNow();
      var wheelOk = Math.abs(vbP1[0] - (vbP0[0] + 46 / scP)) < 0.5 &&
                    Math.abs(vbP1[1] - (vbP0[1] - 23 / scP)) < 0.5;
      var pd = docPt(100, 100);
      pev("svg", "pointerdown", pd.x, pd.y, { button: 1 });
      pev("svg", "pointermove", pd.x + 30, pd.y, { button: 1 });
      pev("svg", "pointerup", pd.x + 30, pd.y, { button: 1 });
      var vbP2 = vbNow();
      var midOk = Math.abs(vbP2[0] - (vbP1[0] - 30 / scP)) < 0.5 &&
                  Math.abs(vbP2[1] - vbP1[1]) < 0.5;
      document.querySelector('[data-act="fit"]').click();
      var vbP3 = vbNow();
      report.steps.push(["pan", wheelOk && midOk &&
        vbP3[0] === 0 && vbP3[1] === 0 && vbP3[2] === state.width]);
      // The W2 steps are board-agnostic like the v1.2 ones: they add their own fixture right of
      // the existing content, on the grid, and clean up afterwards.
      var igrid = state.grid || 10;
      var iW = state.width;
      var X0 = Math.ceil((iW + 40) / igrid) * igrid;
      state.nodes.push({ id: "st-i1", x: X0, y: 4 * igrid, w: 150, h: 80, text: "i-one",
        kind: "box", tone: "accent" });
      state.nodes.push({ id: "st-i2", x: X0 + 320, y: 4 * igrid, w: 150, h: 80, text: "i-two",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-i3", x: X0 + 320, y: 300, w: 140, h: 100, text: "i-three",
        kind: "box", tone: "ok" });
      state.edges.push({ from: "st-i1", to: "st-i2" });
      state.width = X0 + 700;
      render();
      // W2.2: an empty-canvas drag draws a marquee and selects what it intersects (st-i2, st-i3).
      var m1 = docPt(X0 + 600, 500), m2 = docPt(X0 + 290, 20);
      pev("svg", "pointerdown", m1.x, m1.y);
      pev("svg", "pointermove", m2.x, m2.y);
      var marqueeShown = !!host.querySelector(".ck-marquee");
      pev("svg", "pointerup", m2.x, m2.y);
      report.steps.push(["marquee", marqueeShown && !host.querySelector(".ck-marquee") &&
        ringed(".ck-node", "st-i2") && ringed(".ck-node", "st-i3") && !ringed(".ck-node", "st-i1")]);
      // W2.1: click selects alone, shift+click toggles, the panel tracks the primary, an
      // empty-canvas click clears.
      clickNode("st-i1");
      var bAlone = ringed(".ck-node", "st-i1") && !ringed(".ck-node", "st-i2") &&
        !ringed(".ck-node", "st-i3");
      clickNode("st-i2", { shiftKey: true });
      var both = ringed(".ck-node", "st-i1") && ringed(".ck-node", "st-i2");
      var primaryB = document.getElementById("ck-text").value === "i-one";
      clickNode("st-i2", { shiftKey: true });
      var toggledOff = ringed(".ck-node", "st-i1") && !ringed(".ck-node", "st-i2");
      var empty = docPt(X0 + 600, 500);
      pev("svg", "pointerdown", empty.x, empty.y);
      pev("svg", "pointerup", empty.x, empty.y);
      var cleared = !ringed(".ck-node", "st-i1") && !panel.classList.contains("on");
      report.steps.push(["mselect", bAlone && both && primaryB && toggledOff && cleared]);
      // W2.3: dragging one selected item moves all of them by the primary's snapped delta.
      // X6: when a guide matches, the drop aligns to the guide instead of the grid — the v1.6
      // fixture's table has a fractional effective height whose bottom anchor sits 2 document
      // px off this drop's grid line, so the y drop lands at +2*grid + 2.000000000000014.
      // Assert the W2.3 invariant (both items move by ONE shared delta) and that the delta is
      // the grid step or within a guide's reach of it, not bit-exact grid landing.
      clickNode("st-i1");
      clickNode("st-i2", { shiftKey: true });
      var scG = svgScaleNow();
      var g0 = { x: nodeById("st-i1").x, y: nodeById("st-i1").y };
      var h0 = { x: nodeById("st-i2").x, y: nodeById("st-i2").y };
      var gr = host.querySelector('.ck-node[data-id="st-i1"]').getBoundingClientRect();
      var gcx = gr.left + gr.width / 2, gcy = gr.top + gr.height / 2;
      pev('.ck-node[data-id="st-i1"]', "pointerdown", gcx, gcy);
      pev('.ck-node[data-id="st-i1"]', "pointermove", gcx + 2 * igrid * scG, gcy + 2 * igrid * scG);
      pev("svg", "pointerup", gcx + 2 * igrid * scG, gcy + 2 * igrid * scG);
      var mb = nodeById("st-i1"), mc = nodeById("st-i2");
      var dxG = mb.x - g0.x, dyG = mb.y - g0.y;
      report.steps.push(["groupmove",
        Math.abs((mc.x - h0.x) - dxG) < 1e-6 && Math.abs((mc.y - h0.y) - dyG) < 1e-6 &&
        Math.abs(dxG - 2 * igrid) <= GUIDE && Math.abs(dyG - 2 * igrid) <= GUIDE]);
      // W2.4: ctrl+D duplicates with new ids at (+20, +20); the edge between the two selected
      // nodes is duplicated onto the copies. ctrl+C/ctrl+V pastes from the in-editor clipboard.
      clickNode("st-i1");
      clickNode("st-i2", { shiftKey: true });
      var nBefore = state.nodes.length, eBefore = state.edges.length;
      key("d", { ctrlKey: true });
      var dupA = state.nodes[state.nodes.length - 2], dupB = state.nodes[state.nodes.length - 1];
      var dupEdge = state.edges[state.edges.length - 1];
      var dupOk = state.nodes.length === nBefore + 2 && state.edges.length === eBefore + 1 &&
        dupA.x === nodeById("st-i1").x + 20 && dupA.y === nodeById("st-i1").y + 20 &&
        dupB.x === nodeById("st-i2").x + 20 && dupB.y === nodeById("st-i2").y + 20 &&
        dupA.id !== "st-i1" && dupB.id !== "st-i2" && dupA.id !== dupB.id &&
        state.nodes.filter(function (n) { return n.id === dupA.id; }).length === 1 &&
        dupEdge.from === dupA.id && dupEdge.to === dupB.id &&
        !!host.querySelector('.ck-node[data-id="' + dupA.id + '"]');
      key("c", { ctrlKey: true });
      key("v", { ctrlKey: true });
      var pastedA = state.nodes[state.nodes.length - 2];
      var pasteOk = state.nodes.length === nBefore + 4 && state.edges.length === eBefore + 2 &&
        pastedA.x === dupA.x + 20 && pastedA.y === dupA.y + 20 &&
        pastedA.id !== dupA.id &&
        state.nodes.filter(function (n) { return n.id === pastedA.id; }).length === 1;
      report.steps.push(["duplicate", dupOk && pasteOk]);
      // v1.4 (X1/X2/X7, amended in gap round 1): the panel hides what does not apply, the split
      // count is a stepper that cannot lose a part's sub, and arrange moves geometric items
      // only. Board-agnostic as usual: own fixture right of the content, cleaned up at the end.
      var pX = Math.ceil((state.width + 40) / igrid) * igrid;
      var pW0 = state.width, pH0 = state.height;
      state.nodes.push({ id: "st-ph1", x: pX, y: 40, w: 120, h: 60, text: "ph one",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-ph2", x: pX + 200, y: 40, w: 120, h: 60, text: "ph two",
        kind: "diamond", tone: "default" });
      state.nodes.push({ id: "st-phn2", x: pX + 400, y: 40, w: 120, h: 60, text: "node note",
        kind: "note", tone: "warn" });
      state.nodes.push({ id: "st-phi", x: pX, y: 200, w: 120, h: 90, text: "srv",
        kind: "server", tone: "ok" });
      state.notes.push({ id: "st-phn", x: pX + 200, y: 200, w: 160, h: 60, text: "ph note",
        kind: "note", tone: "warn" });
      state.edges.push({ from: "st-ph1", to: "st-ph2", label: "ph", style: "dashed" });
      state.width = pX + 620;
      render();
      function rowOn(id) { var r = document.getElementById(id); return !!r && !r.hidden; }
      function rowOff(id) { var r = document.getElementById(id); return !r || r.hidden; }
      // X1: a split box shows its parts and hides the edge rows... (Y1.5: a PARTS-LESS split box
      // draws its own sub but has no use for the split direction — the rows follow that state)
      setSelection([{ type: "node", id: "st-ph1" }]);
      openPanel();
      var boxMap = rowOn("ck-row-text") && rowOn("ck-row-sub") && rowOn("ck-row-parts") &&
        rowOff("ck-row-split") && rowOn("ck-row-kind") && rowOn("ck-row-size") &&
        rowOff("ck-row-start") && rowOff("ck-row-end") && rowOff("ck-row-label") &&
        rowOff("ck-row-rows") && rowOff("ck-row-arrange");
      // ...a diamond is a node but draws no parts (both renderers guard parts to box|pill)...
      setSelection([{ type: "node", id: "st-ph2" }]);
      openPanel();
      var diamondMap = rowOn("ck-row-text") && rowOn("ck-row-kind") && rowOff("ck-row-parts") &&
        rowOff("ck-row-split") && rowOff("ck-row-start");
      // ...a notes-list note shows text/tone/size and (Y4.7) text size/bold...
      setSelection([{ type: "note", id: "st-phn" }]);
      openPanel();
      var noteMap = rowOn("ck-row-text") && rowOn("ck-row-tone") && rowOn("ck-row-size") &&
        rowOn("ck-row-text-size") && rowOn("ck-row-bold") &&
        rowOff("ck-row-sub") && rowOff("ck-row-kind") && rowOff("ck-row-parts") &&
        rowOff("ck-row-start");
      // ...a node of kind "note" draws sub/align/size/weight and its shape — the v1.4 map
      // wrongly hid them (Y1.5)...
      setSelection([{ type: "node", id: "st-phn2" }]);
      openPanel();
      var nodeNoteMap = rowOn("ck-row-text") && rowOn("ck-row-sub") && rowOn("ck-row-kind") &&
        rowOn("ck-row-align") && rowOn("ck-row-size") && rowOn("ck-row-text-size") &&
        rowOn("ck-row-bold") && rowOff("ck-row-parts") && rowOff("ck-row-split");
      // ...an icon kind is caption-only (no sub, no align)...
      setSelection([{ type: "node", id: "st-phi" }]);
      openPanel();
      var iconMap = rowOn("ck-row-text") && rowOn("ck-row-kind") && rowOn("ck-row-text-size") &&
        rowOff("ck-row-sub") && rowOff("ck-row-parts") && rowOff("ck-row-align");
      // ...and an edge shows the edge rows and none of the node ones.
      setSelection([{ type: "edge", id: state.edges.length - 1 }]);
      openPanel();
      var edgeMap = rowOn("ck-row-start") && rowOn("ck-row-end") && rowOn("ck-row-head-size") &&
        rowOn("ck-row-label") && rowOn("ck-row-style") && rowOn("ck-row-route") &&
        rowOff("ck-row-text") && rowOff("ck-row-sub") && rowOff("ck-row-kind") &&
        rowOff("ck-row-parts") && rowOff("ck-row-size");
      report.steps.push(["panelhide", boxMap && diamondMap && noteMap && nodeNoteMap &&
        iconMap && edgeMap]);
      // X2: 3 parts -> + makes 4 in the field and the model -> − returns to 3; a part's sub
      // survives every count change and undo restores a dropped part WITH its sub.
      state.nodes.push({ id: "st-sp", x: pX, y: 330, w: 180, h: 110, text: "", kind: "box",
        tone: "default", split: "rows",
        parts: [{ text: "a", sub: "sa" }, { text: "b", sub: "sb" }, { text: "c", sub: "sc" }] });
      setSelection([{ type: "node", id: "st-sp" }]);
      openPanel();
      var spField = document.getElementById("ck-parts");
      var step3 = document.getElementById("ck-parts-count").textContent === "3 parts" &&
        document.getElementById("ck-parts-hint").textContent === "one part per line · 3 parts";
      document.querySelector('[data-act="part-add"]').click();
      var parts4 = normaliseParts(nodeById("st-sp").parts);
      var step4 = spField.value.split("\n").length === 4 && parts4.length === 4 &&
        parts4[0].sub === "sa" && parts4[2].sub === "sc" &&
        document.getElementById("ck-parts-count").textContent === "4 parts";
      document.querySelector('[data-act="part-del"]').click();
      var parts3 = normaliseParts(nodeById("st-sp").parts);
      var back3 = spField.value.split("\n").length === 3 && parts3.length === 3 &&
        parts3[0].sub === "sa" && parts3[2].sub === "sc" &&
        document.getElementById("ck-parts-count").textContent === "3 parts";
      document.querySelector('[data-act="part-del"]').click();
      var parts2 = normaliseParts(nodeById("st-sp").parts);
      var droppedOk = parts2.length === 2 && parts2[0].sub === "sa" && parts2[1].sub === "sb";
      document.querySelector('[data-act="undo"]').click();   // a stepper click is one snapshot
      var restored = normaliseParts(nodeById("st-sp").parts);
      var undone = restored.length === 3 && restored[2].text === "c" && restored[2].sub === "sc";
      report.steps.push(["splitstep", step3 && step4 && back3 && droppedOk && undone]);
      // X7: align-top levels the tops; spread-h equalises the two gaps with the extremes fixed.
      state.nodes.push({ id: "st-a1", x: pX, y: 500, w: 100, h: 40, text: "a1",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-a2", x: pX + 150, y: 560, w: 80, h: 50, text: "a2",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-a3", x: pX + 400, y: 530, w: 60, h: 30, text: "a3",
        kind: "box", tone: "default" });
      state.groups.push({ id: "st-grp2", x: pX + 40, y: 640, w: 100, h: 60, text: "g",
        tone: "muted" });
      state.height = Math.max(state.height, 740);
      setSelection([{ type: "node", id: "st-a1" }, { type: "node", id: "st-a2" },
        { type: "node", id: "st-a3" }]);
      openPanel();
      var arrangeShown = rowOn("ck-row-arrange") && rowOn("ck-row-spread") &&
        document.getElementById("ck-multi-hint").textContent === "3 items selected";
      document.querySelector('[data-act="align-top"]').click();
      var tops = [nodeById("st-a1").y, nodeById("st-a2").y, nodeById("st-a3").y];
      var aligned = tops[0] === 500 && tops[1] === 500 && tops[2] === 500;
      document.querySelector('[data-act="spread-h"]').click();
      var nA1 = nodeById("st-a1"), nA2 = nodeById("st-a2"), nA3 = nodeById("st-a3");
      var gapA = nA2.x - (nA1.x + nA1.w), gapB = nA3.x - (nA2.x + nA2.w);
      var spreadOk = nA1.x === pX && nA3.x === pX + 400 && Math.abs(gapA - gapB) < 1;
      // a group primary in a multi-selection still opens the panel for the hint + arrange row,
      // and the group moves like any geometric item
      setSelection([{ type: "group", id: "st-grp2" }, { type: "node", id: "st-a1" }]);
      syncPanel();
      var groupPanel = panel.classList.contains("on") && rowOn("ck-row-arrange") &&
        document.getElementById("ck-multi-hint").textContent === "2 items selected";
      document.querySelector('[data-act="align-left"]').click();
      var groupMoved = groupById("st-grp2").x === pX && nodeById("st-a1").x === pX;
      // two selected EDGES are not geometric: no arrange row, and nothing may move
      state.edges.push({ from: "st-a2", to: "st-a3" });
      render();
      var e1 = state.edges.length - 2, e2 = state.edges.length - 1;
      setSelection([{ type: "edge", id: e1 }, { type: "edge", id: e2 }]);
      openPanel();
      var edgesNoArrange = rowOff("ck-row-arrange") &&
        document.getElementById("ck-multi-hint").textContent === "2 items selected" &&
        nodeById("st-a1").x === pX && nodeById("st-a3").x === pX + 400;
      report.steps.push(["arrange", arrangeShown && aligned && spreadOk && groupPanel &&
        groupMoved && edgesNoArrange]);
      // leave nothing behind
      var pIds = { "st-ph1": true, "st-ph2": true, "st-phn2": true, "st-phi": true,
        "st-sp": true, "st-a1": true, "st-a2": true, "st-a3": true };
      state.nodes = state.nodes.filter(function (n) { return !pIds[n.id]; });
      state.notes = state.notes.filter(function (t) { return t.id !== "st-phn"; });
      state.groups = state.groups.filter(function (g) { return g.id !== "st-grp2"; });
      state.edges = state.edges.filter(function (e) { return !(pIds[e.from] || pIds[e.to]); });
      state.width = pW0;
      state.height = pH0;
      setSelection([]);
      closePanel();
      render();
      // Clean up the v1.3 fixture so any board can self-test.
      var mine = { "st-i1": true, "st-i2": true, "st-i3": true };
      [dupA.id, dupB.id, pastedA.id, state.nodes[state.nodes.length - 1].id]
        .forEach(function (cid) { mine[cid] = true; });
      state.nodes = state.nodes.filter(function (n) { return !mine[n.id]; });
      state.edges = state.edges.filter(function (e) { return !mine[e.from] && !mine[e.to]; });
      state.width = iW;
      setSelection([]);
      closePanel();
      render();

      /* ---- Lane R's two steps (X5 resize handles, X6 smart guides). Lane P's panel steps belong
       * immediately below this block, still before `report.ok`. ---- */

      // X5: eight 8x8 handles for ONE selected node — each with its own side and cursor — and none
      // at all for a multi-selection, a live marquee or connect mode. Then the real handle drags.
      var rsGrid = state.grid || 10;
      var rsWidth0 = state.width;
      var rsX = Math.ceil((rsWidth0 + 40) / rsGrid) * rsGrid;   // right of the board's own content
      var rsSnap = state.snap;
      state.snap = false;
      view = { x: 0, y: 0, k: 1 };
      setSelection([]);
      state.nodes.push({ id: "st-rs", x: rsX, y: 4 * rsGrid, w: 160, h: 80, text: "resize me",
        kind: "box", tone: "default" });
      state.width = rsX + 700;
      setSelection([{ type: "node", id: "st-rs" }]);
      render();
      var rsEls = host.querySelectorAll(".ck-handle");
      var rsNames = [], rsLook = true, i3;
      for (i3 = 0; i3 < rsEls.length; i3++) {
        var rsPos = rsEls[i3].getAttribute("data-handle");
        rsNames.push(rsPos);
        rsLook = rsLook && rsEls[i3].getAttribute("width") === "8" &&
          rsEls[i3].getAttribute("height") === "8" && rsEls[i3].getAttribute("fill") === "var(--ck-1)" &&
          String(rsEls[i3].getAttribute("style")).indexOf(HANDLE_CURSOR[rsPos]) !== -1;
      }
      var rsEight = rsEls.length === 8 &&
        HANDLE_POS.every(function (p) { return rsNames.indexOf(p) !== -1; });
      setSelection([{ type: "node", id: "st-rs" }, { type: "node", id: first }]);
      render();
      var rsMulti = !host.querySelector(".ck-handle");
      setSelection([{ type: "node", id: "st-rs" }]);
      render();
      var rsM0 = docPt(rsX + 300, 4 * rsGrid + 300), rsM1 = docPt(rsX + 620, 4 * rsGrid + 150);
      pev("svg", "pointerdown", rsM0.x, rsM0.y);
      pev("svg", "pointermove", rsM1.x, rsM1.y);
      var rsBand = !host.querySelector(".ck-handle") && !!host.querySelector(".ck-marquee");
      pev("svg", "pointerup", rsM1.x, rsM1.y);
      setSelection([{ type: "node", id: "st-rs" }]);
      render();
      var rsConnect = true;
      var rsConnectBtn = document.querySelector('[data-act="connect"]');
      if (rsConnectBtn) {
        rsConnectBtn.click();   // connect mode: no handles
        render();
        rsConnect = !host.querySelector(".ck-handle");
        rsConnectBtn.click();
        render();
      }
      // se: w/h grow by the dragged delta, x/y stay; nw: x/y move and the opposite corner is
      // pinned; the size never goes below 40 x 28; one resize takes one undo snapshot.
      var rsA = { x: nodeById("st-rs").x, y: nodeById("st-rs").y, w: nodeById("st-rs").w,
                  h: nodeById("st-rs").h };
      var rsSe = host.querySelector('.ck-handle[data-handle="se"]').getBoundingClientRect();
      var rsHist = history.length;
      var rsTo1 = docPt(rsA.x + rsA.w + 30, rsA.y + rsA.h + 20);
      pev('.ck-handle[data-handle="se"]', "pointerdown", rsSe.left + rsSe.width / 2,
        rsSe.top + rsSe.height / 2);
      pev("svg", "pointermove", rsTo1.x, rsTo1.y);
      pev("svg", "pointermove", rsTo1.x, rsTo1.y);   // still one snapshot for the whole resize
      pev("svg", "pointerup", rsTo1.x, rsTo1.y);
      var rsB = nodeById("st-rs");
      var rsGrow = Math.abs(rsB.w - (rsA.w + 30)) < 0.5 && Math.abs(rsB.h - (rsA.h + 20)) < 0.5 &&
        rsB.x === rsA.x && rsB.y === rsA.y && history.length === Math.min(50, rsHist + 1);
      var rsC = { x: rsB.x, y: rsB.y, w: rsB.w, h: rsB.h };
      var rsNw = host.querySelector('.ck-handle[data-handle="nw"]').getBoundingClientRect();
      var rsTo2 = docPt(rsC.x - 30, rsC.y - 20);
      pev('.ck-handle[data-handle="nw"]', "pointerdown", rsNw.left + rsNw.width / 2,
        rsNw.top + rsNw.height / 2);
      pev("svg", "pointermove", rsTo2.x, rsTo2.y);
      pev("svg", "pointerup", rsTo2.x, rsTo2.y);
      var rsD = nodeById("st-rs");
      var rsShrink = Math.abs(rsD.x - (rsC.x - 30)) < 0.5 && Math.abs(rsD.y - (rsC.y - 20)) < 0.5 &&
        Math.abs(rsD.w - (rsC.w + 30)) < 0.5 && Math.abs(rsD.h - (rsC.h + 20)) < 0.5 &&
        Math.abs((rsD.x + rsD.w) - (rsC.x + rsC.w)) < 0.5 &&
        Math.abs((rsD.y + rsD.h) - (rsC.y + rsC.h)) < 0.5;
      var rsMin = host.querySelector('.ck-handle[data-handle="nw"]').getBoundingClientRect();
      var rsTo3 = docPt(rsD.x + 900, rsD.y + 900);   // far past the minimum
      pev('.ck-handle[data-handle="nw"]', "pointerdown", rsMin.left + rsMin.width / 2,
        rsMin.top + rsMin.height / 2);
      pev("svg", "pointermove", rsTo3.x, rsTo3.y);
      pev("svg", "pointerup", rsTo3.x, rsTo3.y);
      var rsE = nodeById("st-rs");
      var rsSmall = rsE.w === MIN_W && rsE.h === MIN_H;
      // X3.3 (amended): a table whose w/h are 0 is a legal document and resolves an EFFECTIVE size.
      // The handles must sit on the resolved box and a marquee must pick the table up by it, not by
      // the degenerate point, or the two readers disagree with the drawn table.
      state.nodes.push({ id: "st-tbl", x: rsX + 300, y: 4 * rsGrid + 200, w: 0, h: 0, text: "",
        kind: "table", tone: "default", rows: 3, cols: 2, header: true });
      setSelection([{ type: "node", id: "st-tbl" }]);
      render();
      var tblBox = boxOf(nodeById("st-tbl"));
      var tblSe = host.querySelector('.ck-handle[data-handle="se"]');
      var tblHandles = !!tblSe && tblBox.w === 180 && tblBox.h === 76 &&
        parseFloat(tblSe.getAttribute("x")) === tblBox.x + tblBox.w - HANDLE / 2 &&
        parseFloat(tblSe.getAttribute("y")) === tblBox.y + tblBox.h - HANDLE / 2;
      var tM0 = docPt(rsX + 400, 4 * rsGrid + 250), tM1 = docPt(rsX + 470, 4 * rsGrid + 290);
      pev("svg", "pointerdown", tM0.x, tM0.y);
      pev("svg", "pointermove", tM1.x, tM1.y);
      pev("svg", "pointerup", tM1.x, tM1.y);
      var tblMarquee = selection.some(function (s) { return s.type === "node" && s.id === "st-tbl"; });
      state.nodes = state.nodes.filter(function (n) { return n.id !== "st-rs" && n.id !== "st-tbl"; });
      state.width = rsWidth0;
      state.snap = rsSnap;
      setSelection([]);
      closePanel();
      render();
      report.steps.push(["resize", rsEight && rsLook && rsMulti && rsBand && rsConnect &&
        rsGrow && rsShrink && rsSmall && tblHandles && tblMarquee]);

      // X6 (amended): the dragged node is aimed at the anchor's LEFT edge and stops 2 px short of
      // it — a near miss inside the 6 px threshold. With snap OFF the guide is DRAWN but nothing is
      // nudged: the drop must stay exactly where the pointer put it. Exact edge equality is the
      // snap-ON half's job, where the guide wins over the grid rounding.
      var gdGrid = state.grid || 10;
      var gdWidth0 = state.width;
      var gdSnap = state.snap;
      var GX = Math.ceil((gdWidth0 + 40) / gdGrid) * gdGrid;
      var GY = 4 * gdGrid;
      var GBX = GX + 303;   // deliberately off the grid: the rounding cannot produce this value
      state.width = GX + 900;
      view = { x: 0, y: 0, k: 1 };
      state.snap = false;
      state.nodes.push({ id: "st-gb", x: GBX, y: GY, w: 160, h: 80, text: "anchor",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-ga", x: GX + 343, y: GY + 200, w: 160, h: 80, text: "dragged",
        kind: "box", tone: "accent" });
      setSelection([{ type: "node", id: "st-ga" }]);
      render();
      var gaBox = host.querySelector('.ck-node[data-id="st-ga"]').getBoundingClientRect();
      var gaMid = { x: nodeById("st-ga").x + 80, y: nodeById("st-ga").y + 40 };
      var gaNear = docPt(gaMid.x - 38, gaMid.y);   // left edge: 2 px right of the anchor's
      pev('.ck-node[data-id="st-ga"]', "pointerdown", gaBox.left + gaBox.width / 2,
        gaBox.top + gaBox.height / 2);
      pev("svg", "pointermove", gaNear.x, gaNear.y);
      var gdLine = host.querySelector(".ck-guide");
      var gdDrawn = !!gdLine && gdLine.tagName.toLowerCase() === "line" &&
        gdLine.getAttribute("stroke") === "var(--ck-1)" &&
        gdLine.getAttribute("stroke-width") === "1" &&
        gdLine.getAttribute("stroke-dasharray") === "4 3";
      var gdKids = gdLine ? Array.prototype.slice.call(gdLine.parentNode.parentNode.children) : [];
      var gdAbove = !!gdLine && gdKids.indexOf(gdLine.parentNode) >
        gdKids.indexOf(host.querySelector('.ck-node[data-id="st-ga"]'));
      pev("svg", "pointerup", gaNear.x, gaNear.y);
      var gdHeld = Math.abs(nodeById("st-ga").x - (GBX + 2)) < 0.5 &&
        Math.abs(nodeById("st-ga").y - (GY + 200)) < 0.5;
      var gdCleared = !host.querySelector(".ck-guide");
      // X6: with snap ON the same off-grid near miss is dragged again, and now the guide wins: the
      // drop lands on the anchor, which is NOT where the grid rounding would have put it.
      state.snap = true;
      var gaNow = nodeById("st-ga");
      gaNow.x = GX + 520;
      gaNow.y = GY + 200;
      setSelection([{ type: "node", id: "st-ga" }]);
      render();
      var gBox = host.querySelector('.ck-node[data-id="st-ga"]').getBoundingClientRect();
      var gTo = docPt(gaNow.x + 80 + (GBX + 2 - gaNow.x), gaNow.y + 40);
      pev('.ck-node[data-id="st-ga"]', "pointerdown", gBox.left + gBox.width / 2,
        gBox.top + gBox.height / 2);
      pev("svg", "pointermove", gTo.x, gTo.y);
      pev("svg", "pointerup", gTo.x, gTo.y);
      var gdSnapped = Math.abs(nodeById("st-ga").x - nodeById("st-gb").x) < 0.5 &&
        Math.round((GBX + 2) / gdGrid) * gdGrid !== nodeById("st-gb").x;
      state.nodes = state.nodes.filter(function (n) { return n.id !== "st-ga" && n.id !== "st-gb"; });
      state.width = gdWidth0;
      state.snap = gdSnap;
      setSelection([]);
      closePanel();
      render();
      report.steps.push(["guides", gdDrawn && gdAbove && gdHeld && gdCleared && gdSnapped]);

      /* ---- v1.5 (lane P): sections, validation, the table v2 controls, and the panel map ---- */

      // Y1 sections: a plain node shows content/shape/layout and hides edge/table/cell; an
      // edge shows the edge section and hides content/table/cell (the shape section STAYS for
      // an edge: it carries the tone swatch, and an edge's tone recolours its stroke — the
      // spec's example sentence says "shape hidden", but Y1's own rule hides a section only
      // when it has NO visible row, and panelhide's edge map requires the tone row). Collapsing
      // is the browser's own <details> behaviour (session-only — nothing is persisted); this
      // Chrome keeps a closed section's content laid out (auto-expandable details), so the
      // collapse assertion is the SECTION's height dropping to the summary's, not empty rects.
      // (Fresh probe nodes: the arrange step deletes panelhide's st-ph* set.)
      function secOn(name) {
        var d = document.querySelector('.ck-sec[data-section="' + name + '"]');
        return !!d && !d.hidden;
      }
      state.nodes.push({ id: "st-sc1", x: 40, y: 720, w: 140, h: 60, text: "sec one",
        kind: "box", tone: "default", sub: "sub" });
      state.nodes.push({ id: "st-sc2", x: 240, y: 720, w: 140, h: 60, text: "sec two",
        kind: "box", tone: "default" });
      state.height = Math.max(state.height, 860);
      render();
      setSelection([{ type: "node", id: "st-sc1" }]);
      openPanel();
      var secNode = secOn("content") && secOn("shape") && secOn("layout") &&
        !secOn("edge") && !secOn("table") && !secOn("cell");
      state.edges.push({ from: "st-sc1", to: "st-sc2", label: "sec" });
      render();
      setSelection([{ type: "edge", id: state.edges.length - 1 }]);
      openPanel();
      var secEdge = secOn("edge") && !secOn("content") && !secOn("table") &&
        !secOn("cell") && !secOn("parts") && !secOn("arrange") &&
        secOn("shape");   // tone lives in shape and an edge honours it (see the comment above)
      setSelection([{ type: "node", id: "st-sc1" }]);
      openPanel();
      var shapeSec = document.querySelector('.ck-sec[data-section="shape"]');
      var toneRowEl = document.getElementById("ck-tone");
      var secShown = toneRowEl.getClientRects().length > 0;
      var secOpenH = shapeSec.offsetHeight;
      shapeSec.open = false;
      var secCollapsed = shapeSec.offsetHeight < secOpenH - 10;
      shapeSec.open = true;
      var secExpanded = shapeSec.offsetHeight === secOpenH &&
        toneRowEl.getClientRects().length > 0;
      report.steps.push(["sections", secNode && secEdge && secShown && secCollapsed && secExpanded]);
      if (!(secNode && secEdge && secShown && secCollapsed && secExpanded)) {
        report.sections = { node: secNode, edge: secEdge, shown: secShown,
          collapsed: secCollapsed, expanded: secExpanded };
      }

      // Y3 validate: an invalid value never reaches the model — the row is marked and the hint
      // carries the reason; a valid value writes and clears the mark, restoring the base hint.
      state.nodes.push({ id: "st-vt", x: 40, y: 780, w: 180, h: 48, text: "", kind: "table",
        tone: "default", rows: 2, cols: 2, cells: ["a", "b", "c", "d"] });
      state.height = Math.max(state.height, 900);
      render();
      setSelection([{ type: "node", id: "st-vt" }]);
      openPanel();
      var rowsEl = document.getElementById("ck-rows");
      var rowsRowEl = document.getElementById("ck-row-rows");
      var rowsBefore = nodeById("st-vt").rows;
      rowsEl.value = "0";
      rowsEl.dispatchEvent(new Event("input"));
      var vBadKept = nodeById("st-vt").rows === rowsBefore;
      var vBadMark = rowsRowEl.getAttribute("aria-invalid") === "true";
      var vBadHint = hintFor("ck-rows").textContent === "1 or more";
      rowsEl.value = "3";
      rowsEl.dispatchEvent(new Event("input"));
      var vGood = nodeById("st-vt").rows === 3 &&
        !rowsRowEl.hasAttribute("aria-invalid") &&
        hintFor("ck-rows").textContent === hintText["ck-rows"];
      report.steps.push(["validate", vBadKept && vBadMark && vBadHint && vGood]);

      // Y5 cellcontrols: on a 2x2 table, pick cell 2/1 and give it tone warn + align right →
      // that cell becomes an object with exactly those two keys and the other cells stay plain
      // strings; clear both → a plain string again; set column 1's tone → col_tones[0].
      // (Cell 2/1 is flat index 2 in row-major order — the renderer's own cell order (X3.2);
      // the spec sentence says "cells[1]", which is row 1/col 2 under that same order.)
      state.nodes.push({ id: "st-cc", x: 400, y: 780, w: 180, h: 48, text: "", kind: "table",
        tone: "default", rows: 2, cols: 2, cells: ["a", "b", "c", "d"] });
      render();
      setSelection([{ type: "node", id: "st-cc" }]);
      openPanel();
      var crEl = document.getElementById("ck-cell-row");
      var ccEl = document.getElementById("ck-cell-col");
      crEl.value = "2"; crEl.dispatchEvent(new Event("input"));
      ccEl.value = "1"; ccEl.dispatchEvent(new Event("input"));
      document.querySelector('#ck-cell-tone [data-tone="warn"]').click();
      document.querySelector('#ck-cell-align [data-cell-align="right"]').click();
      var ccCell = nodeById("st-cc").cells[2];
      var ccSet = ccCell && typeof ccCell === "object" && ccCell.tone === "warn" &&
        ccCell.align === "right" && ccCell.text === "c" &&
        typeof nodeById("st-cc").cells[0] === "string" &&
        typeof nodeById("st-cc").cells[1] === "string" &&
        typeof nodeById("st-cc").cells[3] === "string";
      document.querySelector('#ck-cell-tone [data-tone=""]').click();
      document.querySelector('#ck-cell-align [data-cell-align="inherit"]').click();
      var ccCleared = nodeById("st-cc").cells[2] === "c";
      document.querySelector('#ck-coltones [data-col-tone="0"] [data-tone="ok"]').click();
      var ccColTone = (nodeById("st-cc").col_tones || [])[0] === "ok";
      report.steps.push(["cellcontrols", ccSet && ccCleared && ccColTone]);

      // Y5 celledit: cell writes are index-preserving — an unrelated text edit in the textarea
      // and a column add leave the surviving cell's own tone/align alone.
      document.querySelector('#ck-cell-tone [data-tone="danger"]').click();
      document.querySelector('#ck-cell-align [data-cell-align="left"]').click();
      var cellsField = document.getElementById("ck-cells");
      var ceLines = cellsField.value.split("\n");
      ceLines[0] = "edited elsewhere";
      cellsField.value = ceLines.join("\n");
      cellsField.dispatchEvent(new Event("input"));
      var ceAfterEdit = nodeById("st-cc").cells[2];
      var ceKept1 = ceAfterEdit && typeof ceAfterEdit === "object" &&
        ceAfterEdit.tone === "danger" && ceAfterEdit.align === "left" &&
        ceAfterEdit.text === "c" && nodeById("st-cc").cells[0] === "edited elsewhere";
      var colsEl = document.getElementById("ck-cols");
      colsEl.value = "3"; colsEl.dispatchEvent(new Event("input"));
      var ceGrid = nodeById("st-cc");
      var ceSurv = ceGrid.cells[3];   // row 2, col 1 of a 2x3 grid = index (2-1)*3 + 0
      var ceKept2 = ceGrid.cols === 3 && ceGrid.cells.length === 6 &&
        ceSurv && typeof ceSurv === "object" && ceSurv.tone === "danger" &&
        ceSurv.align === "left";
      report.steps.push(["celledit", ceKept1 && ceKept2]);

      /* Y1.5 panelfields: the panel map must follow the renderer mechanically — for every kind
       * and both note representations, a row is visible exactly when changing its field changes
       * the rendered SVG (probed in a discriminating state per field, e.g. sub is probed
       * without parts and split with three parts). Declared exceptions — the cell pickers
       * (change which cell is edited, not the picture) and the col-width inputs — PLUS, only
       * while lane T's table-v2 rendering has not landed in this tree, the table v2 writer
       * rows (align, row/col tones, cell align/tone): the step detects the renderer version
       * and expects exactly the matching exception set, so it is green on both sides of the
       * merge and still FAILS on any other hidden-but-effective or visible-but-inert row,
       * naming the representation, the row and the mismatch. */
      var pfFail = [];
      var pfY0 = 980;
      state.height = Math.max(state.height, pfY0 + 600);
      state.width = Math.max(state.width, 1600);
      var pfDetect = { id: "st-pft", x: 40, y: pfY0, w: 180, h: 48, text: "", kind: "table",
        tone: "default", rows: 2, cols: 2, cells: ["a", "b", "c", "d"] };
      state.nodes.push(pfDetect);
      render();
      // detect the renderer's table version once: does a cell tone paint a tone overlay?
      pfDetect.cells = [{ text: "a", tone: "warn" }, "b", "c", "d"];
      render();
      var pfDetectSvg = host.querySelector('.ck-node[data-id="st-pft"]');
      var pfV2 = !!(pfDetectSvg && pfDetectSvg.innerHTML.indexOf("--ck-tone-warn-fill") !== -1);
      pfDetect.cells = ["a", "b", "c", "d"];
      render();
      var PF_ROW_FIELDS = {
        "ck-row-text": ["text"], "ck-row-sub": ["sub"], "ck-row-tone": ["tone"],
        "ck-row-kind": ["kind"], "ck-row-align": ["align"], "ck-row-parts": ["parts"],
        "ck-row-split": ["split"], "ck-row-rows": ["rows"], "ck-row-cols": ["cols"],
        "ck-row-colw": ["col_widths"], "ck-row-cells": ["cells"], "ck-row-header": ["header"],
        "ck-row-rowtones": ["row_tones"], "ck-row-coltones": ["col_tones"],
        "ck-row-text-size": ["text_size"], "ck-row-bold": ["bold"], "ck-row-size": ["w", "h"],
        "ck-row-cellpick": [], "ck-row-celltext": ["cells"],
        "ck-row-cellalign": ["cell_align"], "ck-row-celltone": ["cell_tone"]
      };
      var PF_LANET = ["ck-row-align", "ck-row-rowtones", "ck-row-coltones", "ck-row-cellalign",
        "ck-row-celltone"];   // inert only until lane T's table v2 rendering lands
      function pfSet(item, field) {   // one discriminating change; the caller restores
        var g, i2;
        switch (field) {
          case "text": item.text = "pf text probe"; break;
          case "sub": item.sub = "pf sub probe"; break;
          case "tone": item.tone = item.tone === "danger" ? "ok" : "danger"; break;
          case "kind": item.kind = item.kind === "diamond" ? "box" : "diamond"; break;
          case "align": item.align = item.align === "left" ? "center" : "left"; break;
          case "w": item.w = (parseFloat(item.w) || 100) + 37; break;
          case "h": item.h = (parseFloat(item.h) || 60) + 23; break;
          case "text_size": item.text_size = "large"; break;
          case "bold": item.bold = true; break;
          case "parts": item.parts = [{ text: "pz" }, { text: "pq" }, { text: "pr" }]; break;
          case "split": item.split = item.split === "columns" ? "rows" : "columns"; break;
          case "rows": item.rows = tableGrid(item).rows + 1; break;
          case "cols": item.cols = tableGrid(item).cols + 1; break;
          case "cells": g = rawCellsSized(item); g[0] = "pf cell probe"; item.cells = g; break;
          case "header": item.header = item.header !== true; break;
          case "col_widths":
            g = []; for (i2 = 0; i2 < tableGrid(item).cols; i2++) g.push(i2 ? 1 : 2);
            item.col_widths = g; break;
          case "row_tones":
            g = []; for (i2 = 0; i2 < tableGrid(item).rows; i2++) g.push(i2 ? null : "danger");
            item.row_tones = g; break;
          case "col_tones":
            g = []; for (i2 = 0; i2 < tableGrid(item).cols; i2++) g.push(i2 ? null : "danger");
            item.col_tones = g; break;
          case "cell_align":   // keep the cell's own text — only the align may change the picture
            g = rawCellsSized(item); g[0] = { text: cellParts(g[0]).text, align: "right" };
            item.cells = g; break;
          case "cell_tone":
            g = rawCellsSized(item); g[0] = { text: cellParts(g[0]).text, tone: "danger" };
            item.cells = g; break;
        }
      }
      function pfWalk(rep, item, selType) {
        var saved = JSON.stringify(item);
        var isTable = selType === "node" && (JSON.parse(saved).kind || "box") === "table";
        setSelection([{ type: selType, id: item.id }]);
        render();   // settle the selection ring first, or it moves DURING the first probe
        openPanel();
        var visible = {};
        for (var rowId in PF_ROW_FIELDS) {
          var row = document.getElementById(rowId);
          var sec = row && row.closest ? row.closest(".ck-sec") : null;
          visible[rowId] = !!row && !row.hidden && !(sec && sec.hidden);
        }
        var eff = {};
        var effDiff = {};
        ["text", "sub", "tone", "kind", "align", "w", "h", "text_size", "bold", "parts",
          "split", "rows", "cols", "cells", "header", "col_widths", "row_tones", "col_tones",
          "cell_align", "cell_tone"].forEach(function (field) {
          /* Pre-merge the v1.4 table renderer STRINGIFIES an object cell ("[object Object]"),
           * so probing a cell's align/tone would change the picture via the TEXT, not the
           * field — skip the mutation and treat the field as inert (it is, pre-merge). */
          if (!pfV2 && (field === "cell_align" || field === "cell_tone")) {
            eff[field] = false; return;
          }
          var before = host.innerHTML;
          pfSet(item, field);
          render();
          var after = host.innerHTML;
          eff[field] = after !== before;
          if (eff[field]) {
            var d = 0;
            while (d < before.length && d < after.length && before[d] === after[d]) d++;
            effDiff[field] = "@" + d + " [" + before.slice(Math.max(0, d - 30), d + 50) +
              "] vs [" + after.slice(Math.max(0, d - 30), d + 50) + "]";
          }
          // restore the probe item to the state the map was measured in
          for (var key in item) delete item[key];
          var back = JSON.parse(saved);
          for (var k2 in back) item[k2] = back[k2];
          render();
        });
        // Only the cell picker is inert: it changes which cell the cell section edits, not the
        // picture. The col-width row IS effective once the renderer honours col_widths, so it must
        // not be excused (the step caught it as soon as lane T's table landed next to lane P's panel).
        var exceptions = ["ck-row-cellpick"];
        if (isTable && !pfV2) exceptions = exceptions.concat(PF_LANET);
        var visibleInert = [];
        for (var rid in PF_ROW_FIELDS) {
          var rowEff = PF_ROW_FIELDS[rid].some(function (f) { return eff[f]; });
          if (rowEff && !visible[rid]) pfFail.push(rep + ": " + rid + " hidden but changes the SVG " +
            (effDiff[PF_ROW_FIELDS[rid][0]] || ""));
          if (!rowEff && visible[rid]) {
            if (exceptions.indexOf(rid) === -1) pfFail.push(rep + ": " + rid + " visible but inert");
            else visibleInert.push(rid);
          }
        }
        if (isTable) {
          var want = exceptions.slice().sort().join("|"), got = visibleInert.sort().join("|");
          if (want !== got) pfFail.push(rep + ": visible-inert rows [" + got +
            "] != exceptions [" + want + "] cellalign{vis:" + visible["ck-row-cellalign"] +
            ",eff:" + eff.cell_align + "} celltone{vis:" + visible["ck-row-celltone"] +
            ",eff:" + eff.cell_tone + "} alignEff:" + eff.align +
            (effDiff.cell_align ? " dAlign:" + effDiff.cell_align : "") +
            (effDiff.cell_tone ? " dTone:" + effDiff.cell_tone : ""));
        } else if (visibleInert.length) {
          pfFail.push(rep + ": unexpected visible-inert rows [" + visibleInert.join(",") + "]");
        }
      }
      var pfNodes = [];
      KINDS.forEach(function (kind, i) {
        var node = { id: "st-pf-" + kind, x: 40 + (i % 8) * 190, y: pfY0 + Math.floor(i / 8) * 160,
          w: 160, h: 80, text: "pf " + kind, kind: kind, tone: "default" };
        if (kind === "table") {
          node.w = 180; node.h = 48; node.rows = 2; node.cols = 2;
          node.cells = ["a", "b", "c", "d"]; node.text = "";
        }
        state.nodes.push(node);
        pfNodes.push(node);
      });
      render();
      pfNodes.forEach(function (node) { pfWalk("node:" + node.kind, node, "node"); });
      // box/pill additionally WITH parts: sub is drawn only without them, split only with them
      ["box", "pill"].forEach(function (kind) {
        var node = nodeById("st-pf-" + kind);
        node.parts = [{ text: "one" }, { text: "two" }];
        render();
        pfWalk("node:" + kind + "+parts", node, "node");
        delete node.parts;
        render();
      });
      // the second note representation: a note in the notes list (Y4.7 drew its size/weight)
      var pfNote = { id: "st-pfnote", x: 40, y: pfY0 + 420, w: 160, h: 60, text: "pf note",
        tone: "warn" };
      state.notes.push(pfNote);
      render();
      pfWalk("note", pfNote, "note");
      report.steps.push(["panelfields", pfFail.length === 0]);
      if (pfFail.length) report.panelfields = pfFail.slice(0, 20);

      /* v1.5 verifier round 1, findings 4, 5 and 7 — the three panel-side defects, asserted here so a
       * regression cannot come back quietly:
       *   4. a table's own align is a tri-state; the group shows `right` for a table only, pressed when
       *      the model says right (it used to display right as center and offer no way to set it);
       *   5. a fractional row/col count is refused with a reason instead of being truncated by parseInt;
       *   7. a node of kind "note" draws its `sub`, so the field must show it, not hide it. */
      var set = function (id, value) {
        var el = document.getElementById(id);
        if (!el) return null;
        el.value = value;
        el.dispatchEvent(new Event("input"));
        el.dispatchEvent(new Event("change"));
        return el;
      };
      var piFail = [];
      state.nodes.push({ id: "pi-tbl", x: 40, y: 40, w: 0, h: 0, text: "", kind: "table",
        tone: "default", rows: 2, cols: 2, cells: ["a", "b", "c", "d"], align: "center" });
      state.nodes.push({ id: "pi-note", x: 260, y: 40, w: 160, h: 80, text: "n", kind: "note",
        tone: "warn", sub: "kept subtitle" });
      setSelection([{ type: "node", id: "pi-tbl" }]);
      syncPanel();          // the panel syncs on a selection change, not on a bare render
      render();
      var grp = document.getElementById("ck-align");
      var alignBtn = function (value) {
        return grp ? grp.querySelector('[data-align="' + value + '"]') : null;
      };
      var rightBtn = alignBtn("right");
      var pressedAlign = function () {
        var b = grp ? grp.querySelector('[data-align][aria-pressed="true"]') : null;
        return b ? b.getAttribute("data-align") : null;
      };
      var alignNow = function () {
        var t = host.querySelector('.ck-node[data-id="pi-tbl"] text');
        return { model: nodeById("pi-tbl").align || "absent",
          anchor: t ? t.getAttribute("text-anchor") : null, pressed: pressedAlign() };
      };
      if (!rightBtn || rightBtn.hidden !== false) {
        piFail.push(["tableAlign", "right is not offered for a table",
          rightBtn ? rightBtn.hidden : "no button"]);
      }
      // 4 (r2): from CENTER, clicking `right` must move the model, the rendered anchor and the press.
      var piCenter = alignNow();
      if (rightBtn) rightBtn.click();
      var piRight = alignNow();
      if (!(piCenter.model === "center" && piCenter.anchor === "middle" &&
            piCenter.pressed === "center" && piRight.model === "right" &&
            piRight.anchor === "end" && piRight.pressed === "right")) {
        piFail.push(["alignSet", "center -> right did not change model/anchor/pressed",
          JSON.stringify([piCenter, piRight])]);
      }
      // ...and again from LEFT, so one lucky pre-set value cannot carry the step.
      var leftBtn = alignBtn("left");
      if (leftBtn) leftBtn.click();
      var piLeft = alignNow();
      if (rightBtn) rightBtn.click();
      var piBack = alignNow();
      if (!(piLeft.model === "left" && piLeft.anchor === "start" && piLeft.pressed === "left" &&
            piBack.model === "right" && piBack.anchor === "end" && piBack.pressed === "right")) {
        piFail.push(["alignSetLeft", "left -> right did not change model/anchor/pressed",
          JSON.stringify([piLeft, piBack])]);
      }
      // 5 (r2): BOTH counts. A fractional value is refused — the model and the grid are untouched,
      // the row is marked invalid and the hint carries the reason — and a valid value recovers.
      var countRefused = function (label, id) {
        var item = nodeById("pi-tbl");
        var before = [item.rows, item.cols, item.cells.length];
        var el = set(id, "2.5");
        var row = el ? el.closest(".ck-prow") : null;
        var after = [item.rows, item.cols, item.cells.length];
        var hint = row ? row.querySelector(".ck-hint").textContent : null;
        if (!(el && row && row.getAttribute("aria-invalid") === "true" &&
              hint === "a whole number, please" && before.join() === after.join())) {
          piFail.push([label, "a fractional count was not refused with a reason",
            JSON.stringify([before, after, hint,
              row ? row.getAttribute("aria-invalid") : "no row"])]);
        }
      };
      var countRecovered = function (label, id, field, value) {
        var el = set(id, value);
        var item = nodeById("pi-tbl");
        var row = el ? el.closest(".ck-prow") : null;
        if (!(item[field] === value && item.cells.length === item.rows * item.cols &&
              row && row.getAttribute("aria-invalid") === null &&
              hintFor(id).textContent === hintText[id])) {
          piFail.push([label, "a valid count did not clear the mark and refill the grid",
            JSON.stringify([item[field], item.cells.length,
              row ? row.getAttribute("aria-invalid") : null])]);
        }
      };
      countRefused("fraction", "ck-rows");
      countRecovered("fractionBack", "ck-rows", "rows", 3);
      countRefused("fractionCols", "ck-cols");
      countRecovered("fractionColsBack", "ck-cols", "cols", 4);
      // 4 (r2): the right button is a TABLE control — a note must not be offered it.
      setSelection([{ type: "node", id: "pi-note" }]);
      syncPanel();
      render();
      if (rightBtn && rightBtn.hidden !== true) {
        piFail.push(["alignHidden", "a note is offered the table-only right align", rightBtn.hidden]);
      }
      // 7: a node of kind "note" draws its sub, so the field shows it
      var subEl = document.getElementById("ck-sub");
      var noteOk = !!subEl && subEl.value === "kept subtitle";
      if (!noteOk) piFail.push(["noteSub", "the sub field lies", subEl ? subEl.value : null]);
      state.nodes = state.nodes.filter(function (n) { return n.id !== "pi-tbl" && n.id !== "pi-note"; });
      setSelection([]);
      closePanel();
      render();
      report.steps.push(["panelinputs", piFail.length === 0]);
      if (piFail.length) report.panelinputs = piFail;

      /* v1.5 verifier ROUND 2, finding 1 — the JS READER. A JSON import can carry the string
       * "Infinity" (or a pair whose SUM overflows: 1e308 + 1e308) as a column share; neither may
       * reach the geometry as NaN. Goes through the pane's own import entry point, and the two bad
       * documents must draw exactly the all-1 table: divider at 90, texts at 45/135. */
      var bwOk = true, bwDetail = null;
      var savedForWidths = exportJson();
      try {
        replaceState({ nodes: [
          { id: "bw-inf", x: 50, y: 50, w: 180, h: 48, text: "", kind: "table", tone: "default",
            rows: 2, cols: 2, col_widths: ["Infinity", 1], cells: ["long label", "other", "", ""] },
          { id: "bw-big", x: 50, y: 150, w: 180, h: 48, text: "", kind: "table", tone: "default",
            rows: 2, cols: 2, col_widths: [1e308, 1e308], cells: ["long label", "other", "", ""] }] });
        render();
        var bwSeen = [];
        ["bw-inf", "bw-big"].forEach(function (id) {
          var g = host.querySelector('.ck-node[data-id="' + id + '"]');
          var div = g ? g.querySelector('path[d*=" V "]') : null;
          var txt = g ? g.querySelector("text") : null;
          bwSeen.push([id, div ? div.getAttribute("d") : "no divider", txt ? txt.getAttribute("x") : "no text"]);
        });
        bwOk = bwSeen.every(function (row) {
          return row[1] === "M 90,0 V 48" && row[2] === "45";
        });
        if (!bwOk) bwDetail = JSON.stringify(bwSeen);
      } catch (bwErr) {
        bwOk = false;
        bwDetail = String(bwErr);
      }
      report.steps.push(["badwidth", bwOk]);
      if (bwDetail) report.badwidth = bwDetail;
      replaceState(JSON.parse(savedForWidths));
      render();

      /* v1.5 verifier ROUND 2, finding 6 — the JS CLIP. The clip-path must be appended after the
       * weight attribute's closing quote; concatenated inside `font-weight="` Chrome computes a
       * clip of "none" and the text draws into the neighbouring column. Asserted on the RENDERED
       * element: the computed clip really is the cell's own clipPath, that clipPath's rect is the
       * cell's box, and the text's own geometry DOES extend past that box — otherwise the check
       * would pass on a table that never needed the clip. A table that fits carries no clip bytes. */
      var clipOk = true, clipDetail = null;
      var savedForClip = exportJson();
      try {
        replaceState({ nodes: [{ id: "clip-t", x: 50, y: 50, w: 180, h: 48, text: "", kind: "table",
          tone: "default", rows: 2, cols: 2, col_widths: [0.1, 100],
          cells: ["long label", "other", "", ""] },
          { id: "clip-n", x: 50, y: 250, w: 180, h: 48, text: "", kind: "table", tone: "default",
            rows: 2, cols: 2, cells: ["a", "b", "c", "d"] }] });
        render();
        var cg = host.querySelector('.ck-node[data-id="clip-t"]');
        var ct = cg ? cg.querySelector("text") : null;
        var crect = cg ? cg.querySelector("clipPath rect") : null;
        var ccomputed = ct ? getComputedStyle(ct).clipPath : null;
        var cbox = ct ? ct.getBBox() : null;
        var cellW = crect ? parseFloat(crect.getAttribute("width")) : NaN;
        var cellX = crect ? parseFloat(crect.getAttribute("x")) : NaN;
        var ng = host.querySelector('.ck-node[data-id="clip-n"]');
        var clipApplied = !!ct && !!crect && !!ccomputed && ccomputed.indexOf("tclip-clip-t-0") !== -1 &&
          ct.getAttribute("clip-path") === "url(#tclip-clip-t-0)";
        var clipIsCell = cellX === 0 && cellW > 0 && cellW < 1;
        var clipDoesWork = !!cbox && (cbox.x < cellX - 0.5 || cbox.x + cbox.width > cellX + cellW + 0.5);
        var clipFree = !!ng && ng.innerHTML.indexOf("clip-path") === -1 &&
          ng.querySelectorAll("clipPath").length === 0;
        clipOk = clipApplied && clipIsCell && clipDoesWork && clipFree;
        if (!clipOk) {
          clipDetail = JSON.stringify({ computed: ccomputed, attr: ct ? ct.getAttribute("clip-path") : null,
            rect: [cellX, cellW], bbox: cbox ? [cbox.x, cbox.x + cbox.width] : null,
            applied: clipApplied, isCell: clipIsCell, works: clipDoesWork, clipFree: clipFree });
        }
      } catch (clErr) {
        clipOk = false;
        clipDetail = String(clErr);
      }
      report.steps.push(["clipjs", clipOk]);
      if (clipDetail) report.clipjs = clipDetail;
      replaceState(JSON.parse(savedForClip));
      render();

      /* v1.4 gate round 1 (finding 4): an agent update or an import that omits a collection list
       * must render the new board, not throw and leave the old picture on screen. The step goes
       * through the same entry point the pane's bridge uses, and puts the board back afterwards. */
      var savedForReplace = exportJson();
      var replaceOk = true, replaceErr = null;
      try {
        replaceState({ nodes: [{ id: "solo", x: 10, y: 10, w: 120, h: 60, text: "solo",
                                 kind: "box", tone: "default" }] });
        render();
        replaceOk = state.nodes.length === 1 && state.notes.length === 0 &&
          state.groups.length === 0 && state.edges.length === 0 &&
          host.innerHTML.indexOf('data-id="solo"') !== -1;
      } catch (err) {
        replaceOk = false;
        replaceErr = String(err);
      }
      report.steps.push(["replace", replaceOk]);
      if (replaceErr) report.replaceError = replaceErr;
      replaceState(JSON.parse(savedForReplace));
      render();

      /* ---- v1.6 (lane P, Z6): the panel's `line` section ---- */
      // Driven, never described: a line is put in the model, selected through the panel's own entry
      // point, and every claim is read back from the model, the pressed control or the rendered
      // picture. The reader half (lane L) installs `state.lines` from the document; a tree that
      // does not carry it yet gets the probe line here, so this step measures the PANEL and not the
      // reader (`line`/`linesnap` own that half) — the same declared excuse `panelfields` uses for
      // the table-v2 rows before their renderer lands. The picture half is asserted wherever a
      // renderer actually draws lines (`lpDrawn`).
      var lpFail = [];
      if (!Array.isArray(state.lines)) state.lines = [];
      var lpLine = { id: "st-ln", points: [[40, 1240], [130, 1290], [230, 1250]],
        tone: "default", style: "solid", start: "none", end: "arrow" };
      var lpBefore = host.innerHTML;
      state.lines.push(lpLine);
      state.height = Math.max(state.height, 1400);
      render();
      var lpDrawn = host.innerHTML !== lpBefore;   // does THIS tree's renderer draw lines at all?

      function lpVisible(rowId) {
        var row = document.getElementById(rowId);
        var sec = row && row.closest ? row.closest(".ck-sec") : null;
        return !!row && !row.hidden && !(sec && sec.hidden);
      }
      var LP_ROWS = ["ck-row-points", "ck-row-line-tone", "ck-row-line-style", "ck-row-line-start",
        "ck-row-line-end"];
      var LP_FIELDS = ["points", "tone", "style", "start", "end"];
      // Every other field any panel row writes, probed on the line: nothing outside `LP_FIELDS` may
      // change a line's picture, or a row would be hiding a field the renderer honours.
      var LP_HIDDEN = ["text", "sub", "w", "h", "kind", "align", "parts", "split", "text_size",
        "bold", "rows", "cols", "cells", "header", "col_widths", "row_tones", "col_tones"];
      setSelection([{ type: "line", id: lpLine.id }]);
      render();
      syncPanel();   // the panel syncs on a selection change, not on a bare render
      var lpShown = LP_ROWS.filter(lpVisible);
      if (!secOn("line")) lpFail.push(["section", "the line section is hidden for a line selection"]);
      if (lpShown.length !== LP_ROWS.length) {
        lpFail.push(["rows", "no visible row for: " + LP_ROWS.filter(function (r) {
          return !lpVisible(r);
        }).join(",")]);
      }
      // Z6.1/Z6.2: a line has no text, no size and no table — the node and edge sections are hidden.
      var lpOtherSecs = ["content", "shape", "layout", "parts", "table", "edge", "cell"].filter(secOn);
      if (lpOtherSecs.length) lpFail.push(["sections", "shown for a line: " + lpOtherSecs.join(",")]);
      // The map is mechanical row by row, BOTH directions: every row the map gives the `line` kind
      // is visible and every other row of the panel is hidden. A row missing from the map would
      // stay visible for every kind and is caught here.
      for (var lpRowId in PANEL_ROWS) {
        if (lpVisible(lpRowId) !== (PANEL_ROWS[lpRowId].indexOf("line") !== -1)) {
          lpFail.push(["map", lpRowId + " visible:" + lpVisible(lpRowId) + " vs its map entry"]);
        }
      }
      if (lpVisible("ck-row-arrange") || lpVisible("ck-row-spread")) {
        lpFail.push(["arrange", "the arrange rows show for a single line selection"]);
      }
      function lpSet(item, field) {   // one discriminating change per field
        switch (field) {
          case "points": item.points = [[40, 1240], [130, 1290], [230, 1250], [300, 1220]]; break;
          case "tone": item.tone = item.tone === "danger" ? "ok" : "danger"; break;
          case "style": item.style = item.style === "dashed" ? "dotted" : "dashed"; break;
          case "start": item.start = item.start === "circle" ? "bar" : "circle"; break;
          case "end": item.end = item.end === "diamond" ? "triangle" : "diamond"; break;
          case "text": item.text = "lp text probe"; break;
          case "sub": item.sub = "lp sub probe"; break;
          case "w": item.w = 300; break;
          case "h": item.h = 200; break;
          case "kind": item.kind = "diamond"; break;
          case "align": item.align = "left"; break;
          case "parts": item.parts = [{ text: "lz" }, { text: "lq" }]; break;
          case "split": item.split = "columns"; break;
          case "text_size": item.text_size = "large"; break;
          case "bold": item.bold = true; break;
          case "rows": item.rows = 3; break;
          case "cols": item.cols = 3; break;
          case "cells": item.cells = [{ text: "cx", tone: "danger", align: "right" }, "cy"]; break;
          case "header": item.header = true; break;
          case "col_widths": item.col_widths = [2, 1]; break;
          case "row_tones": item.row_tones = ["danger", null]; break;
          case "col_tones": item.col_tones = ["danger", null]; break;
        }
      }
      var lpSaved = JSON.stringify(lpLine);
      var lpEff = {};
      LP_FIELDS.concat(LP_HIDDEN).forEach(function (field) {
        var before = host.innerHTML;
        lpSet(lpLine, field);
        render();
        lpEff[field] = host.innerHTML !== before;
        for (var key in lpLine) delete lpLine[key];        // back to the state the map was read in
        var back = JSON.parse(lpSaved);
        for (var k2 in back) lpLine[k2] = back[k2];
        render();
      });
      var lpLeak = LP_HIDDEN.filter(function (f) { return lpEff[f]; });
      if (lpLeak.length) {
        lpFail.push(["hiddenfield", "a hidden row's field changes the line's picture: " +
          lpLeak.join(",")]);
      }
      var lpInert = LP_FIELDS.filter(function (f) { return !lpEff[f]; });
      if (lpDrawn && lpInert.length) {
        lpFail.push(["inert", "visible line rows that change nothing: " + lpInert.join(",")]);
      }
      if (!lpDrawn && lpInert.length !== LP_FIELDS.length) {
        lpFail.push(["detect", "a line field is effective although this tree draws no line: " +
          LP_FIELDS.filter(function (f) { return lpEff[f]; }).join(",")]);
      }
      // The controls DRIVE the model. The stepper appends at the END and refuses to go below 2,
      // with the count on screen following it; the tone swatch, the style group and both head
      // grids write the field the model carries and move their own pressed state.
      setSelection([{ type: "line", id: lpLine.id }]);
      render();
      syncPanel();
      var lpAdd = document.querySelector('[data-act="point-add"]');
      var lpDel = document.querySelector('[data-act="point-del"]');
      var lpCount = document.getElementById("ck-points-count");
      var lpN0 = lpLine.points.length;
      if (lpAdd) lpAdd.click();
      var lpAppended = lpLine.points.length === lpN0 + 1 &&
        lpLine.points[lpLine.points.length - 1][0] ===
          lpLine.points[lpLine.points.length - 2][0] + 40 &&
        !!lpCount && lpCount.textContent === (lpN0 + 1) + " points";
      if (lpDel) lpDel.click();
      var lpRemoved = lpLine.points.length === lpN0 && !!lpCount &&
        lpCount.textContent === lpN0 + " points";
      while (lpLine.points.length > 2 && lpDel) lpDel.click();
      var lpTwo = lpLine.points.length === 2;
      if (lpDel) lpDel.click();     // never below 2
      var lpHeld = lpLine.points.length === 2 && lpCount.textContent === "2 points";
      if (!(lpAppended && lpRemoved && lpTwo && lpHeld)) {
        lpFail.push(["stepper", JSON.stringify([lpN0, lpLine.points.length, lpAppended, lpRemoved,
          lpTwo, lpHeld, lpCount ? lpCount.textContent : null])]);
      }
      if (!hintFor("ck-points") || hintFor("ck-points").textContent.indexOf("2 now") === -1) {
        lpFail.push(["hint", "the count hint does not follow: " +
          (hintFor("ck-points") ? hintFor("ck-points").textContent : "no hint")]);
      }
      var lpIdText = document.getElementById("ck-panel-id").textContent;
      if (lpIdText.indexOf("line") !== 0) lpFail.push(["panelid", lpIdText]);
      // ...and ONE undo brings the path back: every stepper write snapshots first (Z5.1's rule —
      // `undo` restores one step at a time). `undo` replaces `state` wholesale AND clears the
      // selection, so the line is re-read from the restored document and re-selected below,
      // never used through a stale reference.
      var lpUndoBtn = document.querySelector('[data-act="undo"]');
      var lpUndoOk = false;
      if (lpUndoBtn) {
        var lpDepth = history.length;
        lpUndoBtn.click();
        var lpBack = (state.lines || []).filter(function (l) { return l.id === "st-ln"; })[0];
        lpUndoOk = !!lpBack && lpBack.points.length === lpN0 && history.length === lpDepth - 1;
        if (lpBack) lpLine = lpBack;
      }
      if (!lpUndoOk) lpFail.push(["undo", "a stepper write is not one undo step"]);
      setSelection([{ type: "line", id: lpLine.id }]);
      render();
      syncPanel();
      var lpToneBtn = document.querySelector('#ck-line-tone [data-tone="danger"]');
      var lpStyleBtn = document.querySelector('#ck-line-style [data-style="dashed"]');
      var lpStartBtn = document.querySelector('#ck-line-start [data-end="circle"]');
      var lpEndBtn = document.querySelector('#ck-line-end [data-end="triangle"]');
      if (lpToneBtn) lpToneBtn.click();
      if (lpStyleBtn) lpStyleBtn.click();
      if (lpStartBtn) lpStartBtn.click();
      if (lpEndBtn) lpEndBtn.click();
      function lpPressed(btn) { return btn ? btn.getAttribute("aria-pressed") : null; }
      if (!(lpLine.tone === "danger" && lpPressed(lpToneBtn) === "true" &&
            lpLine.style === "dashed" && lpPressed(lpStyleBtn) === "true" &&
            lpLine.start === "circle" && lpPressed(lpStartBtn) === "true" &&
            lpLine.end === "triangle" && lpPressed(lpEndBtn) === "true")) {
        lpFail.push(["controls", JSON.stringify([lpLine.tone, lpLine.style, lpLine.start, lpLine.end,
          lpPressed(lpToneBtn), lpPressed(lpStyleBtn), lpPressed(lpStartBtn), lpPressed(lpEndBtn)])]);
      }
      // Z6.2: HIDDEN — not disabled — for every other selection, including both note
      // representations. A lone group primary closes the panel, which hides the section with it.
      var lpOthers = [];
      if (state.nodes.length) lpOthers.push(["node", state.nodes[0].id]);
      var lpTable = null, lpNodenote = null;
      state.nodes.forEach(function (n) {
        if (!lpTable && (n.kind || "box") === "table") lpTable = n.id;
        if (!lpNodenote && n.kind === "note") lpNodenote = n.id;
      });
      if (lpTable) lpOthers.push(["node", lpTable]);
      if (lpNodenote) lpOthers.push(["node", lpNodenote]);
      if (state.notes.length) lpOthers.push(["note", state.notes[0].id]);
      if (state.edges.length) lpOthers.push(["edge", 0]);
      state.groups.push({ id: "st-lpg", x: 900, y: 1240, w: 130, h: 80, text: "lp group",
        tone: "muted" });
      render();
      lpOthers.push(["group", "st-lpg"]);
      lpOthers.forEach(function (sel) {
        setSelection([{ type: sel[0], id: sel[1] }]);
        render();
        syncPanel();
        var lpOpen = panel.classList.contains("on");
        var lpShownRows = LP_ROWS.filter(lpVisible);
        if (lpOpen && secOn("line")) {
          lpFail.push(["otherkind", "the line section shows for " + sel.join(":")]);
        }
        if (lpOpen && lpShownRows.length) {
          lpFail.push(["otherrow", "a line row shows for " + sel.join(":") + " -> " +
            lpShownRows.join(",")]);
        }
      });
      state.groups = state.groups.filter(function (g) { return g.id !== "st-lpg"; });
      state.lines = state.lines.filter(function (l) { return l.id !== "st-ln"; });
      setSelection([]);
      closePanel();
      render();
      report.steps.push(["linepanel", lpFail.length === 0]);
      if (lpFail.length) report.linepanel = lpFail;
      report.linepanelDrawn = lpDrawn;

      /* Round-1 finding 1: every v1.6 step name must land in the report even when its body
       * throws halfway — the outer catch used to OMIT lineedit/edgepath after a null deref, and
       * an absent step read as a skip. The lane-I names start FALSE and are set when the step
       * completes, so an interrupted run reports them present and false. */
      var v16slot = {};
      ["line", "linesnap", "lineedit", "edgepath", "linereader"].forEach(function (nm) {
        v16slot[nm] = report.steps.length;
        report.steps.push([nm, false]);
      });
      function v16set(nm, val) { report.steps[v16slot[nm]] = [nm, !!val]; }
      /* Same discipline for the v1.7 names (A8): present from the start, TRUE only when the step
       * completes — an interrupted run reports them present and false, never absent. */
      var v17slot = {};
      ["anchorpoints", "anchorsnap", "anchordrag"].forEach(function (nm) {
        v17slot[nm] = report.steps.length;
        report.steps.push([nm, false]);
      });
      function v17set(nm, val) { report.steps[v17slot[nm]] = [nm, !!val]; }
      /* Same discipline for the v1.8 name (S-gate): present from the start, TRUE only when the
       * step completes. */
      var v18slot = {};
      ["straightdraw"].forEach(function (nm) {
        v18slot[nm] = report.steps.length;
        report.steps.push([nm, false]);
      });
      function v18set(nm, val) { report.steps[v18slot[nm]] = [nm, !!val]; }

      /* ---- v1.6 (lane I): the line tool (Z4), path editing (Z5.1), edge waypoints (Z5.2/Z5.3)
       * and screen-space hit testing (Z5.4). Board-agnostic like the rest: own fixture right of
       * the content, driven with real controls and synthetic pointer events, read back from the
       * model, cleaned up at the end. ---- */
      var linesBefore = (state.lines || []).length;
      var lW0 = state.width, lH0 = state.height, lGrid = state.grid || 10;
      var LX = Math.ceil((lW0 + 40) / lGrid) * lGrid;
      state.nodes.push({ id: "st-la", x: LX, y: 60, w: 160, h: 80, text: "la",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-lb", x: LX + 320, y: 60, w: 160, h: 80, text: "lb",
        kind: "box", tone: "default" });
      state.width = LX + 1120;
      state.height = Math.max(state.height, 1150);
      view = { x: 0, y: 0, k: 1 };
      setSelection([]);
      closePanel();
      render();
      function segMidOff(l2, seg, dist) {   // a doc point `dist` px perpendicularly off a midpoint
        var a2 = l2.points[seg], b2 = l2.points[seg + 1];
        var mx2 = (a2[0] + b2[0]) / 2, my2 = (a2[1] + b2[1]) / 2;
        var dx2 = b2[0] - a2[0], dy2 = b2[1] - a2[1], ln = Math.hypot(dx2, dy2) || 1;
        return docPt(mx2 - dy2 / ln * dist, my2 + dx2 / ln * dist);
      }

      // Z4.1/Z4.2/Z4.4/Z4.5: the line tool is a mode driven by its toolbar button; a draft never
      // touches the model until it commits; Esc cancels without a trace; <2 points drops silently.
      var lineBtn = document.querySelector('[data-tool="line"]');
      var selectBtn = document.querySelector('[data-tool="select"]');
      var connectGone = !document.querySelector('[data-act="connect"]');   // Z4.5
      var hist0 = history.length;
      if (lineBtn) lineBtn.click();
      // Z1.1, round-1 finding 1: the select control must EXIST and be driven — an absent one
      // is a failure, never a tolerated skip.
      var modeOn = tool === "line" && !!lineBtn && !!selectBtn &&
        lineBtn.getAttribute("aria-pressed") === "true" &&
        selectBtn.getAttribute("aria-pressed") === "false" &&
        host.style.cursor === "crosshair";
      var lp1 = docPt(LX + 40, 300), lp2 = docPt(LX + 200, 380), lp3 = docPt(LX + 420, 300);
      pev("svg", "pointerdown", lp1.x, lp1.y);
      pev("svg", "pointerup", lp1.x, lp1.y);
      pev("svg", "pointerdown", lp2.x, lp2.y);
      pev("svg", "pointerup", lp2.x, lp2.y);
      pev("svg", "pointermove", lp3.x, lp3.y);
      var previewShown = !!host.querySelector(".ck-draft");   // the rubber preview follows the pointer
      var midDraft = (state.lines || []).length === linesBefore;   // the draft is not in the model
      key("Enter");
      var newLine = (state.lines || [])[linesBefore];
      var committed = !!newLine && newLine.points.length === 2 && !newLine.from && !newLine.to &&
        Math.abs(newLine.points[0][0] - (LX + 40)) < 1 && Math.abs(newLine.points[0][1] - 300) < 1 &&
        Math.abs(newLine.points[1][0] - (LX + 200)) < 1 && Math.abs(newLine.points[1][1] - 380) < 1 &&
        !host.querySelector(".ck-draft") &&
        selection.length === 1 && selection[0].type === "line" && selection[0].id === newLine.id &&
        history.length === Math.min(50, hist0 + 1);   // exactly one undo entry
      // Esc cancels with no trace in the model or the undo history
      pev("svg", "pointerdown", lp1.x, lp1.y);
      pev("svg", "pointerup", lp1.x, lp1.y);
      key("Escape");
      var escClean = (state.lines || []).length === linesBefore + 1 &&
        history.length === Math.min(50, hist0 + 1) && !host.querySelector(".ck-draft");
      // a draft with fewer than 2 points is dropped silently
      pev("svg", "pointerdown", lp1.x, lp1.y);
      pev("svg", "pointerup", lp1.x, lp1.y);
      key("Enter");
      var shortDropped = (state.lines || []).length === linesBefore + 1 &&
        history.length === Math.min(50, hist0 + 1);
      // a double-click commits, driven with the events the browser REALLY delivers for one —
      // measured over CDP (Input.dispatchMouseEvent): pointerdown, then mousedown with detail
      // counting the presses (1, then 2). click/dblclick never arrive in line mode: render()
      // rebuilds the SVG between press and release, so the compatibility events are dropped.
      // Dispatching a synthetic dblclick here was the vacuous shortcut that shipped v1.6.0.
      pev("svg", "pointerdown", lp1.x, lp1.y);
      mev(lp1.x, lp1.y, 1);
      pev("svg", "pointerup", lp1.x, lp1.y);
      pev("svg", "pointerdown", lp3.x, lp3.y);
      mev(lp3.x, lp3.y, 1);
      pev("svg", "pointerup", lp3.x, lp3.y);
      var firstPressKept = (state.lines || []).length === linesBefore + 1 &&   // detail 1 is not
        !!host.querySelector(".ck-draft");                                     // a commit gesture
      pev("svg", "pointerdown", lp3.x, lp3.y);
      mev(lp3.x, lp3.y, 2);
      pev("svg", "pointerup", lp3.x, lp3.y);
      var dblLine = (state.lines || [])[linesBefore + 1];
      var dblOk = firstPressKept && !!dblLine && dblLine.points.length === 2 &&
        Math.abs(dblLine.points[1][0] - (LX + 420)) < 1 && Math.abs(dblLine.points[1][1] - 300) < 1;
      // leaving the mode returns the cursor and the pressed state (Z4.1) — the REAL select
      // control drives it; a missing button throws here rather than reading as a pass.
      selectBtn.click();
      var modeOff = tool === "select" && host.style.cursor === "" &&
        selectBtn.getAttribute("aria-pressed") === "true" &&
        lineBtn.getAttribute("aria-pressed") === "false";
      v16set("line", connectGone && modeOn && previewShown && midDraft && committed &&
        escClean && shortDropped && dblOk && modeOff);

      // Z4.3: the two ENDS snap — inside a rect or within 8 screen px of its border (the tolerance
      // is screen-space: at 2x zoom the same 8 px is 4 document units). Interior points never
      // snap; the snapped point is KEPT in points as the Z3.4 fallback.
      if (lineBtn) lineBtn.click();
      var sA = docPt(LX + 40, 100);            // inside st-la
      var sB = docPt(LX + 200, 300);           // a free interior point
      var sC = docPt(LX + 320 - 5, 100);       // 5 document px left of st-lb's left border
      pev("svg", "pointerdown", sA.x, sA.y); pev("svg", "pointerup", sA.x, sA.y);
      pev("svg", "pointerdown", sB.x, sB.y); pev("svg", "pointerup", sB.x, sB.y);
      pev("svg", "pointerdown", sC.x, sC.y); pev("svg", "pointerup", sC.x, sC.y);
      key("Enter");
      var snapLine = (state.lines || [])[(state.lines || []).length - 1];
      var snapOk = !!snapLine && snapLine.points.length === 3 &&
        snapLine.from && snapLine.from.ref === "st-la" &&
        snapLine.to && snapLine.to.ref === "st-lb" && snapLine.to.side === "left" &&
        Math.abs(snapLine.points[0][0] - (LX + 40)) < 1 && Math.abs(snapLine.points[0][1] - 100) < 1 &&
        Math.abs(snapLine.points[1][0] - (LX + 200)) < 1 && Math.abs(snapLine.points[1][1] - 300) < 1 &&
        Math.abs(snapLine.points[2][0] - (LX + 315)) < 1;
      // A5 (the Z4.3 successor): the tolerance is 12 SCREEN px of one of the 12 candidate points,
      // not v1.6's 8 px border band — snapEnd is gone. The far/near pair is therefore measured
      // from the nearest candidate (st-la's left midpoint) through the ACTUAL rendered scale: at
      // 2x zoom 13 screen px is too far, 10 is not. The box still owns the far point (13 < 24),
      // so the test exercises the candidate tolerance, never the ownership boundary.
      view.k = 2;
      render();
      var sTol = svgScaleNow();
      var sIn = docPt(LX + 40, 100);
      var sFar = docPt(LX - 13 / sTol, 100);  // 13 screen px from the candidate: outside 12
      var sNear = docPt(LX - 10 / sTol, 100); // 10 screen px: inside
      pev("svg", "pointerdown", sIn.x, sIn.y); pev("svg", "pointerup", sIn.x, sIn.y);
      pev("svg", "pointerdown", sFar.x, sFar.y); pev("svg", "pointerup", sFar.x, sFar.y);
      key("Enter");
      var farLine = (state.lines || [])[(state.lines || []).length - 1];
      var farOk = !!farLine && farLine.from && farLine.from.ref === "st-la" && !farLine.to;
      pev("svg", "pointerdown", sIn.x, sIn.y); pev("svg", "pointerup", sIn.x, sIn.y);
      pev("svg", "pointerdown", sNear.x, sNear.y); pev("svg", "pointerup", sNear.x, sNear.y);
      key("Enter");
      var nearLine = (state.lines || [])[(state.lines || []).length - 1];
      var nearOk = !!nearLine && nearLine.to && nearLine.to.ref === "st-la" &&
        nearLine.to.side === "left";
      view.k = 1;
      render();
      selectBtn.click();
      v16set("linesnap", snapOk && farOk && nearOk);

      // Z5.1 + Z5.4: a committed line is re-shaped — a screen-space hit selects it, vertex handles
      // drag points (clamped >= 0), Alt-click inserts/removes/detaches, drop snapping sets and
      // clears the end anchors, delete uses the existing path, undo restores one step at a time.
      if (lineBtn) lineBtn.click();
      var e1 = docPt(LX + 40, 420), e2 = docPt(LX + 160, 460), e3 = docPt(LX + 280, 420);
      pev("svg", "pointerdown", e1.x, e1.y); pev("svg", "pointerup", e1.x, e1.y);
      pev("svg", "pointerdown", e2.x, e2.y); pev("svg", "pointerup", e2.x, e2.y);
      pev("svg", "pointerdown", e3.x, e3.y); pev("svg", "pointerup", e3.x, e3.y);
      key("Enter");
      var editId = (state.lines || [])[(state.lines || []).length - 1].id;
      selectBtn.click();
      // Z5.4: a click within ~6 screen px of the polyline selects it (half the tolerance off)
      var tol1 = 6 / svgScaleNow();
      var hitPt = segMidOff(lineOf(editId), 0, tol1 / 2);
      pev("svg", "pointerdown", hitPt.x, hitPt.y);
      pev("svg", "pointerup", hitPt.x, hitPt.y);
      var selByHit = selection.length === 1 && selection[0].type === "line" &&
        selection[0].id === editId;
      var vtxSel = '.ck-vtx[data-line="' + editId + '"]';
      var threeHandles = host.querySelectorAll(vtxSel).length === 3;
      // drag the middle vertex: the drop point is exactly where the point lands
      var hist1 = history.length;
      var h1r = host.querySelector(vtxSel + '[data-vtx="1"]').getBoundingClientRect();
      var vDrop = docPt(LX + 160, 520);
      pev(vtxSel + '[data-vtx="1"]', "pointerdown", h1r.left + h1r.width / 2,
        h1r.top + h1r.height / 2);
      pev("svg", "pointermove", vDrop.x, vDrop.y);
      pev("svg", "pointerup", vDrop.x, vDrop.y);
      var editLine = lineOf(editId);
      var vtxMoved = !!editLine && editLine.points.length === 3 &&
        Math.abs(editLine.points[1][0] - (LX + 160)) < 1 &&
        Math.abs(editLine.points[1][1] - 520) < 1 &&
        history.length === Math.min(50, hist1 + 1);
      // a drag past the canvas corner clamps to >= 0 (Z2)
      var h1b = host.querySelector(vtxSel + '[data-vtx="1"]').getBoundingClientRect();
      var vNeg = docPt(-60, -40);
      pev(vtxSel + '[data-vtx="1"]', "pointerdown", h1b.left + h1b.width / 2,
        h1b.top + h1b.height / 2);
      pev("svg", "pointermove", vNeg.x, vNeg.y);
      pev("svg", "pointerup", vNeg.x, vNeg.y);
      var clamped = lineOf(editId).points[1][0] === 0 && lineOf(editId).points[1][1] === 0;
      // undo restores the previous path one step at a time (every mutation snapshots first)
      document.querySelector('[data-act="undo"]').click();
      var undone = Math.abs(lineOf(editId).points[1][1] - 520) < 1;
      // re-select (undo cleared the selection), then Alt-click a segment: a vertex at the point
      var rePt = segMidOff(lineOf(editId), 0, tol1 / 2);
      pev("svg", "pointerdown", rePt.x, rePt.y);
      pev("svg", "pointerup", rePt.x, rePt.y);
      var insPt = segMidOff(lineOf(editId), 1, 0);
      pev("svg", "pointerdown", insPt.x, insPt.y, { altKey: true });
      pev("svg", "pointerup", insPt.x, insPt.y, { altKey: true });
      var inserted = lineOf(editId).points.length === 4 &&
        Math.abs(lineOf(editId).points[2][0] - (LX + 220)) < 1 &&
        Math.abs(lineOf(editId).points[2][1] - 470) < 1;
      // Alt-click the new vertex removes it again
      var h2r = host.querySelector(vtxSel + '[data-vtx="2"]').getBoundingClientRect();
      pev(vtxSel + '[data-vtx="2"]', "pointerdown", h2r.left + h2r.width / 2,
        h2r.top + h2r.height / 2, { altKey: true });
      pev("svg", "pointerup", h2r.left + h2r.width / 2, h2r.top + h2r.height / 2, { altKey: true });
      var removed = lineOf(editId).points.length === 3;
      // dragging an END into a box anchors it (the point is kept); dragging it out clears it
      var h0r = host.querySelector(vtxSel + '[data-vtx="0"]').getBoundingClientRect();
      var intoBox = docPt(LX + 40, 100);
      pev(vtxSel + '[data-vtx="0"]', "pointerdown", h0r.left + h0r.width / 2,
        h0r.top + h0r.height / 2);
      pev("svg", "pointermove", intoBox.x, intoBox.y);
      pev("svg", "pointerup", intoBox.x, intoBox.y);
      var anchored = lineOf(editId).from && lineOf(editId).from.ref === "st-la" &&
        Math.abs(lineOf(editId).points[0][0] - (LX + 40)) < 1 &&
        Math.abs(lineOf(editId).points[0][1] - 100) < 1;
      var h0b = host.querySelector(vtxSel + '[data-vtx="0"]').getBoundingClientRect();
      var backOut = docPt(LX + 40, 420);
      pev(vtxSel + '[data-vtx="0"]', "pointerdown", h0b.left + h0b.width / 2,
        h0b.top + h0b.height / 2);
      pev("svg", "pointermove", backOut.x, backOut.y);
      pev("svg", "pointerup", backOut.x, backOut.y);
      var clearedAnchor = !lineOf(editId).from &&
        Math.abs(lineOf(editId).points[0][0] - (LX + 40)) < 1 &&
        Math.abs(lineOf(editId).points[0][1] - 420) < 1;
      // anchor again, then Alt-click the anchored vertex: the anchor detaches, the point stays
      var h0c = host.querySelector(vtxSel + '[data-vtx="0"]').getBoundingClientRect();
      pev(vtxSel + '[data-vtx="0"]', "pointerdown", h0c.left + h0c.width / 2,
        h0c.top + h0c.height / 2);
      pev("svg", "pointermove", intoBox.x, intoBox.y);
      pev("svg", "pointerup", intoBox.x, intoBox.y);
      var h0d = host.querySelector(vtxSel + '[data-vtx="0"]').getBoundingClientRect();
      pev(vtxSel + '[data-vtx="0"]', "pointerdown", h0d.left + h0d.width / 2,
        h0d.top + h0d.height / 2, { altKey: true });
      pev("svg", "pointerup", h0d.left + h0d.width / 2, h0d.top + h0d.height / 2, { altKey: true });
      var detached = !lineOf(editId).from &&
        Math.abs(lineOf(editId).points[0][0] - (LX + 40)) < 1 &&
        Math.abs(lineOf(editId).points[0][1] - 100) < 1;
      // Z5.4: at 2x zoom the 6 px tolerance halves in document units — twice it misses, half hits
      view.k = 2;
      render();
      var tol2 = 6 / svgScaleNow();
      var missPt = segMidOff(lineOf(editId), 0, tol2 * 2);
      pev("svg", "pointerdown", missPt.x, missPt.y);
      pev("svg", "pointerup", missPt.x, missPt.y);
      var missed = !(selection.length === 1 && selection[0].type === "line" &&
        selection[0].id === editId);
      var hit2 = segMidOff(lineOf(editId), 0, tol2 / 2);
      pev("svg", "pointerdown", hit2.x, hit2.y);
      pev("svg", "pointerup", hit2.x, hit2.y);
      var hitZoom = selection.length === 1 && selection[0].type === "line" &&
        selection[0].id === editId;
      view.k = 1;
      render();
      // delete uses the existing path
      key("Delete");
      var deleted = !lineOf(editId);
      // Z5.4: a marquee that crosses a line's segment selects it (no vertex inside the band)
      if (lineBtn) lineBtn.click();
      var q1 = docPt(LX + 40, 560), q2 = docPt(LX + 160, 600), q3 = docPt(LX + 280, 560);
      pev("svg", "pointerdown", q1.x, q1.y); pev("svg", "pointerup", q1.x, q1.y);
      pev("svg", "pointerdown", q2.x, q2.y); pev("svg", "pointerup", q2.x, q2.y);
      pev("svg", "pointerdown", q3.x, q3.y); pev("svg", "pointerup", q3.x, q3.y);
      key("Enter");
      var l2Id = (state.lines || [])[(state.lines || []).length - 1].id;
      selectBtn.click();
      var away = docPt(LX + 560, 200);   // an empty click clears the commit selection
      pev("svg", "pointerdown", away.x, away.y);
      pev("svg", "pointerup", away.x, away.y);
      var mFrom = docPt(LX + 70, 540), mTo = docPt(LX + 140, 615);
      pev("svg", "pointerdown", mFrom.x, mFrom.y);
      pev("svg", "pointermove", mTo.x, mTo.y);
      pev("svg", "pointerup", mTo.x, mTo.y);
      var marqueeLine = selection.some(function (s2) {
        return s2.type === "line" && s2.id === l2Id;
      });
      v16set("lineedit", selByHit && threeHandles && vtxMoved && clamped && undone &&
        inserted && removed && anchored && clearedAnchor && detached && missed && hitZoom &&
        deleted && marqueeLine);

      // Z5.2/Z5.3: an edge's path is editable — a middle segment drag materialises two waypoints
      // and moves them together, a waypoint handle moves one, Alt-click removes one; a waypoint
      // never lands inside a box (clearWaypoint pushes it through the boundary the segment heads
      // toward + 12) — covering both the third-box crossing and the own-box intrusion.
      state.nodes.push({ id: "st-ea", x: LX, y: 700, w: 160, h: 80, text: "ea",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-eb", x: LX + 560, y: 900, w: 160, h: 80, text: "eb",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-ec", x: LX + 380, y: 900, w: 140, h: 80, text: "ec",
        kind: "box", tone: "muted" });
      state.nodes.push({ id: "st-ep", x: LX + 620, y: 700, w: 100, h: 60, text: "ep",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-eq", x: LX + 940, y: 700, w: 100, h: 60, text: "eq",
        kind: "box", tone: "default" });
      var ewIdx = state.edges.length;
      state.edges.push({ from: "st-ea", to: "st-eb" });
      var eiIdx = state.edges.length;
      state.edges.push({ from: "st-ep", to: "st-eq", route: "straight" });
      setSelection([]);
      render();
      // select the edge by clicking its polyline, then drag the middle segment right by 120
      var ewSel = '.ck-edge[data-id="e' + ewIdx + '"]';
      var segAt = docPt(LX + 360, 800);
      pev(ewSel, "pointerdown", segAt.x, segAt.y);
      pev("svg", "pointerup", segAt.x, segAt.y);
      var edgeSel = selection.length === 1 && selection[0].type === "edge" &&
        selection[0].id === ewIdx;
      var histE = history.length;
      pev(ewSel, "pointerdown", segAt.x, segAt.y);
      var segTo = docPt(LX + 480, 800);
      pev("svg", "pointermove", segTo.x, segTo.y);
      pev("svg", "pointerup", segTo.x, segTo.y);
      var ew = state.edges[ewIdx];
      // the segment materialised as exactly two waypoints, one undo entry (Z5.2); the upper one
      // followed the drag, the lower landed inside st-ec and was pushed out through the boundary
      // the segment heads toward, plus 12 of clearance (Z5.3 — the third-box crossing)
      var materialised = Array.isArray(ew.waypoints) && ew.waypoints.length === 2 &&
        history.length === Math.min(50, histE + 1);
      var thirdBox = materialised &&
        Math.abs(ew.waypoints[0][0] - (LX + 480)) < 1 && Math.abs(ew.waypoints[0][1] - 740) < 1 &&
        Math.abs(ew.waypoints[1][0] - (LX + 380 + 140 + 12)) < 1 &&
        Math.abs(ew.waypoints[1][1] - 940) < 1;
      // a waypoint handle drag moves waypoints[i]
      var wpSel = '.ck-vtx[data-edge="' + ewIdx + '"][data-wp="1"]';
      var w1r = host.querySelector(wpSel).getBoundingClientRect();
      var wpTo = docPt(LX + 532, 820);
      pev(wpSel, "pointerdown", w1r.left + w1r.width / 2, w1r.top + w1r.height / 2);
      pev("svg", "pointermove", wpTo.x, wpTo.y);
      pev("svg", "pointerup", wpTo.x, wpTo.y);
      var wpMoved = Math.abs(ew.waypoints[1][0] - (LX + 532)) < 1 &&
        Math.abs(ew.waypoints[1][1] - 820) < 1;
      // Alt-click removes a waypoint; removing the last drops the key
      var w0Sel = '.ck-vtx[data-edge="' + ewIdx + '"][data-wp="0"]';
      var w0a = host.querySelector(w0Sel).getBoundingClientRect();
      pev(w0Sel, "pointerdown", w0a.left + w0a.width / 2, w0a.top + w0a.height / 2, { altKey: true });
      pev("svg", "pointerup", w0a.left + w0a.width / 2, w0a.top + w0a.height / 2, { altKey: true });
      var wpRemoved = ew.waypoints.length === 1 && Math.abs(ew.waypoints[0][1] - 820) < 1;
      var w0b2 = host.querySelector(w0Sel).getBoundingClientRect();
      pev(w0Sel, "pointerdown", w0b2.left + w0b2.width / 2, w0b2.top + w0b2.height / 2,
        { altKey: true });
      pev("svg", "pointerup", w0b2.left + w0b2.width / 2, w0b2.top + w0b2.height / 2,
        { altKey: true });
      var wpGone = !("waypoints" in ew);
      // the own-box intrusion (Z5.3): a straight edge's segment materialises at its anchors, then
      // a waypoint dragged into its OWN source box exits through the boundary the segment heads
      // toward — without the rule the path draws backwards through the box.
      var eiSel = '.ck-edge[data-id="e' + eiIdx + '"]';
      var midEI = docPt(LX + 830, 730);
      pev(eiSel, "pointerdown", midEI.x, midEI.y);
      pev("svg", "pointerup", midEI.x, midEI.y);
      pev(eiSel, "pointerdown", midEI.x, midEI.y);
      var eiTo = docPt(LX + 830, 770);
      pev("svg", "pointermove", eiTo.x, eiTo.y);
      pev("svg", "pointerup", eiTo.x, eiTo.y);
      var ei = state.edges[eiIdx];
      var matStraight = Array.isArray(ei.waypoints) && ei.waypoints.length === 2 &&
        Math.abs(ei.waypoints[0][0] - (LX + 720)) < 1 && Math.abs(ei.waypoints[0][1] - 770) < 1 &&
        Math.abs(ei.waypoints[1][0] - (LX + 940)) < 1 && Math.abs(ei.waypoints[1][1] - 770) < 1;
      var wp0Sel = '.ck-vtx[data-edge="' + eiIdx + '"][data-wp="0"]';
      var w0c = host.querySelector(wp0Sel).getBoundingClientRect();
      var intoOwn = docPt(LX + 670, 730);
      pev(wp0Sel, "pointerdown", w0c.left + w0c.width / 2, w0c.top + w0c.height / 2);
      pev("svg", "pointermove", intoOwn.x, intoOwn.y);
      pev("svg", "pointerup", intoOwn.x, intoOwn.y);
      var ownBox = Math.abs(ei.waypoints[0][0] - (LX + 620 - 12)) < 1 &&
        Math.abs(ei.waypoints[0][1] - 730) < 1;
      // with waypoints in place, dragging the segment between them moves both together (Z5.2)
      var mid2 = docPt(LX + 774, 750);
      pev(eiSel, "pointerdown", mid2.x, mid2.y);
      var seg2 = docPt(LX + 774, 800);
      pev("svg", "pointermove", seg2.x, seg2.y);
      pev("svg", "pointerup", seg2.x, seg2.y);
      var pairMoved = Math.abs(ei.waypoints[0][0] - (LX + 608)) < 1 &&
        Math.abs(ei.waypoints[0][1] - 780) < 1 &&
        Math.abs(ei.waypoints[1][0] - (LX + 940)) < 1 && Math.abs(ei.waypoints[1][1] - 820) < 1;
      v16set("edgepath", edgeSel && materialised && thirdBox && wpMoved && wpRemoved &&
        wpGone && matStraight && ownBox && pairMoved);

      /* Round-1 findings 2+3: the reader's validated-points policy is the ONLY producer for the
       * interaction paths, so an imported malformed line is dropped by the SAME rule everywhere —
       * not drawn (Z2, one rule in both readers), not hit, and a REAL pointer event where its raw
       * entries sit neither throws nor breaks the selection that follows. A negative coordinate
       * is still clamped, never dropped (Z2). */
      var badIds = { "st-badnull": true, "st-badstr": true, "st-badbool": true, "st-neg2": true };
      state.lines.push({ id: "st-badnull", points: [[LX + 40, 1050], null] });
      state.lines.push({ id: "st-badstr", points: [["12", "20"], [LX + 300, 1050]] });
      state.lines.push({ id: "st-badbool", points: [[true, 1050], [LX + 380, 1050]] });
      state.lines.push({ id: "st-neg2", points: [[-30, 1080], [LX + 500, 1080]] });
      setSelection([]);
      render();
      var winErrs = [];
      var onWinErr = function (e2) { winErrs.push(String(e2 && e2.message || e2)); };
      window.addEventListener("error", onWinErr);
      // pointerdown over the raw null entry's would-be segment, then over the bool one's
      var b1 = docPt(LX + 40, 1050), b2 = docPt(170, 1050);
      pev("svg", "pointerdown", b1.x, b1.y); pev("svg", "pointerup", b1.x, b1.y);
      pev("svg", "pointerdown", b2.x, b2.y); pev("svg", "pointerup", b2.x, b2.y);
      var noThrow = winErrs.length === 0;
      var badDropped = !host.querySelector('.ck-line[data-id="st-badnull"]') &&
        !host.querySelector('.ck-line[data-id="st-badstr"]') &&
        !host.querySelector('.ck-line[data-id="st-badbool"]');
      var badUnhit = !selection.some(function (s3) {
        return s3.type === "line" && s3.id.indexOf("st-bad") === 0;
      });
      // the clamped line IS drawn from x=0 and a click on it still selects (selection survives)
      var negPoly = host.querySelector('.ck-line[data-id="st-neg2"] polyline');
      var negMid = docPt((LX + 500) / 2, 1080);
      pev("svg", "pointerdown", negMid.x, negMid.y); pev("svg", "pointerup", negMid.x, negMid.y);
      var clampedOk = winErrs.length === 0 && !!negPoly &&
        negPoly.getAttribute("points").indexOf("0.0,1080.0") === 0 &&
        selection.length === 1 && selection[0].type === "line" && selection[0].id === "st-neg2";
      window.removeEventListener("error", onWinErr);
      state.lines = state.lines.filter(function (l4) { return !badIds[l4.id]; });
      setSelection([]);
      render();
      v16set("linereader", noThrow && badDropped && badUnhit && clampedOk);
      if (!(noThrow && badDropped && badUnhit && clampedOk)) {
        report.linereader = { noThrow: noThrow, errs: winErrs, dropped: badDropped,
          unhit: badUnhit, clamped: clampedOk };
      }

      // leave nothing behind: every fixture this block added goes, the board's own content and
      // geometry stay (the v1.3 steps set the precedent).
      var lMine = { "st-la": true, "st-lb": true, "st-ea": true, "st-eb": true, "st-ec": true,
        "st-ep": true, "st-eq": true };
      state.nodes = state.nodes.filter(function (n2) { return !lMine[n2.id]; });
      state.edges = state.edges.filter(function (e4) { return !lMine[e4.from] && !lMine[e4.to]; });
      var lKeep = {};
      (state.lines || []).forEach(function (l3, i4) { if (i4 < linesBefore) lKeep[l3.id] = true; });
      state.lines = (state.lines || []).filter(function (l3) { return lKeep[l3.id]; });
      state.width = lW0;
      state.height = lH0;
      setTool("select");
      setSelection([]);
      closePanel();
      render();

      /* ---- v1.7 (lane I): A4 the affordance, A5 the snap and the re-anchoring drags, and the
       * guarded A6.2–A6.4 panel assertions. Board-agnostic like the rest: own fixture right of
       * the content, REAL pointer sequences, the MODEL and the RENDERED DOM read back. The panel
       * markup is lane G's and the lanes run in parallel, so in THIS tree the rows may be absent:
       * every panel assertion fails on a MISMATCH when the element exists and is skipped when it
       * does not — an absent element never reads as exercised (the rendered-row assertions land
       * on the merged head). ---- */
      var aLines0 = (state.lines || []).length;
      var aW0 = state.width, aH0 = state.height, aGrid = state.grid || 10;
      var AX = Math.ceil((aW0 + 40) / aGrid) * aGrid;
      state.nodes.push({ id: "st-aa", x: AX, y: 60, w: 160, h: 100, text: "aa",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-ab", x: AX + 400, y: 60, w: 160, h: 100, text: "ab",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-ac", x: AX + 400, y: 400, w: 160, h: 100, text: "ac",
        kind: "box", tone: "default" });
      // A4: a large area-kind backdrop BEHIND st-aa — a backdrop never owns the overlay.
      state.nodes.push({ id: "st-area", x: AX - 40, y: 20, w: 400, h: 300, text: "",
        kind: "area", tone: "muted" });
      // A4 ties: st-tie-small sits inside st-tie-big (the SMALLER rect wins); the identical
      // st-tie-b is pushed BEFORE st-tie-a (the lowest id wins, never the document order).
      state.nodes.push({ id: "st-tie-big", x: AX + 680, y: 40, w: 200, h: 160, text: "tk",
        kind: "box", tone: "muted" });
      state.nodes.push({ id: "st-tie-small", x: AX + 700, y: 60, w: 80, h: 60, text: "ts",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-tie-b", x: AX + 1000, y: 60, w: 100, h: 80, text: "tb",
        kind: "box", tone: "default" });
      state.nodes.push({ id: "st-tie-a", x: AX + 1000, y: 60, w: 100, h: 80, text: "ta",
        kind: "box", tone: "default" });
      // A5: a note overlapping st-ac and SMALLER than it — an edge end still snaps to st-ac only
      // (from/to are node ids; without the nodesOnly rule this note would own the overlay).
      state.notes.push({ id: "st-an", x: AX + 440, y: 380, w: 90, h: 60, text: "an",
        kind: "note", tone: "warn" });
      state.width = AX + 1250;
      state.height = Math.max(state.height, 950);
      view = { x: 0, y: 0, k: 1 };
      setSelection([]);
      closePanel();
      render();
      var aLineBtn = document.querySelector('[data-tool="line"]');
      var aSelBtn = document.querySelector('[data-tool="select"]');
      var aSc1 = svgScaleNow();
      function aStroke(pts) {   // paint and commit one stroke, return the new line
        var n0 = (state.lines || []).length;
        pts.forEach(function (p) {
          var sp = docPt(p[0], p[1]);
          pev("svg", "pointerdown", sp.x, sp.y);
          pev("svg", "pointerup", sp.x, sp.y);
        });
        key("Enter");
        var ls = state.lines || [];
        return ls.length === n0 + 1 ? ls[ls.length - 1] : null;
      }
      function aAnchorOk(a, ref, side, at) {
        return !!a && a.ref === ref && a.side === side && a.at === at;
      }

      // A4: the affordance — 12 dots of the ONE owning box, the would-be candidate ringed, all
      // of it screen-space through the rendered scale, inside the board SVG, never in the model.
      var apFail = [];
      if (aLineBtn) aLineBtn.click();
      var apStart = docPt(AX - 300, 500);   // a free first point, far from every box
      pev("svg", "pointerdown", apStart.x, apStart.y);
      pev("svg", "pointerup", apStart.x, apStart.y);
      var apHov = docPt(AX + 30, 70);       // inside st-aa — and inside the area backdrop
      pev("svg", "pointermove", apHov.x, apHov.y);
      var apOv = host.querySelector(".ck-anchors");
      if (!apOv || apOv.getAttribute("data-owner") !== "st-aa") {
        apFail.push(["owner", apOv ? apOv.getAttribute("data-owner") : "no overlay"]);
      }
      if (!host.querySelector("svg .ck-anchors")) apFail.push(["chrome", "the overlay left the board SVG"]);
      var apDots = host.querySelectorAll(".ck-adot");
      var apRings = host.querySelectorAll(".ck-aring");
      if (apDots.length !== 12) apFail.push(["dots", apDots.length]);
      if (apRings.length !== 1) apFail.push(["rings", apRings.length]);
      // the dots are the spec's 12 points (A2's convention), derived here from the rect itself
      var apWant = [
        ["left", "0.25", AX, 60 + 0.25 * 100], ["right", "0.75", AX + 160, 60 + 0.75 * 100],
        ["top", "0.25", AX + 0.25 * 160, 60], ["bottom", "0.5", AX + 0.5 * 160, 60 + 100]
      ];
      apWant.forEach(function (w) {
        var d = host.querySelector('.ck-adot[data-side="' + w[0] + '"][data-at="' + w[1] + '"]');
        if (!d) { apFail.push(["dotpos", w[0] + "@" + w[1] + " missing"]); return; }
        var dw = parseFloat(d.getAttribute("width"));
        var cx = parseFloat(d.getAttribute("x")) + dw / 2;
        var cy = parseFloat(d.getAttribute("y")) + dw / 2;
        if (Math.abs(cx - w[2]) > 0.6 || Math.abs(cy - w[3]) > 0.6) {
          apFail.push(["dotpos", w[0] + "@" + w[1] + " at " + cx + "," + cy]);
        }
        // every size is screen-space: 3px through svgMetrics().scale, never view.k alone
        if (Math.abs(dw - 3 / aSc1) > 0.15 / aSc1) apFail.push(["dotsize", dw + " vs " + 3 / aSc1]);
      });
      // the ring sits on the candidate the pointer WOULD take: (AX+30,70) is nearest top 0.25
      var apRing = host.querySelector(".ck-aring");
      if (!apRing || apRing.getAttribute("data-side") !== "top" ||
          apRing.getAttribute("data-at") !== "0.25") {
        apFail.push(["ringat", apRing ? apRing.getAttribute("data-side") + "@" +
          apRing.getAttribute("data-at") : "no ring"]);
      } else if (Math.abs(parseFloat(apRing.getAttribute("r")) - 4 / aSc1) > 0.2 / aSc1) {
        apFail.push(["ringsize", apRing.getAttribute("r") + " vs " + 4 / aSc1]);
      }
      // nothing about the overlay reaches the model or the exported JSON
      var apExport = exportJson();
      if (apExport.indexOf("ck-adot") !== -1 || apExport.indexOf("ck-anchors") !== -1) {
        apFail.push(["model", "the overlay leaked into the exported document"]);
      }
      // the area backdrop alone owns nothing: a hover inside it (and away from st-aa) draws none
      var apArea = docPt(AX + 300, 250);
      pev("svg", "pointermove", apArea.x, apArea.y);
      if (host.querySelector(".ck-anchors")) {
        apFail.push(["area", "the area backdrop owns the overlay"]);
      }
      // the 24 SCREEN px border band: 20 px off st-aa's border still owns it, 30 px does not
      var apIn20 = docPt(AX - 20 / aSc1, 110);
      pev("svg", "pointermove", apIn20.x, apIn20.y);
      var apOv20 = host.querySelector(".ck-anchors");
      if (!apOv20 || apOv20.getAttribute("data-owner") !== "st-aa") {
        apFail.push(["band20", apOv20 ? apOv20.getAttribute("data-owner") : "no overlay"]);
      }
      var apOut30 = docPt(AX - 30 / aSc1, 110);   // within the area's own band — still nothing
      pev("svg", "pointermove", apOut30.x, apOut30.y);
      if (host.querySelector(".ck-anchors")) {
        apFail.push(["band30", "a box owns the overlay from 30 screen px away"]);
      }
      /* Round-1 finding 1: the ring is a HINT independent of the snap's 12 px commitment —
       * exactly ONE ring, on the nearest candidate, throughout the whole 24 px ownership
       * band (straight off the left side, where the nearest candidate is left@0.5). */
      [[12.5, "ringband12"], [20, "ringband20"], [23.5, "ringband24"]].forEach(function (bp) {
        var bpt = docPt(AX - bp[0] / aSc1, 110);
        pev("svg", "pointermove", bpt.x, bpt.y);
        var bOv = host.querySelector(".ck-anchors");
        var bRings = host.querySelectorAll(".ck-aring");
        if (!bOv || bOv.getAttribute("data-owner") !== "st-aa" || bRings.length !== 1 ||
            bRings[0].getAttribute("data-side") !== "left" ||
            bRings[0].getAttribute("data-at") !== "0.5") {
          apFail.push([bp[1], !bOv ? "no overlay" : bRings.length + " ring(s)" +
            (bRings[0] ? " at " + bRings[0].getAttribute("data-side") + "@" +
              bRings[0].getAttribute("data-at") : "")]);
        }
      });
      /* Round-1 finding 2: ownership is the RADIAL border distance, not the expanded
       * rectangle — 20 px left AND 20 px above the corner is ~28 screen px away, so NOTHING
       * owns the overlay there, while a 16/16 diagonal (~23 px) still owns it. */
      var apDiagOut = docPt(AX - 20 / aSc1, 60 - 20 / aSc1);
      pev("svg", "pointermove", apDiagOut.x, apDiagOut.y);
      if (host.querySelector(".ck-anchors")) {
        apFail.push(["corner24", "a diagonal ~28 screen px away owns the overlay"]);
      }
      var apDiagIn = docPt(AX - 16 / aSc1, 60 - 16 / aSc1);
      pev("svg", "pointermove", apDiagIn.x, apDiagIn.y);
      var apOvDI = host.querySelector(".ck-anchors");
      if (!apOvDI || apOvDI.getAttribute("data-owner") !== "st-aa" ||
          host.querySelectorAll(".ck-aring").length !== 1) {
        apFail.push(["cornerin", apOvDI ? host.querySelectorAll(".ck-aring").length +
          " ring(s)" : "no overlay"]);
      }
      // ... and both gates again at a second zoom: every threshold is SCREEN space through
      // svgMetrics().scale (never view.k alone), so k=2 decides identically
      view = { x: 0, y: 0, k: 2 };
      render();
      var aSc2 = svgScaleNow();
      var apZout = docPt(AX - 20 / aSc2, 60 - 20 / aSc2);
      pev("svg", "pointermove", apZout.x, apZout.y);
      if (host.querySelector(".ck-anchors")) {
        apFail.push(["corner24z", "zoomed: the far diagonal owns the overlay"]);
      }
      var apZband = docPt(AX - 20 / aSc2, 110);
      pev("svg", "pointermove", apZband.x, apZband.y);
      var apOvZ = host.querySelector(".ck-anchors");
      var apRZ = host.querySelectorAll(".ck-aring");
      if (!apOvZ || apOvZ.getAttribute("data-owner") !== "st-aa" || apRZ.length !== 1) {
        apFail.push(["ringbandz", apOvZ ? apRZ.length + " ring(s)" : "no overlay"]);
      }
      view = { x: 0, y: 0, k: 1 };
      render();
      // ties: the smaller rect wins, then the lowest id — never the document order
      var apTie1 = docPt(AX + 720, 80);   // inside st-tie-small and st-tie-big alike
      pev("svg", "pointermove", apTie1.x, apTie1.y);
      var apOvT1 = host.querySelector(".ck-anchors");
      if (!apOvT1 || apOvT1.getAttribute("data-owner") !== "st-tie-small") {
        apFail.push(["tiesize", apOvT1 ? apOvT1.getAttribute("data-owner") : "no overlay"]);
      }
      var apTie2 = docPt(AX + 1050, 100);   // inside the identical st-tie-b and st-tie-a
      pev("svg", "pointermove", apTie2.x, apTie2.y);
      var apOvT2 = host.querySelector(".ck-anchors");
      if (!apOvT2 || apOvT2.getAttribute("data-owner") !== "st-tie-a") {
        apFail.push(["tieid", apOvT2 ? apOvT2.getAttribute("data-owner") : "no overlay"]);
      }
      // ...and nothing otherwise: no draft, no drag — no overlay
      key("Escape");
      if (host.querySelector(".ck-anchors")) apFail.push(["off", "the overlay survives the draft"]);
      if (aSelBtn) aSelBtn.click();
      v17set("anchorpoints", apFail.length === 0);
      if (apFail.length) report.anchorpoints = apFail;

      // A5: a painted stroke's two ENDS take the nearest candidate of the OWNING box ({ref, side,
      // at}); the stored point is kept as the Z3.4 fallback; interior points never snap.
      var asFail = [];
      if (aLineBtn) aLineBtn.click();
      // both ends snap: from st-ab's left midpoint (3px off the border), to st-aa's top 0.25
      var asL1 = aStroke([[AX + 400 - 3, 110], [AX + 43, 57]]);
      if (!asL1 || !aAnchorOk(asL1.from, "st-ab", "left", 0.5) ||
          !aAnchorOk(asL1.to, "st-aa", "top", 0.25)) {
        asFail.push(["ends", JSON.stringify([asL1 && asL1.from, asL1 && asL1.to])]);
      }
      if (!asL1 || Math.abs(asL1.points[0][0] - (AX + 397)) > 1 ||
          Math.abs(asL1.points[0][1] - 110) > 1 ||
          Math.abs(asL1.points[1][0] - (AX + 43)) > 1 || Math.abs(asL1.points[1][1] - 57) > 1) {
        asFail.push(["kept", "the snapped points are not kept as the fallback"]);
      }
      // an interior point inside a box snaps nothing: a 3-point stroke stays anchor-free
      var asL2 = aStroke([[AX - 300, 600], [AX + 30, 70], [AX - 200, 700]]);
      if (!asL2 || asL2.from || asL2.to ||
          Math.abs(asL2.points[1][0] - (AX + 30)) > 1 || Math.abs(asL2.points[1][1] - 70) > 1) {
        asFail.push(["interior", JSON.stringify([asL2 && asL2.from, asL2 && asL2.to,
          asL2 && asL2.points[1]])]);
      }
      // the 12 SCREEN px candidate tolerance: 13 px away does not snap, 10 px does — the box
      // owns both (13 < 24), so this is the candidate rule, never the ownership rule
      var asL3 = aStroke([[AX - 300, 800], [AX - 13 / aSc1, 110]]);
      if (!asL3 || asL3.to) asFail.push(["far", JSON.stringify(asL3 && asL3.to)]);
      var asL4 = aStroke([[AX - 320, 850], [AX - 10 / aSc1, 110]]);
      if (!asL4 || !aAnchorOk(asL4.to, "st-aa", "left", 0.5)) {
        asFail.push(["near", JSON.stringify(asL4 && asL4.to)]);
      }
      /* Round-2 finding: the 12 SCREEN px cutoff is EXCLUSIVE (A8) — a click EXACTLY 12 px from
       * the candidate must NOT snap, 11.9 px still does, 20 px does not. All three land inside
       * the 24 px ownership band, so this is the candidate rule, never the ownership rule — and
       * the tolerance is screen-space, so the same three clicks are repeated at a second zoom. */
      var asL6 = aStroke([[AX - 340, 810], [AX - 12 / aSc1, 110]]);
      if (!asL6 || asL6.to) asFail.push(["boundary12", JSON.stringify(asL6 && asL6.to)]);
      var asL7 = aStroke([[AX - 360, 855], [AX - 20 / aSc1, 110]]);
      if (!asL7 || asL7.to) asFail.push(["far20", JSON.stringify(asL7 && asL7.to)]);
      var asL8 = aStroke([[AX - 380, 895], [AX - 11.9 / aSc1, 110]]);
      if (!asL8 || !aAnchorOk(asL8.to, "st-aa", "left", 0.5)) {
        asFail.push(["near119", JSON.stringify(asL8 && asL8.to)]);
      }
      // ... and the same three at k=2, the scale re-read: a screen-space rule decides identically
      view = { x: 0, y: 0, k: 2 };
      render();
      var aScZ = svgScaleNow();
      var asZ1 = aStroke([[AX - 300, 900], [AX - 12 / aScZ, 110]]);
      if (!asZ1 || asZ1.to) asFail.push(["boundary12z", JSON.stringify(asZ1 && asZ1.to)]);
      var asZ2 = aStroke([[AX - 320, 905], [AX - 20 / aScZ, 110]]);
      if (!asZ2 || asZ2.to) asFail.push(["far20z", JSON.stringify(asZ2 && asZ2.to)]);
      var asZ3 = aStroke([[AX - 340, 910], [AX - 11.9 / aScZ, 110]]);
      if (!asZ3 || !aAnchorOk(asZ3.to, "st-aa", "left", 0.5)) {
        asFail.push(["near119z", JSON.stringify(asZ3 && asZ3.to)]);
      }
      view = { x: 0, y: 0, k: 1 };
      render();
      // inside the box the nearest candidate is taken — even further than 12 px away
      var asL5 = aStroke([[AX - 250, 880], [AX + 80, 115]]);
      if (!asL5 || !aAnchorOk(asL5.to, "st-aa", "bottom", 0.5)) {
        asFail.push(["inside", JSON.stringify(asL5 && asL5.to)]);
      }
      if (aSelBtn) aSelBtn.click();
      v17set("anchorsnap", asFail.length === 0);
      if (asFail.length) report.anchorsnap = asFail;

      // A5: dragging an endpoint re-anchors it between candidate points and the drawn path
      // follows; a far drop detaches a LINE's anchor and keeps the point; an EDGE's end handle
      // re-anchors on the same box, RE-POINTS on another box, and changes nothing far away.
      var adFail = [];
      if (aLineBtn) aLineBtn.click();
      var adLine = aStroke([[AX + 43, 57], [AX - 300, 600]]);   // from {st-aa, top, 0.25}
      if (aSelBtn) aSelBtn.click();
      var adId = adLine && adLine.id;
      setSelection([{ type: "line", id: adId }]);
      render();
      var adVtx = '.ck-vtx[data-line="' + adId + '"][data-vtx="0"]';
      var adH0 = host.querySelector(adVtx).getBoundingClientRect();
      var adDrop = docPt(AX + 4, 108);   // inside st-aa, nearest its left midpoint
      pev(adVtx, "pointerdown", adH0.left + adH0.width / 2, adH0.top + adH0.height / 2);
      pev("svg", "pointermove", adDrop.x, adDrop.y);
      var adOv = host.querySelector(".ck-anchors");   // the overlay tracks an endpoint drag too
      if (!adOv || adOv.getAttribute("data-owner") !== "st-aa") {
        adFail.push(["dragoverlay", adOv ? adOv.getAttribute("data-owner") : "no overlay"]);
      }
      pev("svg", "pointerup", adDrop.x, adDrop.y);
      var adL = lineOf(adId);
      if (!adL || !aAnchorOk(adL.from, "st-aa", "left", 0.5)) {
        adFail.push(["reanchor", JSON.stringify(adL && adL.from)]);
      }
      // the drawn path follows: the resolved from-point sits on the left side's midpoint
      var adPoly = host.querySelector('.ck-line[data-id="' + adId + '"] polyline');
      var adP0 = adPoly ? adPoly.getAttribute("points").split(" ")[0].split(",") : [];
      if (!(adP0.length === 2 && Math.abs(parseFloat(adP0[0]) - AX) < 0.6 &&
            Math.abs(parseFloat(adP0[1]) - 110) < 0.6)) {
        adFail.push(["follows", adP0.join(",")]);
      }
      // dragging the same end far from every box detaches the anchor and keeps the point (v1.6)
      var adH0b = host.querySelector(adVtx).getBoundingClientRect();
      var adFar = docPt(AX - 300, 500);
      pev(adVtx, "pointerdown", adH0b.left + adH0b.width / 2, adH0b.top + adH0b.height / 2);
      pev("svg", "pointermove", adFar.x, adFar.y);
      pev("svg", "pointerup", adFar.x, adFar.y);
      var adL2 = lineOf(adId);
      if (!adL2 || adL2.from || Math.abs(adL2.points[0][0] - (AX - 300)) > 1 ||
          Math.abs(adL2.points[0][1] - 500) > 1) {
        adFail.push(["detach", JSON.stringify([adL2 && adL2.from, adL2 && adL2.points[0]])]);
      }
      // the edge's two ends: handles at the resolved anchors, answering BEFORE any segment bend
      var adEi = state.edges.length;
      state.edges.push({ from: "st-aa", to: "st-ab" });
      setSelection([]);
      render();
      var adMid = docPt(AX + 280, 110);   // the auto route's middle, far from both ends
      pev('.ck-edge[data-id="e' + adEi + '"]', "pointerdown", adMid.x, adMid.y);
      pev("svg", "pointerup", adMid.x, adMid.y);
      if (!(selection.length === 1 && selection[0].type === "edge" && selection[0].id === adEi)) {
        adFail.push(["edgeselect", JSON.stringify(selection)]);
      }
      var adFromH = '.ck-vtx[data-edge="' + adEi + '"][data-eend="from"]';
      var adToH = '.ck-vtx[data-edge="' + adEi + '"][data-eend="to"]';
      var adFH = host.querySelector(adFromH), adTH = host.querySelector(adToH);
      if (!adFH || !adTH) {
        adFail.push(["handles", "the edge's end handles are missing"]);
      } else {
        var adHx = parseFloat(adFH.getAttribute("x")) + parseFloat(adFH.getAttribute("width")) / 2;
        var adHy = parseFloat(adFH.getAttribute("y")) + parseFloat(adFH.getAttribute("height")) / 2;
        if (Math.abs(adHx - (AX + 160)) > 0.6 || Math.abs(adHy - 110) > 0.6) {
          adFail.push(["handleat", adHx + "," + adHy]);
        }
      }
      // another box's candidate: the end is RE-POINTED — and the note covering the drop point
      // (smaller than st-ac) must NOT own the overlay: an edge's ends are node ids only
      var adHist0 = history.length;
      var adTr = host.querySelector(adToH).getBoundingClientRect();
      var adDropTo = docPt(AX + 482, 403);   // st-ac's top midpoint, inside the st-an note too
      pev(adToH, "pointerdown", adTr.left + adTr.width / 2, adTr.top + adTr.height / 2);
      pev("svg", "pointermove", adDropTo.x, adDropTo.y);
      var adOv2 = host.querySelector(".ck-anchors");
      if (!adOv2 || adOv2.getAttribute("data-owner") !== "st-ac") {
        adFail.push(["nodesonly", adOv2 ? adOv2.getAttribute("data-owner") : "no overlay"]);
      }
      pev("svg", "pointerup", adDropTo.x, adDropTo.y);
      var adE = state.edges[adEi];
      if (!(adE.to === "st-ac" && adE.to_side === "top" && adE.to_at === 0.5 &&
            history.length === Math.min(50, adHist0 + 1) && !adE.waypoints)) {
        adFail.push(["repoint", JSON.stringify([adE.to, adE.to_side, adE.to_at,
          history.length - adHist0, adE.waypoints])]);
      }
      var adPolyE = host.querySelector('.ck-edge[data-id="e' + adEi + '"] polyline');
      var adPE = adPolyE ? adPolyE.getAttribute("points").split(" ") : [];
      var adPEnd = adPE.length ? adPE[adPE.length - 1].split(",") : [];
      if (!(adPEnd.length === 2 && Math.abs(parseFloat(adPEnd[0]) - (AX + 480)) < 0.6 &&
            Math.abs(parseFloat(adPEnd[1]) - 400) < 0.6)) {
        adFail.push(["repointdrawn", adPEnd.join(",")]);
      }
      // the same box's candidate: from_side/from_at change, the id stays
      var adHist1 = history.length;
      var adFr2 = host.querySelector(adFromH).getBoundingClientRect();
      var adDropF = docPt(AX + 122, 157);   // st-aa's bottom 0.75
      pev(adFromH, "pointerdown", adFr2.left + adFr2.width / 2, adFr2.top + adFr2.height / 2);
      pev("svg", "pointermove", adDropF.x, adDropF.y);
      pev("svg", "pointerup", adDropF.x, adDropF.y);
      var adPolyF = host.querySelector('.ck-edge[data-id="e' + adEi + '"] polyline');
      var adPF = adPolyF ? adPolyF.getAttribute("points").split(" ")[0].split(",") : [];
      if (!(adE.from === "st-aa" && adE.from_side === "bottom" && adE.from_at === 0.75 &&
            history.length === Math.min(50, adHist1 + 1) && !adE.waypoints &&
            adPF.length === 2 && Math.abs(parseFloat(adPF[1]) - 160) < 0.6 &&
            Math.abs(parseFloat(adPF[0]) - (AX + 160)) > 1)) {
        adFail.push(["samebox", JSON.stringify([adE.from, adE.from_side, adE.from_at,
          history.length - adHist1, adE.waypoints, adPF.join(",")])]);
      }
      // far from every box an edge changes NOTHING — no detach, not even an undo entry
      var adHist2 = history.length;
      var adTr3 = host.querySelector(adToH).getBoundingClientRect();
      var adDropFar = docPt(AX + 900, 800);
      pev(adToH, "pointerdown", adTr3.left + adTr3.width / 2, adTr3.top + adTr3.height / 2);
      pev("svg", "pointermove", adDropFar.x, adDropFar.y);
      pev("svg", "pointerup", adDropFar.x, adDropFar.y);
      if (!(adE.to === "st-ac" && adE.to_side === "top" && adE.to_at === 0.5 &&
            history.length === adHist2)) {
        adFail.push(["farnoop", JSON.stringify([adE.to, adE.to_side, adE.to_at,
          history.length - adHist2])]);
      }
      // the handle drag snapshotted for undo: ONE undo restores the pre-same-box end
      document.querySelector('[data-act="undo"]').click();
      var adE2 = state.edges[adEi];
      if (!adE2 || adE2.from !== "st-aa" || adE2.from_side !== undefined ||
          adE2.from_at !== undefined || adE2.to !== "st-ac") {
        adFail.push(["undo", JSON.stringify(adE2)]);
      }
      /* A6.2–A6.4, guarded: lane G emits the rows in page(), and in THIS tree they are absent.
       * Every assertion below fails on a MISMATCH when the element exists and is skipped when it
       * does not — on the merged head this block is the rendered-row gate. */
      if (document.getElementById("ck-line-from-at")) {
        var apLine = lineOf(adId);
        apLine.from = { ref: "st-aa", side: "left", at: 0.4 };   // an op-authored fraction
        setSelection([{ type: "line", id: adId }]);
        render();
        syncPanel();
        var apRowF = document.getElementById("ck-row-line-from-at");
        var apRowT = document.getElementById("ck-row-line-to-at");
        if (!apRowF || apRowF.hidden) adFail.push(["a6vis", "a resolved end's rows are hidden"]);
        if (!apRowT || !apRowT.hidden) adFail.push(["a6vis2", "a free end's rows are shown"]);
        // A6.4: no preset pressed for 0.4, the hint names the exact percentage, the model is
        // untouched by merely opening the panel
        if (document.querySelectorAll('#ck-line-from-at [aria-pressed="true"]').length !== 0) {
          adFail.push(["a64press", "a non-preset fraction presses a preset"]);
        }
        var apHint = hintFor("ck-line-from-at");
        if (!apHint || apHint.textContent.indexOf("40 %") === -1) {
          adFail.push(["a64hint", apHint ? apHint.textContent : "no hint"]);
        }
        if (apLine.from.at !== 0.4) adFail.push(["a64model", "opening the panel rewrote the model"]);
        // a preset click writes that preset and presses it
        var apBtn = document.querySelector('#ck-line-from-at [data-at="0.75"]');
        if (apBtn) apBtn.click();
        if (apLine.from.at !== 0.75 ||
            !apBtn || apBtn.getAttribute("aria-pressed") !== "true") {
          adFail.push(["a64click", JSON.stringify(apLine.from)]);
        }
        // the side select mirrors the model and writes back
        var apSel = document.getElementById("ck-line-from-side");
        if (!apSel || apSel.value !== "left") {
          adFail.push(["a6side", apSel ? apSel.value : "absent"]);
        } else {
          apSel.value = "bottom";
          apSel.dispatchEvent(new Event("change"));
          if (apLine.from.side !== "bottom") adFail.push(["a6sidew", apLine.from.side]);
        }
        /* Round-1 finding 3 (A3's pane-writer column): an invalid offered value is refused
         * BEFORE the snapshot and the write — the model keeps its value, NO undo entry is
         * added, the row is marked with a reason — for ALL FOUR at groups; `null` is the
         * documented reset to 0.5, NOT an error. Driven through the REAL controls (the offer
         * is hacked into a button's data-at, then clicked), plus the validator itself. */
        var wrClick = function (groupId, offer) {   // one hacked real-control click
          var b = document.querySelector("#" + groupId + ' [data-at="0.5"]');
          if (!b) { adFail.push(["writer-absent", groupId]); return; }
          b.setAttribute("data-at", offer);
          b.click();
          b.setAttribute("data-at", "0.5");
        };
        var wrRowOk = function (rowId, wantInvalid, tag) {
          var row = document.getElementById(rowId);
          var marked = !!(row && row.getAttribute("aria-invalid") === "true");
          if (!row || marked !== wantInvalid) {
            adFail.push([tag, row ? "aria-invalid=" + row.getAttribute("aria-invalid") :
              "no row"]);
          }
          var h = row ? row.querySelector(".ck-hint[data-hint-for]") : null;
          if (row && wantInvalid && (!h || h.textContent.indexOf("please") === -1)) {
            adFail.push([tag + "-reason", h ? h.textContent : "no hint"]);
          }
        };
        apLine.to = { ref: "st-ab", side: "right", at: 0.5 };   // both ends resolve now
        syncPanel();
        // the line's from: a finite offer OUTSIDE [0,1] is refused — model and history keep
        var wrHist0 = history.length;
        wrClick("ck-line-from-at", "1.4");
        if (apLine.from.at !== 0.75 || history.length !== wrHist0) {
          adFail.push(["writerrefusal-range", JSON.stringify([apLine.from.at,
            history.length - wrHist0])]);
        }
        wrRowOk("ck-row-line-from-at", true, "writerrefusal-range-row");
        // the line's to: a STRING offer is refused the same way
        wrClick("ck-line-to-at", "half");
        if (apLine.to.at !== 0.5 || history.length !== wrHist0) {
          adFail.push(["writerrefusal-string", JSON.stringify([apLine.to.at,
            history.length - wrHist0])]);
        }
        wrRowOk("ck-row-line-to-at", true, "writerrefusal-string-row");
        // the documented null reset clears to 0.5, is NOT an error and lifts the mark
        wrClick("ck-line-from-at", "null");
        if (apLine.from.at !== 0.5 ||
            history.length !== Math.min(50, wrHist0 + 1)) {
          adFail.push(["writer-null", JSON.stringify([apLine.from.at,
            history.length - wrHist0])]);
        }
        wrRowOk("ck-row-line-from-at", false, "writer-null-row");
        syncPanel();
        // a dangling ref hides the rows exactly like a free end
        apLine.to = { ref: "st-zzz", side: "left", at: 0.5 };
        syncPanel();
        var apRowT2 = document.getElementById("ck-row-line-to-at");
        if (!apRowT2 || !apRowT2.hidden) adFail.push(["a6dangle", "a dangling ref's rows are shown"]);
        delete apLine.to;
        // an edge's rows always show, and the group mirrors an op-authored from_at
        state.edges[adEi].from_at = 0.75;
        setSelection([{ type: "edge", id: adEi }]);
        render();
        syncPanel();
        var apRowEF = document.getElementById("ck-row-from-at");
        var apRowET = document.getElementById("ck-row-to-at");
        if (!apRowEF || apRowEF.hidden || !apRowET || apRowET.hidden) {
          adFail.push(["a6edge", "an edge end's rows are hidden"]);
        }
        var apEBtn = document.querySelector('#ck-from-at [data-at="0.75"]');
        if (!apEBtn || apEBtn.getAttribute("aria-pressed") !== "true") {
          adFail.push(["a6edgepress", "the edge's from_at is not pressed"]);
        }
        var apEBtn25 = document.querySelector('#ck-to-at [data-at="0.25"]');
        if (apEBtn25) apEBtn25.click();
        if (state.edges[adEi].to_at !== 0.25) {
          adFail.push(["a6edgeclick", JSON.stringify(state.edges[adEi].to_at)]);
        }
        // round-1 finding 3, the edge's two groups: a NON-FINITE offer (1e400) and a
        // boolean-shaped offer ("true") are refused — model, history and mark as above
        var wrHist1 = history.length;
        wrClick("ck-from-at", "1e400");
        if (state.edges[adEi].from_at !== 0.75 || history.length !== wrHist1) {
          adFail.push(["writerrefusal-inf", JSON.stringify([state.edges[adEi].from_at,
            history.length - wrHist1])]);
        }
        wrRowOk("ck-row-from-at", true, "writerrefusal-inf-row");
        wrClick("ck-to-at", "true");
        if (state.edges[adEi].to_at !== 0.25 || history.length !== wrHist1) {
          adFail.push(["writerrefusal-bool", JSON.stringify([state.edges[adEi].to_at,
            history.length - wrHist1])]);
        }
        wrRowOk("ck-row-to-at", true, "writerrefusal-bool-row");
        syncPanel();
        // the validator itself, offered the raw values a DOM control cannot carry
        [["string", "0.25"], ["boolean", true], ["nan", NaN], ["over", 1.4],
         ["under", -3], ["inf", Infinity]].forEach(function (wu) {
          if (acceptAtOffer(wu[1]).ok) {
            adFail.push(["writerrefusal-offer-" + wu[0], "accepted " + String(wu[1])]);
          }
        });
        var wrAcc = acceptAtOffer(0.4), wrRes = acceptAtOffer(null);
        if (!wrAcc.ok || wrAcc.at !== 0.4) {
          adFail.push(["writer-accept", JSON.stringify(wrAcc)]);
        }
        if (!wrRes.ok || wrRes.at !== 0.5) {
          adFail.push(["writer-reset", JSON.stringify(wrRes)]);
        }
      }
      v17set("anchordrag", adFail.length === 0);
      if (adFail.length) report.anchordrag = adFail;
      report.anchorPanelRows = !!document.getElementById("ck-line-from-at");   // see the lane report

      // leave nothing behind: every fixture this block added goes, the board's own content and
      // geometry stay (the v1.3/v1.6 steps set the precedent).
      var aMine = { "st-aa": true, "st-ab": true, "st-ac": true, "st-area": true,
        "st-tie-big": true, "st-tie-small": true, "st-tie-a": true, "st-tie-b": true };
      state.nodes = state.nodes.filter(function (n2) { return !aMine[n2.id]; });
      state.notes = state.notes.filter(function (n3) { return n3.id !== "st-an"; });
      state.edges = state.edges.filter(function (e4) { return !aMine[e4.from] && !aMine[e4.to]; });
      state.lines = (state.lines || []).filter(function (l3, i4) { return i4 < aLines0; });
      state.width = aW0;
      state.height = aH0;
      setTool("select");
      setSelection([]);
      closePanel();
      render();

      /* ---- v1.8 (lane S, S1-S7): the straight tool. A press-drag-release commits ONE two-point
       * line through the existing addLine factory; the PLAIN drag is locked to the nearest 45°
       * step (horizontal, vertical, diagonal) with the length projected onto it, and Shift frees
       * the angle; a <3-unit stroke and Esc leave no trace; select is the way out. Driven with
       * the REAL button and synthetic pointer events, read back from the model. ---- */
      var sFail = [];
      var sLines0 = (state.lines || []).length;
      var sW0 = state.width, sH0 = state.height, sGrid = state.grid || 10;
      var SX = Math.ceil((sW0 + 40) / sGrid) * sGrid;
      state.width = SX + 900;
      state.height = Math.max(state.height, 800);
      view = { x: 0, y: 0, k: 1 };
      setSelection([]);
      closePanel();
      render();
      var sBtn = document.querySelector('[data-tool="straight"]');
      var sSelBtn = document.querySelector('[data-tool="select"]');
      function sPressed() { return document.querySelectorAll('[data-tool][aria-pressed="true"]'); }
      function sLast() { return (state.lines || [])[(state.lines || []).length - 1]; }
      if (!sBtn) {
        sFail.push(["button", "no [data-tool=\"straight\"] control in the bar"]);
      } else {
        if (sBtn.getAttribute("aria-label") !== "draw a straight line") {
          sFail.push(["label", String(sBtn.getAttribute("aria-label"))]);
        }
        if ((sBtn.getAttribute("title") || "").indexOf("Shift") === -1) {
          sFail.push(["title", String(sBtn.getAttribute("title"))]);
        }
        sBtn.click();
        if (!(tool === "straight" && sBtn.getAttribute("aria-pressed") === "true" &&
              sSelBtn && sSelBtn.getAttribute("aria-pressed") === "false" &&
              sPressed().length === 1 && sPressed()[0] === sBtn &&
              host.style.cursor === "crosshair")) {
          sFail.push(["modeon", tool + "/" + host.style.cursor]);
        }
        if (document.getElementById("ck-status").textContent !==
            "straight line: drag for a 45°-step line (horizontal, vertical or diagonal), " +
            "hold Shift for any angle") {
          sFail.push(["message", document.getElementById("ck-status").textContent]);
        }
        // S2: a plain mostly-horizontal drag commits ONE two-point line, locked to 0° (S4)
        var sHist0 = history.length;
        // F1 (v1.8 round 3): the LIVE straight drag refreshes chrome only. Capture the svg and
        // its top-level scene children BEFORE the press; while the draft is live (after the
        // move, before the release) the same svg object must be in place, every captured child
        // still connected to it, and the scene-rebuild counter unmoved. A check that only reads
        // the model back cannot see a rebuild.
        var sSvgA = host.querySelector("svg.ck-board");
        var sKidsA = [].slice.call(sSvgA.childNodes, 0, sceneKids);   // scene children only
        var sR0 = sceneRebuilds;
        var pA = docPt(SX + 100, 300), pB = docPt(SX + 240, 318);
        pev("svg", "pointerdown", pA.x, pA.y);
        var sNoFlash = !host.querySelector(".ck-draft");   // S3: a press alone draws nothing
        pev("svg", "pointermove", pB.x, pB.y);
        var sPreview = !!host.querySelector(".ck-draft");
        var sMidDraft = (state.lines || []).length === sLines0;   // not in the model yet
        var sLiveSame = host.querySelector("svg.ck-board") === sSvgA;
        var sLiveKept = sKidsA.every(function (kA) {
          return kA.isConnected && kA.parentNode === sSvgA;
        });
        var sLiveRebuilds = sceneRebuilds - sR0;
        pev("svg", "pointerup", pB.x, pB.y);
        var sLine = sLast();
        if (!(sNoFlash && sPreview && sMidDraft)) {
          sFail.push(["preview", JSON.stringify([sNoFlash, sPreview, sMidDraft])]);
        }
        if (!(sLiveSame && sLiveKept && sLiveRebuilds === 0)) {
          sFail.push(["livedrag", JSON.stringify([sLiveSame, sLiveKept, sLiveRebuilds])]);
        }
        if (!sLine || (state.lines || []).length !== sLines0 + 1 || sLine.points.length !== 2 ||
            Math.abs(sLine.points[0][0] - (SX + 100)) >= 1 ||
            Math.abs(sLine.points[0][1] - 300) >= 1 ||
            Math.abs(sLine.points[1][0] - (SX + 240)) >= 1 ||
            sLine.points[1][1] !== sLine.points[0][1] ||   // the lock is exact: y0 === y1
            sLine.from || sLine.to ||
            selection.length !== 1 || selection[0].type !== "line" || selection[0].id !== sLine.id ||
            history.length !== Math.min(50, sHist0 + 1) ||
            !panel.classList.contains("on") || !secOn("line")) {
          sFail.push(["horizontal", JSON.stringify(sLine ? sLine.points : null)]);
        }
        // a plain mostly-vertical drag locks to 90°: x0 === x1
        var pV1 = docPt(SX + 100, 340), pV2 = docPt(SX + 118, 480);
        pev("svg", "pointerdown", pV1.x, pV1.y);
        pev("svg", "pointermove", pV2.x, pV2.y);
        pev("svg", "pointerup", pV2.x, pV2.y);
        var sVert = sLast();
        if (!sVert || sVert.points.length !== 2 ||
            sVert.points[1][0] !== sVert.points[0][0] ||
            Math.abs(sVert.points[1][1] - 480) >= 1) {
          sFail.push(["vertical", JSON.stringify(sVert ? sVert.points : null)]);
        }
        // a plain ~40° drag locks to the 45° step: |dy| === |dx|
        var pD1 = docPt(SX + 300, 300), pD2 = docPt(SX + 440, 420);
        pev("svg", "pointerdown", pD1.x, pD1.y);
        pev("svg", "pointermove", pD2.x, pD2.y);
        pev("svg", "pointerup", pD2.x, pD2.y);
        var sDiag = sLast();
        if (!sDiag || sDiag.points.length !== 2 ||
            Math.abs(Math.abs(sDiag.points[1][1] - sDiag.points[0][1]) -
                     Math.abs(sDiag.points[1][0] - sDiag.points[0][0])) >= 1e-9) {
          sFail.push(["diagonal", JSON.stringify(sDiag ? sDiag.points : null)]);
        }
        // a SHIFT drag is free: the committed end is exactly the pointer's own point
        var pS1 = docPt(SX + 300, 520), pS2 = docPt(SX + 440, 553);
        pev("svg", "pointerdown", pS1.x, pS1.y, { shiftKey: true });
        pev("svg", "pointermove", pS2.x, pS2.y, { shiftKey: true });
        pev("svg", "pointerup", pS2.x, pS2.y, { shiftKey: true });
        var sFree = sLast();
        if (!sFree || sFree.points.length !== 2 ||
            Math.abs(sFree.points[1][0] - (SX + 440)) >= 1 ||
            Math.abs(sFree.points[1][1] - 553) >= 1 ||
            sFree.points[1][1] === sFree.points[0][1] ||
            Math.abs(sFree.points[1][1] - sFree.points[0][1]) ===
              Math.abs(sFree.points[1][0] - sFree.points[0][0])) {
          sFail.push(["shiftfree", JSON.stringify(sFree ? sFree.points : null)]);
        }
        // S4: the helper itself, on the five pinned cases
        var c1 = constrainStep45(50, 50, 60, 53, false);    // (10,3) -> horizontal
        var c2 = constrainStep45(50, 50, 53, 60, false);    // (3,10) -> vertical
        var c3 = constrainStep45(50, 50, 60, 59, false);    // (10,9) -> the 45° step
        var c4 = constrainStep45(50, 50, 60, 51, false);    // (10,1) -> horizontal, length 10
        var c5 = constrainStep45(50, 50, 60, 53, true);     // free -> untouched
        if (!(c1[1] === 50 && c2[0] === 50 &&
              Math.abs(c3[1] - 50) === Math.abs(c3[0] - 50) &&
              c4[1] === 50 && c4[0] === 60 &&
              c5[0] === 60 && c5[1] === 53)) {
          sFail.push(["helper", JSON.stringify([c1, c2, c3, c4, c5])]);
        }
        // S2: a 2-unit stroke writes nothing — no line, no undo entry — and the mode stays armed.
        // F1: a canceled stroke is "nothing happened", so the scene is not even rebuilt.
        var sHist1 = history.length;
        var sSvgN = host.querySelector("svg.ck-board");
        var sKidsN = [].slice.call(sSvgN.childNodes, 0, sceneKids);   // scene only: the chrome
        var sRN = sceneRebuilds;                                      // of the selected line is
        var pN1 = docPt(SX + 600, 300), pN2 = docPt(SX + 602, 300);   // legitimately refreshed
        pev("svg", "pointerdown", pN1.x, pN1.y);
        pev("svg", "pointermove", pN2.x, pN2.y);
        pev("svg", "pointerup", pN2.x, pN2.y);
        var sShortKept = host.querySelector("svg.ck-board") === sSvgN &&
          sKidsN.every(function (kN) { return kN.isConnected && kN.parentNode === sSvgN; }) &&
          sceneRebuilds - sRN === 0;
        if (!((state.lines || []).length === sLines0 + 4 && history.length === sHist1 &&
              tool === "straight" && sBtn.getAttribute("aria-pressed") === "true" && sShortKept)) {
          sFail.push(["shortstroke", JSON.stringify([(state.lines || []).length - sLines0,
            history.length - sHist1, tool, sShortKept])]);
        }
        // S5: Esc during a live straight draft leaves no trace — and no panel section.
        // F1: cancelling costs nothing — same svg, every scene child kept, no rebuild.
        closePanel();
        var sSvgE = host.querySelector("svg.ck-board");
        var sKidsE = [].slice.call(sSvgE.childNodes, 0, sceneKids);   // scene only, as above
        var sRE = sceneRebuilds;
        var pE1 = docPt(SX + 600, 420), pE2 = docPt(SX + 700, 440);
        pev("svg", "pointerdown", pE1.x, pE1.y);
        pev("svg", "pointermove", pE2.x, pE2.y);
        key("Escape");
        var sEscKept = host.querySelector("svg.ck-board") === sSvgE &&
          sKidsE.every(function (kE) { return kE.isConnected && kE.parentNode === sSvgE; }) &&
          sceneRebuilds - sRE === 0;
        if (!((state.lines || []).length === sLines0 + 4 && history.length === sHist1 &&
              !host.querySelector(".ck-draft") && !panel.classList.contains("on") && sEscKept)) {
          sFail.push(["esc", JSON.stringify([(state.lines || []).length - sLines0,
            history.length - sHist1, !!host.querySelector(".ck-draft"), sEscKept])]);
        }
        // S6: leaving the mode drops a live straight draft, commits nothing, and clears the mode
        var pL1 = docPt(SX + 600, 560), pL2 = docPt(SX + 700, 560);
        pev("svg", "pointerdown", pL1.x, pL1.y);
        pev("svg", "pointermove", pL2.x, pL2.y);
        if (!sSelBtn) { sFail.push(["selectbtn", "no [data-tool=\"select\"] control"]); }
        else {
          sSelBtn.click();   // the REAL way out — a missing or inert button fails here
          if (!(tool === "select" && host.style.cursor === "" &&
                sSelBtn.getAttribute("aria-pressed") === "true" &&
                sBtn.getAttribute("aria-pressed") === "false" &&
                sPressed().length === 1 && sPressed()[0] === sSelBtn &&
                (state.lines || []).length === sLines0 + 4 && history.length === sHist1 &&
                !host.querySelector(".ck-draft") &&
                document.getElementById("ck-status").textContent === "")) {
            sFail.push(["modeoff", tool + "/" + host.style.cursor + "/" +
              document.getElementById("ck-status").textContent]);
          }
        }
      }
      // leave nothing behind: the four committed lines and the fixture geometry go
      state.lines = (state.lines || []).filter(function (l4, i5) { return i5 < sLines0; });
      state.width = sW0;
      state.height = sH0;
      setTool("select");
      setSelection([]);
      closePanel();
      render();
      v18set("straightdraw", sFail.length === 0);
      if (sFail.length) report.straightdraw = sFail;
      /* ---- v1.8 (lane R): the live render path. A gesture must not rebuild the board: the
       * one <svg class="ck-board"> survives, every top-level scene child captured before a
       * gesture is still connected to it afterwards, the editor's own scene-rebuild counter
       * stays at 0 across both gestures, the draft preview tracks the pointer while the
       * stroke is live, and the patched post-drag DOM is byte-identical to a forced full
       * render of the same model (R4). ---- */
      var lpSlot = report.steps.length;
      report.steps.push(["livepatch", false]);
      try {
        var lpFail = [];
        view = { x: 0, y: 0, k: 1 };
        setSelection([]);
        closePanel();
        render();   // a settled board: chrome empty, the svg and its scene children final
        var lpNode = nodeById(first);
        var lpHome = { x: lpNode.x, y: lpNode.y };

        // gesture 1: a 20-move line stroke, kept LIVE (Esc drops it — never committed)
        var lpToolBtn = document.querySelector('[data-tool="line"]');
        if (!lpToolBtn) lpFail.push("no line tool button");
        else lpToolBtn.click();
        var lpSvgA = host.querySelector("svg.ck-board");
        var lpKidsA = [].slice.call(lpSvgA.childNodes);
        var lpR0 = sceneRebuilds;
        var lpStart = docPt(60, state.height - 160);
        pev("svg", "pointerdown", lpStart.x, lpStart.y);
        pev("svg", "pointerup", lpStart.x, lpStart.y);
        var lpLastDoc = null;
        for (var lm = 1; lm <= 20; lm++) {
          lpLastDoc = [60 + lm * 15, state.height - 160 + (lm % 3) * 12];
          var lpMove = docPt(lpLastDoc[0], lpLastDoc[1]);
          pev("svg", "pointermove", lpMove.x, lpMove.y);
        }
        var lpPoly = host.querySelector(".ck-draft");
        var lpDraftOk = false;
        if (lpPoly) {
          var lpPts = lpPoly.getAttribute("points").split(" ");
          var lpTail = lpPts[lpPts.length - 1].split(",");
          lpDraftOk = Math.abs(parseFloat(lpTail[0]) - lpLastDoc[0]) < 0.6 &&
                      Math.abs(parseFloat(lpTail[1]) - lpLastDoc[1]) < 0.6;
        }
        var lpStrokeSame = host.querySelector("svg.ck-board") === lpSvgA;
        var lpStrokeKept = lpKidsA.every(function (k) {
          return k.isConnected && k.parentNode === lpSvgA;
        });
        var lpStrokeRebuilds = sceneRebuilds - lpR0;
        key("Escape");   // the draft is chrome: cancelling it touches nothing in the scene
        var lpSelBtn = document.querySelector('[data-tool="select"]');
        if (lpSelBtn) lpSelBtn.click();
        if (!lpDraftOk) lpFail.push("the draft preview does not end at the pointer");
        if (!lpStrokeSame) lpFail.push("the svg was replaced during the stroke");
        if (!lpStrokeKept) lpFail.push("a scene child was replaced during the stroke");
        if (lpStrokeRebuilds !== 0) lpFail.push("stroke scene rebuilds: " + lpStrokeRebuilds);

        // gesture 2: a 20-move node drag, ending in a drop — patched in place throughout
        var lpSvgB = host.querySelector("svg.ck-board");
        var lpKidsB = [].slice.call(lpSvgB.childNodes);
        var lpR1 = sceneRebuilds;
        var lpRect = host.querySelector('.ck-node[data-id="' + first + '"]')
          .getBoundingClientRect();
        var lpCx = lpRect.left + lpRect.width / 2, lpCy = lpRect.top + lpRect.height / 2;
        pev('.ck-node[data-id="' + first + '"]', "pointerdown", lpCx, lpCy);
        for (var ld = 1; ld <= 20; ld++) pev("svg", "pointermove", lpCx + ld * 6, lpCy + ld * 5);
        var lpDragSameMid = host.querySelector("svg.ck-board") === lpSvgB;
        pev("svg", "pointerup", lpCx + 120, lpCy + 100);
        var lpDragSame = lpDragSameMid && host.querySelector("svg.ck-board") === lpSvgB;
        var lpDragKept = lpKidsB.every(function (k) {
          return k.isConnected && k.parentNode === lpSvgB;
        });
        var lpDragRebuilds = sceneRebuilds - lpR1;
        var lpMoved = lpNode.x !== lpHome.x || lpNode.y !== lpHome.y;
        // R4: the patched DOM equals a full render of the same model, byte for byte
        var lpPatched = host.innerHTML;
        render();   // render() sets the dirty flag itself: this forces the full scene rebuild
        var lpDrift = host.innerHTML === lpPatched;
        if (!lpDragSame) lpFail.push("the svg was replaced during the drag");
        if (!lpDragKept) lpFail.push("a scene child was replaced during the drag");
        if (lpDragRebuilds !== 0) lpFail.push("drag scene rebuilds: " + lpDragRebuilds);
        if (!lpMoved) lpFail.push("the drag did not move the node");
        if (!lpDrift) lpFail.push("the patched DOM differs from a full render");

        // leave nothing behind: the node goes home, the tool and selection are restored
        lpNode.x = lpHome.x;
        lpNode.y = lpHome.y;
        setTool("select");
        setSelection([]);
        closePanel();
        render();
        report.steps[lpSlot] = ["livepatch", lpFail.length === 0];
        if (lpFail.length) report.livepatch = lpFail;
      } catch (lpErr) {
        report.livepatch = String(lpErr && lpErr.message ? lpErr.message : lpErr);
      }

      report.ok = report.steps.every(function (s) { return s[1]; });
    } catch (err) {
      report.error = String(err && err.message ? err.message : err);
    }
    var pre = document.createElement("pre");
    pre.id = "ck-selftest";
    pre.textContent = JSON.stringify(report);
    document.body.appendChild(pre);
  }

  /* v1.6 (lane P, Z9): `barfit` — the toolbar at the pane's OWN width. It runs in its own load of
   * this page (`#selftest-pane`): the harness sets the pane's 440px viewport through CDP, because
   * Chrome's new headless clamps a window to 500px (`--window-size=440,900` reports `innerWidth`
   * 500) and a layout claim measured at another width proves nothing. The functional steps above
   * stay in the run they always had — they are written in DOCUMENT units (the drag threshold in
   * `pointermove`, the grid landing `groupmove` asserts), so a 440px window changes their numbers,
   * not their subjects. `boardkit.selftest` merges the two reports into the one it returns; a
   * browser opening `#selftest` by hand simply never sees this step. */
  if (location.hash === "#selftest-pane") {
    var pane = { ok: false, steps: [] };
    try {
      setSelection([]);
      closePanel();
      render();   // a clean page: the message block and the panel are not part of the bar
      var paneBar = document.querySelector(".ck-bar");
      var paneKids = [];
      if (paneBar) {
        for (var pk1 = 0; pk1 < paneBar.children.length; pk1++) {
          if (paneBar.children[pk1].getClientRects().length) paneKids.push(paneBar.children[pk1]);
        }
      }
      // Exactly ONE distinct top among the bar's visible children (Z9): every child of the bar is
      // 22px tall for that reason, separators included.
      var paneTops = [];
      paneKids.forEach(function (k) {
        var t = Math.round(k.getBoundingClientRect().top);
        if (paneTops.indexOf(t) === -1) paneTops.push(t);
      });
      var paneH = paneBar ? paneBar.getBoundingClientRect().height : -1;
      // At most ONE accent-marked control, and it is the pressed TOOL (Z1). Measured, not read off
      // the selector: a probe element resolves `var(--ck-1)`, and every visible bar child whose
      // computed border AND text colour is that value counts as accented.
      var paneProbe = document.createElement("span");
      paneProbe.setAttribute("style", "color:var(--ck-1);position:absolute;visibility:hidden");
      document.body.appendChild(paneProbe);
      var paneAccent = getComputedStyle(paneProbe).color;
      var paneAccented = paneKids.filter(function (k) {
        var cs = getComputedStyle(k);
        return cs.color === paneAccent && cs.borderTopColor === paneAccent;
      });
      paneProbe.remove();
      var paneActs = ["add", "addnote", "grid", "zoom-in", "zoom-out", "fit", "undo", "copy",
        "download", "reset"];
      var paneMissing = paneActs.filter(function (a) {
        return !paneBar || !paneBar.querySelector('[data-act="' + a + '"]');
      });
      var paneTools = paneBar ? paneBar.querySelectorAll('[data-tool="line"]') : [];
      // Every control that shows no text names itself for a screen reader AND carries a tooltip;
      // the separators are decoration (Z1).
      var paneUnnamed = paneKids.filter(function (k) {
        return !k.classList.contains("ck-sep") && !k.getAttribute("aria-label") &&
          !k.getAttribute("title");
      }).length;
      pane.info = { width: window.innerWidth, kids: paneKids.length, tops: paneTops, height: paneH,
        scrollWidth: document.body.scrollWidth, accent: paneAccent, accented: paneAccented.length,
        tools: paneTools.length, missing: paneMissing, unnamed: paneUnnamed,
        seps: paneBar ? paneBar.querySelectorAll(".ck-sep").length : -1,
        connect: !!(paneBar && paneBar.querySelector('[data-act="connect"]')) };
      pane.steps.push(["barfit", !!paneBar && window.innerWidth === 440 && paneTops.length === 1 &&
        paneH <= 30 && document.body.scrollWidth <= window.innerWidth && pane.info.seps === 4 &&
        !pane.info.connect && paneTools.length === 1 && paneMissing.length === 0 &&
        paneUnnamed === 0 && paneAccented.length <= 1 &&
        paneAccented.every(function (k) { return !!k.getAttribute("data-tool"); })]);
      pane.ok = pane.steps.every(function (s) { return s[1]; });
      if (!pane.ok) pane.barfit = pane.info;
    } catch (err) {
      pane.error = String(err && err.message ? err.message : err);
    }
    var panePre = document.createElement("pre");
    panePre.id = "ck-selftest";
    panePre.textContent = JSON.stringify(pane);
    document.body.appendChild(panePre);
  }
})();
