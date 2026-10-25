"""The graph state and every edit the browser can make to it.

A graph is a plain dict, the same shape as the JSON files on disk:

    {"nodes": [{"id": 0, "nval": 1, "current": 0, "total": 1}, ...],
     "links": [{"source": 0, "target": 1, "lval": 1, "current": 0, "total": 1}, ...]}

`nval` is the node's group (it picks the colour), `lval` the link's weight
(it sets the stroke width), and `current` / `total` are the load counters
that the A and D keys change. Links are undirected and stored with
source < target, so there is at most one link between two nodes.

`web/static/local.js` mirrors these operations for the server-less demo;
keep the two in step when changing one.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

Graph = dict[str, list[dict[str, Any]]]
StepFn = Callable[[Graph], Graph]

HISTORY_LIMIT = 200


class GraphError(ValueError):
    """An edit that cannot be applied, e.g. a link to a missing node."""


def empty_graph() -> Graph:
    return {"nodes": [], "links": []}


def normalize(data: dict[str, Any]) -> Graph:
    """Clean up a graph loaded from a file or the browser.

    Fills in missing counters, accepts the older "group"/"weight" keys,
    drops links to missing nodes, self-links and duplicates, and sorts
    everything by id so the output is stable.
    """
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise GraphError("a graph needs a 'nodes' list")

    nodes: dict[int, dict[str, Any]] = {}
    for raw in data["nodes"]:
        nid = int(raw["id"])
        node = {
            "id": nid,
            "nval": int(raw.get("nval", raw.get("group", 1))),
            "current": int(raw.get("current", 0)),
            "total": int(raw.get("total", 1)),
        }
        for key in ("x", "y"):
            if isinstance(raw.get(key), (int, float)):
                node[key] = round(float(raw[key]), 1)
        nodes[nid] = node

    links: dict[tuple[int, int], dict[str, Any]] = {}
    for raw in data.get("links", []):
        s, t = _endpoint(raw["source"]), _endpoint(raw["target"])
        if s == t or s not in nodes or t not in nodes:
            continue
        s, t = min(s, t), max(s, t)
        links.setdefault((s, t), {
            "source": s,
            "target": t,
            "lval": int(raw.get("lval", raw.get("weight", 1))),
            "current": int(raw.get("current", 0)),
            "total": int(raw.get("total", 1)),
        })

    return {
        "nodes": [nodes[k] for k in sorted(nodes)],
        "links": [links[k] for k in sorted(links)],
    }


def _endpoint(value: Any) -> int:
    # D3 replaces link endpoints with node objects; accept either form.
    return int(value["id"] if isinstance(value, dict) else value)


class GraphState:
    """The live graph plus undo/redo history.

    `apply(op)` takes one edit message from the browser, e.g.
    {"op": "add_link", "source": 1, "target": 4}, and changes the graph.
    """

    def __init__(self, graph: Graph | None = None, step: StepFn | None = None):
        self.graph = normalize(graph) if graph is not None else empty_graph()
        self.step_fn = step
        self.auto_step = False
        self.version = 0
        self._undo: list[Graph] = []
        self._redo: list[Graph] = []

    # ------------------------------------------------------------ public

    def snapshot(self) -> dict[str, Any]:
        """Everything the browser needs to draw the current state."""
        return {
            "graph": self.graph,
            "version": self.version,
            "auto_step": self.auto_step,
            "can_undo": bool(self._undo),
            "can_redo": bool(self._redo),
            "has_model": self.step_fn is not None,
        }

    def apply(self, op: dict[str, Any]) -> dict[str, Any] | None:
        """Apply one edit. Returns the new node for add_node, else None."""
        name = op.get("op")
        handler = getattr(self, f"_op_{name}", None)
        if handler is None:
            raise GraphError(f"unknown op {name!r}")

        if name in ("undo", "redo", "set_auto_step"):
            handler(op)
            self.version += 1
            return None

        before = copy.deepcopy(self.graph)
        try:
            result = handler(op)
            if self.auto_step and name not in ("step", "load"):
                self._run_step()
        except Exception:
            self.graph = before  # all or nothing
            raise
        if self.graph != before:
            self._undo.append(before)
            del self._undo[:-HISTORY_LIMIT]
            self._redo.clear()
        self.version += 1
        return result

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
        allowed = ("nval", "current", "total") if "id" in item else ("lval", "current", "total")
        if field not in allowed:
            raise GraphError(f"cannot set {field!r}")
        item[field] = int(op["value"])

    def _op_step(self, op: dict[str, Any]) -> None:
        self._run_step()

    def _op_load(self, op: dict[str, Any]) -> None:
        self.graph = normalize(op["graph"])

    def _op_set_auto_step(self, op: dict[str, Any]) -> None:
        self.auto_step = bool(op.get("value")) and self.step_fn is not None

    def _op_undo(self, op: dict[str, Any]) -> None:
        if self._undo:
            self._redo.append(self.graph)
            self.graph = self._undo.pop()

    def _op_redo(self, op: dict[str, Any]) -> None:
        if self._redo:
            self._undo.append(self.graph)
            self.graph = self._redo.pop()
