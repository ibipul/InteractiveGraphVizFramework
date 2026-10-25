import copy

import pytest

from interactive_graph.graph import GraphError, GraphState, normalize
from interactive_graph.model import step


def make(step_fn=None):
    return GraphState({"nodes": [{"id": 0}, {"id": 1}, {"id": 2}],
                       "links": [{"source": 1, "target": 0}]}, step_fn)


def test_normalize_fills_defaults_and_cleans_links():
    g = normalize({
        "nodes": [{"id": 2, "group": 3}, {"id": 0}],
        "links": [
            {"source": 2, "target": 0, "weight": 4},
            {"source": 0, "target": 2},          # duplicate (reversed)
            {"source": 0, "target": 0},          # self-link
            {"source": 0, "target": 9},          # missing node
        ],
    })
    assert g["nodes"] == [{"id": 0, "nval": 1, "current": 0, "total": 1},
                          {"id": 2, "nval": 3, "current": 0, "total": 1}]
    assert g["links"] == [{"source": 0, "target": 2, "lval": 4, "current": 0, "total": 1}]


def test_normalize_rejects_non_graph():
    with pytest.raises(GraphError):
        normalize({"links": []})


def test_add_node_gets_next_id_and_position():
    s = make()
    node = s.apply({"op": "add_node", "x": 10.04, "y": -3})
    assert node == {"id": 3, "nval": 1, "current": 0, "total": 1, "x": 10.0, "y": -3.0}


def test_add_link_is_undirected_and_not_duplicated():
    s = make()
    s.apply({"op": "add_link", "source": 2, "target": 1})
    s.apply({"op": "add_link", "source": 1, "target": 2})
    assert [(l["source"], l["target"]) for l in s.graph["links"]] == [(0, 1), (1, 2)]
    with pytest.raises(GraphError):
        s.apply({"op": "add_link", "source": 1, "target": 1})
    with pytest.raises(GraphError):
        s.apply({"op": "add_link", "source": 1, "target": 7})


def test_delete_node_removes_its_links():
    s = make()
    s.apply({"op": "delete", "kind": "node", "id": 0})
    assert [n["id"] for n in s.graph["nodes"]] == [1, 2]
    assert s.graph["links"] == []


def test_adjust_changes_current_and_total():
    s = make()
    s.apply({"op": "adjust", "kind": "node", "id": 1, "delta": 1})
    s.apply({"op": "adjust", "kind": "link", "source": 0, "target": 1, "delta": -1})
    assert s.graph["nodes"][1]["current"] == 1 and s.graph["nodes"][1]["total"] == 2
    assert s.graph["links"][0]["current"] == -1 and s.graph["links"][0]["total"] == 0


def test_set_only_allows_known_fields():
    s = make()
    s.apply({"op": "set", "kind": "node", "id": 2, "field": "nval", "value": "5"})
    assert s.graph["nodes"][2]["nval"] == 5
    with pytest.raises(GraphError):
        s.apply({"op": "set", "kind": "node", "id": 2, "field": "lval", "value": 1})


def test_undo_redo():
    s = make()
    s.apply({"op": "add_node"})
    s.apply({"op": "delete", "kind": "node", "id": 0})
    s.apply({"op": "undo"})
    assert [n["id"] for n in s.graph["nodes"]] == [0, 1, 2, 3]
    s.apply({"op": "undo"})
    assert len(s.graph["nodes"]) == 3
    s.apply({"op": "redo"})
    assert len(s.graph["nodes"]) == 4
    assert s.snapshot()["can_redo"]


def test_failed_op_leaves_graph_untouched():
    def broken(graph):
        raise RuntimeError("model bug")

    s = make(broken)
    s.apply({"op": "set_auto_step", "value": True})
    before = copy.deepcopy(s.graph)
    with pytest.raises(RuntimeError):
        s.apply({"op": "add_node"})
    assert s.graph == before and not s.snapshot()["can_undo"]


def test_step_and_auto_step():
    s = make(step)
    s.graph["nodes"][0]["current"] = 4
    s.apply({"op": "step"})
    assert [n["nval"] for n in s.graph["nodes"]] == [3, 2, 3]
    assert s.graph["nodes"][0]["current"] == 0

    s.apply({"op": "set_auto_step", "value": True})
    s.apply({"op": "add_node"})
    assert [n["nval"] for n in s.graph["nodes"]] == [3, 1, 3, 2]


def test_step_without_model_is_an_error():
    s = make()
    with pytest.raises(GraphError):
        s.apply({"op": "step"})
    s.apply({"op": "set_auto_step", "value": True})
    assert s.auto_step is False
