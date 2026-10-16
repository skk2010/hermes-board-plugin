#!/usr/bin/env python3
"""Lane A render checks for v1.2 primitives (R2-R7) against the parity fixture.

    python3 tests/test_render.py

Asserts the SVG structure of every new shape kind, split-box dividers, left alignment,
per-end arrow-head markers, the background grid, and back-compat for legacy boards.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("boardkit", ROOT / "vendor" / "boardkit.py")
boardkit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boardkit)

FAILURES: list[str] = []


def check(name: str, condition, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def group_of(svg: str, nid: str) -> str:
    marker = f'<g class="ck-node" data-id="{nid}"'
    start = svg.index(marker)
    return svg[start:svg.index("</g>", start)]


def render(board: dict) -> str:
    return boardkit.render_svg(board)


def main() -> int:
    fixture = boardkit.load(str(ROOT / "docs" / "fixture-v1.2.json"))
    svg = render(fixture)

    print("authoring surface (R1/R6.4)")
    node = boardkit.box("n", "t", 0, 0, align="left",
                        parts=["A", {"text": "B", "sub": "s"}, {"text": "  "}], split="columns")
    check("box writes align/parts/split only when informative",
          node.get("align") == "left"
          and node.get("parts") == [{"text": "A"}, {"text": "B", "sub": "s"}]
          and node.get("split") == "columns", str(node))
    plain = boardkit.box("p", "t", 0, 0, align="center", parts=[], split="rows")
    check("box omits uninformative align/parts/split",
          "align" not in plain and "parts" not in plain and "split" not in plain, str(plain))
    check("new kinds are accepted by box()",
          all(boardkit.box("k", "t", 0, 0, kind=k)["kind"] == k
              for k in ("cloud", "cylinder", "document", "hexagon", "parallelogram",
                        "server", "computer", "actor", "area")))
    item = boardkit.edge("a", "b", bidir=True, start="open", end="bar")
    check("edge keeps bidir and writes start/end",
          item.get("bidir") is True and item.get("start") == "open" and item.get("end") == "bar",
          str(item))
    check("edge without heads writes none",
          "start" not in boardkit.edge("a", "b") and "end" not in boardkit.edge("a", "b"))

    print("fixture: shape kinds (R2)")
    cloud = group_of(svg, "cloud")
    check("cloud is a single path", cloud.count("<path") == 1 and "<polygon" not in cloud)
    cyl = group_of(svg, "cyl")
    check("cylinder is a path plus an ellipse",
          cyl.count("<path") == 1 and cyl.count("<ellipse") == 1)
    doc = group_of(svg, "doc")
    check("document is a single path", doc.count("<path") == 1 and "<ellipse" not in doc)
    check("hexagon is a polygon", group_of(svg, "hex").count("<polygon") == 1)
    check("parallelogram is a polygon", group_of(svg, "para").count("<polygon") == 1)
    srv = group_of(svg, "srv")
    check("server is a body, 3 slot rects, 3 led circles plus the caption",
          srv.count("<rect") == 4 and srv.count("<circle") == 3 and ">api-1<" in srv)
    pc = group_of(svg, "pc")
    check("computer is monitor + stand + base, sub ignored",
          pc.count("<rect") == 1 and pc.count("<path") == 2 and "ignored" not in pc)
    actor = group_of(svg, "actor")
    check("actor is a head circle plus 3 stroke paths",
          actor.count("<circle") == 1 and actor.count("<path") == 3)
    area = group_of(svg, "area")
    check("area has the header rule and its title",
          'd="M 0,24 H 420"' in area and ">Runtime zone<" in area)
    check("area is drawn before the edges and ordinary nodes",
          svg.index('data-id="area"') < svg.index('<g class="ck-edge"')
          and svg.index('data-id="area"') < svg.index('data-id="lefty"'))
    ordered = render(boardkit.board(
        nodes=[boardkit.box("late", "L", 300, 300),
               {"id": "zone", "x": 0, "y": 0, "w": 200, "h": 200, "text": "Z",
                "kind": "area"}]))
    check("area renders first even when listed last",
          ordered.index('data-id="zone"') < ordered.index('data-id="late"'))

    print("split boxes (R4)")
    rows = render(boardkit.board(nodes=[
        boardkit.box("r", "", 0, 0, w=180, h=90, parts=["a", "b", "c"])]))
    rgroup = group_of(rows, "r")
    check("3-part rows box emits exactly 2 dividers at the R4 y coordinates",
          rgroup.count("<path") == 2 and 'd="M 0,30 H 180"' in rgroup
          and 'd="M 0,60 H 180"' in rgroup, rgroup)
    headed = render(boardkit.board(nodes=[
        boardkit.box("hd", "hdr", 0, 0, w=180, h=118, parts=["a", "b", "c"])]))
    hgroup = group_of(headed, "hd")
    check("a header shifts the dividers by head_h=28",
          'd="M 0,58 H 180"' in hgroup and 'd="M 0,88 H 180"' in hgroup, hgroup)
    cols = render(boardkit.board(nodes=[
        boardkit.box("c", "hdr", 0, 0, w=160, h=118, parts=["w", "e"], split="columns")]))
    cgroup = group_of(cols, "c")
    check("2-column split emits 1 vertical divider",
          cgroup.count("<path") == 1 and 'd="M 80,28 V 118"' in cgroup, cgroup)
    plain = render(boardkit.board(nodes=[boardkit.box("pb", "plain", 0, 0)]))
    check("a box without parts emits no dividers", "<path" not in group_of(plain, "pb"))
    hexsplit = render(boardkit.board(nodes=[
        boardkit.box("hx", "h", 0, 0, kind="hexagon", parts=["a", "b"])]))
    check("parts on a non-box kind are ignored", "<path" not in group_of(hexsplit, "hx"))
    nosub = render(boardkit.board(nodes=[
        boardkit.box("ns", "hdr", 0, 0, sub="node sub", parts=["a"])]))
    check("the node's own sub is not drawn when parts render",
          "node sub" not in group_of(nosub, "ns"))
    partsub = render(boardkit.board(nodes=[
        boardkit.box("ps", "", 0, 0, h=90, parts=[{"text": "a", "sub": "5 ms"}])]))
    check("a part's sub renders under the part text",
          ">5 ms</text>" in group_of(partsub, "ps"))

    print("text placement (R3)")
    lefty = group_of(svg, "lefty")
    check('align "left" anchors text at the inset',
          'text-anchor="start"' in lefty and 'x="12"' in lefty, lefty[:300])
    centred = render(boardkit.board(nodes=[boardkit.box("cb", "centred", 0, 0)]))
    check("default alignment stays centred",
          'text-anchor="middle"' in group_of(centred, "cb"))

    print("edge heads (R6)")
    defs = boardkit._markers()
    missing = [f"ck-head-{tone}-{head}" for tone in boardkit.TONES
               for head in boardkit.HEADS if head != "none"
               if f'id="ck-head-{tone}-{head}"' not in defs]
    check("_markers holds one marker per tone x non-none head", not missing, str(missing))
    positions = [defs.index(f'id="ck-head-{tone}-{head}"') for tone in boardkit.TONES
                 for head in boardkit.HEADS if head != "none"]
    check("markers are ordered tone-major, then HEADS order", positions == sorted(positions))
    heads = render(boardkit.board(
        nodes=[boardkit.box("a", "A", 0, 0), boardkit.box("b", "B", 300, 0)],
        edges=[boardkit.edge("a", "b", start="bar", end="diamond")]))
    check("an edge with start/end emits both markers pointing at those ids",
          'marker-start="url(#ck-head-default-bar)"' in heads
          and 'marker-end="url(#ck-head-default-diamond)"' in heads)
    check("the fixture's bidir edge still gets an arrow at both ends",
          'marker-start="url(#ck-head-default-arrow)"' in svg
          and svg.count('marker-end="url(#ck-head-default-arrow)"') >= 1)
    headless = render(boardkit.board(
        nodes=[boardkit.box("a", "A", 0, 0), boardkit.box("b", "B", 300, 0)],
        edges=[boardkit.edge("a", "b", start="none", end="none")]))
    check('heads "none" emit no marker attributes', "marker-start" not in headless
          and "marker-end" not in headless)

    print("grid (R5)")
    check("the fixture (dots) emits the ck-grid pattern and rect",
          '<pattern id="ck-grid" width="20" height="20"' in svg
          and '<circle cx="0.5" cy="0.5" r="1" fill="var(--ck-grid)"/>' in svg
          and '<rect class="ck-grid"' in svg)
    check("the grid rect is the first drawing element",
          svg.index("</defs>") < svg.index('<rect class="ck-grid"') < svg.index("<g "),
          "order: defs, grid rect, groups")
    off = render(boardkit.board(nodes=[boardkit.box("a", "A", 0, 0)]))
    check("grid_on absent emits neither pattern nor rect",
          "ck-grid" not in off and "<pattern" not in off)
    board = boardkit.board(nodes=[boardkit.box("a", "A", 0, 0)])
    board["grid_on"] = False
    nogrid = render(board)
    check("grid_on false emits neither pattern nor rect",
          "ck-grid" not in nogrid and "<pattern" not in nogrid)
    lines_board = boardkit.board(nodes=[boardkit.box("a", "A", 0, 0)])
    lines_board["grid_on"] = True
    lines_svg = render(lines_board)
    check("grid_style lines is the default look",
          'stroke="var(--ck-grid)" stroke-width="1"' in lines_svg
          and "<pattern" in lines_svg)

    print("v1.3 edges: attach sides (W3.1/W3.2)")
    A = {"id": "a", "x": 0, "y": 0, "w": 100, "h": 50, "text": "A", "kind": "box",
         "tone": "default"}
    B = {"id": "b", "x": 300, "y": 0, "w": 100, "h": 50, "text": "B", "kind": "box",
         "tone": "default"}

    def edge_render(**edge_kw):
        item = {"from": "a", "to": "b"}
        item.update(edge_kw)
        return render(boardkit.board(nodes=[dict(A), dict(B)], edges=[item]))

    def edge_points(**edge_kw):
        match = re.search(r'<polyline points="([^"]+)"', edge_render(**edge_kw))
        return [tuple(float(v) for v in p.split(",")) for p in match.group(1).split()]

    # W3.2: every explicit side anchors at that side's midpoint (a: 0,0 100x50;
    # b: 300,0 100x50). `straight` keeps the polyline to exactly the two anchors.
    for side, point in (("left", (0.0, 25.0)), ("right", (100.0, 25.0)),
                        ("top", (50.0, 0.0)), ("bottom", (50.0, 50.0))):
        pts = edge_points(route="straight", from_side=side)
        check(f"from_side {side} anchors the first point at the {side} midpoint",
              pts[0] == point, str(pts))
    for side, point in (("left", (300.0, 25.0)), ("right", (400.0, 25.0)),
                        ("top", (350.0, 0.0)), ("bottom", (350.0, 50.0))):
        pts = edge_points(route="straight", to_side=side)
        check(f"to_side {side} anchors the last point at the {side} midpoint",
              pts[-1] == point, str(pts))
    pts = edge_points(route="straight")
    check("two auto ends keep the v1.2 anchors (right-mid to left-mid here)",
          pts == [(100.0, 25.0), (300.0, 25.0)], str(pts))
    pts = edge_points(from_side="left")
    check("an explicit from_side leaves the auto to end on the position rule",
          pts[0] == (0.0, 25.0) and pts[-1] == (300.0, 25.0), str(pts))
    pts = edge_points(to_side="right")
    check("an explicit to_side leaves the auto from end on the position rule",
          pts[0] == (100.0, 25.0) and pts[-1] == (400.0, 25.0), str(pts))
    pts = edge_points(from_side="top", to_side="bottom")
    check("elbow routes keep the explicit anchors as the first/last points",
          pts[0] == (50.0, 0.0) and pts[-1] == (350.0, 50.0) and len(pts) == 4, str(pts))
    check("spelling out the auto defaults is byte-identical to omitting them",
          edge_render() == edge_render(from_side="auto", to_side="auto"))
    check("an unknown side falls back to auto and is never rendered raw",
          edge_render(from_side="middle", to_side="sideways") == edge_render()
          and 'points="middle' not in edge_render(from_side="middle"))
    pts = edge_points(from_side="top", to_side="bottom",
                      waypoints=[[150, 200], [250, 200]])
    check("a waypointed edge still starts/ends at the explicit side midpoints",
          pts[0] == (50.0, 0.0) and pts[-1] == (350.0, 50.0)
          and pts[1] == (150.0, 200.0) and pts[2] == (250.0, 200.0), str(pts))
    pts = edge_points(waypoints=[[150, 200], [250, 200]])
    check("auto ends of a waypointed edge aim at the neighbouring waypoint as before",
          pts[0] == (50.0, 50.0) and pts[-1] == (350.0, 50.0), str(pts))

    print("v1.3 edges: head size (W3.3)")

    def marker_of(svg: str, marker_id: str) -> str:
        start = svg.index(f'<marker id="{marker_id}"')
        return svg[start:svg.index("</marker>", start)]

    small = edge_render(head_size="small")
    check("a small head references the -small marker",
          'marker-end="url(#ck-head-default-arrow-small)"' in small)
    marker = marker_of(small, "ck-head-default-arrow-small")
    # W3.3 (round-1 disposition 3): the size goes into markerWidth/markerHeight ONLY. refX/refY are
    # coordinates in the marker's own `0 0 10 10` viewBox, where the tip sits at (10,5); anchoring at
    # box/2 would pull the head off the endpoint and break the v1.2 geometry.
    check("the small marker scales the box to 8 and keeps the (10,5) anchor",
          'markerWidth="8" markerHeight="8"' in marker
          and 'refX="10" refY="5"' in marker, marker)
    large = edge_render(head_size="large")
    check("a large head references the -large marker",
          'marker-end="url(#ck-head-default-arrow-large)"' in large)
    marker = marker_of(large, "ck-head-default-arrow-large")
    check("the large marker scales the box to 16 and keeps the (10,5) anchor",
          'markerWidth="16" markerHeight="16"' in marker
          and 'refX="10" refY="5"' in marker, marker)
    check("sized markers keep the 0 0 10 10 viewBox and the tone's shape",
          'viewBox="0 0 10 10"' in marker
          and boardkit._HEAD_SHAPES["arrow"].format(tone="default") in marker)
    medium = edge_render(head_size="medium")
    check("an explicit medium keeps the plain v1.2 marker id and emits no -medium marker",
          'marker-end="url(#ck-head-default-arrow)"' in medium
          and "arrow-medium" not in medium)
    check("an absent head_size renders exactly like medium", medium == edge_render())
    check("an unknown head_size falls back to medium and is never rendered raw",
          edge_render(head_size="huge") == edge_render() and "huge" not in edge_render(head_size="huge"))
    check("sized markers are emitted only when an edge uses them",
          "-small" not in edge_render() and "-large" not in edge_render())
    both = edge_render(head_size="small", start="circle", end="open")
    check("both ends of one edge carry the size",
          'marker-start="url(#ck-head-default-circle-small)"' in both
          and 'marker-end="url(#ck-head-default-open-small)"' in both
          and 'id="ck-head-default-circle-small"' in both
          and 'id="ck-head-default-open-small"' in both)
    toned = render(boardkit.board(
        nodes=[dict(A), dict(B)],
        edges=[{"from": "a", "to": "b", "tone": "ok", "head_size": "large"}]))
    check("the sized marker is emitted per (tone, type, size)",
          'id="ck-head-ok-arrow-large"' in toned and "ck-head-ok-arrow-small" not in toned)
    check("the v1.2 fixture gains no sized markers (back-compat)",
          svg.count("<marker ") == 36 and "-small" not in svg and "-large" not in svg)

    print("v1.3 edges: v1.3 fixture (W3.2/W3.3 against the shared fixture)")
    svg13 = render(boardkit.load(str(ROOT / "docs" / "fixture-v1.3.json")))

    def fixture_points(from_id, to_id):
        seg = re.search(r'<g class="ck-edge"[^>]*data-from="%s"[^>]*data-to="%s".*?</g>'
                        % (from_id, to_id), svg13, re.S)
        return re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", seg.group(0)) if seg else []

    pts = fixture_points("a", "b")
    check("fixture: top/bottom attach at the side midpoints",
          pts and pts[0] == ("135.0", "60.0") and pts[-1] == ("455.0", "140.0"), str(pts[:3]))
    pts = fixture_points("e", "f")
    check("fixture: a waypointed edge starts at the explicit right side",
          pts and pts[0] == ("210.0", "340.0"), str(pts[:2]))
    ids13 = re.findall(r'marker-(?:start|end)="url\(#([^)]+)\)"', svg13)
    check("fixture: large/small suffix the ids, medium does not, unknown falls back",
          "ck-head-default-open-large" in ids13 and "ck-head-default-diamond-small" in ids13
          and "ck-head-default-triangle" in ids13
          and not [i for i in ids13 if i.endswith("-medium") or i.endswith("-huge")],
          str(sorted(set(ids13))))

    print("back-compat and determinism")
    legacy = {"title": "old", "nodes": [{"id": "a", "x": 0, "y": 0, "w": 190, "h": 72,
                                         "text": "A", "kind": "box"},
                                        {"id": "b", "x": 300, "y": 0, "w": 190, "h": 72,
                                         "text": "B", "kind": "box"}],
              "edges": [{"from": "a", "to": "b", "bidir": True}]}
    old = render(legacy)
    check("a legacy bidir edge renders an arrow at both ends",
          'marker-start="url(#ck-head-default-arrow)"' in old
          and 'marker-end="url(#ck-head-default-arrow)"' in old)
    check("render_svg is deterministic", render(fixture) == svg)
    check("the fixture renders every node and edge",
          svg.count('<g class="ck-node"') == len(fixture["nodes"])
          and svg.count('<g class="ck-edge"') == len(fixture["edges"]))

    print("editor page HTML (R7)")
    page = boardkit.page(fixture)
    check("the toolbar gains the one-row grid toggle", 'data-act="grid"' in page)
    check("the panel gains the new controls",
          all(needle in page for needle in
              ('id="ck-align"', 'id="ck-parts"', 'id="ck-split"',
               'id="ck-start"', 'id="ck-end"')))
    # Y2/Y8 (v1.5 gap finding 12): the selects are REPLACED by button groups, so the old <option>
    # assertions would fail a correct implementation.
    check("the shape picker lists every kind as a button (Y2)",
          not re.search(r'<select id="ck-kind"', page)
          and all(f'data-kind="{k}"' in page for k in boardkit.KINDS))
    check("the head pickers offer every HEADS value as a button, start before end (Y2)",
          not re.search(r'<select id="ck-(start|end)"', page)
          and all(f'data-end="{hd}"' in page for hd in boardkit.HEADS)
          and page.index('id="ck-start"') < page.index('id="ck-end"'))

    print("v1.3 edges: editor page (W3.4/W3.5)")
    check("the panel gains the three edge v2 selects",
          all(needle in page for needle in
              ('id="ck-from-side"', 'id="ck-to-side"', 'id="ck-head-size"')))

    def options_of(select_id: str) -> list[str]:
        segment = page[page.index(f'id="{select_id}"'):].split("</select>", 1)[0]
        return re.findall(r'<option value="([^"]+)">', segment)

    check("the side selects list auto/left/right/top/bottom in that order",
          options_of("ck-from-side") == ["auto", "left", "right", "top", "bottom"]
          and options_of("ck-to-side") == ["auto", "left", "right", "top", "bottom"])
    # Y2/Y8 (v1.5 gap finding 12): the head-size control is a button group now, not a select.
    check("the head size control lists small/medium/large in that order (Y2)",
          not re.search(r'<select id="ck-head-size"', page)
          and [page.index('data-head="%s"' % h) for h in ("small", "medium", "large")]
          == sorted(page.index('data-head="%s"' % h) for h in ("small", "medium", "large")))
    check("the controls are labelled and ordered as specified",
          page.index("<label>from side</label>") < page.index('id="ck-from-side"')
          < page.index("<label>to side</label>") < page.index('id="ck-to-side"')
          < page.index("<label>head size</label>") < page.index('id="ck-head-size"'))
    editor_js = (ROOT / "vendor" / "board_editor.js").read_text(encoding="utf-8")
    check('the editor selftest gains an "attach" step', '["attach",' in editor_js)
    check("the editor binds all three new panel controls (the head size as a button group, Y2)",
          all(f'bind("ck-{name}"' in editor_js for name in ("from-side", "to-side"))
          and 'ck-head-size' in editor_js and "pressGroup(" in editor_js)

    print("v1.4 tables (X3)")

    def table_board(**node_kw):
        node = {"id": "t", "x": 0, "y": 0, "w": 0, "h": 0, "text": "not drawn",
                "sub": "also not drawn", "kind": "table", "tone": "accent"}
        node.update(node_kw)
        return render(boardkit.board(nodes=[node]))

    fixture14 = boardkit.load(str(ROOT / "docs" / "fixture-v1.4.json"))
    svg14 = render(fixture14)
    tbl = group_of(svg14, "tbl")
    horiz = [int(m.group(2)) for m in re.finditer(r'<path d="M (\d+),(\d+) H (\d+)"', tbl)]
    vert = [int(m.group(1)) for m in re.finditer(r'<path d="M (\d+),(\d+) V (\d+)"', tbl)]
    check("X3.1: 'table' is a kind", "table" in boardkit.KINDS)
    # Y4.6 (v1.4 gate round 1): `rows` counts EVERY row and row 0 IS the header band, so a 3-row
    # header table is 28 + 24*2 = 76 tall with interior boundaries at 28 and 52.
    check("Y4.6: the 3x2 header table auto-sizes to 180x76",
          'width="180" height="76"' in tbl)
    check("Y4.6: three divider paths — two interior boundaries and one vertical",
          tbl.count("<path ") == 3)
    check("Y4.6: interior boundaries at 28 and 52; the vertical spans the height",
          sorted(horiz) == [28, 52] and vert == [90], f"horiz={horiz} vert={vert}")
    check("X3.2: an empty cell is an empty compartment — five cells, five texts",
          tbl.count("<text") == 5)
    check("X3.2: the table's own text and sub are NOT drawn",
          "not drawn" not in svg14 and "also not drawn" not in svg14)
    check("X3.1: the header row is bold, the body is the split-box 600",
          'font-weight="700"' in tbl and 'font-weight="600"' in tbl)
    check("Y4.6: a header cell's text sits inside row 0's own band (y = 19), not under an empty one",
          bool(re.search(r'<text[^>]*y="19(\.\d+)?"[^>]*font-weight="700"', tbl)))
    tbl2 = group_of(svg14, "tbl2")
    check("X3.1: an absent grid spec is the 2x2 default (two dividers, 48 high)",
          tbl2.count("<path ") == 2 and 'd="M 0,24 H 180"' in tbl2 and 'd="M 90,0 V 48"' in tbl2
          and 'width="180" height="48"' in tbl2)
    narrow = table_board(rows=2, cols=2, col_widths=[2, 1],
                         cells=["a", {"text": "L", "align": "left"}, "b", {"text": "C", "align": "center"}])
    check("Y4.5: a cell's text is placed inside ITS OWN column, not an equal-width one",
          re.search(r'<text x="126"[^>]*text-anchor="start"[^>]*>L</text>', narrow) is not None and
          re.search(r'<text x="150"[^>]*text-anchor="middle"[^>]*>C</text>', narrow) is not None,
          "in the 60-wide column left is 126 and centre is 150; an equal-width formula gives 96 and 135")
    taller = table_board(rows=3, cols=2, header=True, w=180, h=152, cells=["a", "b", "c", "d", "e", "f"])
    check("Y4.6: an explicitly taller table fills its box — the bands scale, nothing is orphaned",
          'height="152"' in taller and 'd="M 0,56 H 180"' in taller and 'd="M 0,104 H 180"' in taller,
          "h=152 scales the nominal 28/24/24 by 2 -> boundaries at 56 and 104, last row ends at 152")
    explicit = table_board(rows=1, cols=3, w=200, h=90, cells=["a", "b", "c"])
    check("X3.3: an explicit positive w/h is respected, dividers follow the real cell size",
          'width="200" height="90"' in explicit and explicit.count('V 90') == 2
          and '<path d="M 66.6667,0 V 90"' in explicit)
    check("X3.1: unusable rows/cols fall back to the 2x2 default",
          'width="180" height="48"' in table_board(rows="nope", cols=0, cells=["a"]))
    extra = table_board(rows=2, cols=2, cells=["a", "b", "c", "d", "extra"])
    check("X3.1: cells normalise to exactly rows*cols — extras drop, the empty tail pads",
          extra.count("<text") == 4 and "extra" not in extra)
    check("X3.1: a short cell list pads with empty compartments",
          table_board(rows=2, cols=2, cells=["only"]).count("<text") == 1)
    mini = {"nodes": [{"id": "t", "x": 60, "y": 60, "w": 0, "h": 0, "text": "", "kind": "table",
                       "tone": "default", "rows": 3, "cols": 2, "header": True,
                       "cells": ["a", "b", "c", "d", "e", "f"]},
                      {"id": "b", "x": 460, "y": 260, "w": 150, "h": 80, "text": "b",
                       "kind": "box", "tone": "default"}],
            "edges": [{"from": "t", "to": "b", "from_side": "bottom", "to_side": "top"}]}
    mseg = re.search(r'<g class="ck-edge".*?</g>', render(mini), re.S).group(0)
    mpts = re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", mseg)
    check("Y4.6: edge anchors use the effective size (bottom centre of the auto 180x76)",
          mpts and mpts[0] == ("150.0", "136.0"), str(mpts[:1]))

    print("v1.4 text (X4)")
    sized = {"id": "s", "x": 0, "y": 0, "w": 300, "h": 60, "text": "some words", "kind": "box",
             "tone": "default"}
    check("X4.2: small is 10, large is 17",
          'font-size="10"' in render(boardkit.board(nodes=[dict(sized, text_size="small")]))
          and 'font-size="17"' in render(boardkit.board(nodes=[dict(sized, text_size="large")])))
    check("X4.3: bold turns the body's 600 into 700",
          'font-weight="700"' in render(boardkit.board(nodes=[dict(sized, bold=True)])))
    icon = {"nodes": [{"id": "i", "x": 0, "y": 0, "w": 120, "h": 110, "text": "cap",
                       "kind": "server", "tone": "default"}]}
    check("X4.3: an icon caption emits 400 today and bold overrides it to 700",
          'font-weight="400"' in render(icon)
          and 'font-weight="700"' in render({"nodes": [dict(icon["nodes"][0], bold=True)]}))
    cap = {"nodes": [{"id": "c", "x": 0, "y": 0, "w": 160, "h": 40, "text": "cap text",
                      "kind": "caption", "tone": "default"}]}
    check("X4.3: the caption kind emits no weight today; bold adds 700",
          'font-weight' not in render(cap)
          and 'font-weight="700"' in render({"nodes": [dict(cap["nodes"][0], bold=True)]}))
    zone = {"id": "z", "x": 0, "y": 0, "w": 240, "h": 120, "text": "zone", "kind": "area",
            "tone": "default"}
    check("X4.1: the area title follows (11/600 defaults; large gives 17)",
          'font-size="11" font-weight="600"' in render(boardkit.board(nodes=[zone]))
          and 'font-size="17"' in render(boardkit.board(nodes=[dict(zone, text_size="large")])))
    split = {"id": "sp", "x": 0, "y": 0, "w": 320, "h": 140, "text": "hdr", "kind": "box",
             "tone": "default", "parts": [{"text": "part one"}], "split": "rows"}
    sp_large = render(boardkit.board(nodes=[dict(split, text_size="large", bold=True)]))
    sp_plain = render(boardkit.board(nodes=[split]))
    check("X4.1: the split-box header follows text_size/bold",
          'font-size="17" font-weight="700"' in sp_large)
    check("X4.1: a part's text does NOT follow text_size/bold (stays 12.5/600)",
          'font-size="12.5" font-weight="600"' in sp_large and 'font-size="17"' not in
          group_of(sp_large, "sp").split('font-weight="700"')[-1])
    check("X4.1: `sub` does not follow text_size/bold (stays 10.5, no weight)",
          'font-size="10.5"' in render(boardkit.board(nodes=[dict(sized, sub="a sub",
                                                                  text_size="large", bold=True)]))
          and 'font-weight="700"' not in
          render(boardkit.board(nodes=[dict(sized, sub="a sub")])))
    tbl_styled = table_board(rows=2, cols=2, header=True, cells=["a", "b", "c", "d"],
                             text_size="large", bold=True)
    check("X4.1: table cells do NOT follow text_size/bold (cells stay 12.5)",
          'font-size="17"' not in tbl_styled and 'font-size="12.5"' in tbl_styled)
    wrap_probe = {"id": "wp", "x": 0, "y": 0, "w": 200, "h": 90,
                  "text": "aaaa bbbb cccc ddd", "kind": "box", "tone": "default"}
    check("X4.4: the wrap uses the effective size (one line at default, more at large)",
          render(boardkit.board(nodes=[wrap_probe])).count("<text") == 1
          and render(boardkit.board(nodes=[dict(wrap_probe, text_size="large")])).count("<text") > 1)
    plain14 = render(boardkit.board(nodes=[dict(sized)]))
    check("X4.4: spelled-out defaults stay byte-identical to the absent fields",
          plain14 == render(boardkit.board(nodes=[dict(sized, text_size="default", bold=False)])))
    unknown14 = render(boardkit.board(nodes=[dict(sized, text_size="huge")]))
    check("X4.4: an unknown text_size renders exactly like the default, never raw",
          unknown14 == plain14 and "huge" not in unknown14)
    check("X4.4: bold:false stays byte-identical to bold absent",
          plain14 == render(boardkit.board(nodes=[dict(sized, bold=False)])))

    print("v1.4 fixture")
    check("the v1.4 fixture renders every node and edge",
          svg14.count('<g class="ck-node"') == len(fixture14["nodes"])
          and svg14.count('<g class="ck-edge"') == len(fixture14["edges"]))
    check("the v1.4 render is deterministic", render(fixture14) == svg14)
    check("the fixture's tbl carries its cells",
          all(word in tbl for word in ("name", "value", "38%")))

    print("v1.5 tables: per-column widths (Y4.2)")
    wide = group_of(table_board(rows=1, cols=2, cells=["a", "b"], col_widths=[2, 1]), "t")
    check("Y4.2: [2,1] on a 180-wide table puts the divider at 120 and the texts at 60/150",
          'd="M 120,0 V 24"' in wide and '<text x="60"' in wide and '<text x="150"' in wide,
          wide)
    check("Y4.2: [1,2] puts the divider at 60",
          'd="M 60,0 V 24"' in group_of(table_board(rows=1, cols=2, cells=["a", "b"],
                                                    col_widths=[1, 2]), "t"))
    check("Y4.2: non-positive/non-numeric entries and a missing tail all normalise to 1",
          'd="M 90,0 V 24"' in table_board(rows=1, cols=2, cells=["a", "b"], col_widths=[0, "x"])
          and 'd="M 90,0 V 72"' in table_board(rows=1, cols=3, w=270, h=72,
                                               cells=["a", "b", "c"], col_widths=[-2]))
    check("Y4.2: extra widths drop",
          'd="M 120,0 V 24"' in table_board(rows=1, cols=2, cells=["a", "b"],
                                            col_widths=[2, 1, 99]))
    narrow = group_of(table_board(rows=1, cols=2, cells=["aaaaaaaaaaaaa", "b"],
                                  col_widths=[1, 3]), "t")
    even = group_of(table_board(rows=1, cols=2, cells=["aaaaaaaaaaaaa", "b"]), "t")
    spill = table_board(rows=2, cols=2, w=180, cells=["long label", "other", "", ""],
                         col_widths=[0.1, 100])
    check("Y4.2/Y4.5: an extreme share clips the cell's text instead of spilling into the next column",
          '<clipPath id="tclip-t-0">' in spill and 'clip-path="url(#tclip-t-0)"' in spill
          and "clipPath" not in table_board(rows=2, cols=2, cells=["a", "b", "c", "d"]),
          spill[:0])
    check("Y4.2: the wrap width follows the column's own width (3 lines at w=45, 2 at w=90)",
          narrow.count("<text") == 4 and even.count("<text") == 3,
          f"narrow={narrow.count('<text')} even={even.count('<text')}")
    # Y4.2/Y4.1a (v1.5 verifier ROUND 2, CRITICAL): the RENDERER'S OWN reader — not `store`'s and not
    # the ops' — has to survive a width an import can carry. A document may hold the STRING
    # `"Infinity"` (or a NaN), which passes `> 0` and makes the column sum infinite (every column
    # then resolves to NaN and the text dies with "cannot convert float NaN to integer"); and a pair
    # of large FINITE shares (1e308, 1e308) overflows its own sum, so the ratio collapses and the
    # divider lands at x = 0. Both must read as the 1-share table they are: divider at 90, texts at
    # 45/135 — the reader normalises a non-finite share to 1 and divides by the largest share before
    # summing, which no finite input can overflow.
    def reader_case(widths):
        return table_board(rows=2, cols=2, cells=["long label", "other", "", ""], col_widths=widths)

    for label, widths, why in (('the string "Infinity" a JSON import can carry', ["Infinity", 1],
                                "float('Infinity') is inf: it passes `> 0` and the sum goes infinite"),
                               ("a huge but finite pair [1e308, 1e308]", [1e308, 1e308],
                                "1e308 + 1e308 is inf: the ratio collapses to 0 or NaN"),
                               ("a 310-digit JSON integer", [10 ** 309, 1],
                                "float(10**309) is not a double at all — the conversion itself raises "
                                "OverflowError (v1.5 verifier round 3)")):
        imported = reader_case(widths)
        marks = [m.group(0) for m in re.finditer(r'<path d="[^"]*"|<text x="[^"]*"',
                                                 group_of(imported, "t"))][:4]
        check(f"Y4.2: a column width that would break the sum reads as 1 — {label} (Y4.1a)",
              'd="M 90,0 V 48"' in imported and '<text x="45"' in imported
              and '<text x="135"' in imported and 'd="M 0,0 V 48"' not in imported
              and "nan" not in imported.lower() and "infinity" not in imported.lower(),
              f"{why}; got {marks}")
    # ...and the same width through a REAL JSON round trip, which is the shape an imported document
    # actually reaches the renderer in (`json.dumps` writes the 310-digit literal, `json.loads` gives
    # the int back). The editor's `JSON.parse` reads that literal as `Infinity` and its own reader
    # normalises it to 1; this asserts the Python half independently of the editor half.
    round_trip = json.loads(json.dumps(boardkit.board(nodes=[
        {"id": "t", "kind": "table", "x": 0, "y": 0, "w": 180, "h": 48, "tone": "default",
         "rows": 2, "cols": 2, "col_widths": [10 ** 309, 1], "cells": ["long label", "other", "", ""]}])))
    tripped = group_of(render(round_trip), "t")
    check("Y4.2: the 310-digit width still reads as 1 after a json.dumps/json.loads round trip (Y4.1a)",
          'd="M 90,0 V 48"' in tripped and '<text x="45"' in tripped and '<text x="135"' in tripped
          and "nan" not in tripped.lower(), tripped[:200])
    # ...and the same document through the OTHER reader path — `boardkit.table(...)` runs the same
    # normalisation — so the builder cannot hand a caller a share the renderer would choke on.
    built_bad = boardkit.table("w", 0, 0, 2, 2, ["a", "b", "c", "d"], col_widths=["Infinity", 1])
    check('Y4.2: boardkit.table(...) normalises an imported non-finite share to 1 too (Y6)',
          built_bad["col_widths"] == [1.0, 1.0], str(built_bad.get("col_widths")))
    legacy_fits = reader_case([1, 1])
    check("Y4.2: a table that fits carries no clip bytes (the v1.4 identity argument)",
          "clipPath" not in legacy_fits and "clip-path" not in legacy_fits,
          legacy_fits[:0])

    print("v1.5 tables: object cells (Y4.1)")
    obj = group_of(table_board(rows=1, cols=2, tone="default",
                               cells=["a", {"text": "b", "tone": "warn", "align": "right",
                                            "surplus": 1}]), "t")
    check("Y4.1: an object cell renders its text with its tone and align, surplus keys ignored",
          ">b</text>" in obj and 'fill="var(--ck-tone-warn-fill)"' in obj
          and 'text-anchor="end"' in obj, obj)
    check("Y4.1: a non-string text is stringified",
          ">42</text>" in group_of(table_board(rows=1, cols=2, cells=[{"text": 42}, "b"]), "t"))
    unknown = group_of(table_board(rows=1, cols=2, tone="default",
                                   cells=[{"text": "a", "tone": "bogus", "align": "sideways"},
                                          "b"]), "t")
    check("Y4.1: an unknown tone/align is ignored — no fill, no extra rect, centred like v1.4",
          unknown.count("<rect") == 1 and 'rx="9"' in unknown and "bogus" not in unknown
          and 'text-anchor="middle"' in unknown
          and 'text-anchor="start"' not in unknown and 'text-anchor="end"' not in unknown,
          unknown)
    empty_obj = group_of(table_board(rows=1, cols=2, tone="default",
                                     cells=[{"text": "", "tone": "warn"}, "b"]), "t")
    check("Y4.1: an object cell with empty text paints its tone but emits no <text>",
          empty_obj.count("<rect") == 2 and empty_obj.count("<text") == 1
          and 'fill="var(--ck-tone-warn-fill)"' in empty_obj, empty_obj)

    print("v1.5 tables: tone precedence (Y4.3) and painting (Y4.4)")
    col_only = group_of(table_board(rows=1, cols=2, tone="default", cells=["a", "b"],
                                    col_tones=[None, "accent"]), "t")
    check("Y4.3: a column tone paints that column's cell over its own cell box",
          '<rect x="90" y="0" width="90" height="24" fill="var(--ck-tone-accent-fill)"/>'
          in col_only and col_only.count('fill="var(--ck-tone-accent-fill)"') == 1, col_only)
    row_over = group_of(table_board(rows=1, cols=2, tone="default", cells=["a", "b"],
                                    row_tones=["warn"], col_tones=["accent", "accent"]), "t")
    check("Y4.3: a row tone beats the column tone",
          row_over.count('fill="var(--ck-tone-warn-fill)"') == 2
          and "accent-fill" not in row_over, row_over)
    cell_over = group_of(table_board(rows=1, cols=2, tone="default",
                                     cells=["a", {"text": "b", "tone": "danger"}],
                                     row_tones=["warn"]), "t")
    check("Y4.3: the cell's own tone beats the row's",
          cell_over.count('fill="var(--ck-tone-warn-fill)"') == 1
          and cell_over.count('fill="var(--ck-tone-danger-fill)"') == 1, cell_over)
    same = group_of(table_board(rows=1, cols=2, tone="warn",
                                cells=[{"text": "a", "tone": "warn"}, "b"]), "t")
    check("Y4.3/Y4.4: a cell tone equal to the table's paints nothing and keeps rx=9",
          same.count("<rect") == 1 and 'rx="9"' in same, same)
    check("Y4.4: a no-colour table keeps rx=9 exactly as v1.4 (the whole v1.4 fixture)",
          'rx="9"' in tbl and 'rx="0"' not in svg14)
    check("Y4.4: a table that paints a cell/row/column tone squares its base rect",
          'rx="0"' in obj and 'rx="0"' in row_over and 'rx="0"' in col_only)
    ordered = group_of(table_board(rows=1, cols=2, tone="default",
                                   cells=["a", {"text": "b", "tone": "warn"}]), "t")
    check("Y4.4: the cell fill comes after the base rect and before the dividers and texts",
          ordered.index('rx="0"') < ordered.index('fill="var(--ck-tone-warn-fill)"')
          < ordered.index("<path ") < ordered.index("<text"), ordered)

    print("v1.5 tables: per-cell alignment (Y4.5)")
    left_cells = group_of(table_board(rows=1, cols=2,
                                      cells=[{"text": "a", "align": "left"}, "b"]), "t")
    check("Y4.5: align left anchors at the cell's left edge + 6",
          '<text x="6"' in left_cells and 'text-anchor="start"' in left_cells
          and '<text x="135"' in left_cells, left_cells)
    right_cells = group_of(table_board(rows=1, cols=2,
                                       cells=[{"text": "a", "align": "right"}, "b"]), "t")
    check("Y4.5: align right anchors at the cell's right edge - 6",
          '<text x="84"' in right_cells and 'text-anchor="end"' in right_cells, right_cells)
    tab_left = group_of(table_board(rows=1, cols=2, align="left", cells=["a", "b"]), "t")
    check("Y4.5: the table's own align applies to every cell without one",
          '<text x="6"' in tab_left and '<text x="96"' in tab_left
          and 'text-anchor="middle"' not in tab_left, tab_left)
    cell_wins = group_of(table_board(rows=1, cols=2, align="left",
                                     cells=["a", {"text": "b", "align": "right"}]), "t")
    check("Y4.5: the cell's align beats the table's",
          '<text x="6"' in cell_wins and '<text x="174"' in cell_wins
          and 'text-anchor="end"' in cell_wins, cell_wins)
    head_right = group_of(table_board(rows=2, cols=1, header=True,
                                      cells=[{"text": "h", "align": "right"}, "b"]), "t")
    check("Y4.5: a header cell honours align and keeps the 700 weight",
          'text-anchor="end"' in head_right and 'font-weight="700"' in head_right, head_right)
    check("Y4.5: an unknown table align falls back to center, byte-identical to absent",
          table_board(rows=1, cols=2, align="sideways", cells=["a", "b"])
          == table_board(rows=1, cols=2, cells=["a", "b"]))
    check("Y4.5: align center spelled out is byte-identical to absent (v1.4 behaviour)",
          table_board(rows=1, cols=2, align="center", cells=["a", "b"])
          == table_board(rows=1, cols=2, cells=["a", "b"]))

    print("v1.5 tables: v1.4 byte-identity and the v1.5 fixture")
    plain_t = group_of(table_board(rows=2, cols=2, header=True, cells=["a", "b", "c", "d"]), "t")
    check("Y4: a v1.4-shaped table emits one rx=9 rect, two dividers, four centred texts",
          plain_t.count("<rect") == 1 and 'rx="9"' in plain_t and plain_t.count("<path ") == 2
          and plain_t.count('text-anchor="middle"') == 4
          and 'text-anchor="start"' not in plain_t and 'text-anchor="end"' not in plain_t,
          plain_t)
    svg15 = render(boardkit.load(str(ROOT / "docs" / "fixture-v1.5.json")))
    t2 = group_of(svg15, "t2")
    check("Y4: t2 paints four cells (accent column, muted row x2, warn cell) and squares the base",
          t2.count("<rect") == 5 and 'rx="0"' in t2
          and t2.count('fill="var(--ck-tone-accent-fill)"') == 1
          and t2.count('fill="var(--ck-tone-muted-fill)"') == 2
          and t2.count('fill="var(--ck-tone-warn-fill)"') == 1, t2)
    check("Y4.2/Y4.5: t2 follows the [2,1] widths (divider at 120) and right-aligns the warn cell",
          'd="M 120,0 V 76"' in t2 and '<text x="174"' in t2 and 'text-anchor="end"' in t2, t2)
    check("Y4.6: t2 (header, 3 rows) is 76 tall with interior boundaries at 28 and 52",
          'height="76"' in t2 and 'd="M 0,28 H 180"' in t2 and 'd="M 0,52 H 180"' in t2, t2)
    t3 = group_of(svg15, "t3")
    check("Y4.2: t3's [1,2,1] widths put the dividers at 67.5 and 202.5 and keep rx=9",
          'd="M 67.5,0 V 48"' in t3 and 'd="M 202.5,0 V 48"' in t3 and 'rx="9"' in t3
          and t3.count("<rect") == 1, t3)
    t1g = group_of(svg15, "t1")
    check("Y4: t1 (no new fields) stays a v1.4 table — one rx=9 rect, one centred text",
          t1g.count("<rect") == 1 and 'rx="9"' in t1g and t1g.count("<text") == 1
          and 'text-anchor="middle"' in t1g, t1g)

    print("v1.5 notes: text formatting (Y4.7)")
    note14 = {"id": "nt", "x": 40, "y": 300, "w": 300, "h": 60, "text": "a note"}
    note_plain = render(boardkit.board(notes=[note14]))
    check("Y4.7: a note without text_size/bold is byte-identical to v1.4 (12.5, no weight)",
          'font-size="12.5" fill=' in note_plain and "font-weight" not in note_plain)
    check("Y4.7: spelled-out defaults stay byte-identical",
          note_plain == render(boardkit.board(notes=[dict(note14, text_size="default",
                                                          bold=False)])))
    check("Y4.7: a note honours text_size small/large",
          'font-size="10"' in render(boardkit.board(notes=[dict(note14, text_size="small")]))
          and 'font-size="17"' in render(boardkit.board(notes=[dict(note14, text_size="large")])))
    check("Y4.7: a note honours bold (adds the 700 attribute)",
          'font-weight="700"' in render(boardkit.board(notes=[dict(note14, bold=True)])))
    check("Y4.7: an unknown text_size renders like the default, never raw",
          render(boardkit.board(notes=[dict(note14, text_size="huge")])) == note_plain)
    note_group = svg[svg.index('<g class="ck-note"'):]
    note_group = note_group[:note_group.index("</g>")]
    check("Y4.7: the v1.2 fixture's notes stay weight-less (byte-identity)",
          "font-weight" not in note_group and 'font-size="12.5"' in note_group)

    # ----------------------------------------------------------------- v1.6: lines (Z3)
    print("v1.6 lines: the factory (Z3)")
    ln = boardkit.line([[10, 20], [30, 40]])
    check("Z3: line() writes the doc shape with the edge vocabulary defaults",
          ln["id"].startswith("line-") and len(ln["id"]) == 13
          and ln["points"] == [[10.0, 20.0], [30.0, 40.0]]
          and ln["tone"] == "default" and ln["style"] == "solid"
          and ln["start"] == "none" and ln["end"] == "arrow"
          and "from" not in ln and "to" not in ln, json.dumps(ln))
    ln2 = boardkit.line([[1, 2], [3, 4]], lid="mine", tone="ok", style="dashed",
                        start="circle", end="triangle",
                        from_anchor={"ref": "n1", "side": "left"})
    check("Z3: line() honours the vocabulary and carries the anchor verbatim",
          ln2["id"] == "mine" and ln2["tone"] == "ok" and ln2["style"] == "dashed"
          and ln2["start"] == "circle" and ln2["end"] == "triangle"
          and ln2["from"] == {"ref": "n1", "side": "left"}, json.dumps(ln2))
    check("Z3: line() falls back to the defaults on unknown enum values",
          boardkit.line([[0, 0], [1, 1]], tone="hotpink", style="wavy", start="fork",
                        end="spoon")
          == boardkit.line([[0, 0], [1, 1]]))
    for bad in ([[10, 20]], [[10, 20], [30]], [[10, -5], [30, 40]],
                [[10, 20], [float("nan"), 0]], "not-a-list"):
        try:
            boardkit.line(bad)
            check(f"Z2 ops layer: line() raises naming points for {bad!r}", False, "no raise")
        except ValueError as exc:
            check(f"Z2 ops layer: line() raises naming points for {str(bad)[:24]}",
                  "points" in str(exc), str(exc))

    print("v1.6 lines: the reader's lenient half (Z2/Z3.1)")
    base16 = {"width": 400, "height": 300, "nodes": [
        {"id": "a", "x": 40, "y": 40, "w": 100, "h": 50, "text": "a"}]}
    dropped = [None, "x", {"points": "nope"}, {"points": [[1, 2]]},
               {"points": [[1, 2], [3]]}, {"points": [[1, 2], [float("inf"), 4]]},
               {"points": [[1, 2], ["x", 4]]}, {"points": [[1, 2, 3], [4, 5]]}]
    svg_drop = render(dict(base16, lines=dropped))
    check("Z2: an unusable points list drops the whole item, the board still renders",
          'class="ck-line"' not in svg_drop and 'data-id="a"' in svg_drop)
    # Round-1 finding 3: the reader validates the JSON TYPES — a null, a numeric string or a
    # boolean coordinate drops the whole item in BOTH readers (the editor applies the same
    # typeof rule); it is never coerced to 0/12/1.
    coerced = [{"id": "c-null", "points": [[None, 20], [30, 40]]},
               {"id": "c-str", "points": [["12", "20"], [30, 40]]},
               {"id": "c-bool", "points": [[True, 20], [30, 40]]},
               {"id": "c-bool2", "points": [[10, 20], [False, 40]]}]
    svg_coerced = render(dict(base16, lines=coerced))
    check("Z2: a null/string/boolean coordinate DROPS the item — the reader never coerces",
          'class="ck-line"' not in svg_coerced and 'data-id="a"' in svg_coerced,
          svg_coerced[-400:])
    svg_lenient = render(dict(base16, lines=[
        {"id": "neg", "points": [[-40, 25], [120, 25]]},
        {"id": "weird", "points": [[10, 200], [60, 200]], "tone": "hotpink",
         "style": "wavy", "start": "fork", "end": "spoon"}]))
    check("Z2: a negative coordinate clamps to 0, never re-rounded",
          'data-id="neg"' in svg_lenient and 'points="0.0,25.0 120.0,25.0"' in svg_lenient,
          svg_lenient[svg_lenient.find('data-id="neg"'):][:200])
    check("Z2: unknown tone/style/start/end become the defaults (default/solid/none/arrow)",
          'stroke="var(--ck-edge-default)"' in svg_lenient
          and 'data-id="weird"' in svg_lenient
          and "stroke-dasharray" not in svg_lenient
          and 'marker-end="url(#ck-head-default-arrow)"' in svg_lenient
          and "marker-start" not in svg_lenient, svg_lenient[-500:])

    print("v1.6 lines: anchors (Z3.3/Z3.4)")
    anchored = dict(base16, notes=[{"id": "nt", "x": 200, "y": 180, "w": 300, "h": 60,
                                    "text": "n", "kind": "note"}], lines=[
        {"id": "L1", "points": [[0, 0], [300, 300]],
         "from": {"ref": "a", "side": "right"}, "to": {"ref": "nt", "side": "top"}}])
    svg_a = render(anchored)
    # node a's right mid (140, 65); the note's top mid (350, 180) — a NOTES entry resolves.
    check("Z3.3: a resolvable anchor replaces its end (explicit side, node and note)",
          'points="140.0,65.0 350.0,180.0"' in svg_a, svg_a[svg_a.find('ck-line'):][:200])
    auto_pts = boardkit.anchors((40.0, 40.0, 100.0, 50.0), (300.0, 300.0, 0, 0), "auto", "auto")[0]
    svg_auto = render(dict(base16, lines=[{"id": "L2", "points": [[300, 300], [10, 10]],
                                           "from": {"ref": "a", "side": "auto"}}]))
    check("Z3.3: an auto end resolves through the SAME resolver as the edges (zero-size rect)",
          f'points="{auto_pts[0]:.1f},{auto_pts[1]:.1f} 10.0,10.0"' in svg_auto,
          svg_auto[svg_auto.find('ck-line'):][:200])
    svg_fallback = render(dict(base16, lines=[
        {"id": "L3", "points": [[80, 250], [220, 260]],
         "from": {"ref": "ghost", "side": "left"}},
        {"id": "L4", "points": [[90, 260], [230, 270]],
         "from": "a", "to": {"side": "top"}}]))
    check("Z3.4: a dangling or malformed anchor keeps the stored point — the line still draws",
          'points="80.0,250.0 220.0,260.0"' in svg_fallback
          and 'points="90.0,260.0 230.0,270.0"' in svg_fallback, svg_fallback[-600:])
    svg_bad_edge = render(dict(base16, edges=[{"from": "a", "to": "ghost"}],
                               lines=[{"id": "L5", "points": [[5, 5], [6, 6]],
                                       "from": {"ref": "ghost"}}]))
    check("Z3.4/Z8: a bad ref still vanishes an EDGE, but only falls back for a line",
          'class="ck-edge"' not in svg_bad_edge and 'data-id="L5"' in svg_bad_edge)

    print("v1.6 lines: bounds and counts (Z3.5)")
    far = dict(base16, lines=[{"id": "far", "points": [[800, 700], [900, 750]]}])
    check("Z3.5: bounds() includes every line point",
          boardkit.bounds(far) == (940.0, 790.0), str(boardkit.bounds(far)))
    check("Z3.5: the viewBox grows for a line outside the boxes",
          'viewBox="0 0 940 790"' in render(far))
    out_of_the_way = dict(base16, lines=[{"id": "d", "points": [[900, 900], [1, 1]],
                                           "from": {"ref": "a", "side": "left"}}])
    check("Z3.5: bounds use the RESOLVED anchor — the stored (900, 900) end contributes nothing",
          boardkit.bounds(out_of_the_way) == (320.0, 200.0),
          str(boardkit.bounds(out_of_the_way)))
    page_doc = boardkit.page(dict(base16, lines=[ln]))
    counts = boardkit.verify(page_doc)
    check("Z3.5: verify() reports lines alongside nodes/edges",
          counts.get("lines") == 1 and counts.get("nodes") == 1 and counts.get("edges") == 0,
          str(counts))

    print("v1.6 lines: the fixture (Z10)")
    fx16 = boardkit.load(str(ROOT / "docs" / "fixture-v1.6.json"))
    svg16 = render(fx16)
    check("Z10: all six fixture lines draw, after the notes",
          svg16.count('class="ck-line"') == 6
          and svg16.index('class="ck-line"') > svg16.index('class="ck-note"'))
    check("Z10: the note-anchored line resolves identically on both ends",
          'data-id="line-note"' in svg16
          and 'points="260.0,536.0 420.0,530.0"' in svg16)
    check("Z10: the table anchor resolves through the effective table box",
          'points="720.0,106.0 640.0,300.0"' in svg16)
    check("Z10: dangling and malformed anchors keep their stored points",
          'points="80.0,400.0 170.0,152.0"' in svg16
          and 'points="500.0,460.0 560.0,500.0"' in svg16)
    check("Z10: the line's heads sit in the medium box (no sized markers for lines)",
          'marker-start="url(#ck-head-danger-bar)"' in svg16
          and "ck-head-danger-bar-small" not in svg16)

    print("v1.7 anchors: the fraction along a side (A2/A3, the A8 coordinate gate)")
    # The rect of known geometry: (x=10, y=20, w=200, h=100). The expected points come from the
    # formula A2 PINS, written out here — never from the implementation's own helper, so a wrong
    # walk in both the helper and the test cannot agree by construction.
    rect = (10.0, 20.0, 200.0, 100.0)

    def pinned(rect, side, at):
        x, y, w, h = rect
        if side in ("left", "right"):   # the y walk: from the TOP edge, increasing downward
            return (x if side == "left" else x + w, y + at * h)
        return (x + at * w, y if side == "top" else y + h)   # the x walk: from the LEFT edge

    for side in ("left", "right", "top", "bottom"):
        for at in (0.25, 0.75):
            check(f"A2: side {side} at {at} resolves to the pinned point",
                  boardkit._side_mid(rect, side, at) == pinned(rect, side, at),
                  f"{boardkit._side_mid(rect, side, at)} != {pinned(rect, side, at)}")
    check("A2: an absent at IS the midpoint (every pre-v1.7 document's point)",
          all(boardkit._side_mid(rect, side) == pinned(rect, side, 0.5)
              for side in ("left", "right", "top", "bottom")))
    check("A2: the defaults are byte-identical to the spelled-out midpoint",
          render(boardkit.board(nodes=[dict(A), dict(B)],
                                edges=[{"from": "a", "to": "b"}]))
          == render(boardkit.board(nodes=[dict(A), dict(B)],
                                   edges=[{"from": "a", "to": "b",
                                           "from_at": 0.5, "to_at": 0.5}])))
    # A3's reader table, on the renderer's own read: -3 clamps to 0, JSON 1e400 (+inf) clamps to
    # 1, a string/boolean is IGNORED (0.5), a literal NaN defaults to 0.5, null resets to 0.5.
    inf = json.loads("1e400")
    check("A3 reader: -3 clamps to 0 and 1e400 (+inf) clamps to 1",
          boardkit._at_of(-3) == 0.0 and boardkit._at_of(inf) == 1.0
          and boardkit._side_mid(rect, "left", -3) == pinned(rect, "left", 0)
          and boardkit._side_mid(rect, "top", inf) == pinned(rect, "top", 1))
    check("A3 reader: a string, a boolean, a NaN and null all read as 0.5",
          boardkit._at_of("0.25") == 0.5 and boardkit._at_of(True) == 0.5
          and boardkit._at_of(float("nan")) == 0.5 and boardkit._at_of(None) == 0.5)

    print("v1.7 anchors: resolved endpoints in the rendered SVG (A8)")
    # An EDGE's two ends: a is (0,0 100x50), b is (300,0 100x50). from left@0.25 -> (0, 12.5)
    # by the y walk; to bottom@0.75 -> (375, 50) by the x walk.
    pts = edge_points(route="straight", from_side="left", from_at=0.25,
                      to_side="bottom", to_at=0.75)
    check("A3: an edge's from/to resolve at from_at/to_at (both walks)",
          pts == [(0.0, 12.5), (375.0, 50.0)], str(pts))
    pts = edge_points(route="straight", from_side="right", from_at=0.75)
    check("A3: one positioned end never moves the other end's auto choice",
          pts[0] == (100.0, 37.5) and pts[-1] == (300.0, 25.0), str(pts))
    pts = edge_points(route="straight", from_at=1e400, to_at=-3)
    check("A3: the rendered edge clamps an out-of-range position (1e400 -> 1, -3 -> 0)",
          pts == [(100.0, 50.0), (300.0, 0.0)], str(pts))
    pts = edge_points(route="straight", from_at="half", to_at=True)
    check("A3: the rendered edge IGNORES a non-numeric position (0.5)",
          pts == [(100.0, 25.0), (300.0, 25.0)], str(pts))
    # A LINE's two ends through the anchor objects (A2: that end's `at`).
    line_doc = dict(base16, notes=[{"id": "nt", "x": 200, "y": 180, "w": 300, "h": 60,
                                    "text": "n", "kind": "note"}], lines=[
        {"id": "La", "points": [[0, 0], [300, 300]],
         "from": {"ref": "a", "side": "left", "at": 0.25},
         "to": {"ref": "nt", "side": "right", "at": 0.75}}])
    svg_la = render(line_doc)
    # node a is (40,40 100x50) -> left@0.25 = (40, 52.5); the note's right@0.75 = (500, 225).
    check("A3: a line's from and to resolve at their own at (node and note)",
          'points="40.0,52.5 500.0,225.0"' in svg_la, svg_la[svg_la.find('ck-line'):][:200])
    # An AUTO side keeps its v1.2 choice; only the position moves (the aim point (400, 50) sits
    # clearly right of a's centre (90, 65), so the side is right and at 0.75 the point is
    # (140, 77.5) — NOT the midpoint (140, 65)).
    svg_auto_at = render(dict(base16, lines=[{"id": "Lb", "points": [[400, 50], [10, 10]],
                                              "from": {"ref": "a", "side": "auto",
                                                       "at": 0.75}}]))
    check("A3: an auto side honours a non-midpoint at (0.75, the side choice unchanged)",
          'points="140.0,77.5 10.0,10.0"' in svg_auto_at
          and 'points="140.0,65.0' not in svg_auto_at, svg_auto_at[svg_auto_at.find('ck-line'):][:200])
    # A dangling/malformed anchor still falls back to the stored point, at or no at (A3/Z3.4).
    svg_dangle_at = render(dict(base16, lines=[
        {"id": "Lc", "points": [[80, 250], [220, 260]],
         "from": {"ref": "ghost", "side": "left", "at": 0.25}}]))
    check("A3: a dangling ref with an at keeps the stored point",
          'points="80.0,250.0 220.0,260.0"' in svg_dangle_at, svg_dangle_at[-400:])

    print("v1.7 anchors: the wave fixture (A9)")
    fx17 = boardkit.load(str(ROOT / "docs" / "fixture-v1.7.json"))
    svg17 = render(fx17)
    # box-a (80,80 180x72): left@0.25 = (80, 98). note-1 (420,470 300x60): right@0.75 = (720, 515).
    check("A9: the quarter-anchored lines resolve at the pinned points",
          'points="80.0,98.0 300.0,210.0"' in svg17
          and 'points="700.0,500.0 720.0,515.0"' in svg17, svg17[-800:])
    # box-b (420,80 180x72): the aim point (600,200) sits right of centre -> right@0.75 = (600, 134).
    check("A9: the auto side keeps its v1.2 choice and honours at 0.75",
          'points="600.0,134.0 700.0,330.0"' in svg17, svg17[-800:])
    # bottom@1.4 clamps to 1 -> (260, 152); top@"half" reads 0.5 -> (510, 80).
    check("A9: the clamp and the ignore render exactly as the table says",
          'points="260.0,152.0 240.0,620.0"' in svg17
          and 'points="520.0,600.0 510.0,80.0"' in svg17, svg17[-800:])
    check("A9: the dangling ref keeps its stored points",
          'points="80.0,400.0 220.0,430.0"' in svg17, svg17[-800:])
    # edge box-a -> box-b: bottom@0.25 = (125, 152), left@0.75 = (420, 134), h-elbow at x 272.5.
    check("A9: the positioned edge routes from its resolved anchors",
          'points="125.0,152.0 272.5,152.0 272.5,134.0 420.0,134.0"' in svg17,
          svg17[svg17.find('ck-edge'):][:400])
    # edge box-b -> tbl-1 (an auto pair with positions): tbl-1 is a 3x3 table, effective
    # (760,80 270x72); right@0.75 = (600, 134), left@0.25 = (760, 98), elbow at x 680.
    check("A9: an auto edge honours from_at/to_at (through the effective table box)",
          'points="600.0,134.0 680.0,134.0 680.0,98.0 760.0,98.0"' in svg17,
          svg17[svg17.rfind('ck-edge'):][:400])

    print("v1.7 panel markup: page() emits the rows lane I wires (A6.1/A6.2)")
    page17 = boardkit.page(boardkit.load(str(ROOT / "docs" / "fixture-v1.7.json")))
    for gid in ("ck-line-from-at", "ck-line-to-at", "ck-from-at", "ck-to-at"):
        start = page17.find(f'id="{gid}"')
        check(f"A6: page() emits {gid} with the three pinned presets and a hint",
              start != -1
              and page17[start:start + 700].count('data-at="0.25"') == 1
              and page17[start:start + 700].count('data-at="0.5"') == 1
              and page17[start:start + 700].count('data-at="0.75"') == 1
              and f'data-hint-for="{gid}"' in page17[start:start + 900],
              page17[start:start + 200] if start != -1 else "absent")
    check("A6.1: the line's own from/to side selects are emitted too",
          all(f'id="{sid}"' in page17
              for sid in ("ck-line-from-side", "ck-line-to-side")))
    check("A6.3: every new row starts hidden — the wiring shows it, it is never disabled",
          all(f'id="ck-row-{row}" hidden' in page17
              for row in ("line-from-side", "line-from-at", "line-to-side", "line-to-at",
                          "from-at", "to-at")))
    check("A1: the markup carries no hardcoded colour (var(--ck-…) only)",
          not re.search(r'ck-(?:line-)?(?:from|to)-at[^>]*(?:#[0-9a-fA-F]{3}|rgb\()', page17))

    print("v1.7 byte-identity baseline (docs/baseline-v1.7.json, A0)")
    baseline17 = json.loads((ROOT / "docs" / "baseline-v1.7.json").read_text())["fixtures"]
    check("A0: the baseline pins exactly the six pre-wave documents (the v1.7 fixture stays out)",
          sorted(baseline17) == ["fixture-v1.2.json", "fixture-v1.3.json", "fixture-v1.4.json",
                                 "fixture-v1.5.json", "fixture-v1.6-route.json",
                                 "fixture-v1.6.json"], str(sorted(baseline17)))
    for name, ref in baseline17.items():
        old = render(boardkit.load(str(ROOT / "docs" / name)))
        digest = hashlib.sha256(old.encode("utf-8")).hexdigest()
        check(f"A0 byte-identity: {name} still hashes to the pinned v1.7 python_sha256",
              digest == ref["python_sha256"], digest)

    print("v1.6 byte-identity baseline (docs/baseline-v1.6.json)")
    baseline = json.loads((ROOT / "docs" / "baseline-v1.6.json").read_text())["fixtures"]
    for name, ref in baseline.items():
        old = render(boardkit.load(str(ROOT / "docs" / name)))
        digest = hashlib.sha256(old.encode("utf-8")).hexdigest()
        check(f"byte-identity: {name} still hashes to the pinned python_sha256",
              digest == ref["python_sha256"], digest)

    print("v1.6 parity: render_svg vs the editor DOM (fixture-v1.6)")
    if boardkit.find_chrome():
        ed_spec = importlib.util.spec_from_file_location("test_editor",
                                                         ROOT / "tests" / "test_editor.py")
        test_editor = importlib.util.module_from_spec(ed_spec)
        ed_spec.loader.exec_module(test_editor)
        html_path = boardkit.report(str(pathlib.Path(
            __import__("tempfile").mkdtemp(prefix="v16-parity-")) / "board.html"),
            fx16, bridge=True)
        host = test_editor.dump_host(html_path)
        problems = test_editor.compare(svg16, host)
        check("Z3.6: the Python render and the editor DOM agree element for element",
              not problems, "\n".join(problems[:4]))
    else:
        print("  skip editor parity — no Chrome/Chromium on PATH")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
