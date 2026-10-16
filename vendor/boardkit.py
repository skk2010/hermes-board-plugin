#!/usr/bin/env python3
"""boardkit — a small Miro for Hermes: a board is a JSON document, the agent draws it.

Two renderings come out of the same document:

  * `render_svg(board)` — static inline SVG, the form that survives the chat widget and can
    be delivered as a picture anywhere;
  * `page(board, interactive=True)` — the same picture plus an inline editor (drag nodes,
    edit text, connect, add, delete, export JSON). The editor works wherever inline JS runs
    (desktop preview pane, browser, `::preview` widget) and needs no server.

A board is plain data, so the agent edits it with its own file tools: read the JSON, apply a
change, re-render. The user's edits come back either as the editor's exported JSON or as a
request in words. Both sides stay in sync because the document is the only source of truth.

Shape:

    {
      "title": "…", "width": 900, "height": 460, "grid": 10,
      "groups": [{"id": "g1", "x": 20, "y": 30, "w": 400, "h": 220, "text": "Runtime"}],
      "nodes":  [{"id": "n1", "x": 40, "y": 60, "w": 190, "h": 72, "text": "Hermes chat",
                  "sub": "Telegram / desktop", "kind": "box", "tone": "accent"}],
      "edges":  [{"from": "n1", "to": "n2", "label": "writes", "style": "solid",
                  "route": "auto", "bidir": false, "tone": "default"}],
      "notes":  [{"id": "t1", "x": 40, "y": 300, "w": 300, "h": 60, "text": "…"}],
      "lines":  [{"id": "line-…", "points": [[40, 100], [220, 60]], "tone": "default",
                  "style": "solid", "start": "none", "end": "arrow",
                  "from": {"ref": "n1", "side": "auto"}}]        # Z3: optional
    }

Authoring shortcuts keep placement consistent: `layout_row`, `layout_column`, `layout_grid`
and `flow` (row + edges through the chain) return nodes/edges ready to merge into a board.
"""

from __future__ import annotations

import html
import json
import math
import os
import sys
import zlib

TONES = ("default", "accent", "ok", "warn", "danger", "muted")
KINDS = ("box", "pill", "diamond", "note", "caption", "cloud", "cylinder", "document",
         "hexagon", "parallelogram", "server", "computer", "actor", "area", "table")
HEADS = ("none", "arrow", "open", "triangle", "circle", "diamond", "bar")
SIDES = ("auto", "left", "right", "top", "bottom")      # W3.1: `auto` is the default
HEAD_SIZES = ("small", "medium", "large")               # W3.1: `medium` is the default
CELL_ALIGNS = ("left", "center", "right")   # Y4.1/Y4.5: a table cell's (and table's) align
HEAD_BOX = {"small": 8, "medium": 11, "large": 16}      # W3.3: marker box in user units
ICON_KINDS = ("server", "computer", "actor")
FONT = 12.5
SUB_FONT = 10.5
LINE = 15.0
PAD = 12.0               # inner padding of a node box / band
CHAR_W = 0.60            # rough advance width per character, in font units
TABLE_CELL_W = 90.0      # X3.3: one column's auto width
TABLE_HEAD_H = 28.0      # Y4.6: the height of ROW 0 when it is the header (the split-box head_h)
TABLE_ROW_H = 24.0       # X3.3: one row's auto height
TEXT_SIZE_PX = {"small": 10.0, "large": 17.0}   # X4.2: "default" is each site's own size
EDITOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_editor.js")


# --------------------------------------------------------------------------- authoring


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def box(nid: str, text: str, x: float, y: float, w: float = 190, h: float = 72,
        sub: str | None = None, tone: str = "default", kind: str = "box",
        align: str | None = None, parts=None, split: str | None = None) -> dict:
    node = {"id": nid, "x": x, "y": y, "w": w, "h": h, "text": text,
            "kind": kind if kind in KINDS else "box", "tone": tone if tone in TONES else "default"}
    if sub:
        node["sub"] = sub
    if align == "left":
        node["align"] = "left"
    compartments = _normalise_parts(parts)
    if compartments:
        node["parts"] = compartments
    if split == "columns":
        node["split"] = "columns"
    return node


def note(text: str, x: float, y: float, w: float = 300, tone: str = "warn") -> dict:
    """A free-text note; its height follows the wrapped line count."""
    lines = _wrap(text, w - 20, FONT)
    digest = zlib.crc32(f"{text}|{x}|{y}".encode("utf-8"))
    return {"id": f"note-{digest % 100000}", "x": x, "y": y, "w": w,
            "h": 12 + len(lines) * LINE + 10, "text": text, "kind": "note",
            "tone": tone if tone in TONES else "warn"}


def caption(text: str, x: float, y: float, w: float = 220, tone: str = "muted") -> dict:
    """Free-floating text, no box — the label for a bus or an annotation between boxes."""
    lines = _wrap(text, w, FONT)
    digest = zlib.crc32(f"{text}|{x}|{y}".encode("utf-8"))
    return {"id": f"cap-{digest % 100000}", "x": x, "y": y, "w": w,
            "h": len(lines) * LINE, "text": text, "kind": "caption",
            "tone": tone if tone in TONES else "muted"}


def group(gid: str, text: str, x: float, y: float, w: float, h: float,
          tone: str = "muted") -> dict:
    return {"id": gid, "x": x, "y": y, "w": w, "h": h, "text": text,
            "tone": tone if tone in TONES else "muted"}


def edge(source: str, target: str, label: str | None = None, style: str = "solid",
         route: str = "auto", bidir: bool = False, tone: str = "default",
         waypoints=None, label_at: str | None = None,
         start: str | None = None, end: str | None = None,
         from_side: str | None = None, to_side: str | None = None,
         from_at=None, to_at=None) -> dict:
    item = {"from": source, "to": target, "style": style, "route": route,
            "bidir": bool(bidir), "tone": tone if tone in TONES else "default"}
    if label:
        item["label"] = label
    if waypoints:
        item["waypoints"] = [list(p) for p in waypoints]
    if label_at:
        item["label_at"] = label_at
    if start is not None:
        item["start"] = start
    if end is not None:
        item["end"] = end
    # v1.7 (A3): the factory passes the attach fields through unchanged — the readers clamp
    # or ignore a bad value, the ops layer is the one that validates.
    if from_side is not None:
        item["from_side"] = from_side
    if to_side is not None:
        item["to_side"] = to_side
    if from_at is not None:
        item["from_at"] = from_at
    if to_at is not None:
        item["to_at"] = to_at
    return item


def line(points, *, lid: str | None = None, tone: str = "default", style: str = "solid",
         start: str | None = None, end: str | None = None,
         from_anchor=None, to_anchor=None) -> dict:
    """A free polyline (Z3): the user's own path, optionally anchored at its two ends.

    `points` is the stored fallback path — a list of at least 2 `[x, y]` pairs, finite and
    >= 0; it is what the renderer draws when an end's anchor does not resolve (Z3.4). Unlike
    the other factories this one VALIDATES `points` like the ops do (Z2: raise, naming the
    field) — a half polyline that renders wrong is worse than an error at authoring time.
    `from_anchor`/`to_anchor` are `{"ref": <node-or-note id>, "side": ..., "at": ...}` anchors
    (Z3.3; v1.7's `at` passes through verbatim);
    heads come from the edge vocabulary (Z3.2), `start` "none" / `end` "arrow" by default.
    """
    norm = []
    try:
        entries = list(points)
    except TypeError:
        entries = []
    for p in entries:
        try:
            pair_ok = len(p) == 2
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, OverflowError):
            pair_ok, x, y = False, 0.0, 0.0
        if not pair_ok or not (math.isfinite(x) and math.isfinite(y)) or x < 0 or y < 0:
            raise ValueError(f"points: every entry must be an [x, y] pair, finite and >= 0 "
                             f"(got {p!r})")
        norm.append([x, y])
    if len(norm) < 2:
        raise ValueError(f"points: a line needs at least 2 points (got {len(norm)})")
    digest = zlib.crc32(json.dumps(norm, separators=(",", ":")).encode("utf-8"))
    item = {"id": lid or f"line-{digest % 100000000:08d}", "points": norm,
            "tone": tone if tone in TONES else "default",
            "style": style if style in ("solid", "dashed", "dotted") else "solid",
            "start": start if start in HEADS else "none",
            "end": end if end in HEADS else "arrow"}
    if from_anchor:
        item["from"] = dict(from_anchor)
    if to_anchor:
        item["to"] = dict(to_anchor)
    return item


def layout_row(items, x: float = 40, y: float = 40, gap: float = 60,
               w: float = 190, h: float = 72, **kwargs) -> list[dict]:
    """Place boxes left to right. `items` is [(id, text, sub?), …]."""
    nodes, cursor = [], x
    for item in items:
        nid, text = item[0], item[1]
        sub = item[2] if len(item) > 2 else None
        nodes.append(box(nid, text, cursor, y, w=w, h=h, sub=sub, **kwargs))
        cursor += w + gap
    return nodes


def layout_column(items, x: float = 40, y: float = 40, gap: float = 40,
                  w: float = 200, h: float = 66, **kwargs) -> list[dict]:
    nodes, cursor = [], y
    for item in items:
        nid, text = item[0], item[1]
        sub = item[2] if len(item) > 2 else None
        nodes.append(box(nid, text, x, cursor, w=w, h=h, sub=sub, **kwargs))
        cursor += h + gap
    return nodes


def layout_grid(items, cols: int = 3, x: float = 40, y: float = 40, gap_x: float = 40,
                gap_y: float = 36, w: float = 190, h: float = 72, **kwargs) -> list[dict]:
    nodes = []
    for index, item in enumerate(items):
        nid, text = item[0], item[1]
        sub = item[2] if len(item) > 2 else None
        row, col = divmod(index, max(cols, 1))
        nodes.append(box(nid, text, x + col * (w + gap_x), y + row * (h + gap_y),
                         w=w, h=h, sub=sub, **kwargs))
    return nodes


def flow(steps, direction: str = "right", labels=None, **kwargs):
    """A chain of boxes plus the edges between them → (nodes, edges)."""
    horizontal = direction in ("right", "lr", "h")
    nodes = layout_row(steps, **kwargs) if horizontal else layout_column(steps, **kwargs)
    edges = []
    for i in range(len(nodes) - 1):
        label = labels[i] if labels and i < len(labels) else None
        edges.append(edge(nodes[i]["id"], nodes[i + 1]["id"], label,
                          route="h" if horizontal else "v"))
    return nodes, edges


def place_layers(layers, *, sizes=None, direction: str = "down", x: float = 40, y: float = 40,
                 gap_layer: float = 90, gap_node: float = 40, default_size=(190.0, 72.0),
                 labels=None, bands: bool = True) -> tuple[dict, list[dict], float, float]:
    """Place given layers into bands — the geometry core, honouring per-node sizes.

    `layers` is a list of lists of keys (order inside a layer is the order on screen);
    `sizes` maps key -> (w, h). Returns `(positions, groups, width, height)`.
    """
    vertical = direction in ("down", "tb", "v")
    norm = [[str(key) for key in layer] for layer in layers]
    dims = [[sizes.get(key, default_size) for key in layer] for layer in norm]
    # A layer extends along the row (sum of sizes + gaps) and across it (the largest member);
    # which axis is which flips with the direction, and a band must fit BOTH.
    row_w = [sum(d[0] for d in row) + max(len(row) - 1, 0) * gap_node for row in dims]
    row_h = [sum(d[1] for d in row) + max(len(row) - 1, 0) * gap_node for row in dims]
    span_x = [row_w[i] if vertical else max((d[0] for d in row), default=default_size[0])
              for i, row in enumerate(dims)]
    span_y = [max((d[1] for d in row), default=default_size[1]) if vertical else row_h[i]
              for i, row in enumerate(dims)]
    label_room = 26 if (bands and labels) else 0
    band_across = max(span_x if vertical else span_y) + 2 * PAD + label_room
    positions, groups = {}, []
    cursor = y if vertical else x
    for index, layer in enumerate(norm):
        along = span_y[index] if vertical else span_x[index]
        band_along = along + 2 * PAD + label_room
        if vertical:
            band_w, band_h = band_across, band_along
            offset = x + (band_across - span_x[index]) / 2
            node_y = cursor + label_room + PAD
            for k, key in enumerate(layer):
                positions[key] = (round(offset, 1), round(node_y, 1))
                offset += dims[index][k][0] + gap_node
        else:
            band_w, band_h = band_along, band_across
            node_x = cursor + label_room + PAD
            offset = y + (band_across - span_y[index]) / 2
            for k, key in enumerate(layer):
                positions[key] = (round(node_x, 1), round(offset, 1))
                offset += dims[index][k][1] + gap_node
        if bands:
            text = labels[index] if labels and index < len(labels) else None
            groups.append(group("layer-%d" % (index + 1), text or ("rank %d" % (index + 1)),
                                x if vertical else cursor, cursor if vertical else y,
                                band_w, band_h))
        cursor += band_along + gap_layer
    width = (band_across + x + 40) if vertical else (cursor - gap_layer + 40)
    height = (cursor - gap_layer + 40) if vertical else (band_across + y + 40)
    return positions, groups, width, height


def layer_layout(layers_spec, *, direction: str = "down", x: float = 40, y: float = 40,
                 gap_layer: float = 90, gap_node: float = 40, w: float = 190, h: float = 72,
                 labels=None, bands: bool = True) -> tuple[dict, list[dict], float, float]:
    """Bands for hand-chosen layers: `[[key|(key, text, sub), …], …]` on a uniform grid.

    Use this when the grouping is the point (a layered architecture). For layers derived from
    the edges themselves, call `auto_layout` instead.
    """
    layers = []
    for layer in layers_spec:
        keys = []
        for entry in layer:
            keys.append(str(entry[0]) if isinstance(entry, (list, tuple)) else str(entry))
        layers.append(keys)
    sizes = {key: (w, h) for layer in layers for key in layer}
    return place_layers(layers, sizes=sizes, direction=direction, x=x, y=y, gap_layer=gap_layer,
                        gap_node=gap_node, default_size=(w, h), labels=labels, bands=bands)


def rank_layers(node_ids, edges) -> list[list[str]]:
    """Longest-path ranking of the graph (Sugiyama step 1) with DFS cycle breaking.

    Cycles cannot be layered, so the DFS back edges are dropped for ranking — they still get
    drawn, they just may point "up". Self-loops and edges to unknown nodes are ignored.
    """
    ids = [str(i) for i in node_ids]
    known = set(ids)
    children = {i: [] for i in ids}
    pairs = []
    for e in edges:
        source, target = str(e.get("from")), str(e.get("to"))
        if source in known and target in known and source != target:
            children[source].append(target)
            pairs.append((source, target))
    color = dict.fromkeys(ids, 0)
    back = set()

    def dfs(node):
        color[node] = 1
        for nxt in children[node]:
            if color[nxt] == 1:
                back.add((node, nxt))
            elif color[nxt] == 0:
                dfs(nxt)
        color[node] = 2

    for node in ids:
        if color[node] == 0:
            dfs(node)

    forward = [(s, t) for s, t in pairs if (s, t) not in back]
    out_edges = {i: [] for i in ids}
    indegree = dict.fromkeys(ids, 0)
    for source, target in forward:
        out_edges[source].append(target)
        indegree[target] += 1
    rank = dict.fromkeys(ids, 0)
    queue = [i for i in ids if indegree[i] == 0]
    while queue:                                   # Kahn over the DAG: ranks grow along paths
        node = queue.pop(0)
        for nxt in out_edges[node]:
            rank[nxt] = max(rank[nxt], rank[node] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    layers = [[] for _ in range(max(rank.values(), default=0) + 1)]
    for node in ids:
        layers[rank[node]].append(node)
    return layers


def order_layers(layers, edges, *, sweeps: int = 4) -> list[list[str]]:
    """Barycentre ordering (Sugiyama step 2): sweeps down and up, averaging neighbour positions.

    Fewer crossings is the whole point: an unordered layer puts a node's edges across the
    diagram, an ordered one keeps them near each other.
    """
    up, down = {}, {}
    ids = {node for layer in layers for node in layer}
    for node in ids:
        up[node], down[node] = [], []
    for e in edges:
        source, target = str(e.get("from")), str(e.get("to"))
        if source in ids and target in ids and source != target:
            down[source].append(target)
            up[target].append(source)
    position = {node: i for layer in layers for i, node in enumerate(layer)}
    for sweep in range(max(sweeps, 0)):
        order = range(1, len(layers)) if sweep % 2 == 0 else range(len(layers) - 2, -1, -1)
        neighbours = up if sweep % 2 == 0 else down
        for index in order:
            barycentre = {}
            for i, node in enumerate(layers[index]):
                seen = [position[n] for n in neighbours[node] if n in position]
                barycentre[node] = (sum(seen) / len(seen)) if seen else i
            layers[index] = sorted(layers[index], key=lambda n: (barycentre[n], n))
            for i, node in enumerate(layers[index]):
                position[node] = i
    return layers


def count_crossings(layers, edges) -> int:
    """Edge crossings between adjacent layers — the number ordering is trying to shrink."""
    rank = {node: i for i, layer in enumerate(layers) for node in layer}
    position = {node: i for layer in layers for i, node in enumerate(layer)}
    spans: dict[int, list] = {}
    for e in edges:
        source, target = str(e.get("from")), str(e.get("to"))
        if source in rank and target in rank and rank[source] != rank[target]:
            low, high = sorted((rank[source], rank[target]))
            pair = (position[source], position[target]) if rank[source] < rank[target] \
                else (position[target], position[source])
            spans.setdefault(low, []).append(pair)
    crossings = 0
    for pairs in spans.values():
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                if (pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1]) < 0:
                    crossings += 1
    return crossings


