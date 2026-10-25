"""The graph state and every edit the browser can make to it.

A graph is a plain dict, the same shape as the JSON files on disk:

    {"nodes": [{"id": 0, "nval": 1, "current": 0, "total": 1}, ...],
     "links": [{"source": 0, "target": 1, "lval": 1, "current": 0, "total": 1,
                "length": 1.0}, ...],
     "meta": {"name": "Karate club", ...}}

Node fields: `nval` is the node's group (a colour in the editor), `current`
/ `total` are load counters (the A and D keys). Optional: `label` (the
original name), `truth` (ground-truth community), `community` (found by
Ricci flow), `x` / `y` (starting position).

Link fields: `lval` weight, `current` / `total` load counters, `length` (the
edge length the Ricci flow evolves), and optionally `capacity`, `curvature`
and `pinned` (the flow leaves pinned lengths alone). Links are undirected
and stored with source < target, so there is at most one link between two
nodes.

`web/local.js` mirrors the basic edits for the server-less demo; keep the
two in step when changing one.
"""

from __future__ import annotations

import copy
import json
import math
import threading
from typing import Any, Callable

Graph = dict[str, Any]
StepFn = Callable[[Graph], Graph]

HISTORY_BYTES = 200_000_000   # total size of the undo history
NODE_EXTRAS = ("label", "truth", "community", "x", "y")
LINK_EXTRAS = ("capacity", "length", "curvature", "pinned")
NODE_FIELDS = ("nval", "current", "total")
LINK_FIELDS = ("lval", "current", "total", "capacity", "length", "pinned")


class GraphError(ValueError):
    """An edit that cannot be applied, e.g. a link to a missing node."""


def empty_graph() -> Graph:
    return {"nodes": [], "links": [], "meta": {}}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    return float(value) if math.isfinite(value) else None


def normalize(data: dict[str, Any]) -> Graph:
    """Clean up a graph loaded from a file or the browser.

    Fills in missing counters, accepts the older "group"/"weight" keys and
    "edges" for "links", drops links to missing nodes, self-links and
    duplicates, and sorts everything by id so the output is stable.
    """
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise GraphError("a graph needs a 'nodes' list")

    nodes: dict[int, dict[str, Any]] = {}
    for raw in data["nodes"]:
        nid = int(raw["id"])
        node = {
            "id": nid,
            "nval": _int(raw.get("nval", raw.get("group", 1)), 1),
            "current": _int(raw.get("current", 0), 0),
            "total": _int(raw.get("total", 1), 1),
        }
        for key in NODE_EXTRAS:
            value = raw.get(key)
            if key == "label" and value is not None:
                node[key] = str(value)
            elif key in ("truth", "community") and value is not None:
                node[key] = _int(value, 0)
            elif key in ("x", "y") and _num(value) is not None:
                node[key] = round(_num(value), 1)
        nodes[nid] = node

    links: dict[tuple[int, int], dict[str, Any]] = {}
    for raw in data.get("links", data.get("edges", [])):
        s, t = _endpoint(raw["source"]), _endpoint(raw["target"])
        if s == t or s not in nodes or t not in nodes:
            continue
        s, t = min(s, t), max(s, t)
        if (s, t) in links:
            continue
        link = {
            "source": s,
            "target": t,
            "lval": _int(raw.get("lval", raw.get("weight", 1)), 1),
            "current": _int(raw.get("current", 0), 0),
            "total": _int(raw.get("total", 1), 1),
        }
        for key in LINK_EXTRAS:
            if key == "pinned":
                if raw.get("pinned"):
                    link["pinned"] = True
            elif _num(raw.get(key)) is not None:
                link[key] = _num(raw[key])
        links[(s, t)] = link

    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return {
        "nodes": [nodes[k] for k in sorted(nodes)],
        "links": [links[k] for k in sorted(links)],
        "meta": copy.deepcopy(meta),
    }


def _endpoint(value: Any) -> int:
    # D3 replaces link endpoints with node objects; accept either form.
    return int(value["id"] if isinstance(value, dict) else value)


def fill_lengths(graph: Graph) -> None:
    """Give every link a length: 1 / capacity, scaled so the mean is 1.

    Only touches links without one, unless none have one yet.
    """
    links = graph["links"]
    missing = [l for l in links if "length" not in l]
    if not missing:
        return
    if len(missing) == len(links):
        raw = [1 / l["capacity"] if l.get("capacity", 0) > 0 else 1.0 for l in links]
        scale = len(raw) / sum(raw) if raw else 1.0
        for l, r in zip(links, raw):
            l["length"] = r * scale
    else:
        for l in missing:
            l["length"] = 1.0


