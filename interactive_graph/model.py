"""The model step: the computation that runs on the graph you drew.

The server calls `step(graph)` when you press Step (or after every edit
with auto-step on) and draws whatever it returns. Replace this function
with your own, or point the server at another one without editing this
file:

    MODEL=mypackage.mymodule:my_step ./run.sh

The function gets a copy of the graph (see graph.py for the shape) and
returns the new graph. It may change any field, add or remove nodes and
links; the result is cleaned up with graph.normalize before it is drawn.
"""

from __future__ import annotations

from .graph import Graph


def step(graph: Graph) -> Graph:
    """Example step, carried over from the original framework.

    Moves every node to a new group (alternating between groups 2-3 and
    1-2 so the colours visibly change) and resets its current load to 0.
    Links are left as they are.
    """
    for i, node in enumerate(graph["nodes"]):
        offset = 2 if i % 2 == 0 else 1
        node["nval"] = node["nval"] % 2 + offset
        node["current"] = 0
    return graph