def auto_layout(nodes, edges, *, direction: str = "down", x: float = 40, y: float = 40,
                gap_node: float = 40, gap_layer: float = 90, bands: bool = False, labels=None,
                sweeps: int = 4, route_edges: bool = True,
                skip_kinds=("caption", "note")) -> dict:
    """Sugiyama-lite: derive the layers from the edges, order them, then place the nodes.

    `nodes` are board node dicts (only `id`, `w`, `h`, `kind`, `text` are read), `edges` are
    board edges. Captions and notes are not ranked — they are annotations, not graph nodes.

    Returns `{positions, layers, groups, width, height, crossings, edges, rank}`; the returned
    `edges` carry a per-edge `route` (`v` across ranks, `h` within one) unless `route_edges=False`.
    """
    skip = set(skip_kinds)
    drawable = [n for n in nodes if n.get("kind", "box") not in skip]
    ids = [str(n["id"]) for n in drawable]
    known = set(ids)
    used = [e for e in edges
            if str(e.get("from")) in known and str(e.get("to")) in known]
    layers = order_layers(rank_layers(ids, used), used, sweeps=sweeps)
    sizes = {str(n["id"]): (table_size(n) if n.get("kind") == "table"
                            else (float(n["w"]), float(n["h"]))) for n in drawable}
    positions, groups, width, height = place_layers(
        layers, sizes=sizes, direction=direction, x=x, y=y, gap_node=gap_node,
        gap_layer=gap_layer, labels=labels, bands=bands)
    rank_of = {node: i for i, layer in enumerate(layers) for node in layer}
    vertical = direction in ("down", "tb", "v")
    routed = []
    for e in edges:
        item = dict(e)
        source, target = str(e.get("from")), str(e.get("to"))
        if route_edges and source in rank_of and target in rank_of:
            same = rank_of[source] == rank_of[target]
            item["route"] = ("h" if same else "v") if vertical else ("v" if same else "h")
        routed.append(item)
    return {"positions": positions, "layers": layers, "groups": groups, "width": width,
            "height": height, "crossings": count_crossings(layers, used), "edges": routed,
            "rank": rank_of}


def apply_auto_layout(board: dict, **kwargs) -> dict:
    """Re-place a board with `auto_layout` in one call; returns the layout info."""
    info = auto_layout(board.get("nodes", []), board.get("edges", []), **kwargs)
    for node in board.get("nodes", []):
        key = str(node["id"])
        if key in info["positions"]:
            node["x"], node["y"] = info["positions"][key]
    board["edges"] = info["edges"]
    if kwargs.get("bands") or kwargs.get("labels"):
        board["groups"] = [g for g in board.get("groups", [])
                           if not str(g.get("id", "")).startswith("layer-")]
        board["groups"].extend(info["groups"])
    board["width"] = max(float(board.get("width") or 0), info["width"])
    board["height"] = max(float(board.get("height") or 0), info["height"])
    return info


def layers(layers_spec, *, direction: str = "down", tone: str = "default",
           kind: str = "box", labels=None, **kwargs):
    """Layered auto-layout that builds the nodes too -> `(nodes, groups)`."""
    positions, groups, _, _ = layer_layout(layers_spec, direction=direction, labels=labels,
                                           **kwargs)
    box_w = kwargs.get("w", 190)
    box_h = kwargs.get("h", 72)
    nodes = []
    for layer in layers_spec:
        for entry in layer:
            if isinstance(entry, (list, tuple)):
                key = str(entry[0])
                text = str(entry[1]) if len(entry) > 1 else key
                sub = entry[2] if len(entry) > 2 else None
            else:
                key = text = str(entry)
                sub = None
            px, py = positions[key]
            nodes.append(box(key, text, px, py, w=box_w, h=box_h, sub=sub, tone=tone, kind=kind))
    return nodes, groups


def bus_edges(rects: dict, sources, targets, *, direction: str = "v", trunk: float | None = None,
              label: str | None = None, tone: str = "default", style: str = "solid",
              bidir: bool = False, label_at: str | None = None) -> list[dict]:
    """Edges sharing one trunk line between two bands — the "bus" idiom.

    Sources and targets are node ids, or `(id, label)` pairs for per-edge labels. The trunk sits
    in the gap between the bands unless `trunk` gives an explicit coordinate. Labels ride on each
    edge's own stub (`label_at="first"`), never on the shared trunk.
    """
    def pairs(items):
        out = []
        for entry in items:
            if isinstance(entry, (list, tuple)):
                out.append((str(entry[0]), entry[1] if len(entry) > 1 else None))
            else:
                out.append((str(entry), None))
        return out

    def rect_of(entry):
        """Accept a node dict or an (x, y, w, h) sequence."""
        if isinstance(entry, dict):
            return (float(entry["x"]), float(entry["y"]), float(entry["w"]), float(entry["h"]))
        return tuple(float(v) for v in entry[:4])

    src, dst = pairs(sources), pairs(targets)
    vertical = direction in ("v", "down", "tb")
    if trunk is None:
        if vertical:
            bottoms = [rect_of(rects[s])[1] + rect_of(rects[s])[3] for s, _ in src if s in rects]
            tops = [rect_of(rects[t])[1] for t, _ in dst if t in rects]
        else:
            bottoms = [rect_of(rects[s])[0] + rect_of(rects[s])[2] for s, _ in src if s in rects]
            tops = [rect_of(rects[t])[0] for t, _ in dst if t in rects]
        trunk = (max(bottoms) + min(tops)) / 2 if bottoms and tops else 200.0

    out = []
    for s, s_label in src:
        for t, t_label in dst:
            if s not in rects or t not in rects or s == t:
                continue
            sx, sy, sw, sh = rect_of(rects[s])
            tx, ty, tw, th = rect_of(rects[t])
            if vertical:
                waypoints = [[sx + sw / 2, trunk], [tx + tw / 2, trunk]]
            else:
                waypoints = [[trunk, sy + sh / 2], [trunk, ty + th / 2]]
            item = edge(s, t, t_label or s_label or label, style=style, route="auto",
                        bidir=bidir, tone=tone, waypoints=waypoints)
            if item.get("label"):
                # Label the stub on the side with fewer nodes; identical stubs would collide.
                item["label_at"] = label_at or ("last" if len(src) >= len(dst) else "first")
            out.append(item)
    return out


def board(nodes=(), edges=(), groups=(), notes=(), title: str | None = None,
          width: float | None = None, height: float | None = None, grid: int = 10) -> dict:
    return {"title": title or "", "width": width, "height": height, "grid": grid,
            "groups": [dict(g) for g in groups], "nodes": [dict(n) for n in nodes],
            "edges": [dict(e) for e in edges], "notes": [dict(n) for n in notes]}


# --------------------------------------------------------------------------- geometry


def _normalise_parts(parts) -> list[dict]:
    """Split-box compartments (R1): a bare string is `{"text": s}`; empty texts drop out."""
    out = []
    for part in parts or []:
        if isinstance(part, str):
            item = {"text": part}
        elif isinstance(part, dict):
            item = {"text": str(part.get("text") or "")}
            if part.get("sub"):
                item["sub"] = str(part["sub"])
        else:
            continue
        if item["text"].strip():
            out.append(item)
    return out


def _wrap(text: str, width_px: float, font: float) -> list[str]:
    """Greedy word wrap by an approximate character width (no font metrics available)."""
    limit = max(int(width_px / (font * CHAR_W)), 6)
    lines, current = [], ""
    for word in str(text).split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > limit:          # a single word longer than the line
            lines.append(word[:limit])
            word = word[limit:]
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def _table_counts(n) -> tuple[int, int]:
    """X3.1: rows/cols are ints >= 1; anything absent or unusable is the 2x2 default."""
    def count(value) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 2
        return value if value >= 1 else 2
    return count(n.get("rows")), count(n.get("cols"))


def _table_col_widths(n, cols: int) -> list[float]:
    """Y4.2: `col_widths` normalised to exactly `cols` positive shares — a missing, short or
    non-numeric/non-positive/**non-finite** entry is 1, extras drop.

    The finiteness test is the reader's half of Y4.1a (v1.5 verifier round 2, CRITICAL): a JSON
    import can carry the string `"Infinity"` (or a NaN), which `float()` turns into a non-finite
    share that passes `> 0`; the sum then goes infinite and every column resolves to NaN. This is
    the renderer's OWN reader — the ops' strict writer is a different layer. A conversion that
    **overflows** is the same kind of unusable share (v1.5 verifier round 3): a JSON number with
    310 digits is a finite, positive integer, but it is not a double, `float()` raises
    `OverflowError`, and that escaped the reader and crashed the render. The editor's `JSON.parse`
    reads that same literal as `Infinity`, so `1` is also the only value the two renderers agree on.
    """
    raw = n.get("col_widths")
    if not isinstance(raw, (list, tuple)):
        raw = []
    out = []
    for value in list(raw)[:cols]:
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            value = 1.0
        if not (value > 0 and math.isfinite(value)):
            value = 1.0
        out.append(value)
    return (out + [1.0] * cols)[:cols]


def _table_shares(widths, w: float) -> list[float]:
    """Y4.2: the column widths, `widths[j] / sum(widths) * w`, computed so no FINITE share list can
    overflow its own sum (v1.5 verifier round 2, CRITICAL: `[1e308, 1e308]` summed to `inf`, the
    divider collapsed to `M 0,0 V 48`).

    The shares are first divided by the largest of them — every scaled share is then <= 1, so the
    sum is <= `cols` and cannot overflow — and the division is undone by working from the scaled
    values on both sides of the ratio. For a list that is already all-1 (every v1.4-shaped table)
    this reduces to the old `share / cols * w` bit for bit."""
    peak = max(widths) or 1.0
    scaled = [share / peak for share in widths]
    total = sum(scaled) or 1.0
    return [share / total * w for share in scaled]


def _table_tone_list(values, count: int) -> list:
    """Y4.3: `row_tones`/`col_tones` normalised to exactly `count` entries — null or an
    unknown tone name means "no tone there"."""
    raw = values if isinstance(values, (list, tuple)) else []
    out = [value if value in TONES else None for value in list(raw)[:count]]
    return (out + [None] * count)[:count]


def table(id, x=40.0, y=40.0, rows=2, cols=2, cells=None, header=False, col_widths=None,
          row_tones=None, col_tones=None, tone="default", align=None, w=None, h=None) -> dict:
    """Y6: the node dict the table renderer reads, built through the SAME normalisation.

    Exists so a caller holding this library (a test, a script, another tool) does not have to
    reproduce Y4 by hand: `_table_grid` decides the shape, the cells, the column shares and the tones,
    and the cell list it hands back is written the way a document holds it — a plain string, or an
    object only where the cell carries its own tone or alignment (Y4.1).

    `w`/`h` left out mean auto (0), exactly as in a document: every reader resolves the effective size
    through `_table_rect`. An unknown tone or align is dropped rather than stored (Y4.1a: a writer here
    is like the ops - it does not invent values, and the ops reject what they cannot store).
    """
    node = {"id": str(id), "x": float(x), "y": float(y), "text": "", "kind": "table",
            "tone": tone if tone in TONES else "default",
            "rows": int(rows) if isinstance(rows, int) and rows > 0 else 2,
            "cols": int(cols) if isinstance(cols, int) and cols > 0 else 2,
            "header": bool(header),
            "cells": list(cells) if isinstance(cells, (list, tuple)) else []}
    if col_widths is not None:
        node["col_widths"] = list(col_widths) if isinstance(col_widths, (list, tuple)) else []
    if row_tones is not None:
        node["row_tones"] = list(row_tones) if isinstance(row_tones, (list, tuple)) else []
    if col_tones is not None:
        node["col_tones"] = list(col_tones) if isinstance(col_tones, (list, tuple)) else []
    if align in CELL_ALIGNS:
        node["align"] = align
    for name in ("col_widths", "row_tones", "col_tones"):
        if name in node:
            node[name] = (_table_col_widths(node, node["cols"]) if name == "col_widths"
                          else _table_tone_list(node[name], node["rows"] if name == "row_tones"
                                                else node["cols"]))
    grid = _table_grid(node)
    node["rows"], node["cols"], node["header"] = grid["rows"], grid["cols"], grid["header"]
    built = []
    for index in range(node["rows"] * node["cols"]):
        attrs = grid["attrs"][index]
        if attrs.get("tone") or attrs.get("align"):     # Y4.1: an object only where it is needed
            item = {"text": grid["cells"][index]}
            if attrs.get("tone"):
                item["tone"] = attrs["tone"]
            if attrs.get("align"):
                item["align"] = attrs["align"]
            built.append(item)
        else:
            built.append(grid["cells"][index])
    node["cells"] = built
    node["w"] = float(w) if isinstance(w, (int, float)) and w > 0 else 0.0
    node["h"] = float(h) if isinstance(h, (int, float)) and h > 0 else 0.0
    return node


def _table_grid(n) -> dict:
    """Y4: the table's normalised grid — the single source for the table renderer.

    `cells` stays the flat list of TEXTS (Y4.1: an object cell `{text, tone?, align?}`
    contributes its `text`, stringified; a missing text is ""). The per-cell overrides live
    in the parallel `attrs` list ({tone, align}, each None when absent or unknown), so a
    reader that only wants texts never meets an object. `widths` is exactly `cols` positive
    shares (Y4.2); `row_tones`/`col_tones` are exactly `rows`/`cols` entries (Y4.3).
    """
    rows, cols = _table_counts(n)
    raw = n.get("cells") or []
    if isinstance(raw, str):
        raw = [raw]
    cells, attrs = [], []
    for cell in raw:
        if isinstance(cell, dict):                       # Y4.1: {text, tone?, align?}
            text = cell.get("text")
            cells.append("" if text is None else str(text))
            tone = cell.get("tone")
            align = cell.get("align")
            attrs.append({"tone": tone if tone in TONES else None,
                          "align": align if align in CELL_ALIGNS else None})
        else:
            cells.append("" if cell is None else str(cell))
            attrs.append({"tone": None, "align": None})
    count = rows * cols
    cells = (cells + [""] * count)[:count]
    attrs = (attrs + [{"tone": None, "align": None} for _ in range(count)])[:count]
    return {"rows": rows, "cols": cols, "header": bool(n.get("header")),
            "cells": cells, "attrs": attrs, "widths": _table_col_widths(n, cols),
            "row_tones": _table_tone_list(n.get("row_tones"), rows),
            "col_tones": _table_tone_list(n.get("col_tones"), cols)}


def _table_cells(n, rows: int, cols: int) -> list[str]:
    """X3.1: the flat row-major cell list, normalised to exactly rows*cols strings
    (missing ones become "", extras are dropped). Y4.1: an object cell contributes its text."""
    texts = _table_grid(n)["cells"]
    return (texts + [""] * (rows * cols))[: rows * cols]


def _cell_tone(grid: dict, index: int):
    """Y4.3: a cell's effective background tone — the cell's own, then the row's, then the
    column's; None means the table's own tone (no fill is drawn)."""
    r, c = divmod(index, grid["cols"])
    return grid["attrs"][index]["tone"] or grid["row_tones"][r] or grid["col_tones"][c]


def _table_paints(n: dict, tone: str) -> bool:
    """Y4.4: True when at least one cell's effective tone differs from the table's own — such a
    table draws cell fills and squares its base rect (rx=0) so a corner fill cannot poke out."""
    grid = _table_grid(n)
    for index in range(grid["rows"] * grid["cols"]):
        eff = _cell_tone(grid, index)
        if eff and eff != tone:
            return True
    return False


def table_size(n) -> tuple[float, float]:
    """X3.3: a table's ONE effective (w, h) — absent or non-positive means auto-size.

    Every reader (drawing, edge anchors, layout, bounds) resolves through this helper, so
    the visible table never disagrees with its anchors; writers store resolved numbers.
    """
    rows, cols = _table_counts(n)
    try:
        w = float(n.get("w"))
    except (TypeError, ValueError):
        w = 0.0
    try:
        h = float(n.get("h"))
    except (TypeError, ValueError):
        h = 0.0
    if w <= 0:
        w = TABLE_CELL_W * cols
    if h <= 0:
        # Y4.6: `rows` includes the header row — h = 28 + 24*(rows-1) with a header, 24*rows without
        h = (TABLE_HEAD_H if n.get("header") else TABLE_ROW_H) + TABLE_ROW_H * (rows - 1)
    return w, h


def _text_style(n, default_size: float, weight):
    """X4: the node's own text — `text_size` and `bold`.

    `default_size`/`weight` are what the site emits today, so absent fields (and an
    explicit `text_size: "default"` / `bold: false`) stay byte-identical to v1.3, and an
    unknown `text_size` falls back to the default instead of being rendered raw (X4.4).
    `bold: true` is an OVERRIDE (X4.3): whatever weight the site would emit — 600, 400 or
    no attribute at all — becomes 700; absent/false emits exactly what v1.3 emits.
    """
    font = TEXT_SIZE_PX.get(n.get("text_size"), default_size)
    return font, 700 if n.get("bold") else weight