class GraphState:
    """The live graph plus undo/redo history.

    `apply(op)` takes one edit message from the browser, e.g.
    {"op": "add_link", "source": 1, "target": 4}, and changes the graph.
    `lock` guards the graph; the Ricci flow thread takes it too.
    """

    def __init__(self, graph: Graph | None = None, step: StepFn | None = None):
        self.graph = normalize(graph) if graph is not None else empty_graph()
        fill_lengths(self.graph)
        self.step_fn = step
        self.auto_step = False
        self.version = 0
        self.structure = 0          # bumps when nodes or links are added or removed
        self.busy: str | None = None  # set while the Ricci flow runs
        self.lock = threading.RLock()
        self.analysis: dict[str, Any] = {}
        self._undo: list[str] = []
        self._redo: list[str] = []

    # ------------------------------------------------------------ public

    def snapshot(self) -> dict[str, Any]:
        """Everything the browser needs to draw the current state."""
        return {
            "graph": self.graph,
            "version": self.version,
            "structure": self.structure,
            "auto_step": self.auto_step,
            "can_undo": bool(self._undo) and not self.busy,
            "can_redo": bool(self._redo) and not self.busy,
            "has_model": self.step_fn is not None,
            "busy": self.busy,
            "analysis": self.analysis,
        }

    def apply(self, op: dict[str, Any]) -> dict[str, Any] | None:
        """Apply one edit. Returns the new node for add_node, else None."""
        name = op.get("op")
        handler = getattr(self, f"_op_{name}", None)
        if handler is None:
            raise GraphError(f"unknown op {name!r}")
        if self.busy and name in ("undo", "redo", "load", "step", "set_auto_step"):
            raise GraphError("stop the Ricci flow first")

        with self.lock:
            if name in ("undo", "redo", "set_auto_step"):
                handler(op)
                self.version += 1
                return None

            before = json.dumps(self.graph)
            shape = (len(self.graph["nodes"]), len(self.graph["links"]))
            try:
                result = handler(op)
                if self.auto_step and name not in ("step", "load"):
                    self._run_step()
            except Exception:
                self.graph = json.loads(before)  # all or nothing
                raise
            fill_lengths(self.graph)
            if json.dumps(self.graph) != before:
                self.checkpoint(before)
            if name in ("add_node", "add_link", "delete", "delete_many", "step", "load") or \
                    shape != (len(self.graph["nodes"]), len(self.graph["links"])):
                self.structure += 1
            self.version += 1
            return result

    def checkpoint(self, before: str | None = None) -> None:
        """Push the current (or given) graph onto the undo history."""
        self._undo.append(before if before is not None else json.dumps(self.graph))
        while len(self._undo) > 1 and sum(map(len, self._undo)) > HISTORY_BYTES:
            self._undo.pop(0)
        self._redo.clear()

    def replace(self, graph: Graph, undoable: bool = True) -> None:
        """Swap in a whole new graph (a dataset, an import)."""
        with self.lock:
            if undoable:
                self.checkpoint()
            self.graph = normalize(graph)
            fill_lengths(self.graph)
            self.analysis = {}
            self.structure += 1
            self.version += 1

    # ------------------------------------------------------------ helpers

    def _node(self, nid: Any) -> dict[str, Any]:
        for node in self.graph["nodes"]:
            if node["id"] == int(nid):
                return node
        raise GraphError(f"no node {nid}")

    def _link(self, source: Any, target: Any) -> dict[str, Any]:
        s, t = sorted((int(source), int(target)))
        for link in self.graph["links"]:
            if link["source"] == s and link["target"] == t:
                return link
        raise GraphError(f"no link {s}-{t}")

    def _item(self, op: dict[str, Any]) -> dict[str, Any]:
        if op.get("kind") == "link":
            return self._link(op["source"], op["target"])
        return self._node(op["id"])

    def _links_by_key(self, pairs: list[Any]) -> list[dict[str, Any]]:
        wanted = {tuple(sorted((int(s), int(t)))) for s, t in pairs}
        return [l for l in self.graph["links"] if (l["source"], l["target"]) in wanted]

    def _run_step(self) -> None:
        if self.step_fn is None:
            raise GraphError("no model step is configured")
        self.graph = normalize(self.step_fn(copy.deepcopy(self.graph)))

    # ------------------------------------------------------------ ops

    def _op_add_node(self, op: dict[str, Any]) -> dict[str, Any]:
        ids = [n["id"] for n in self.graph["nodes"]]
        node = {"id": max(ids, default=-1) + 1,
                "nval": int(op.get("nval", 1)), "current": 0, "total": 1}
        for key in ("x", "y"):
            if isinstance(op.get(key), (int, float)):
                node[key] = round(float(op[key]), 1)
        self.graph["nodes"].append(node)
        return node

    def _op_add_link(self, op: dict[str, Any]) -> None:
        s, t = sorted((int(op["source"]), int(op["target"])))
        if s == t:
            raise GraphError("a node cannot link to itself")
        self._node(s), self._node(t)
        try:
            self._link(s, t)
            return  # already linked: nothing to do
        except GraphError:
            pass
        self.graph["links"].append(
            {"source": s, "target": t, "lval": int(op.get("lval", 1)),
             "current": 0, "total": 1})
        self.graph["links"].sort(key=lambda l: (l["source"], l["target"]))

    def _op_delete(self, op: dict[str, Any]) -> None:
        if op.get("kind") == "link":
            self.graph["links"].remove(self._link(op["source"], op["target"]))
            return
        node = self._node(op["id"])
        self.graph["nodes"].remove(node)
        self.graph["links"] = [l for l in self.graph["links"]
                               if node["id"] not in (l["source"], l["target"])]

    def _op_delete_many(self, op: dict[str, Any]) -> None:
        """Delete several nodes (with their links) and links at once."""
        gone = {int(n) for n in op.get("nodes", [])}
        drop = {tuple(sorted((int(s), int(t)))) for s, t in op.get("links", [])}
        self.graph["nodes"] = [n for n in self.graph["nodes"] if n["id"] not in gone]
        self.graph["links"] = [l for l in self.graph["links"]
                               if l["source"] not in gone and l["target"] not in gone
                               and (l["source"], l["target"]) not in drop]

    def _op_adjust(self, op: dict[str, Any]) -> None:
        """A / D keys: add `delta` to both current and total."""
        item = self._item(op)
        delta = int(op.get("delta", 1))
        item["current"] += delta
        item["total"] += delta

    def _op_set(self, op: dict[str, Any]) -> None:
        """Edit one field from the side panel."""
        item = self._item(op)
        field = op.get("field")
        allowed = NODE_FIELDS if "id" in item else LINK_FIELDS
        if field not in allowed:
            raise GraphError(f"cannot set {field!r}")
        if field == "pinned":
            item.pop("pinned", None)
            if op["value"]:
                item["pinned"] = True
        elif field in ("capacity", "length"):
            value = _num(op["value"])
            if value is None or value <= 0:
                raise GraphError(f"{field} must be a positive number")
            item[field] = value
            if field == "length" and op.get("pin", True):
                item["pinned"] = True  # so the flow keeps the length you set
        else:
            item[field] = int(op["value"])

    def _op_scale(self, op: dict[str, Any]) -> None:
        """Steering: multiply the length of several links, and pin them."""
        factor = _num(op.get("factor"))
        if factor is None or factor <= 0:
            raise GraphError("factor must be a positive number")
        for link in self._links_by_key(op.get("links", [])):
            link["length"] = max(link.get("length", 1.0) * factor, 1e-9)
            if op.get("pin", True):
                link["pinned"] = True

    def _op_pin(self, op: dict[str, Any]) -> None:
        """Pin or unpin several links."""
        for link in self._links_by_key(op.get("links", [])):
            link.pop("pinned", None)
            if op.get("value", True):
                link["pinned"] = True

    def _op_step(self, op: dict[str, Any]) -> None:
        self._run_step()

    def _op_load(self, op: dict[str, Any]) -> None:
        self.graph = normalize(op["graph"])
        self.analysis = {}

    def _op_set_auto_step(self, op: dict[str, Any]) -> None:
        self.auto_step = bool(op.get("value")) and self.step_fn is not None

    def _op_undo(self, op: dict[str, Any]) -> None:
        if self._undo:
            self._redo.append(json.dumps(self.graph))
            self.graph = json.loads(self._undo.pop())
            self.structure += 1

    def _op_redo(self, op: dict[str, Any]) -> None:
        if self._redo:
            self._undo.append(json.dumps(self.graph))
            self.graph = json.loads(self._redo.pop())
            self.structure += 1
