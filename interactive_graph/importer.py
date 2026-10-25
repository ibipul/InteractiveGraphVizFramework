"""Read a network file uploaded from the browser.

Formats, picked by file extension:

  .csv .tsv .txt .edges .edgelist
      One edge per line: source, target and optionally a capacity, separated
      by commas, tabs or spaces. A header row is optional; with one, the
      capacity column may be called capacity, cap, weight or w. Lines
      starting with # or % are skipped. Edge lists carry no ground truth;
      use GraphML, GML or JSON for that.
  .graphml .gml
      Edge attribute capacity (or weight) becomes the capacity; node
      attribute truth, community, block, club, value or label_true becomes
      the ground truth.
  .json
      This app's format, or networkx node-link JSON ("nodes" + "links" or
      "edges").

Directed and multi-edges are merged into one undirected edge whose
capacity is the sum; self-loops are dropped.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import PurePath
from typing import Any

import networkx as nx

from .datasets import to_graph
from .graph import Graph, normalize

CAPACITY_KEYS = ("capacity", "cap", "weight", "w")
TRUTH_KEYS = ("truth", "community", "block", "club", "value", "label_true", "class")
EDGE_LIST = (".csv", ".tsv", ".txt", ".edges", ".edgelist", ".dat")


def read(name: str, data: bytes) -> tuple[Graph, str]:
    """Parse a file. Returns the graph and a one-line summary."""
    ext = PurePath(name).suffix.lower()
    text = data.decode("utf-8-sig", errors="replace")
    if ext == ".json":
        raw = json.loads(text)
        if "links" not in raw and "edges" in raw:
            raw["links"] = raw["edges"]
        if raw.get("nodes") and not isinstance(raw["nodes"][0].get("id"), int):
            g = nx.node_link_graph(raw, edges="links")
            return _from_nx(g, name, "JSON")
        graph = normalize(raw)
        graph["meta"].setdefault("name", PurePath(name).stem)
        return graph, f"{len(graph['nodes']):,} nodes, {len(graph['links']):,} edges"
    if ext == ".graphml":
        return _from_nx(nx.read_graphml(io.BytesIO(data)), name, "GraphML")
    if ext == ".gml":
        return _from_nx(nx.parse_gml(text, label="id"), name, "GML")
    if ext in EDGE_LIST or not ext:
        return _edge_list(text, name)
    raise ValueError(f"unsupported file type {ext!r}; use CSV/TSV edge list, GraphML, GML or JSON")


def _merge(g: nx.Graph) -> tuple[nx.Graph, int, int]:
    """Collapse to a simple undirected graph, summing capacities."""
    loops = nx.number_of_selfloops(g)
    out = nx.Graph()
    out.add_nodes_from(g.nodes(data=True))
    merged = 0
    for u, v, d in g.edges(data=True):
        if u == v:
            continue
        cap = next((d[k] for k in CAPACITY_KEYS if isinstance(d.get(k), (int, float))), None)
        if out.has_edge(u, v):
            merged += 1
            if cap is not None:
                out[u][v]["capacity"] = out[u][v].get("capacity", 0) + float(cap)
        else:
            out.add_edge(u, v, **({"capacity": float(cap)} if cap is not None else {}))
    return out, loops, merged


def _summary(graph: Graph, loops: int, merged: int) -> str:
    parts = [f"{len(graph['nodes']):,} nodes, {len(graph['links']):,} edges"]
    if merged:
        parts.append(f"{merged:,} repeated edges merged (capacities added)")
    if loops:
        parts.append(f"{loops:,} self-loops dropped")
    if graph["meta"].get("truth_names"):
        parts.append(f"{len(graph['meta']['truth_names'])} ground-truth communities")
    return "; ".join(parts)


def _from_nx(g: nx.Graph, name: str, kind: str) -> tuple[Graph, str]:
    g, loops, merged = _merge(g)
    truth_key = next((k for k in TRUTH_KEYS
                      if g.number_of_nodes() and all(k in d for _, d in g.nodes(data=True))), None)
    truth = {n: d[truth_key] for n, d in g.nodes(data=True)} if truth_key else None
    graph = to_graph(g, truth, {"name": PurePath(name).stem, "source": f"{kind} file {name}",
                                **({"truth_label": truth_key} if truth_key else {})},
                     capacity="capacity")
    return graph, _summary(graph, loops, merged)


def _edge_list(text: str, name: str) -> tuple[Graph, str]:
    lines = [line for line in text.splitlines() if line.strip() and line.lstrip()[0] not in "#%"]
    if not lines:
        raise ValueError("the file has no edges")
    sample = "\n".join(lines[:20])
    if "\t" in sample:
        rows = list(csv.reader(lines, delimiter="\t"))
    elif "," in sample:
        rows = list(csv.reader(lines))
    elif ";" in sample:
        rows = list(csv.reader(lines, delimiter=";"))
    else:
        rows = [line.split() for line in lines]
    rows = [[c.strip() for c in r] for r in rows if len(r) >= 2]

    cap_col: int | None = 2
    header = [c.lower() for c in rows[0]]
    if not _is_number(rows[0][-1]) and len(rows) > 1 and _is_number(rows[1][-1]) or \
            any(h in ("source", "target", "src", "dst", "from", "to", "u", "v") for h in header):
        rows = rows[1:]
        cap_col = next((header.index(k) for k in CAPACITY_KEYS if k in header), None)
    g = nx.MultiGraph()
    for r in rows:
        u, v = _node(r[0]), _node(r[1])
        attrs = {}
        if cap_col is not None and len(r) > cap_col and _is_number(r[cap_col]):
            attrs["capacity"] = float(r[cap_col])
        g.add_edge(u, v, **attrs)
    g2, loops, merged = _merge(g)
    graph = to_graph(g2, None, {"name": PurePath(name).stem, "source": f"edge list {name}"},
                     capacity="capacity")
    return graph, _summary(graph, loops, merged)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _node(s: str) -> Any:
    try:
        return int(s)
    except ValueError:
        return s
