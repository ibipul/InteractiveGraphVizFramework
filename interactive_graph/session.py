"""One editing-and-analysis session: the graph, the Ricci flow and its results.

Both the web server (server.py) and the in-browser build (web/py-worker.js,
running this package under Pyodide) drive a Session the same way:

    reply = session.handle(op)      # one message from the page
    while session.running:          # the server does this on a thread,
        session.iterate()           # the browser between messages

The flow is steerable: each iteration reads the live graph, computes
curvature, then writes new lengths back. Links that are pinned, or whose
length someone changed while the iteration was computing, keep their
length; the other links follow the flow, rescaled so all lengths still sum
to the number of links.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from . import datasets, ricci
from .graph import GraphError, GraphState

DEFAULTS = {"method": "ollivier", "alpha": 0.5, "step": 1.0, "iterations": 20, "cutoff": None}
FLOW_OPS = ("flow_start", "flow_stop", "flow_once", "flow_settings", "reset_lengths",
            "communities", "louvain", "load_dataset", "load_graph")


class Session:
    """`limits` restricts the in-browser demo, e.g.
    {"methods": ["forman"], "max_nodes": 1500, "datasets": ["karate", "sbm", "lfr"]}."""

    def __init__(self, graph: dict[str, Any] | None = None, limits: dict[str, Any] | None = None):
        self.limits = limits or {}
        self.state = GraphState(graph)
        self.settings = dict(DEFAULTS)
        if "methods" in self.limits:
            self.settings["method"] = self.limits["methods"][0]
            self.settings["step"] = ricci.DEFAULT_STEP[self.settings["method"]]
        self.running = False
        self.target = 0
        self.iteration = 0
        self.history: list[dict[str, Any]] = []
        self.louvain_result: dict[str, Any] | None = None
        self.stop_requested = False
        self.last_seconds: float | None = None
        self.refresh()

    # ------------------------------------------------------------ messages

    def handle(self, op: dict[str, Any]) -> dict[str, Any] | None:
        """Apply one message from the page. Returns an extra reply, if any."""
        name = op.get("op")
        if name not in FLOW_OPS:
            # Edits are allowed while the flow runs; the next iteration sees them.
            if name == "load":
                self._check_limits(op["graph"])
            result = self.state.apply(op)
            if name in ("load", "undo", "redo", "delete", "delete_many", "add_link", "add_node"):
                self.refresh()
            else:
                self.publish()
            return {"type": "added", "node": result} if result else None
        return getattr(self, f"_{name}")(op)

    def _check_limits(self, graph: dict[str, Any]) -> None:
        cap = self.limits.get("max_nodes")
        if cap and len(graph.get("nodes", [])) > cap:
            raise GraphError(f"the web demo handles up to {cap:,} nodes; "
                             "clone the repository and use ./run.sh for larger graphs")

    def _load_dataset(self, op: dict[str, Any]) -> dict[str, Any]:
        allowed = self.limits.get("datasets")
        if allowed and op["name"] not in allowed:
            raise GraphError("this dataset needs the full version: clone the repository and use ./run.sh")
        graph = datasets.load(op["name"], op.get("params") or {})
        return self._load_graph({"graph": graph, "summary": f"Loaded {graph['meta']['name']}"})

    def _load_graph(self, op: dict[str, Any]) -> dict[str, Any]:
        self._check_limits(op["graph"])
        self.stop()
        self.state.replace(op["graph"])
        self.iteration, self.history, self.louvain_result = 0, [], None
        self.last_seconds = None
        self.settings["cutoff"] = None
        n = len(self.state.graph["nodes"])
        if n > ricci.OLLIVIER_MAX_NODES:
            self.settings["method"] = "forman"
            self.settings["step"] = ricci.DEFAULT_STEP["forman"]
        self.refresh()
        return {"type": "loaded", "message": op.get("summary", "Loaded")}

    def _flow_settings(self, op: dict[str, Any]) -> None:
        self.configure(op.get("settings", {}))
        self.publish()

    def _flow_start(self, op: dict[str, Any]) -> None:
        self.configure(op.get("settings", {}))
        self._check_size()
        with self.state.lock:
            self.state.checkpoint()
            self.running = True
            self.stop_requested = False
            self.state.busy = "flow"
            n = int(self.settings["iterations"])
            self.target = self.iteration + n if n > 0 else 10**9
        self.publish()

    def _flow_once(self, op: dict[str, Any]) -> None:
        self.configure(op.get("settings", {}))
        self._check_size()
        if not self.running:
            self.state.checkpoint()
            self.iterate()

    def _flow_stop(self, op: dict[str, Any]) -> None:
        self.stop()
        self.publish()

    def _reset_lengths(self, op: dict[str, Any]) -> None:
        mode = op.get("mode", "inverse")
        with self.state.lock:
            self.state.checkpoint()
            arrays = ricci.edge_arrays(self.state.graph)
            length = ricci.initial_lengths(arrays, mode)
            for link, value in zip(self.state.graph["links"], length):
                link["length"] = float(value)
                link.pop("curvature", None)
                link.pop("pinned", None)
            self.iteration, self.history = 0, []
        self.refresh()

    def _communities(self, op: dict[str, Any]) -> None:
        cutoff = op.get("cutoff")
        self.settings["cutoff"] = None if cutoff in (None, "", "auto") else float(cutoff)
        self.refresh()

    def _louvain(self, op: dict[str, Any]) -> None:
        with self.state.lock:
            arrays = ricci.edge_arrays(self.state.graph)
            labels = ricci.louvain(arrays)
            self.louvain_result = {"count": int(labels.max()) + 1 if len(labels) else 0,
                                   "modularity": ricci.modularity(arrays, labels),
                                   **self._scores(labels)}
        self.publish()

    # ------------------------------------------------------------ flow

    def configure(self, settings: dict[str, Any]) -> None:
        s = dict(self.settings)
        if settings.get("method") and settings["method"] != s["method"] and "step" not in settings:
            s["step"] = ricci.DEFAULT_STEP.get(settings["method"], 1.0)
        for key in ("method", "alpha", "step", "iterations"):
            if key in settings and settings[key] not in (None, ""):
                s[key] = settings[key]
        s["alpha"], s["step"], s["iterations"] = float(s["alpha"]), float(s["step"]), int(s["iterations"])
        methods = self.limits.get("methods", ricci.METHODS)
        if s["method"] not in methods:
            raise GraphError(f"method must be one of {', '.join(methods)}")
        if not 0 <= s["alpha"] < 1:
            raise GraphError("alpha must be at least 0 and below 1")
        if not 0 < s["step"] <= 1:
            raise GraphError("step must be above 0 and at most 1")
        if s["iterations"] < 0:
            raise GraphError("iterations cannot be negative")
        self.settings = s

    def _check_size(self) -> None:
        n = len(self.state.graph["nodes"])
        if self.settings["method"] == "ollivier" and n > ricci.OLLIVIER_MAX_NODES:
            raise GraphError(f"Ollivier-Ricci is limited to {ricci.OLLIVIER_MAX_NODES:,} nodes "
                             f"(this graph has {n:,}); choose Forman-Ricci")

    def stop(self) -> None:
        self.stop_requested = True
        self.running = False
        self.state.busy = None
        if self.state.analysis:
            self.state.analysis["running"] = False

    def iterate(self) -> bool:
        """One Ricci-flow iteration. Returns True if the flow should go on."""
        started = time.perf_counter()
        s = self.settings
        with self.state.lock:
            links = self.state.graph["links"]
            if not links:
                self.stop()
                self.publish()
                return False
            arrays = ricci.edge_arrays(self.state.graph)
            keys = [(l["source"], l["target"]) for l in links]
            before = arrays.length.copy()

        try:
            kappa = ricci.curvature(arrays, before, s["method"], s["alpha"],
                                    should_stop=lambda: self.stop_requested and not self.running)
        except InterruptedError:
            return False
        scaled = ricci.flow_signal(arrays, kappa, s["method"])
        proposed = np.maximum(before - s["step"] * scaled * before, ricci.MIN_LENGTH)

        with self.state.lock:
            by_key = {(l["source"], l["target"]): l for l in self.state.graph["links"]}
            free_links, free_values, fixed_sum = [], [], 0.0
            for i, key in enumerate(keys):
                link = by_key.pop(key, None)
                if link is None:
                    continue  # deleted while computing
                link["curvature"] = float(kappa[i])
                if link.get("pinned") or link.get("length") != before[i]:
                    fixed_sum += link["length"]
                else:
                    free_links.append(link)
                    free_values.append(proposed[i])
            fixed_sum += sum(l.get("length", 1.0) for l in by_key.values())  # added while computing
            total = len(self.state.graph["links"])
            free_sum = float(np.sum(free_values)) if free_values else 0.0
            scale = (total - fixed_sum) / free_sum if free_sum > 0 and total > fixed_sum else 1.0
            for link, value in zip(free_links, free_values):
                link["length"] = float(max(value * scale, ricci.MIN_LENGTH))
            self.iteration += 1
            self.last_seconds = time.perf_counter() - started
            if self.iteration >= self.target:
                self.stop()
            self._update_communities(record=True, kappa=kappa)
            self.state.version += 1
            return self.running

    # ------------------------------------------------------------ results

    def _truth(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Mask of nodes with a ground truth, and their truth values."""
        truth = [n.get("truth") for n in self.state.graph["nodes"]]
        known = np.array([t is not None for t in truth], dtype=bool)
        if not known.any():
            return None
        return known, np.array([x for x in truth if x is not None], dtype=np.int64)

    def _scores(self, labels: np.ndarray) -> dict[str, Any]:
        found = self._truth()
        if found is None:
            return {}
        known, t = found
        return {"nmi": ricci.nmi(labels[known], t), "ari": ricci.ari(labels[known], t)}

    def _comparison(self, labels: np.ndarray) -> dict[str, Any] | None:
        found = self._truth()
        if found is None:
            return None
        known, t = found
        n_truth = len(self.state.graph.get("meta", {}).get("truth_names", []))
        return ricci.compare_to_truth(labels[known], t, n_truth, n_found=int(labels.max()) + 1)

    def _update_communities(self, record: bool = False, kappa: np.ndarray | None = None) -> None:
        g = self.state.graph
        arrays = ricci.edge_arrays(g)
        if not g["nodes"]:
            self.state.analysis = self.summary(None)
            return
        if arrays.length is None:
            arrays.length = np.ones(len(g["links"]))
        comms = ricci.find_communities(arrays, arrays.length, self.settings["cutoff"])
        for node, label in zip(g["nodes"], comms.labels):
            node["community"] = int(label)
        info = {"count": comms.count, "cutoff": comms.cutoff, "modularity": comms.modularity,
                "sizes": np.bincount(comms.labels).tolist(), **self._scores(comms.labels),
                "comparison": self._comparison(comms.labels)}
        if record:
            k = kappa if kappa is not None else np.zeros(0)
            self.history.append({"iteration": self.iteration, "seconds": self.last_seconds,
                                 "count": comms.count, "modularity": comms.modularity,
                                 "nmi": info.get("nmi"), "ari": info.get("ari"),
                                 "curvature_min": float(k.min()) if len(k) else None,
                                 "curvature_max": float(k.max()) if len(k) else None})
        self.state.analysis = self.summary(info)

    def summary(self, communities: dict[str, Any] | None) -> dict[str, Any]:
        g = self.state.graph
        lengths = [l["length"] for l in g["links"] if "length" in l]
        return {
            "settings": self.settings,
            "running": self.running,
            "iteration": self.iteration,
            "last_seconds": self.last_seconds,
            "communities": communities,
            "louvain": self.louvain_result,
            "history": self.history[-500:],
            "length_range": [min(lengths), max(lengths)] if lengths else None,
            "pinned": sum(1 for l in g["links"] if l.get("pinned")),
            "ollivier_max_nodes": ricci.OLLIVIER_MAX_NODES,
            "limits": self.limits,
        }

    def refresh(self) -> None:
        with self.state.lock:
            self._update_communities()
            self.state.version += 1

    def publish(self) -> None:
        with self.state.lock:
            self.state.analysis = {**self.state.analysis, **{
                k: v for k, v in self.summary(self.state.analysis.get("communities")).items()
                if k != "communities"}}
            self.state.version += 1

    def snapshot(self) -> dict[str, Any]:
        return {"type": "state", **self.state.snapshot()}