def _node_rect(node) -> tuple[float, float, float, float]:
    if node.get("kind") == "table":
        w, h = table_size(node)               # X3.3: edge anchors use the effective box
        return (float(node["x"]), float(node["y"]), w, h)
    return (float(node["x"]), float(node["y"]), float(node["w"]), float(node["h"]))


def _at_of(value) -> float:
    """v1.7 (A3, the reader layer): how one `at`/`from_at`/`to_at` reads.

    Absent, `null`, a string, a boolean or a NaN (Python's decoder accepts a literal `NaN`)
    all read as 0.5 — the midpoint, i.e. exactly the point every pre-v1.7 document means. A
    finite number outside [0, 1] is CLAMPED (`-3` -> 0, JSON `1e400`'s +inf -> 1); a huge int
    compares without a float() that would OverflowError. The ops layer RAISES on the same
    inputs instead (store._norm_at); the editor's `normalise` writes this resolution back into
    the model so an imported `1e400` exports as 1.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.5
    if isinstance(value, float) and math.isnan(value):
        return 0.5
    if value <= 0:
        return 0.0
    if value >= 1:
        return 1.0
    return float(value)


def _side_mid(rect, side: str, at: float = 0.5) -> tuple[float, float]:
    """The point `at` along one side of a rect (W3.2 + v1.7's A2/A3); `bottom` is the fallback.

    A2 pins the walk: `at` is a fraction of the side's length along its INCREASING coordinate —
    `left`/`right` walk y from the top edge, `top`/`bottom` walk x from the left — so 0 is the
    start corner, 1 the end corner, and 0.5 (the default, and what every document written
    before this wave means) the midpoint. The value reads through `_at_of` (A3's reader layer).
    """
    x, y, w, h = rect
    at = _at_of(at)
    if side == "left":
        return (x, y + at * h)
    if side == "right":
        return (x + w, y + at * h)
    if side == "top":
        return (x + at * w, y)
    return (x + at * w, y + h)


def _auto_side_choice(source, target) -> tuple[str, str]:
    """The side pair v1.2's position-based rule picks: the centre-to-centre delta decides BOTH
    ends (A3: an `auto` end keeps this choice; only its position along the side is new)."""
    ax_, ay_, aw_, ah_ = source
    bx_, by_, bw_, bh_ = target
    sx, sy = bx_ + bw_ / 2 - (ax_ + aw_ / 2), by_ + bh_ / 2 - (ay_ + ah_ / 2)
    if abs(sx) >= abs(sy):
        return ("right", "left") if sx >= 0 else ("left", "right")
    return ("bottom", "top") if sy >= 0 else ("top", "bottom")


def _auto_sides(source, target) -> tuple[tuple[float, float], tuple[float, float]]:
    """The pre-v1.3 position-based anchor pair — the side choice above at its midpoint."""
    from_side, to_side = _auto_side_choice(source, target)
    return _side_mid(source, from_side), _side_mid(target, to_side)


def anchors(a, b, from_side: str = "auto", to_side: str = "auto",
            from_at=0.5, to_at=0.5):
    """The two anchor points of an edge between rects a and b (W3.2 + v1.7's A2/A3).

    An `auto` end keeps the position-based side rule of v1.2 (`_auto_side_choice`), computed
    from the two centres exactly as before — so two `auto` ends at the default 0.5 are
    byte-identical to v1.2, and an explicit side on one end never moves the other end's `auto`
    choice. `from_at`/`to_at` pick the point ALONG the resolved side (A2's walk; an `auto`
    end's position is selectable too — only its side stays chosen). Unknown sides fall back to
    `auto`, out-of-range or non-numeric positions through `_at_of`.
    """
    if from_side not in SIDES:
        from_side = "auto"
    if to_side not in SIDES:
        to_side = "auto"
    auto = _auto_side_choice(a, b)
    return (_side_mid(a, auto[0] if from_side == "auto" else from_side, from_at),
            _side_mid(b, auto[1] if to_side == "auto" else to_side, to_at))


def _route(a, b, route: str = "auto", waypoints=None,
           from_side: str = "auto", to_side: str = "auto",
           from_at=0.5, to_at=0.5) -> list[tuple[float, float]]:
    """Orthogonal (or straight) edge routing between two rectangles."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy, bcx, bcy = ax + aw / 2, ay + ah / 2, bx + bw / 2, by + bh / 2
    dx, dy = bcx - acx, bcy - acy

    if waypoints:
        # W3.2: the first/last anchor of the path is the W3.2 anchor for that end — an
        # explicit side wins, an `auto` end aims at the neighbouring waypoint as before. A3:
        # the edge's own `from_at`/`to_at` ride along; the waypoint-facing auto end keeps 0.5.
        points = [list(p) for p in waypoints]
        start = anchors(a, (points[0][0], points[0][1], 0, 0), from_side, "auto",
                        from_at, 0.5)[0]
        end = anchors((points[-1][0], points[-1][1], 0, 0), b, "auto", to_side,
                      0.5, to_at)[1]
        return [start] + [(float(p[0]), float(p[1])) for p in points] + [end]

    kind = route
    if kind == "auto":
        kind = "h" if abs(dx) >= abs(dy) else "v"
    start, end = anchors(a, b, from_side, to_side, from_at, to_at)
    if kind == "straight":
        return [start, end]
    if kind == "h":
        mid = (start[0] + end[0]) / 2
        return [start, (mid, start[1]), (mid, end[1]), end]
    mid = (start[1] + end[1]) / 2
    return [start, (start[0], mid), (end[0], mid), end]


# --------------------------------------------------------------------------- lines (Z3)


def _normalise_line(item) -> dict | None:
    """Z2 (the reader's lenient half) + Z3.1 for one `lines` entry.

    An entry that is not a dict, or whose `points` is not a list of at least 2 `[x, y]` pairs
    of finite numbers, is dropped WHOLE — half a polyline is worse than no line (Z2). A
    coordinate < 0 is clamped to 0 (the canvas origin is the board's corner); an unknown
    tone/style/start/end becomes the default. Nothing is re-rounded and the document itself
    is never mutated — the normalised copy is what bounds() and render_svg() consume.
    """
    if not isinstance(item, dict):
        return None
    raw = item.get("points")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    points = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            return None
        x, y = p[0], p[1]
        # Z2, round-1 finding 3: an entry that is not a pair of finite NUMBERS drops the whole
        # item. The JSON types are validated, never coerced — float() would read "12" as 12.0
        # and True as 1.0 while the ops layer raises on both, and the editor's reader applies
        # the same typeof rule.
        if (isinstance(x, bool) or isinstance(y, bool)
                or not isinstance(x, (int, float)) or not isinstance(y, (int, float))):
            return None
        x, y = float(x), float(y)
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        points.append((max(x, 0.0), max(y, 0.0)))
    tone = item.get("tone")
    style = item.get("style")
    start = item.get("start")
    end = item.get("end")
    return {"id": item.get("id") or "line", "points": points,
            "tone": tone if tone in TONES else "default",
            "style": style if style in ("solid", "dashed", "dotted") else "solid",
            "start": start if start in HEADS else "none",
            # Z3.2: an unknown/missing `end` is the edge vocabulary's default — the arrow.
            "end": end if end in HEADS else "arrow",
            "from": item.get("from"), "to": item.get("to")}


def _line_anchor_rect(ref: str, b: dict):
    """Z3.4: the rect a line's `from`/`to` anchor resolves against — nodes THEN notes.

    Edge rendering resolves nodes only; a line's ref may also name a notes entry. A table
    resolves through `table_size`, every other node through its stored x/y/w/h, a note
    through its stored box with the renderer's own 300x60 defaults.
    """
    for n in b.get("nodes", []):
        if n.get("id") == ref:
            return _node_rect(n)
    for t in b.get("notes", []):
        if t.get("id") == ref:
            return (float(t.get("x", 0)), float(t.get("y", 0)),
                    float(t.get("w", 300)), float(t.get("h", 60)))
    return None


def _line_points(item: dict, b: dict) -> list:
    """Z3.3/Z3.4: the stored points with each resolvable anchor REPLACING its end.

    Resolved through the SAME resolver the edges use (`anchors`), the stored coordinate pair
    widened into a zero-size rect. A dangling or malformed anchor leaves its stored point —
    the line is still drawn (unlike an edge with a bad ref, which draws nothing; Z8 keeps
    that behaviour untouched).
    """
    points = list(item["points"])
    first, last = points[0], points[-1]
    for key, index in (("from", 0), ("to", len(points) - 1)):
        anchor = item.get(key)
        if not isinstance(anchor, dict) or not isinstance(anchor.get("ref"), str):
            continue                    # Z2: not an object / no string ref -> the end stays FREE
        rect = _line_anchor_rect(anchor["ref"], b)
        if rect is None:
            continue                    # Z3.4: a dangling ref keeps the stored point
        side = anchor.get("side")
        if side not in SIDES:
            side = "auto"
        # A3 (v1.7): `at` rides through the same lenient reader the edges use — an `auto`
        # side keeps the aim-at-the-neighbouring-point rule, only its position moves.
        at = anchor.get("at", 0.5)
        if key == "from":
            points[index] = anchors(rect, (first[0], first[1], 0, 0), side, "auto", at, 0.5)[0]
        else:
            points[index] = anchors((last[0], last[1], 0, 0), rect, "auto", side, 0.5, at)[1]
    return points


def bounds(b: dict) -> tuple[float, float]:
    """Content bounds plus padding — used when the board ships no explicit size."""
    right = bottom = 0.0
    for item in list(b.get("nodes", [])) + list(b.get("notes", [])) + list(b.get("groups", [])):
        if item.get("kind") == "table":     # X3.3: the content bounds use the effective size
            ew, eh = table_size(item)
            right = max(right, float(item.get("x", 0)) + ew)
            bottom = max(bottom, float(item.get("y", 0)) + eh)
            continue
        # Sizes are optional: a hand-written or imported board may carry only id/text/x/y.
        w = float(item.get("w", 190 if item.get("id") in b.get("groups", []) else 190))
        right = max(right, float(item.get("x", 0)) + w)
        bottom = max(bottom, float(item.get("y", 0)) + float(item.get("h", 72)))
    # Z3.5: a line drawn outside the boxes is never clipped — every (resolved) point counts.
    for item in b.get("lines", []):
        item = _normalise_line(item)
        if item is None:
            continue
        for x, y in _line_points(item, b):
            right = max(right, x)
            bottom = max(bottom, y)
    return max(right + 40, 320.0), max(bottom + 40, 200.0)


# --------------------------------------------------------------------------- render


_HEAD_SHAPES = {
    "arrow": '<path d="M 0,0 L 10,5 L 0,10 Z" fill="var(--ck-edge-{tone})"/>',
    "triangle": '<path d="M 0,0 L 10,5 L 0,10 Z" fill="none" '
                'stroke="var(--ck-edge-{tone})" stroke-width="1.2"/>',
    "open": '<path d="M 0,0 L 10,5 L 0,10" fill="none" '
            'stroke="var(--ck-edge-{tone})" stroke-width="1.6"/>',
    "circle": '<circle cx="6" cy="5" r="4" fill="var(--ck-edge-{tone})"/>',
    "diamond": '<path d="M 2,5 L 6,1 L 10,5 L 6,9 Z" fill="var(--ck-edge-{tone})"/>',
    "bar": '<path d="M 8,1 V 9" fill="none" stroke="var(--ck-edge-{tone})" stroke-width="2"/>',
}


def _marker_id(tone: str, head: str, size: str = "medium") -> str:
    """W3.3: the default size keeps the plain v1.2 id so old documents render
    byte-identically; other sizes are suffixed."""
    return f"ck-head-{tone}-{head}" if size == "medium" else f"ck-head-{tone}-{head}-{size}"


def _markers(*, grid_on: bool = False, g: float = 10, grid_style: str = "lines",
             used=None) -> str:
    """The `<defs>` block: one marker per (tone, head type) per R6.2, plus one per USED
    (tone, head, size) combo for the W3.3 non-default head sizes, plus the grid (R5.3).

    `used` is a set of (tone, head, size) triples resolved by the caller; sized markers are
    emitted only when an edge actually references them, so a board without `head_size`
    fields keeps the exact v1.2 defs.
    """
    wanted = used or set()
    parts = ['<defs>']
    for tone in TONES:
        for head in HEADS:
            if head == "none":
                continue
            parts.append(
                f'<marker id="ck-head-{tone}-{head}" viewBox="0 0 10 10" refX="10" refY="5" '
                f'markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" '
                f'orient="auto-start-reverse">{_HEAD_SHAPES[head].format(tone=tone)}</marker>'
            )
            for size in ("small", "large"):
                if (tone, head, size) not in wanted:
                    continue
                box = HEAD_BOX[size]
                # W3.3: refX/refY are coordinates in the marker's OWN `0 0 10 10` viewBox (the tip
                # sits at (10,5) there) — only markerWidth/markerHeight carry the pixel size.
                # `refX = box / 2` would move the tip off the endpoint and off the v1.2 geometry.
                parts.append(
                    f'<marker id="{_marker_id(tone, head, size)}" viewBox="0 0 10 10" '
                    f'refX="10" refY="5" markerWidth="{box}" '
                    f'markerHeight="{box}" markerUnits="userSpaceOnUse" '
                    f'orient="auto-start-reverse">'
                    f'{_HEAD_SHAPES[head].format(tone=tone)}</marker>'
                )
    if grid_on:
        if grid_style == "dots":
            cell = '<circle cx="0.5" cy="0.5" r="1" fill="var(--ck-grid)"/>'
        else:
            cell = (f'<path d="M 0,0 H {g:g} M 0,0 V {g:g}" fill="none" '
                    f'stroke="var(--ck-grid)" stroke-width="1"/>')
        parts.append(f'<pattern id="ck-grid" width="{g:g}" height="{g:g}" '
                     f'patternUnits="userSpaceOnUse">{cell}</pattern>')
    parts.append("</defs>")
    return "".join(parts)


def _polyline_svg(points, tone: str, style: str, start: str, end: str, size: str = "medium",
                  width: str = "1.7") -> str:
    """One `<polyline fill="none">` with the edge's stroke/dash/marker attributes (Z3.2/Z3.6).

    Extracted out of render_svg's edge loop so the edge drawing and `_line_svg` share ONE
    implementation — the dash arrays and the marker ids cannot drift between the two.
    """
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dash = {"solid": "", "dashed": ' stroke-dasharray="7 5"',
            "dotted": ' stroke-dasharray="2 4"'}.get(style, "")
    arrows = ""
    if end != "none":
        arrows += f' marker-end="url(#{_marker_id(tone, end, size)})"'
    if start != "none":
        arrows += f' marker-start="url(#{_marker_id(tone, start, size)})"'
    return (f'<polyline points="{path}" fill="none" stroke="var(--ck-edge-{tone})" '
            f'stroke-width="{width}"{dash}{arrows}/>')


def _line_svg(item: dict, b: dict) -> str:
    """Z3.6: one line = a group with one polyline; heads always in the `medium` box (Z3.2).

    Lines draw AFTER the notes — an annotation arrow is meant to be seen OVER the box it
    crosses, not buried under its fill.
    """
    points = _line_points(item, b)
    return (f'<g class="ck-line" data-id="{esc(item["id"])}">'
            + _polyline_svg(points, item["tone"], item["style"], item["start"], item["end"],
                            "medium")
            + "</g>")


def render_svg(b: dict, *, pad: int = 16) -> str:
    """The static picture: groups, edges, nodes, notes — no JavaScript."""
    need_w, need_h = bounds(b)
    width = max(float(b.get("width") or 0), need_w)
    height = max(float(b.get("height") or 0), need_h)
    nodes = {n["id"]: n for n in b.get("nodes", [])}
    out = [f'<svg class="ck-board" viewBox="0 0 {width:g} {height:g}" width="100%" '
           f'height="auto" preserveAspectRatio="xMidYMid meet" role="img" '
           f'aria-label="{esc(b.get("title") or "board")}">']
    grid_on = bool(b.get("grid_on"))
    grid_g = float(b.get("grid") or 10)
    grid_style = b.get("grid_style") or "lines"
    if grid_style not in ("lines", "dots"):
        grid_style = "lines"
    # W3: resolve each edge's effective heads/sides/size once — the used (tone, head, size)
    # combos decide which sized markers the defs carry (medium markers are always complete).
    prepared = []
    used = set()
    for index, e in enumerate(b.get("edges", [])):
        src, dst = nodes.get(e["from"]), nodes.get(e["to"])
        if not src or not dst:
            continue
        tone = e.get("tone", "default")
        # Effective heads (R6.1/R6.4): a head that is missing, null or not in HEADS is not a
        # head — `end` then means arrow, `start` means whatever the legacy `bidir` flag says.
        # Never build a marker id out of an unknown value.
        end = e.get("end")
        if end not in HEADS:
            end = "arrow"
        start = e.get("start")
        if start not in HEADS:
            start = "arrow" if e.get("bidir") else "none"
        # W3.1: an unknown side/size is treated as the default, never rendered raw.
        from_side = e.get("from_side")
        if from_side not in SIDES:
            from_side = "auto"
        to_side = e.get("to_side")
        if to_side not in SIDES:
            to_side = "auto"
        size = e.get("head_size")
        if size not in HEAD_SIZES:
            size = "medium"
        if end != "none":
            used.add((tone, end, size))
        if start != "none":
            used.add((tone, start, size))
        prepared.append((index, e, src, dst, tone, start, end, from_side, to_side, size))
    out.append(_markers(grid_on=grid_on, g=grid_g, grid_style=grid_style, used=used))
    if grid_on:
        out.append(f'<rect class="ck-grid" x="0" y="0" width="{width:g}" height="{height:g}" '
                   f'fill="url(#ck-grid)"/>')

    for g in b.get("groups", []):
        gx, gy, gw, gh = _node_rect(g)
        out.append(f'<g class="ck-group" data-id="{esc(g["id"])}">'
                   f'<rect x="{gx:g}" y="{gy:g}" width="{gw:g}" height="{gh:g}" rx="12" '
                   f'fill="var(--ck-tone-{g.get("tone", "muted")}-fill)" '
                   f'stroke="var(--ck-tone-{g.get("tone", "muted")}-line)" stroke-width="1" '
                   f'stroke-dasharray="7 5"/>'
                   f'<text x="{gx + 12:g}" y="{gy + 18:g}" font-size="11" font-weight="600" '
                   f'fill="var(--ck-tone-{g.get("tone", "muted")}-line)">{esc(g.get("text", ""))}'
                   f'</text></g>')

    areas = [n for n in b.get("nodes", []) if n.get("kind") == "area"]
    plain = [n for n in b.get("nodes", []) if n.get("kind") != "area"]
    for n in areas:                                  # titled backdrops sit behind everything
        out.append(_node_svg(n))

    for index, e, src, dst, tone, start, end, from_side, to_side, size in prepared:
        # A3 (v1.7): the positions ride through the lenient reader — clamp/ignore in `_at_of`.
        points = _route(_node_rect(src), _node_rect(dst), e.get("route", "auto"),
                        e.get("waypoints"), from_side, to_side,
                        e.get("from_at", 0.5), e.get("to_at", 0.5))
        # Z3.6: the polyline body is shared with the lines (`_polyline_svg`) — the edge keeps
        # its exact v1.2 markup, the extraction is byte-identical.
        out.append(f'<g class="ck-edge" data-id="e{index}" data-from="{esc(src["id"])}" '
                   f'data-to="{esc(dst["id"])}">'
                   + _polyline_svg(points, tone, e.get("style", "solid"), start, end, size))
        if e.get("label"):
            at = e.get("label_at") or "middle"
            if len(points) < 2:
                seg = 0
            elif at == "first":
                seg = 0
            elif at == "last":
                seg = len(points) - 2
            else:
                seg = max(len(points) // 2 - 1, 0)
            p0, p1 = points[seg], points[min(seg + 1, len(points) - 1)]
            lx, ly = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
            if abs(p1[0] - p0[0]) < 0.5:        # vertical segment: label beside the line
                out.append(f'<text x="{lx - 5:g}" y="{ly + 3.5:g}" text-anchor="end" '
                           f'font-size="10" fill="var(--ck-muted)">{esc(e["label"])}</text>')
            else:                                # horizontal segment: label above the line
                out.append(f'<text x="{lx:g}" y="{ly - 5:g}" text-anchor="middle" '
                           f'font-size="10" fill="var(--ck-muted)">{esc(e["label"])}</text>')
        out.append("</g>")

    for n in plain:
        out.append(_node_svg(n))
    for t in b.get("notes", []):
        out.append(_note_svg(t, b))
    # Z3.6: lines draw last of the board content — an annotation arrow is meant to be seen
    # OVER the boxes it crosses. An unusable item is dropped whole (Z2), not half-drawn.
    for item in b.get("lines", []):
        item = _normalise_line(item)
        if item is not None:
            out.append(_line_svg(item, b))
    out.append("</svg>")
    return "".join(out)


def _shape_svg(kind: str, w: float, h: float, fill: str, stroke: str, tone: str) -> str:
    """The node's outline in node-local coordinates (R2); box is the fallback."""
    if kind == "diamond":
        return (f'<polygon points="{w / 2:g},0 {w:g},{h / 2:g} {w / 2:g},{h:g} 0,{h / 2:g}" '
                f'{fill} {stroke}/>')
    if kind == "pill":
        return f'<rect x="0" y="0" width="{w:g}" height="{h:g}" rx="{h / 2:g}" {fill} {stroke}/>'
    if kind == "cloud":
        d = (f"M {0.30 * w:g},{h:g} C {0.14 * w:g},{h:g} {0.04 * w:g},{0.86 * h:g} "
             f"{0.12 * w:g},{0.70 * h:g} "
             f"C {0.02 * w:g},{0.50 * h:g} {0.16 * w:g},{0.30 * h:g} {0.34 * w:g},{0.36 * h:g} "
             f"C {0.42 * w:g},{0.10 * h:g} {0.72 * w:g},{0.08 * h:g} {0.78 * w:g},{0.34 * h:g} "
             f"C {0.94 * w:g},{0.32 * h:g} {1.02 * w:g},{0.56 * h:g} {0.88 * w:g},{0.72 * h:g} "
             f"C {0.96 * w:g},{0.88 * h:g} {0.84 * w:g},{h:g} {0.70 * w:g},{h:g} Z")
        return f'<path d="{d}" {fill} {stroke}/>'
    if kind == "cylinder":
        d = (f"M 0,{0.12 * h:g} A {0.5 * w:g},{0.12 * h:g} 0 0 1 {w:g},{0.12 * h:g} "
             f"V {0.88 * h:g} A {0.5 * w:g},{0.12 * h:g} 0 0 1 0,{0.88 * h:g} Z")
        return (f'<path d="{d}" {fill} {stroke}/>'
                f'<ellipse cx="{0.5 * w:g}" cy="{0.12 * h:g}" rx="{0.5 * w:g}" '
                f'ry="{0.12 * h:g}" fill="none" {stroke}/>')
    if kind == "document":
        d = (f"M 0,0 H {w:g} V {0.84 * h:g} "
             f"C {0.75 * w:g},{0.70 * h:g} {0.50 * w:g},{h:g} {0.25 * w:g},{0.86 * h:g} "
             f"C {0.12 * w:g},{0.78 * h:g} {0.06 * w:g},{0.92 * h:g} 0,{0.84 * h:g} Z")
        return f'<path d="{d}" {fill} {stroke}/>'
    if kind == "hexagon":
        return (f'<polygon points="{0.18 * w:g},0 {0.82 * w:g},0 {w:g},{0.5 * h:g} '
                f'{0.82 * w:g},{h:g} {0.18 * w:g},{h:g} 0,{0.5 * h:g}" {fill} {stroke}/>')
    if kind == "parallelogram":
        return (f'<polygon points="{0.20 * w:g},0 {w:g},0 {0.80 * w:g},{h:g} 0,{h:g}" '
                f'{fill} {stroke}/>')
    return f'<rect x="0" y="0" width="{w:g}" height="{h:g}" rx="9" {fill} {stroke}/>'


def _icon_svg(kind: str, tone: str, w: float, h: float) -> str:
    """The pictogram of an icon kind (R2); the caption line is added by the caller."""
    label_h = LINE + 8
    ph = h - label_h
    fill = f'fill="var(--ck-tone-{tone}-fill)"'
    line = f'stroke="var(--ck-tone-{tone}-line)"'
    out = []
    if kind == "server":
        bw = 0.64 * w
        out.append(f'<rect x="{0.18 * w:g}" y="4" width="{bw:g}" height="{ph - 8:g}" rx="6" '
                   f'{fill} {line} stroke-width="1.4"/>')
        for i in range(3):
            out.append(f'<rect x="{0.18 * w + 0.10 * bw:g}" y="{4 + (ph - 8) * (0.10 + 0.30 * i):g}" '
                       f'width="{0.62 * bw:g}" height="{0.20 * (ph - 8):g}" rx="2" fill="none" '
                       f'{line} stroke-width="1"/>')
            out.append(f'<circle cx="{0.18 * w + 0.88 * bw:g}" '
                       f'cy="{4 + (ph - 8) * (0.20 + 0.30 * i):g}" '
                       f'r="{min(3, 0.06 * (ph - 8)):g}" '
                       f'fill="var(--ck-tone-{tone}-line)" stroke="none"/>')
    elif kind == "computer":
        out.append(f'<rect x="{0.15 * w:g}" y="4" width="{0.70 * w:g}" height="{ph - 18:g}" '
                   f'rx="4" {fill} {line} stroke-width="1.4"/>')
        out.append(f'<path d="M {0.5 * w:g},{ph - 14:g} V {ph - 4:g}" fill="none" {line} '
                   f'stroke-width="1.2"/>')
        out.append(f'<path d="M {0.36 * w:g},{ph - 4:g} H {0.64 * w:g}" fill="none" {line} '
                   f'stroke-width="1.4"/>')
    else:  # actor — a stroke-only stick figure, no node fill
        r = min(0.14 * ph, 0.13 * w)
        shoulder_y = 6 + 2 * r + 2
        torso_bottom = shoulder_y + (ph - 6 - shoulder_y) * 0.68
        arms_y = shoulder_y + (torso_bottom - shoulder_y) * 0.30
        out.append(f'<circle cx="{0.5 * w:g}" cy="{6 + r:g}" r="{r:g}" fill="none" {line} '
                   f'stroke-width="1.4"/>')
        out.append(f'<path d="M {0.5 * w:g},{shoulder_y:g} V {torso_bottom:g}" fill="none" '
                   f'{line} stroke-width="1.4"/>')
        out.append(f'<path d="M {0.26 * w:g},{arms_y:g} H {0.74 * w:g}" fill="none" {line} '
                   f'stroke-width="1.4"/>')
        out.append(f'<path d="M {0.5 * w:g},{torso_bottom:g} L {0.30 * w:g},{ph - 2:g} '
                   f'M {0.5 * w:g},{torso_bottom:g} L {0.70 * w:g},{ph - 2:g}" fill="none" '
                   f'{line} stroke-width="1.4"/>')
    return "".join(out)


def _split_svg(n: dict, items: list[dict], w: float, h: float, tone: str) -> list[str]:
    """Split-box compartments (R4): header text, then dividers, then the parts."""
    left = n.get("align") == "left"
    anchor = "start" if left else "middle"
    out = []
    head_h = 28.0 if str(n.get("text") or "").strip() else 0.0
    if head_h:
        head_font, head_weight = _text_style(n, FONT, 600)   # X4: the header is own text
        hlines = _wrap(n["text"], w - 24, head_font)
        start = (head_h - len(hlines) * LINE) / 2 + head_font
        ax = 12.0 if left else w / 2
        for i, line in enumerate(hlines):
            out.append(f'<text x="{ax:g}" y="{start + i * LINE:g}" text-anchor="{anchor}" '
                       f'font-size="{head_font:g}" font-weight="{head_weight}" fill="var(--ck-fg)">'
                       f'{esc(line)}</text>')
    count = len(items)
    columns = n.get("split") == "columns"
    cell_w = w / count
    cell_h = (h - head_h) / count
    for i in range(1, count):
        if columns:
            out.append(f'<path d="M {i * cell_w:g},{head_h:g} V {h:g}" fill="none" '
                       f'stroke="var(--ck-tone-{tone}-line)" stroke-width="1"/>')
        else:
            out.append(f'<path d="M 0,{head_h + i * cell_h:g} H {w:g}" fill="none" '
                       f'stroke="var(--ck-tone-{tone}-line)" stroke-width="1"/>')
    for i, part in enumerate(items):
        if columns:
            wrap_w = cell_w - 12
            top, bottom = head_h, h
            ax = i * cell_w + 6 if left else i * cell_w + cell_w / 2
        else:
            wrap_w = w - 16
            top, bottom = head_h + i * cell_h, head_h + (i + 1) * cell_h
            ax = 8.0 if left else w / 2
        plines = _wrap(part["text"], wrap_w, FONT)
        block = len(plines) * LINE + (SUB_FONT + 4 if part.get("sub") else 0)
        start = top + (bottom - top - block) / 2 + FONT
        for j, line in enumerate(plines):
            out.append(f'<text x="{ax:g}" y="{start + j * LINE:g}" text-anchor="{anchor}" '
                       f'font-size="{FONT:g}" font-weight="600" fill="var(--ck-fg)">'
                       f'{esc(line)}</text>')
        if part.get("sub"):
            sub_y = start + len(plines) * LINE + 2
            for j, line in enumerate(_wrap(part["sub"], wrap_w, SUB_FONT)):
                out.append(f'<text x="{ax:g}" y="{sub_y + j * (SUB_FONT + 3):g}" '
                           f'text-anchor="{anchor}" font-size="{SUB_FONT:g}" '
                           f'fill="var(--ck-muted)">{esc(line)}</text>')
    return out


def _table_svg(n: dict, w: float, h: float, tone: str) -> list[str]:
    """X3.3/Y4: the cell tone fills, the interior boundaries and column dividers, then the texts.

    Y4.6 geometry: `rows` counts EVERY row and row 0 IS the header band (28 tall) when `header`
    is set — there is no separate header boundary and no empty band; the interior boundaries are
    the cumulative row ends. Text mirrors the split-box constants: FONT 12.5, LINE 15, weight
    600 (700 on the header row), wrapped at the column's width - 12, the block centred in its
    own row's band. The dividers are the split-box stroke, `var(--ck-tone-<tone>-line)` at 1px.
    Y4.2: a column's width is its `col_widths` share of `w`. Y4.4: a cell whose effective tone
    (cell > row > column, Y4.3) differs from the table's own draws one plain rect over its cell
    box, after the base rect and before the dividers and texts.
    """
    grid = _table_grid(n)
    rows, cols, header = grid["rows"], grid["cols"], grid["header"]
    cells, attrs = grid["cells"], grid["attrs"]

    # Y4.6 (gap round 1 of v1.4 + the v1.5 gap pass): `rows` counts EVERY row, row 0 IS the header
    # band when `header` is true (28 instead of 24), and the bands FILL the effective height — so an
    # explicitly taller table has no orphan region below its last row. At the auto height the scale
    # is 1 and the bands are exactly 28/24/24, which every v1.4-shaped table depends on.
    nominal = [TABLE_HEAD_H if (header and r == 0) else TABLE_ROW_H for r in range(rows)]
    span = sum(nominal) or 1.0

    def row_h(r: int) -> float:
        return h * nominal[r] / span

    tops = [0.0]
    for r in range(1, rows):
        tops.append(tops[-1] + row_h(r - 1))
    col_w = _table_shares(grid["widths"], w)          # Y4.2: shares of w, overflow-safely; all-1 is v1.4's w/cols
    col_x = [0.0]
    for j in range(1, cols):
        col_x.append(col_x[-1] + col_w[j - 1])
    style = f'fill="none" stroke="var(--ck-tone-{tone}-line)" stroke-width="1"'
    table_align = n.get("align")                       # Y4.5: the table's align under the cell's
    if table_align not in CELL_ALIGNS:
        table_align = None
    out = []
    for index in range(rows * cols):                   # Y4.4: the differing-tone cell fills
        eff = _cell_tone(grid, index)
        if not eff or eff == tone:
            continue
        r, c = divmod(index, cols)
        out.append(f'<rect x="{col_x[c]:g}" y="{tops[r]:g}" width="{col_w[c]:g}" '
                   f'height="{row_h(r):g}" fill="var(--ck-tone-{eff}-fill)"/>')
    for r in range(rows - 1):        # the interior boundaries, i.e. where each row ends
        out.append(f'<path d="M 0,{tops[r + 1]:g} H {w:g}" {style}/>')
    for j in range(1, cols):         # vertical dividers span the full height, header included
        out.append(f'<path d="M {col_x[j]:g},0 V {h:g}" {style}/>')
    # Y4.2/Y4.5 (v1.5 verifier, MAJOR): a legal but extreme share (col_widths [0.1, 100]) makes a cell
    # narrower than the wrap's own minimum of six characters, and the text then drew straight through
    # the neighbouring column. A cell that cannot fit its text clips it to its own box: nothing else
    # keeps the text in, because the wrap has a floor and the shares are the document's. The clip is
    # emitted only for a cell that needs it, so every table that fits is byte-identical to v1.4.
    clips = []
    for index, cell in enumerate(cells):
        if not cell.strip():
            continue                 # X3.2: an empty cell is an empty compartment — no <text>
        r, c = divmod(index, cols)
        lines = _wrap(cell, col_w[c] - 12, FONT)
        start = tops[r] + (row_h(r) - len(lines) * LINE) / 2 + FONT
        weight = 700 if header and r == 0 else 600
        align = attrs[index]["align"] or table_align or "center"     # Y4.5
        if align == "left":
            ax, anchor = col_x[c] + 6, "start"
        elif align == "right":
            ax, anchor = col_x[c] + col_w[c] - 6, "end"
        else:
            ax, anchor = col_x[c] + col_w[c] / 2, "middle"   # v1.4's behaviour, byte-identical
        widest = max(len(line) for line in lines) * FONT * CHAR_W
        clip_id = None
        if widest > col_w[c] - 12:
            clip_id = f"tclip-{esc(str(n.get('id', 't')))}-{index}"
            clips.append(f'<clipPath id="{clip_id}"><rect x="{col_x[c]:g}" y="{tops[r]:g}" '
                         f'width="{col_w[c]:g}" height="{row_h(r):g}"/></clipPath>')
        clipped = f' clip-path="url(#{clip_id})"' if clip_id else ""
        for k, line in enumerate(lines):
            out.append(f'<text x="{ax:g}" y="{start + k * LINE:g}" text-anchor="{anchor}" '
                       f'font-size="{FONT:g}" font-weight="{weight}"{clipped} '
                       f'fill="var(--ck-fg)">{esc(line)}</text>')
    if clips:
        out.insert(0, "<defs>" + "".join(clips) + "</defs>")
    return out


_INSETS = {"box": 12.0, "pill": 12.0, "cloud": 0.16, "cylinder": 0.14, "document": 0.14,
           "hexagon": 0.22, "parallelogram": 0.24}   # a value < 1 is a fraction of w
_OFFSETS = {"cloud": 0.02, "cylinder": 0.06, "document": -0.02}   # fractions of h


def _node_svg(n: dict) -> str:
    x, y, w, h = _node_rect(n)
    tone = n.get("tone", "default")
    kind = n.get("kind", "box")
    fill = f'fill="var(--ck-tone-{tone}-fill)"'
    stroke = f'stroke="var(--ck-tone-{tone}-line)" stroke-width="1.4"'
    parts = [f'<g class="ck-node" data-id="{esc(n["id"])}" transform="translate({x:g},{y:g})">']
    if kind == "caption":
        cap_font, cap_weight = _text_style(n, FONT, None)   # X4.3: bold adds the attribute
        weight_attr = f' font-weight="{cap_weight}"' if cap_weight is not None else ""
        for i, line in enumerate(_wrap(n.get("text", ""), w, cap_font)):
            parts.append(f'<text x="0" y="{cap_font + i * LINE:g}" font-size="{cap_font:g}"'
                         f'{weight_attr} fill="var(--ck-tone-{tone}-line)">{esc(line)}</text>')
        return "".join(parts) + "</g>"
    if kind == "area":
        parts.append(f'<rect x="0" y="0" width="{w:g}" height="{h:g}" rx="10" {fill} {stroke}/>')
        parts.append(f'<path d="M 0,24 H {w:g}" fill="none" '
                     f'stroke="var(--ck-tone-{tone}-line)" stroke-width="1"/>')
        title_font, title_weight = _text_style(n, 11.0, 600)   # X4: the area title is own text
        title = _wrap(n.get("text", ""), w - 24, title_font)[0]
        parts.append(f'<text x="12" y="17" font-size="{title_font:g}" '
                     f'font-weight="{title_weight}" '
                     f'fill="var(--ck-tone-{tone}-line)">{esc(title)}</text>')
        parts.append("</g>")
        return "".join(parts)
    if kind in ICON_KINDS:
        parts.append(_icon_svg(kind, tone, w, h))
        # R2: ONE line — the first wrapped line at `w - 12` (an unwrapped caption runs out of the
        # node, and the JS mirror must wrap identically or tests/test_editor.py fails).
        cap_font, cap_weight = _text_style(n, FONT, 400)   # X4.1: the caption is own text
        caption_line = _wrap(n.get("text", ""), w - 12, cap_font)[0]
        parts.append(f'<text x="{0.5 * w:g}" y="{h - 6:g}" text-anchor="middle" '
                     f'font-size="{cap_font:g}" font-weight="{cap_weight}" fill="var(--ck-fg)">'
                     f'{esc(caption_line)}</text>')
        parts.append("</g>")
        return "".join(parts)
    if kind == "table":
        # X3.2: the table's own text/sub are NOT drawn — the cells carry everything.
        # Y4.4: a table that paints any cell/row/column tone squares its base rect (rx=0) —
        # a squared cell fill would otherwise poke out of the rounded corner.
        rx = 0 if _table_paints(n, tone) else 9
        parts.append(f'<rect x="0" y="0" width="{w:g}" height="{h:g}" rx="{rx}" {fill} {stroke}/>')
        parts.extend(_table_svg(n, w, h, tone))
        parts.append("</g>")
        return "".join(parts)
    parts.append(_shape_svg(kind, w, h, fill, stroke, tone))
    split_parts = _normalise_parts(n.get("parts")) if kind in ("box", "pill") else []
    if split_parts:
        parts.extend(_split_svg(n, split_parts, w, h, tone))
        parts.append("</g>")
        return "".join(parts)
    inset = h * 0.45 if kind == "diamond" else _INSETS.get(kind, 12.0)
    if inset < 1:                                    # a fraction of w, not an absolute inset
        inset *= w
    offset = _OFFSETS.get(kind, 0.0) * h
    left = n.get("align") == "left"
    anchor = "start" if left else "middle"
    ax = inset if left else w / 2
    text_font, text_weight = _text_style(n, FONT, 600)   # X4: the node's own text
    lines = _wrap(n.get("text", ""), w - 2 * inset, text_font)
    block = len(lines) * LINE + (SUB_FONT + 4 if n.get("sub") else 0)
    start = (h - block) / 2 + text_font + offset
    for i, line in enumerate(lines):
        parts.append(f'<text x="{ax:g}" y="{start + i * LINE:g}" text-anchor="{anchor}" '
                     f'font-size="{text_font:g}" font-weight="{text_weight}" '
                     f'fill="var(--ck-fg)">{esc(line)}</text>')
    if n.get("sub"):
        sub_y = start + len(lines) * LINE + 2
        for i, line in enumerate(_wrap(n["sub"], w - 2 * inset, SUB_FONT)):
            parts.append(f'<text x="{ax:g}" y="{sub_y + i * (SUB_FONT + 3):g}" '
                         f'text-anchor="{anchor}" font-size="{SUB_FONT:g}" '
                         f'fill="var(--ck-muted)">{esc(line)}</text>')
    parts.append("</g>")
    return "".join(parts)


def _note_svg(t: dict, b: dict) -> str:
    x, y, w, h = _node_rect(t)
    tone = t.get("tone", "warn")
    fold = 14
    path = (f"M{x:g},{y:g} H{x + w - fold:g} L{x + w:g},{y + fold:g} V{y + h:g} H{x:g} Z")
    # Y4.7: a note honours text_size/bold like a node's own text; absent stays byte-identical
    # (the site emits no font-weight attribute, bold adds 700 — the X4.3 caption pattern).
    font, weight = _text_style(t, FONT, None)
    lines = _wrap(t.get("text", ""), w - 20, font)
    parts = [f'<g class="ck-note" data-id="{esc(t.get("id", "note"))}">',
             f'<path d="{path}" fill="var(--ck-tone-{tone}-fill)" '
             f'stroke="var(--ck-tone-{tone}-line)" stroke-width="1.2"/>',
             f'<path d="M{x + w - fold:g},{y:g} L{x + w - fold:g},{y + fold:g} '
             f'L{x + w:g},{y + fold:g}" fill="none" '
             f'stroke="var(--ck-tone-{tone}-line)" stroke-width="1.2"/>']
    weight_attr = f' font-weight="{weight}"' if weight is not None else ""
    for i, line in enumerate(lines):
        parts.append(f'<text x="{x + 10:g}" y="{y + 18 + i * LINE:g}" font-size="{font:g}"'
                     f'{weight_attr} fill="var(--ck-fg)">{esc(line)}</text>')
    parts.append("</g>")
    return "".join(parts)


# --------------------------------------------------------------------------- page

# v1.5 (Y2): the panel's shape picker and edge-head grids draw one small inline glyph per
# option (<=16x16, stroke/fill currentColor so they follow the theme). Panel markup owned by
# lane P; keyed by KINDS/HEADS, never a reorder of them.
_KIND_GLYPHS = {
    "box": '<rect x="2.5" y="3.5" width="11" height="9" rx="1.5"/>',
    "pill": '<rect x="1.5" y="4.5" width="13" height="7" rx="3.5"/>',
    "diamond": '<path d="M8 2.5 L13.5 8 L8 13.5 L2.5 8 Z"/>',
    "note": '<path d="M3.5 2.5 H10.5 L12.5 4.5 V13.5 H3.5 Z M10.5 2.5 V4.5 H12.5"/>',
    "caption": '<path d="M2 5 H14 M2 8.5 H11 M2 12 H12.5"/>',
    "cloud": '<path d="M5 12.5 H11 A2.4 2.4 0 0 0 11.6 8 A3.4 3.4 0 0 0 5.6 6.6 '
             'A2.7 2.7 0 0 0 5 12.5 Z"/>',
    "cylinder": '<path d="M4 4.5 V11.5 A4 1.7 0 0 0 12 11.5 V4.5 M4 4.5 A4 1.7 0 0 0 12 4.5 '
                'A4 1.7 0 0 0 4 4.5"/>',
    "document": '<path d="M3.5 2.5 H12.5 V10.5 C10.5 9.5 9.5 12.5 7.5 11.5 '
                'C6 10.8 5 12.3 3.5 11.7 Z"/>',
    "hexagon": '<path d="M5 2.5 H11 L13.5 8 L11 13.5 H5 L2.5 8 Z"/>',
    "parallelogram": '<path d="M5 3.5 H13.5 L11 12.5 H2.5 Z"/>',
    "server": '<rect x="4" y="2.5" width="8" height="11" rx="1.2"/><path d="M6 5.2 H9 M6 8 H9"/>'
              '<circle cx="10.4" cy="5.2" r="0.7" fill="currentColor" stroke="none"/>',
    "computer": '<rect x="2.5" y="3" width="11" height="7.5" rx="1"/>'
                '<path d="M8 10.5 V13 M5.5 13 H10.5"/>',
    "actor": '<circle cx="8" cy="3.6" r="1.7"/>'
             '<path d="M8 5.5 V10 M5 7 H11 M8 10 L5.6 13.5 M8 10 L10.4 13.5"/>',
    "area": '<rect x="2" y="2.5" width="12" height="11" rx="1.5" stroke-dasharray="2.5 1.8"/>'
            '<path d="M2 6 H14"/>',
    "table": '<rect x="2" y="3.5" width="12" height="9.5"/>'
             '<path d="M2 6.7 H14 M2 9.8 H14 M7 3.5 V13"/>',
}

_HEAD_GLYPHS = {
    "none": '<path d="M2 8 L8 2" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "arrow": '<path d="M0 0 L10 5 L0 10 Z" fill="currentColor" stroke="none"/>',
    "open": '<path d="M0 0 L10 5 L0 10" fill="none" stroke="currentColor" stroke-width="1.6"/>',
    "triangle": '<path d="M0 0 L10 5 L0 10 Z" fill="none" stroke="currentColor" stroke-width="1.2"/>',
    "circle": '<circle cx="6" cy="5" r="3.4" fill="currentColor" stroke="none"/>',
    "diamond": '<path d="M2 5 L6 1 L10 5 L6 9 Z" fill="currentColor" stroke="none"/>',
    "bar": '<path d="M8 1 V9" fill="none" stroke="currentColor" stroke-width="2"/>',
}

# v1.6 (Z1): the toolbar's glyphs — one entry per control, keyed by the control's own name, NOT by
# its `data-act` (the two `fit` controls, the readout and the button, share it). Same contract as
# the panel glyphs above: <=16x16, `currentColor` only, no colour of their own.
_BAR_GLYPHS = {
    "add": '<rect x="2.5" y="5.5" width="8" height="8" rx="1.5"/>'
           '<path d="M12.3 2.5 V 8.1 M9.5 5.3 H 15.1"/>',
    "addnote": '<path d="M3 3 H 9.5 L 12.3 5.8 V 13 H 3 Z M9.5 3 V 5.8 H 12.3"/>'
               '<path d="M11.6 8.4 V 13 M9.3 10.7 H 13.9"/>',
    "line": '<path d="M3 12.5 L 7 6 L 10.5 9.5 L 13.5 3.5"/>'
            '<circle cx="3" cy="12.5" r="1.2"/><circle cx="13.5" cy="3.5" r="1.2"/>',
    "select": '<path d="M4.5 2.5 L 12.6 8.1 L 8.9 8.9 L 10.8 13.3 L 8.9 14.1 L 7 9.7 L 4.5 12.2 Z"/>',
    "grid": '<rect x="2.5" y="2.5" width="11" height="11" rx="1"/>'
            '<path d="M6.17 2.5 V 13.5 M9.83 2.5 V 13.5 M2.5 6.17 H 13.5 M2.5 9.83 H 13.5"/>',
    "zoom-out": '<circle cx="6.9" cy="6.9" r="4.4"/><path d="M10.2 10.2 L 13.8 13.8 M4.8 6.9 H 9"/>',
    "zoom-in": '<circle cx="6.9" cy="6.9" r="4.4"/>'
               '<path d="M10.2 10.2 L 13.8 13.8 M4.8 6.9 H 9 M6.9 4.8 V 9"/>',
    "fit": '<path d="M2.5 6 V 2.5 H 6 M10 2.5 H 13.5 V 6 M13.5 10 V 13.5 H 10 '
           'M6 13.5 H 2.5 V 10"/>',
    "undo": '<path d="M5.4 5.6 H 9.6 A 3.5 3.5 0 0 1 9.6 12.6 H 5.6"/>'
            '<path d="M7.9 2.8 L 5.1 5.6 L 7.9 8.4"/>',
    "copy": '<rect x="5.6" y="5.6" width="7.9" height="7.9" rx="1.4"/>'
            '<path d="M10.4 5.6 V 3.7 A 1.2 1.2 0 0 0 9.2 2.5 H 3.7 A 1.2 1.2 0 0 0 2.5 3.7 '
            'V 9.2 A 1.2 1.2 0 0 0 3.7 10.4 H 5.6"/>',
    "download": '<path d="M8 2.5 V 10.2 M4.9 7.1 L 8 10.2 L 11.1 7.1 M3 13.5 H 13"/>',
    "reset": '<path d="M13.4 8.2 A 5.5 5.5 0 1 1 11.4 4.1"/><path d="M14 2.2 V 4.9 H 11.3"/>',
}

_CSS = """
:root{
  --ck-1:#4f8cff;--ck-2:#2fa37a;--ck-3:#d99429;--ck-4:#c0574f;--ck-5:#8465d6;--ck-6:#4aa6b8;
  --ck-fg:var(--foreground,#1f2328);
  --ck-muted:var(--muted-foreground,#6b7280);
  --ck-grid:var(--border,#e4e7ec);
  --ck-tone-default-fill:rgba(127,127,127,.10);--ck-tone-default-line:#8a93a0;
  --ck-tone-accent-fill:rgba(79,140,255,.14);  --ck-tone-accent-line:#4f8cff;
  --ck-tone-ok-fill:rgba(47,163,122,.16);      --ck-tone-ok-line:#2fa37a;
  --ck-tone-warn-fill:rgba(217,148,41,.18);    --ck-tone-warn-line:#c8892b;
  --ck-tone-danger-fill:rgba(192,87,79,.16);   --ck-tone-danger-line:#c0574f;
  --ck-tone-muted-fill:rgba(127,127,127,.06);  --ck-tone-muted-line:#9aa1ab;
  --ck-edge-default:#8a93a0;--ck-edge-accent:#4f8cff;--ck-edge-ok:#2fa37a;
  --ck-edge-warn:#c8892b;--ck-edge-danger:#c0574f;--ck-edge-muted:#a8afb8;
  --ck-font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{
    --ck-1:#6f9dff;--ck-2:#43c191;--ck-3:#e8b053;--ck-4:#e07d75;--ck-5:#a189e8;--ck-6:#5fc0d2;
    --ck-fg:var(--foreground,#e6e8eb);
    --ck-muted:var(--muted-foreground,#9aa1ab);
    --ck-grid:var(--border,#2c3138);
    --ck-tone-default-line:#9aa1ab;--ck-tone-accent-line:#6f9dff;--ck-tone-ok-line:#43c191;
    --ck-tone-warn-line:#e8b053;--ck-tone-danger-line:#e07d75;--ck-tone-muted-line:#79818c;
    --ck-edge-default:#9aa1ab;--ck-edge-accent:#6f9dff;--ck-edge-ok:#43c191;
    --ck-edge-warn:#e8b053;--ck-edge-danger:#e07d75;--ck-edge-muted:#79818c;
  }
}
html,body{background:transparent}
body{margin:0;padding:10px 12px;font:13px/1.45 var(--ck-font);color:var(--ck-fg)}
h1.ck-h1{font-size:16px;margin:0 0 2px}
.ck-sub{color:var(--ck-muted);font-size:11.5px;margin:0 0 10px}
.ck-board{display:block;width:100%;height:auto}
.ck-node text,.ck-note text,.ck-group text{user-select:none}
.ck-note text{white-space:pre}
/* v1.6 (Z1): the toolbar is ONE row at the pane's own 440px. Five groups in DOM order — create ·
   tool · view · history · file — with a .ck-sep between them, every control icon-only (16px
   currentColor SVG, aria-label + title) except the zoom readout, and the transient message in its
   own block (.ck-msg) UNDER the bar instead of inside the flex row. No control of the bar is
   wider than 36px, so 13 controls + 4 separators fit the pane's 416px content box in one line:
   measured, the old flat row of 12 text controls was 664px and wrapped to 476x61. `flex-wrap`
   stays, so a narrowed pane still degrades to a second row instead of overflowing.
   Every child of the bar is 22px tall — including the separators — so the whole row shares ONE
   top line, which is what the `barfit` self-test step measures. v1.9 (L1): the straight tool is
   gone, so the tool group is select + line again and the row's slack is one control wider. */
.ck-bar{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:0 0 4px}
.ck-bar button{font:inherit;font-size:12px;padding:4px 9px;border-radius:7px;
  border:1px solid var(--ck-grid);background:transparent;color:var(--ck-fg);cursor:pointer}
.ck-bar button:hover{border-color:var(--ck-muted)}
.ck-bar .ck-ico{box-sizing:border-box;flex:0 0 auto;width:24px;height:22px;padding:0;
  display:inline-flex;align-items:center;justify-content:center}
.ck-bar .ck-ico svg{display:block}
.ck-bar #ck-zoom{box-sizing:border-box;flex:0 0 auto;height:22px;min-width:36px;padding:0 4px;
  font-size:10.5px;font-variant-numeric:tabular-nums}
.ck-sep{align-self:stretch;flex:0 0 auto;width:1px;background:var(--ck-grid)}
/* At most ONE accent-marked control, and it is the pressed TOOL (Z1). Every other pressed control
   (the grid toggle) reads as "on" without borrowing the accent. */
.ck-bar button[data-tool][aria-pressed="true"]{border-color:var(--ck-1);color:var(--ck-1)}
.ck-bar .ck-ico[aria-pressed="true"]{border-color:var(--ck-muted);
  background:var(--ck-tone-default-fill)}
.ck-msg{display:block;min-height:15px;color:var(--ck-muted);font-size:11px;margin:0 0 6px}
.ck-panel{position:fixed;right:12px;top:12px;width:230px;padding:10px;border-radius:10px;
  border:1px solid var(--ck-grid);background:var(--ck-panel-bg,#fff);box-shadow:0 6px 22px rgba(0,0,0,.14);
  display:none;font-size:12px}
.ck-panel.on{display:block}
.ck-panel label{display:block;color:var(--ck-muted);font-size:10.5px;margin:6px 0 2px}
.ck-panel input,.ck-panel select,.ck-panel textarea{width:100%;box-sizing:border-box;font:inherit;
  padding:4px 6px;border:1px solid var(--ck-grid);border-radius:6px;background:transparent;
  color:var(--ck-fg)}
.ck-panel .ck-row{display:flex;gap:6px;margin-top:8px}
.ck-panel .ck-row button{flex:1}
/* v1.9 (D1-D5): the name line — the name row takes the remaining width, the delete cell is
   right-pulled and starts at the label's own line (the "node name" caption, whose margin-top is
   6px); the trash is an icon-only button. */
.ck-panel .ck-nameline{display:flex;gap:6px;align-items:flex-start;margin-top:4px}
.ck-panel .ck-nameline .ck-prow{flex:1;min-width:0}
.ck-panel .ck-delcell{flex:0 0 auto;margin-top:6px}
.ck-panel .ck-delcell button{box-sizing:border-box;width:24px;height:24px;padding:3px;
  border:1px solid var(--ck-grid);border-radius:6px;background:transparent;color:var(--ck-fg);
  cursor:pointer}
.ck-panel .ck-delcell svg{display:block;margin:0 auto}
/* v1.4 (X1): rows hide by selection — [hidden] must beat .ck-row's flex; checkboxes stay narrow */
.ck-panel [hidden]{display:none}
.ck-panel input[type=checkbox]{width:auto}
.ck-panel .ck-row .ck-hint{flex:1;text-align:center;margin:0;align-self:center}
.ck-hint{color:var(--ck-muted);font-size:11px;margin:6px 0 0}
/* v1.5 (Y1-Y3): collapsible sections, inline button groups, tone swatches, validation */
.ck-panel{max-height:calc(100vh - 24px);overflow-y:auto}
.ck-sec{border-top:1px solid var(--ck-grid);margin-top:6px;padding-top:2px}
.ck-sec>summary{cursor:pointer;color:var(--ck-muted);font-size:10.5px;letter-spacing:.04em;
  text-transform:uppercase;user-select:none}
.ck-panel .ck-group{display:flex;gap:4px;margin-top:2px}
.ck-panel .ck-group button{flex:1;font:inherit;font-size:11px;padding:3px 4px;
  border:1px solid var(--ck-grid);border-radius:6px;background:transparent;color:var(--ck-fg);
  cursor:pointer}
.ck-panel .ck-group button[aria-pressed="true"],.ck-panel .ck-shape[aria-pressed="true"],
.ck-panel .ck-headgrid button[aria-pressed="true"]{border-color:var(--ck-1);color:var(--ck-1)}
.ck-swrow{display:flex;gap:4px;flex-wrap:wrap;margin-top:2px;align-items:center}
.ck-sw{width:20px;height:20px;padding:0;border-radius:5px;border:1px solid var(--ck-grid);
  background:transparent;color:var(--ck-muted);cursor:pointer;font-size:11px;line-height:1}
.ck-sw[data-tone="default"]{background:var(--ck-tone-default-fill);border-color:var(--ck-tone-default-line)}
.ck-sw[data-tone="accent"]{background:var(--ck-tone-accent-fill);border-color:var(--ck-tone-accent-line)}
.ck-sw[data-tone="ok"]{background:var(--ck-tone-ok-fill);border-color:var(--ck-tone-ok-line)}
.ck-sw[data-tone="warn"]{background:var(--ck-tone-warn-fill);border-color:var(--ck-tone-warn-line)}
.ck-sw[data-tone="danger"]{background:var(--ck-tone-danger-fill);border-color:var(--ck-tone-danger-line)}
.ck-sw[data-tone="muted"]{background:var(--ck-tone-muted-fill);border-color:var(--ck-tone-muted-line)}
.ck-sw[data-tone=""]{border-style:dashed}
.ck-sw[aria-pressed="true"]{outline:2px solid var(--ck-1);outline-offset:1px}
.ck-kindgrid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:2px}
.ck-shape{padding:3px;border:1px solid var(--ck-grid);border-radius:6px;background:transparent;
  color:var(--ck-fg);cursor:pointer}
.ck-shape svg{display:block;margin:0 auto}
.ck-headgrid{display:flex;gap:4px;flex-wrap:wrap;margin-top:2px}
.ck-headgrid button{width:26px;height:22px;padding:2px;border:1px solid var(--ck-grid);
  border-radius:6px;background:transparent;color:var(--ck-fg);cursor:pointer}
.ck-headgrid svg{display:block;margin:0 auto}
.ck-colw{flex-wrap:wrap}
.ck-colw input{width:52px}
.ck-prow[aria-invalid="true"] input,.ck-prow[aria-invalid="true"] textarea{
  border-color:var(--ck-tone-danger-line)}
.ck-prow[aria-invalid="true"] .ck-hint{color:var(--ck-tone-danger-line)}
@media (prefers-color-scheme:dark){.ck-panel{--ck-panel-bg:#1b1f24}}
"""


def page(b: dict, *, interactive: bool = True, title: str | None = None,
         subtitle: str | None = None, note_text: str | None = None,
         provenance: str | None = None, bridge: bool = False) -> str:
    """HTML wrapper: always the static SVG; `interactive` adds the inline editor."""
    heading = title if title is not None else b.get("title") or "Board"
    need_w, need_h = bounds(b)
    document = dict(b)
    document["width"] = max(float(b.get("width") or 0), need_w)   # a diagram is never clipped
    document["height"] = max(float(b.get("height") or 0), need_h)

    body = [f"<h1 class='ck-h1'>{esc(heading)}</h1>"]
    if subtitle:
        body.append(f"<p class='ck-sub'>{esc(subtitle)}</p>")
    toolbar, script = "", ""
    if interactive:
        # v1.6 (Z1): the toolbar is five groups in DOM order, separated by .ck-sep — create
        # (`add`, `addnote`) · tool (`select`, `line`) · view (`grid`, `zoom-out`, the readout,
        # `zoom-in`, `fit`) · history (`undo`) · file (`copy`, `download`, `reset`). Three controls
        # used to open with `+` (two "make an object", one "zoom in"); every control is icon-only
        # now (16px currentColor SVG + aria-label + title), so the readout — the only text — is
        # what tells the user which way the glyphs go. `connect` is gone (Z4.5): the line tool does
        # the same job with a path the user owns. `select` is the way OUT of line mode (Z1.1 as
        # amended): pressed when no drawing tool is active, so the tool group carries exactly one
        # pressed button at any time. The transient message is NOT in this row: it is a
        # block of its own below the bar (.ck-msg), so the bar has one job.
        # The `data-act` values are the ones board_editor.js already wires; the tool buttons are
        # `data-tool="select"`/`data-tool="line"` (lane I's behaviour), never a data-act.
        def bar_button(name: str, label: str, hint: str, *, act: str | None = None,
                       tool: str | None = None, pressed: bool = False, on: bool = False) -> str:
            attrs = ['class="ck-ico"']
            if act:
                attrs.append(f'data-act="{act}"')
            if tool:
                attrs.append(f'data-tool="{tool}"')
            if pressed:
                attrs.append(f'aria-pressed="{"true" if on else "false"}"')
            attrs.append(f'aria-label="{esc(label)}"')
            attrs.append(f'title="{esc(hint or label)}"')
            return ('<button ' + " ".join(attrs) + '>'
                    '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" '
                    'stroke="currentColor" stroke-width="1.3" stroke-linecap="round" '
                    'stroke-linejoin="round">' + _BAR_GLYPHS[name] + '</svg></button>')

        sep = '<span class="ck-sep" aria-hidden="true"></span>'
        toolbar = (
            '<div class="ck-bar" role="toolbar" aria-label="board controls">'
            + bar_button("add", "add a box", "add a box to the board", act="add")
            + bar_button("addnote", "add a note", "add a note to the board", act="addnote")
            + sep
            + bar_button("select", "select",
                         "select and move items — the way out of line mode",
                         tool="select", pressed=True, on=True)
            + bar_button("line", "draw a line",
                         "draw a line: click the canvas for each point — every point locks to "
                         "the nearest 45° step, Shift frees the angle and anchors the ends — "
                         "Enter or a double-click commits, Esc cancels", tool="line", pressed=True)
            + sep
            + bar_button("grid", "grid", "show the background grid", act="grid", pressed=True)
            + bar_button("zoom-out", "zoom out", "zoom out", act="zoom-out")
            + '<button id="ck-zoom" data-act="fit" aria-label="zoom level — click to fit" '
              'title="the current zoom level; click to fit the whole board"></button>'
            + bar_button("zoom-in", "zoom in", "zoom in", act="zoom-in")
            + sep
            + bar_button("undo", "undo", "undo the last change", act="undo")
            + sep
            + bar_button("copy", "copy JSON", "copy the board JSON", act="copy")
            + bar_button("download", "download JSON", "download the board JSON", act="download")
            + bar_button("reset", "reset",
                         "reset to the board as it was loaded — undoable, there is no dialog",
                         act="reset")
            + '</div>'
            + '<div class="ck-msg" id="ck-status" role="status"></div>'
        )
        # v1.5 (Y1/Y2/Y3/Y5): the panel is a set of collapsible <details> sections (content,
        # shape, layout, parts, table, edge, arrange, cell — all open by default, the collapsed
        # state is session-only and never persisted). The dropdowns for tone/kind/align/split/
        # text-size/head-size/start/end are inline button groups now: the GROUP keeps the id the
        # select had, every button carries its data- attribute and aria-pressed. Free text and
        # numbers stay real inputs; the edge style/route and the attach sides keep <select>s —
        # long-named, low-frequency options with no meaningful glyph. Every editable control has
        # a .ck-hint[data-hint-for]; the editor replaces the hint with the reason when a value is
        # invalid (Y3). X1's selection-driven row visibility is unchanged — the row ids and the
        # PANEL_ROWS map in board_editor.js still decide, Y1 only groups the rows.
        tone_buttons = "".join(
            f'<button class="ck-sw" data-tone="{t}" aria-pressed="false" '
            f'aria-label="{t}" title="{t}"></button>' for t in TONES)
        kind_buttons = "".join(
            f'<button class="ck-shape" data-kind="{k}" aria-pressed="false" '
            f'aria-label="{k}" title="{k}"><svg width="16" height="16" viewBox="0 0 16 16" '
            f'fill="none" stroke="currentColor" stroke-width="1.2">{_KIND_GLYPHS[k]}</svg>'
            f'</button>' for k in KINDS)
        head_buttons = "".join(
            f'<button data-end="{hd}" aria-pressed="false" aria-label="{hd}" title="{hd}">'
            f'<svg width="14" height="14" viewBox="0 0 10 10">{_HEAD_GLYPHS[hd]}</svg></button>'
            for hd in HEADS)
        none_swatch = ('<button class="ck-sw" data-tone="" data-cell-tone="" aria-pressed="false" '
                       'aria-label="no tone" title="no tone">∅</button>')
        # Y5: the cell tone row carries data-cell-tone next to data-tone (the row edits the
        # picked cell, not the item) — the empty swatch clears the cell's own tone.
        cell_tone_buttons = "".join(
            f'<button class="ck-sw" data-tone="{t}" data-cell-tone="{t}" aria-pressed="false" '
            f'aria-label="{t}" title="{t}"></button>' for t in TONES)
        panel = (
            '<div class="ck-panel" id="ck-panel">'
            '<div><b id="ck-panel-id"></b></div>'
            '<div class="ck-hint" id="ck-multi-hint" hidden></div>'
            # v1.9 (D1-D5): the name line — a flex row of its own at the panel's TOP holding
            # the name row (#ck-row-text: id, hiding and PANEL_ROWS membership unchanged) and
            # a right-pulled cell with the delete control, so the trash sits on the name's own
            # line for a named item, and at the panel's top-right for a table, an edge or a
            # line (the name row hides for those; the cell is NOT a ck-row-* id and stays out
            # of PANEL_ROWS, so it never inherits that hiding). The glyph matches the desktop's
            # own trash: lid + can + two ribs.
            '<div class="ck-nameline">'
            '<div class="ck-prow" id="ck-row-text"><label>text</label><input id="ck-text">'
            '<span class="ck-hint" data-hint-for="ck-text">the main text of the item</span></div>'
            '<div class="ck-delcell"><button id="ck-delete" '
            'aria-label="delete the selected item" title="delete the selected item (Del)">'
            '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
            'stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M2.4 4 H 13.6 M6.2 4 V 2.6 H 9.8 V 4"/>'
            '<path d="M3.9 4 L 4.6 13.4 H 11.4 L 12.1 4"/>'
            '<path d="M6.6 6.6 V 11 M9.4 6.6 V 11"/></svg></button></div>'
            '</div>'
            # Y1 content: sub, text size, bold (the text row moved to the name line, v1.9 D1).
            '<details class="ck-sec" data-section="content" open><summary>content</summary>'
            '<div class="ck-prow" id="ck-row-sub"><label>sub</label><input id="ck-sub">'
            '<span class="ck-hint" data-hint-for="ck-sub">a second line under the text</span></div>'
            '<div class="ck-prow" id="ck-row-text-size"><label>text size</label>'
            '<div class="ck-group" id="ck-text-size" role="group">'
            '<button data-size="small" aria-pressed="false">small</button>'
            '<button data-size="default" aria-pressed="false">default</button>'
            '<button data-size="large" aria-pressed="false">large</button></div>'
            '<span class="ck-hint" data-hint-for="ck-text-size">the text size of the item</span></div>'
            '<div class="ck-prow" id="ck-row-bold"><label>'
            '<input type="checkbox" id="ck-bold"> bold</label>'
            '<span class="ck-hint" data-hint-for="ck-bold">draw the text bold</span></div>'
            '</details>'
            # Y1 shape: kind glyph grid, tone swatches.
            '<details class="ck-sec" data-section="shape" open><summary>shape</summary>'
            '<div class="ck-prow" id="ck-row-kind"><label>shape</label>'
            '<div class="ck-kindgrid" id="ck-kind" role="group">' + kind_buttons + '</div>'
            '<span class="ck-hint" data-hint-for="ck-kind">the shape the item is drawn as</span></div>'
            '<div class="ck-prow" id="ck-row-tone"><label>tone</label>'
            '<div class="ck-swrow" id="ck-tone" role="group">' + tone_buttons + '</div>'
            '<span class="ck-hint" data-hint-for="ck-tone">the colour of the item</span></div>'
            '</details>'
            # Y1 layout: w/h (the same fields the resize handles write) and align.
            '<details class="ck-sec" data-section="layout" open><summary>layout</summary>'
            '<div class="ck-prow" id="ck-row-size"><label>w × h</label>'
            '<div class="ck-row"><input id="ck-w" type="number" min="1" step="1" aria-label="width">'
            '<input id="ck-h" type="number" min="1" step="1" aria-label="height"></div>'
            '<span class="ck-hint" data-hint-for="ck-w">width in board units</span>'
            '<span class="ck-hint" data-hint-for="ck-h">height in board units</span></div>'
            '<div class="ck-prow" id="ck-row-align"><label>align</label>'
            '<div class="ck-group" id="ck-align" role="group">'
            '<button data-align="left" aria-pressed="false">left</button>'
            '<button data-align="center" aria-pressed="false">center</button>'
            # Y4.5a/Y5: `right` is legal for a table (it is the default for its unaligned cells), so
            # the group carries it; the panel shows it only while a table is selected, which keeps the
            # node kinds' two-value group exactly as they were.
            '<button data-align="right" data-align-table-only="true" hidden '
            'aria-pressed="false">right</button></div>'
            '<span class="ck-hint" data-hint-for="ck-align">where the text sits; '
            'the default cell alignment on a table</span></div>' 
            '</details>'
            # Y1 parts: the split-box compartments, the count stepper (X2) and the direction.
            '<details class="ck-sec" data-section="parts" open><summary>parts</summary>'
            '<div class="ck-prow" id="ck-row-parts"><label>parts</label>'
            '<textarea id="ck-parts" rows="4"></textarea>'
            '<div class="ck-row"><button data-act="part-add" aria-label="add a part">+</button>'
            '<span class="ck-hint" id="ck-parts-count"></span>'
            '<button data-act="part-del" aria-label="remove the last part">−</button></div>'
            '<span class="ck-hint" id="ck-parts-hint" data-hint-for="ck-parts">'
            'one part per line</span></div>'
            '<div class="ck-prow" id="ck-row-split"><label>split</label>'
            '<div class="ck-group" id="ck-split" role="group">'
            '<button data-split="rows" aria-pressed="false">rows</button>'
            '<button data-split="columns" aria-pressed="false">columns</button></div>'
            '<span class="ck-hint" data-hint-for="ck-split">'
            'a direction (rows or columns), not a count</span></div>'
            '</details>'
            # Y1 table: the grid controls (X3.4) plus the Y5 rows — col widths, cells, header,
            # whole-row/column tones. Cells keep empty lines (unlike parts).
            '<details class="ck-sec" data-section="table" open><summary>table</summary>'
            '<div class="ck-prow" id="ck-row-rows"><label>rows</label>'
            '<div class="ck-row"><input id="ck-rows" type="number" min="1" step="1">'
            '<button data-act="rows-add" aria-label="add a row">+</button>'
            '<button data-act="rows-del" aria-label="remove a row">−</button></div>'
            '<span class="ck-hint" data-hint-for="ck-rows">how many rows the table has</span></div>'
            '<div class="ck-prow" id="ck-row-cols"><label>cols</label>'
            '<div class="ck-row"><input id="ck-cols" type="number" min="1" step="1">'
            '<button data-act="cols-add" aria-label="add a column">+</button>'
            '<button data-act="cols-del" aria-label="remove a column">−</button></div>'
            '<span class="ck-hint" data-hint-for="ck-cols">'
            'how many columns the table has</span></div>'
            '<div class="ck-prow" id="ck-row-colw"><label>col widths</label>'
            '<div class="ck-row ck-colw" id="ck-colw-row">'
            '<input type="number" id="ck-colw-0" min="0.1" step="any" aria-label="column 1 width">'
            '<input type="number" id="ck-colw-1" min="0.1" step="any" aria-label="column 2 width">'
            '</div><span class="ck-hint" data-hint-for="ck-colw-0">'
            '1 by default; a column is its share of the width</span></div>'
            '<div class="ck-prow" id="ck-row-cells"><label>cells</label>'
            '<textarea id="ck-cells" rows="4"></textarea>'
            '<span class="ck-hint" id="ck-cells-hint" data-hint-for="ck-cells">'
            'one cell per line, empty lines kept</span></div>'
            '<div class="ck-prow" id="ck-row-header"><label>'
            '<input type="checkbox" id="ck-header"> header row</label>'
            '<span class="ck-hint" data-hint-for="ck-header">the first row is a bold header</span></div>'
            '<div class="ck-prow" id="ck-row-rowtones"><label>row tones</label>'
            '<div id="ck-rowtones"></div>'
            '<span class="ck-hint" data-hint-for="ck-rowtones">a colour for a whole row</span></div>'
            '<div class="ck-prow" id="ck-row-coltones"><label>col tones</label>'
            '<div id="ck-coltones"></div>'
            '<span class="ck-hint" data-hint-for="ck-coltones">a colour for a whole column</span></div>'
            '</details>'
            # Y1 edge: label, style, route, the two head grids and the attach sides.
            '<details class="ck-sec" data-section="edge" open><summary>edge</summary>'
            '<div class="ck-prow" id="ck-row-label"><label>label</label><input id="ck-label">'
            '<span class="ck-hint" data-hint-for="ck-label">'
            'text near the middle of the edge</span></div>'
            '<div class="ck-prow" id="ck-row-style"><label>style</label><select id="ck-style">'
            '<option value="solid">solid</option><option value="dashed">dashed</option>'
            '<option value="dotted">dotted</option></select>'
            '<span class="ck-hint" data-hint-for="ck-style">solid, dashed or dotted</span></div>'
            '<div class="ck-prow" id="ck-row-route"><label>route</label><select id="ck-route">'
            '<option value="auto">auto</option><option value="straight">straight</option>'
            '<option value="h">h</option><option value="v">v</option></select>'
            '<span class="ck-hint" data-hint-for="ck-route">how the edge bends</span></div>'
            '<div class="ck-prow" id="ck-row-start"><label>start</label>'
            '<div class="ck-headgrid" id="ck-start" role="group">' + head_buttons + '</div>'
            '<span class="ck-hint" data-hint-for="ck-start">the arrow head at the start</span></div>'
            '<div class="ck-prow" id="ck-row-end"><label>end</label>'
            '<div class="ck-headgrid" id="ck-end" role="group">' + head_buttons + '</div>'
            '<span class="ck-hint" data-hint-for="ck-end">the arrow head at the end</span></div>'
            '<div class="ck-prow" id="ck-row-from-side"><label>from side</label>'
            '<select id="ck-from-side">'
            + "".join(f'<option value="{s}">{s}</option>' for s in SIDES) +
            '</select><span class="ck-hint" data-hint-for="ck-from-side">'
            'the side the edge leaves from</span></div>'
            # v1.7 (A6.2): the position along the side, the v1.5 button-group idiom; hidden
            # until the wiring shows it for an edge selection (hidden, never disabled, A6.3).
            '<div class="ck-prow" id="ck-row-from-at" hidden><label>from position</label>'
            '<div class="ck-group" id="ck-from-at" role="group">'
            '<button data-at="0.25" aria-pressed="false" aria-label="from position 25 %" '
            'title="leave the from side a quarter along">25</button>'
            '<button data-at="0.5" aria-pressed="false" aria-label="from position 50 %" '
            'title="leave the from side at its middle">50</button>'
            '<button data-at="0.75" aria-pressed="false" aria-label="from position 75 %" '
            'title="leave the from side three quarters along">75</button></div>'
            '<span class="ck-hint" data-hint-for="ck-from-at">'
            'where on the from side the edge leaves — 50 % by default</span></div>'
            '<div class="ck-prow" id="ck-row-to-side"><label>to side</label>'
            '<select id="ck-to-side">'
            + "".join(f'<option value="{s}">{s}</option>' for s in SIDES) +
            '</select><span class="ck-hint" data-hint-for="ck-to-side">'
            'the side the edge arrives at</span></div>'
            '<div class="ck-prow" id="ck-row-to-at" hidden><label>to position</label>'
            '<div class="ck-group" id="ck-to-at" role="group">'
            '<button data-at="0.25" aria-pressed="false" aria-label="to position 25 %" '
            'title="arrive a quarter along the to side">25</button>'
            '<button data-at="0.5" aria-pressed="false" aria-label="to position 50 %" '
            'title="arrive at the middle of the to side">50</button>'
            '<button data-at="0.75" aria-pressed="false" aria-label="to position 75 %" '
            'title="arrive three quarters along the to side">75</button></div>'
            '<span class="ck-hint" data-hint-for="ck-to-at">'
            'where on the to side the edge arrives — 50 % by default</span></div>'
            '<div class="ck-prow" id="ck-row-head-size"><label>head size</label>'
            '<div class="ck-group" id="ck-head-size" role="group">'
            '<button data-head="small" aria-pressed="false">small</button>'
            '<button data-head="medium" aria-pressed="false">medium</button>'
            '<button data-head="large" aria-pressed="false">large</button></div>'
            '<span class="ck-hint" data-hint-for="ck-head-size">'
            'the size of the arrow heads</span></div>'
            '</details>'
            # Y1 arrange: X7 — visible with 2+ geometric items selected; spread with 3+.
            '<details class="ck-sec" data-section="arrange" open><summary>arrange</summary>'
            '<div class="ck-prow" id="ck-row-arrange" hidden><label>arrange</label>'
            '<div class="ck-row">'
            '<button data-act="align-left" aria-label="align left">⇤</button>'
            '<button data-act="align-h-centre" aria-label="align horizontal centre">↔</button>'
            '<button data-act="align-right" aria-label="align right">⇥</button>'
            '<button data-act="align-top" aria-label="align top">⤒</button>'
            '<button data-act="align-v-middle" aria-label="align vertical middle">↕</button>'
            '<button data-act="align-bottom" aria-label="align bottom">⤓</button>'
            '</div>'
            '<div class="ck-row" id="ck-row-spread" hidden>'
            '<button data-act="spread-h" aria-label="spread horizontally">⇢</button>'
            '<button data-act="spread-v" aria-label="spread vertically">⇣</button>'
            '</div></div>'
            '</details>'
            # Y1 cell (Y5): the row/col picker, the cell text, its own alignment and tone.
            '<details class="ck-sec" data-section="cell" open><summary>cell</summary>'
            '<div class="ck-prow" id="ck-row-cellpick"><label>cell</label>'
            '<div class="ck-row"><input type="number" id="ck-cell-row" min="1" step="1" value="1" '
            'aria-label="cell row"><input type="number" id="ck-cell-col" min="1" step="1" '
            'value="1" aria-label="cell column"></div>'
            '<span class="ck-hint" data-hint-for="ck-cell-row">'
            'row of the cell being edited, 1-based</span>'
            '<span class="ck-hint" data-hint-for="ck-cell-col">'
            'column of the cell being edited, 1-based</span></div>'
            '<div class="ck-prow" id="ck-row-celltext"><label>cell text</label>'
            '<input id="ck-cell-text">'
            '<span class="ck-hint" data-hint-for="ck-cell-text">'
            'the text of the picked cell</span></div>'
            '<div class="ck-prow" id="ck-row-cellalign"><label>cell align</label>'
            '<div class="ck-group" id="ck-cell-align" role="group">'
            '<button data-cell-align="inherit" aria-pressed="false" aria-label="inherit" '
            'title="inherit">–</button>'
            '<button data-cell-align="left" aria-pressed="false">left</button>'
            '<button data-cell-align="center" aria-pressed="false">center</button>'
            '<button data-cell-align="right" aria-pressed="false">right</button></div>'
            '<span class="ck-hint" data-hint-for="ck-cell-align">'
            'alignment of the picked cell; inherit follows the table</span></div>'
            '<div class="ck-prow" id="ck-row-celltone"><label>cell tone</label>'
            '<div class="ck-swrow" id="ck-cell-tone" role="group">'
            + cell_tone_buttons + none_swatch + '</div>'
            '<span class="ck-hint" data-hint-for="ck-cell-tone">'
            'the colour of the picked cell; the empty swatch inherits</span></div>'
            '</details>'
            # v1.6 (Z6): the LINE section. A line is its own kind (panelKind), so this section is
            # hidden — not disabled — for every other selection and the node/edge sections are
            # hidden for a line. Its rows are exactly the fields whose change alters the picture:
            # a line has no text, no size and no table, so those rows are ABSENT here rather than
            # inert. Same controls as v1.5: the parts row's count stepper with a live hint, the
            # tone swatches, an inline style group (the v1.5 button-group idiom the split/head-size
            # rows already use) and the edge's own two head grids. The point stepper appends at the
            # end of the path and removes the last point, never below 2 (Z6.1).
            '<details class="ck-sec" data-section="line" open><summary>line</summary>'
            '<div class="ck-prow" id="ck-row-points"><label>points</label>'
            '<div class="ck-row"><button data-act="point-add" aria-label="append a point at the end" '
            'title="append a point at the end of the path">+</button>'
            '<span class="ck-hint" id="ck-points-count"></span>'
            '<button data-act="point-del" aria-label="remove the last point" '
            'title="remove the last point — never below 2">−</button></div>'
            '<span class="ck-hint" id="ck-points-hint" data-hint-for="ck-points">'
            'append at the end or remove the last point</span></div>'
            '<div class="ck-prow" id="ck-row-line-tone"><label>tone</label>'
            '<div class="ck-swrow" id="ck-line-tone" role="group">' + tone_buttons + '</div>'
            '<span class="ck-hint" data-hint-for="ck-line-tone">the colour of the line</span></div>'
            '<div class="ck-prow" id="ck-row-line-style"><label>style</label>'
            '<div class="ck-group" id="ck-line-style" role="group">'
            '<button data-style="solid" aria-pressed="false">solid</button>'
            '<button data-style="dashed" aria-pressed="false">dashed</button>'
            '<button data-style="dotted" aria-pressed="false">dotted</button></div>'
            '<span class="ck-hint" data-hint-for="ck-line-style">solid, dashed or dotted</span></div>'
            '<div class="ck-prow" id="ck-row-line-start"><label>start</label>'
            '<div class="ck-headgrid" id="ck-line-start" role="group">' + head_buttons + '</div>'
            '<span class="ck-hint" data-hint-for="ck-line-start">'
            'the head at the start of the line</span></div>'
            '<div class="ck-prow" id="ck-row-line-end"><label>end</label>'
            '<div class="ck-headgrid" id="ck-line-end" role="group">' + head_buttons + '</div>'
            '<span class="ck-hint" data-hint-for="ck-line-end">'
            'the head at the end of the line</span></div>'
            # v1.7 (A6.1): the line's own from/to sub-rows — a side select per end plus the
            # position group (25/50/75, data-at on the buttons), the same control shape the
            # edge section uses. Hidden until the wiring shows them (A6.3: the position row is
            # visible only when the end's anchor resolves, and both are hidden — not disabled —
            # for a free end).
            '<div class="ck-prow" id="ck-row-line-from-side" hidden><label>from side</label>'
            '<select id="ck-line-from-side">'
            + "".join(f'<option value="{s}">{s}</option>' for s in SIDES) +
            '</select><span class="ck-hint" data-hint-for="ck-line-from-side">'
            'the side of the item the line starts on</span></div>'
            '<div class="ck-prow" id="ck-row-line-from-at" hidden><label>from position</label>'
            '<div class="ck-group" id="ck-line-from-at" role="group">'
            '<button data-at="0.25" aria-pressed="false" aria-label="from position 25 %" '
            'title="start a quarter along the from side">25</button>'
            '<button data-at="0.5" aria-pressed="false" aria-label="from position 50 %" '
            'title="start at the middle of the from side">50</button>'
            '<button data-at="0.75" aria-pressed="false" aria-label="from position 75 %" '
            'title="start three quarters along the from side">75</button></div>'
            '<span class="ck-hint" data-hint-for="ck-line-from-at">'
            'where on the side the line starts — 50 % by default</span></div>'
            '<div class="ck-prow" id="ck-row-line-to-side" hidden><label>to side</label>'
            '<select id="ck-line-to-side">'
            + "".join(f'<option value="{s}">{s}</option>' for s in SIDES) +
            '</select><span class="ck-hint" data-hint-for="ck-line-to-side">'
            'the side of the item the line ends on</span></div>'
            '<div class="ck-prow" id="ck-row-line-to-at" hidden><label>to position</label>'
            '<div class="ck-group" id="ck-line-to-at" role="group">'
            '<button data-at="0.25" aria-pressed="false" aria-label="to position 25 %" '
            'title="end a quarter along the to side">25</button>'
            '<button data-at="0.5" aria-pressed="false" aria-label="to position 50 %" '
            'title="end at the middle of the to side">50</button>'
            '<button data-at="0.75" aria-pressed="false" aria-label="to position 75 %" '
            'title="end three quarters along the to side">75</button></div>'
            '<span class="ck-hint" data-hint-for="ck-line-to-at">'
            'where on the side the line ends — 50 % by default</span></div>'
            '</details>'
            # v1.9 (D1): the bottom row keeps close alone — delete lives on the name line.
            '<div class="ck-row"><button id="ck-close">close</button></div></div>'
        )
        with open(EDITOR, "r", encoding="utf-8") as handle:
            flag = "<script>window.CK_BOARD_BRIDGE = true;</script>\n" if bridge else ""
            script = f"{flag}<script>\n{handle.read()}\n</script>\n"
        # The editor re-renders from data, so the payload is always embedded.
        body.append(toolbar)
        body.append(f'<script type="application/json" id="ck-board">{json.dumps(document, ensure_ascii=False)}</script>')
        body.append(panel)
        body.append(f'<div id="ck-board-host">{render_svg(document)}</div>')
    else:
        body.append(render_svg(document))
    if note_text:
        body.append(f"<p class='ck-hint'>{esc(note_text)}</p>")
    tail = f"<!-- {esc(provenance)} -->\n" if provenance else ""
    return ("<!doctype html>\n"
            f"<html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(heading)}</title><style>{_CSS}</style></head>\n<body>\n"
            + "\n".join(x for x in body if x) + "\n" + script + tail + "</body></html>\n")


def report(path: str, b: dict, **kwargs) -> str:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page(b, **kwargs))
    return os.path.abspath(path)


def verify(document: str) -> dict:
    """Static checks: no external reference, one board, and the JSON payload round-trips."""
    lowered = document.lower()
    problems = []
    for needle in ("<script src", "<link ", "@import", "url(http", "src='http", 'src="http'):
        if needle in lowered:
            problems.append(f"external reference found: {needle}")
    if 'class="ck-board"' not in lowered and "ck-board-host" not in lowered:
        problems.append("no board SVG in the document")
    payload = None
    marker = 'id="ck-board">'
    if marker in document:
        raw = document.split(marker, 1)[1].split("</script>", 1)[0]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f"embedded board JSON is invalid: {exc}")
    if problems:
        raise AssertionError("; ".join(problems))
    return {"nodes": len((payload or {}).get("nodes", [])),
            "edges": len((payload or {}).get("edges", [])),
            "lines": len((payload or {}).get("lines", [])),   # Z3.5
            "bytes": len(document.encode("utf-8"))}


def find_chrome():
    import shutil
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def page_size(html_path: str, *, binary: str | None = None,
              window_width: int | None = None) -> tuple[int, int] | None:
    """The page's own rendered size, measured in the browser — in CSS pixels.

    A PNG's window must cover the whole PAGE (title + toolbar + the board scaled to the page
    width), not the board's canvas numbers: sized from the canvas, the picture cut off the bottom
    of every board whose content reaches low — measured: a note at y=460 of a 620-tall board was
    missing from a 900x620 shot and present in a 900x1000 one. The page is copied with a one-line
    script that puts the document's scroll size into the title, and `--dump-dom` reads it back.
    """
    import re
    import subprocess
    import tempfile
    binary = binary or find_chrome()
    if not binary or not os.path.isfile(html_path):
        return None
    with open(html_path, encoding="utf-8") as handle:
        document = handle.read()
    # The BODY's own size, not the document element's scroll size: the latter reports the viewport
    # when the window is taller than the content, which is how a measured 900x3913 PNG happened.
    probe = ('<script>document.title = document.body.scrollHeight + "x" + '
             'document.body.scrollWidth;</script>')
    document = document.replace("</body>", probe + "</body>") if "</body>" in document \
        else document + probe
    with tempfile.TemporaryDirectory(prefix="ck-measure-") as tmp:
        measured = os.path.join(tmp, "size.html")
        with open(measured, "w", encoding="utf-8") as handle:
            handle.write(document)
        args = [binary, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars"]
        # The board scales with the window width, so measure at the width we are going to shoot, and
        # keep the window SHORT so the body's height is the content's, not the viewport's.
        args.append(f"--window-size={int(window_width or 1000)},600")
        dom = subprocess.run(args + ["--virtual-time-budget=4000", "--dump-dom",
                                     "file://" + os.path.abspath(measured)],
                             capture_output=True, text=True, timeout=300).stdout
    hit = re.search(r"<title>(\d+)x(\d+)</title>", dom)
    if not hit:
        return None
    height, width = int(hit.group(1)), int(hit.group(2))
    if not (width and height):
        return None
    return width, height


def render_png(html_path: str, png_path: str, *, size: str | None = None,
               width: int | None = None, budget_ms: int = 6000, extra: str = "") -> str:
    """Screenshot the page. `size` is "W,H"; when it is omitted the page is MEASURED at the
    requested `width` (or its own natural width) so the picture is never short of the board's
    bottom — see `page_size`."""
    import subprocess
    binary = find_chrome()
    if not binary:
        raise RuntimeError("no Chrome/Chromium found")
    if not size:
        measured = page_size(html_path, binary=binary, window_width=width) or (width or 1000, 700)
        across = min(max(int(width or measured[0]), 320), 4000)
        down = min(max(measured[1] + 8, 240), 4000)   # +8: subpixel rounding at the bottom edge
        size = f"{across},{down}"
    subprocess.run([binary, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                    f"--window-size={size}", f"--virtual-time-budget={budget_ms}",
                    f"--screenshot={png_path}", "file://" + os.path.abspath(html_path) + extra],
                   check=True, capture_output=True, timeout=300)
    return png_path


# ------------------------------------------------------- v1.6 (Z9): the exact-viewport harness

# The desktop pane declares `width: '440px'` (desktop/plugin.js) and the toolbar wraps below ~688px
# of content, so the pane's toolbar is the layout that must be measured. Chrome's new headless
# CLAMPS a window to 500px (`--window-size=440,900` reports `innerWidth` 500 — measured), which is
# how a 440px claim quietly becomes a 500px measurement. The harness therefore sets the viewport
# through CDP (`Emulation.setDeviceMetricsOverride`) and the `barfit` step asserts the width it
# actually got before it says anything about the layout.
PANE_VIEWPORT = (440, 900)


def _free_port() -> int:
    """A loopback port nothing is listening on, for the browser's DevTools endpoint."""
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _CdpSocket:
    """One DevTools page target, driven over a raw websocket: a minimal RFC-6455 client.

    The bundled library has no dependency to spend and no websocket client in the standard
    library, and the CDP client below needs exactly two things from the transport — masked text
    frames out, unmasked ones (with ping/pong and continuation) in.
    """

    def __init__(self, sock):
        self._sock = sock
        self._buf = b""
        self._id = 0

    @classmethod
    def open(cls, url: str, timeout: int = 60) -> "_CdpSocket":
        import base64
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("the DevTools websocket closed during the handshake")
            head += chunk
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status:
            raise RuntimeError(f"the DevTools websocket refused the handshake: {status}")
        client = cls(sock)
        client._buf = head.split(b"\r\n\r\n", 1)[1]
        return client

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def _feed(self, count: int) -> None:
        while len(self._buf) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise RuntimeError("the DevTools websocket closed")
            self._buf += chunk

    def _send(self, payload: bytes, opcode: int = 0x1) -> None:
        import struct
        header = bytes([0x80 | opcode])
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", size)
        self._sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _frame(self) -> tuple[bool, int, bytes]:
        import struct
        self._feed(2)
        first, second = self._buf[0], self._buf[1]
        fin, opcode, masked, size = first & 0x80, first & 0x0F, second & 0x80, second & 0x7F
        offset = 2
        if size == 126:
            self._feed(4)
            size, offset = struct.unpack(">H", self._buf[2:4])[0], 4
        elif size == 127:
            self._feed(10)
            size, offset = struct.unpack(">Q", self._buf[2:10])[0], 10
        if masked:
            offset += 4
        self._feed(offset + size)
        payload = self._buf[offset:offset + size]
        self._buf = self._buf[offset + size:]
        return bool(fin), opcode, payload

    def _message(self) -> str:
        data = b""
        while True:
            fin, opcode, payload = self._frame()
            if opcode == 0x9:                      # ping → pong
                self._send(payload, opcode=0xA)
                continue
            if opcode == 0x8:
                raise RuntimeError("the DevTools websocket sent a close frame")
            data += payload
            if fin:
                return data.decode("utf-8", "replace")

    def call(self, method: str, params: dict | None = None) -> dict:
        """One CDP command; the events that arrive while waiting for its reply are dropped."""
        self._id += 1
        self._send(json.dumps({"id": self._id, "method": method, "params": params or {}}).encode())
        while True:
            message = json.loads(self._message())
            if message.get("id") == self._id:
                return message


def _cdp_selftest(html_path: str, width: int, height: int, timeout: int,
                  mode: str = "selftest") -> str:
    """The editor's `#<mode>` report, read out of a browser whose VIEWPORT is exactly
    `width`×`height` — see `PANE_VIEWPORT`.

    No virtual-time budget is used: the page is driven for real, so `requestAnimationFrame` (the
    editor renders on the next frame) and every pointer event the self-test dispatches behave as
    they do in a pane. Raises when the endpoint does not come up or the page never reports.
    """
    import shutil
    import subprocess
    import tempfile
    import time
    import urllib.request
    binary = find_chrome()
    if not binary:
        raise RuntimeError("no Chrome/Chromium found")
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="ck-cdp-")
    browser = subprocess.Popen(
        [binary, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         f"--user-data-dir={profile}", f"--remote-debugging-port={port}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Loopback is never proxied: an ALL_PROXY in the environment must not come between the harness
    # and its own browser.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + min(timeout, 60)
        target = None
        while time.time() < deadline and target is None:
            try:
                listing = json.load(opener.open(base + "/json/list", timeout=2))
            except OSError:
                time.sleep(0.2)
                continue
            pages = [entry for entry in listing if entry.get("type") == "page"]
            target = pages[0] if pages else None
        if target is None:
            raise RuntimeError("the DevTools endpoint did not come up")
        client = _CdpSocket.open(target["webSocketDebuggerUrl"])
        try:
            client.call("Emulation.setDeviceMetricsOverride",
                        {"width": int(width), "height": int(height),
                         "deviceScaleFactor": 1, "mobile": False})
            client.call("Page.navigate",
                        {"url": "file://" + os.path.abspath(html_path) + f"#{mode}"})
            deadline = time.time() + timeout
            while time.time() < deadline:
                answer = client.call("Runtime.evaluate", {
                    "expression": "(function () { var e = document.getElementById('ck-selftest');"
                                  " return e ? e.textContent : null; })()",
                    "returnByValue": True})
                report = (answer.get("result") or {}).get("result", {}).get("value")
                if report:
                    return report
                time.sleep(0.25)
            raise RuntimeError(f"the editor's self-test did not report within {timeout}s")
        finally:
            client.close()
    finally:
        browser.terminate()
        try:
            browser.wait(timeout=10)
        except subprocess.TimeoutExpired:
            browser.kill()
        shutil.rmtree(profile, ignore_errors=True)


def selftest(html_path: str, timeout: int = 300) -> dict:
    """Run the editor's built-in self-test in headless Chrome and return its JSON report.

    The page exercises the editor (select, edit, move, connect, add, export) when loaded with
    `#selftest` and writes the result into `<pre id="ck-selftest">`. This is the only check that
    proves the *interactive* half works — a screenshot cannot.

    v1.6 (Z9): `barfit` measures the toolbar at the pane's own width (440px), which a WINDOW flag
    cannot reach (new headless clamps a window to 500px), so that step runs in a SECOND load of the
    page at `PANE_VIEWPORT`, driven through CDP (`#selftest-pane`), and its report is merged into
    the one returned here. The first run keeps the window it always had: the functional steps are
    written in DOCUMENT units (the drag threshold, the grid landing `groupmove` asserts), so a
    440px window changes their numbers rather than their subject. When the browser cannot be driven
    through CDP, `barfit` is reported FALSE with the reason — never silently green.
    """
    import re
    import subprocess
    binary = find_chrome()
    if not binary:
        raise RuntimeError("no Chrome/Chromium found")
    dom = subprocess.run([binary, "--headless=new", "--disable-gpu", "--no-sandbox",
                          "--virtual-time-budget=8000", "--dump-dom",
                          "file://" + os.path.abspath(html_path) + "#selftest"],
                         check=True, capture_output=True, text=True, timeout=timeout).stdout
    hits = re.findall(r'<pre id="ck-selftest">(.*?)</pre>', dom, re.S)
    if not hits:
        raise AssertionError("self-test did not run (no #ck-selftest block in the DOM)")
    report = None
    for raw in reversed(hits):          # the first hits may be source text of the inlined script
        try:
            report = json.loads(html.unescape(raw))
            break
        except json.JSONDecodeError:
            continue
    if report is None:
        raise AssertionError("self-test block present but not valid JSON")
    try:
        pane = json.loads(_cdp_selftest(html_path, PANE_VIEWPORT[0], PANE_VIEWPORT[1], timeout,
                                        mode="selftest-pane"))
    except Exception as err:            # no viewport, no `barfit`: red, with the reason
        pane = {"ok": False, "steps": [["barfit", False]],
                "barfit": f"the pane's {PANE_VIEWPORT[0]}px viewport could not be set: {err}"}
    report["steps"] = list(report.get("steps") or []) + list(pane.get("steps") or [])
    report["ok"] = bool(report.get("ok")) and bool(pane.get("ok")) and \
        all(bool(step[1]) for step in report["steps"])
    if pane.get("barfit") is not None:
        report["barfit"] = pane["barfit"]
    if pane.get("error") is not None:
        report["paneError"] = pane["error"]
    return report


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save(path: str, b: dict) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(b, handle, ensure_ascii=False, indent=1)
    return os.path.abspath(path)


# --------------------------------------------------------------------------- demo + CLI

def demo() -> dict:
    """A small architecture board — the worked example shipped with the skill."""
    top, top_edges = flow(
        [("chat", "Hermes chat", "Telegram / desktop"), ("agent", "Session", "tools + skills"),
         ("out", "Artifact", "one HTML file")],
        x=40, y=70, gap=70, w=190, h=78,
        labels=["asks", "builds"])
    below = layout_row(
        [("chartkit", "chartkit", "SVG baseline + ECharts"),
         ("boardkit", "boardkit", "this board, editable"),
         ("plugin", "desktop plugin", "live panes, tools")],
        x=40, y=250, gap=70, w=190, h=74, tone="muted")
    return board(
        [*top, *below],
        [*top_edges,
         edge("chat", "chartkit", "renders", route="v", tone="accent"),
         edge("agent", "boardkit", "draws", route="v", style="dashed"),
         edge("out", "plugin", "or docks beside it", route="v", style="dotted", tone="muted")],
        groups=[group("g1", "Delivered in chat", 20, 40, 860, 142)],
        notes=[note("Drag a box, edit its text, connect two boxes, then export the JSON and "
                    "hand it back — the board is the only source of truth.", 780, 245, 280),
               note("The same JSON file is what the agent reads and rewrites; nothing is "
                    "hidden in a database.", 780, 355, 280)],
        title="How a chart gets into the chat")


def demo_layers() -> dict:
    """A layered board with a shared trunk (bus) between bands — the second worked example."""
    nodes, groups = layers(
        [[("chat", "Hermes chat", "Telegram, desktop"), ("cli", "hermes CLI", "one-shot runs")],
         [("tools", "agent tools", "board_apply"), ("sk", "skills", "chartkit, boardkit")],
         [("json", "board.json", "the document"), ("png", "rendered PNG", "for chat delivery")]],
        direction="down", gap_node=64, gap_layer=118, w=196, h=74,
        labels=["clients", "agent", "artifacts"])
    rects = {n["id"]: n for n in nodes}
    edges = bus_edges(rects, ["chat", "cli"], ["tools", "sk"], direction="v")
    edges += bus_edges(rects, ["tools", "sk"], ["json", "png"], direction="v", tone="accent")
    # One caption per bus, beside its trunk: the trunk carries the shared meaning, so four
    # edges do not each need a label fighting for the same few pixels.
    first, second, third = groups[0], groups[1], groups[2]
    captions = [
        caption("tool calls", first["x"] + first["w"] + 18,
                (first["y"] + first["h"] + second["y"]) / 2 - 8, 170),
        caption("writes / renders", second["x"] + second["w"] + 18,
                (second["y"] + second["h"] + third["y"]) / 2 - 8, 170),
    ]
    return board([*nodes, *captions], edges, groups,
                 notes=[note("Every edge between two bands runs into one shared trunk per "
                             "direction, so four connections read as a single bus instead of a web "
                             "of diagonals.", 580, 700, 300)],
                 title="Layered board with a bus")


def demo_graph() -> dict:
    """A request lifecycle placed by the auto-layout: a branch, a merge and a feedback loop.

    No coordinates anywhere in the source — the ranks come from the edges, the order comes from
    the barycentre sweeps, and the feedback edge is a broken cycle rather than a hang.
    """
    make = lambda nid, text, sub=None, kind="box", tone="default": box(  # noqa: E731
        nid, text, 0, 0, w=186, h=76, sub=sub, kind=kind, tone=tone)
    nodes = [
        make("msg", "Message arrives", "Telegram / desktop"),
        make("route", "Needs a tool?", kind="diamond"),
        make("tools", "Tool calls", "board, files, shell"),
        make("draft", "Draft answer"),
        make("verify", "Check against the goal"),
        make("send", "Send the reply"),
        make("log", "Session log", "transcript + state"),
    ]
    edges = [
        edge("msg", "route"),
        edge("route", "tools", "yes"),
        edge("route", "draft", "no"),
        edge("tools", "verify"),
        edge("draft", "verify"),
        edge("verify", "send"),
        edge("send", "log"),
        edge("verify", "draft", "another pass", style="dashed", tone="warn"),
    ]
    # A lifecycle reads left to right; a long chain in a vertical layout is just a tall column.
    preview = auto_layout(nodes, edges, direction="right")
    names = ["arrives", "routing", "work", "proof", "deliver", "record"]
    labels = [names[i] if i < len(names) else None for i in range(len(preview["layers"]))]
    document = board(nodes, edges, title="How a reply is produced")
    info = apply_auto_layout(document, direction="right", bands=True, labels=labels,
                             gap_node=44, gap_layer=96)
    document["nodes"].append(caption("auto-layout: %d ranks, %d crossings"
                                     % (len(info["layers"]), info["crossings"]),
                                     40, info["height"] + 12, 260))
    document["notes"] = [note("Edges decide the ranks, the barycentre sweeps decide the order, "
                              "and the dashed 'another pass' edge is a cycle the ranking breaks "
                              "instead of hanging on.", 340, info["height"] + 10, 560)]
    return document


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command = argv[1]
    if command == "demo":
        out = argv[2] if len(argv) > 2 else "/tmp/board.html"
        path = report(out, demo(), subtitle="boardkit demo", interactive="--static" not in argv)
        print(path)
        return 0
    if command == "demo-layers":
        out = argv[2] if len(argv) > 2 else "/tmp/board-layers.html"
        path = report(out, demo_layers(), subtitle="boardkit layered demo",
                      interactive="--static" not in argv)
        print(path)
        return 0
    if command == "demo-graph":
        out = argv[2] if len(argv) > 2 else "/tmp/board-graph.html"
        path = report(out, demo_graph(), subtitle="boardkit auto-layout demo",
                      interactive="--static" not in argv)
        print(path)
        return 0
    if command == "render" and len(argv) > 3:
        b = load(argv[2])
        path = report(argv[3], b, interactive="--static" not in argv)
        print(path)
        return 0
    print("usage: boardkit.py demo OUT.html | demo-layers OUT.html | demo-graph OUT.html | "
          "render BOARD.json OUT.html [--static]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
