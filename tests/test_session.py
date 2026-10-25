import pytest

from interactive_graph import datasets, importer
from interactive_graph.graph import GraphError
from interactive_graph.session import Session


def run(session, **settings):
    session.handle({"op": "flow_start", "settings": settings})
    while session.running:
        session.iterate()
    return session.state.analysis


def test_flow_on_karate_reports_scores():
    s = Session(datasets.load("karate"))
    a = run(s, method="ollivier", iterations=10)
    assert a["iteration"] == 10 and not a["running"]
    assert len(a["history"]) == 10
    c = a["communities"]
    assert c["count"] >= 2 and 0 < c["nmi"] <= 1 and c["modularity"] > 0.3
    assert all("community" in n for n in s.state.graph["nodes"])
    assert all("curvature" in l for l in s.state.graph["links"])


def test_sbm_blocks_are_recovered():
    s = Session(datasets.load("sbm", {"blocks": 4, "block_size": 25, "p_in": 0.5, "p_out": 0.02}))
    c = run(s, method="ollivier", iterations=10)["communities"]
    assert c["nmi"] == pytest.approx(1)


def test_steering_mid_flow():
    s = Session(datasets.load("karate"))
    s.handle({"op": "flow_start", "settings": {"method": "ollivier", "iterations": 6}})
    s.iterate()
    links = s.state.graph["links"]
    pinned = [[links[0]["source"], links[0]["target"]]]
    s.handle({"op": "scale", "links": pinned, "factor": 3})
    held = links[0]["length"]
    s.handle({"op": "set", "kind": "link", "source": links[1]["source"], "target": links[1]["target"],
              "field": "length", "value": 2.5})
    while s.running:
        s.iterate()
    links = s.state.graph["links"]
    assert links[0]["length"] == pytest.approx(held) and links[0]["pinned"]
    assert links[1]["length"] == pytest.approx(2.5) and links[1]["pinned"]
    assert sum(l["length"] for l in links) == pytest.approx(len(links))  # free links absorb the rest


def test_structure_edits_while_running():
    s = Session(datasets.load("karate"))
    s.handle({"op": "flow_start", "settings": {"iterations": 3}})
    s.iterate()
    s.handle({"op": "delete", "kind": "node", "id": 0})
    s.handle({"op": "add_link", "source": 1, "target": 33})
    while s.running:
        s.iterate()
    assert s.state.analysis["iteration"] == 3
    assert all(l["length"] > 0 for l in s.state.graph["links"])


def test_undo_blocked_while_running_and_one_step_after():
    s = Session(datasets.load("karate"))
    before = [l["length"] for l in s.state.graph["links"]]
    s.handle({"op": "flow_start", "settings": {"iterations": 3}})
    with pytest.raises(GraphError):
        s.handle({"op": "undo"})
    while s.running:
        s.iterate()
    s.handle({"op": "undo"})
    assert [l["length"] for l in s.state.graph["links"]] == before


def test_manual_cutoff_and_louvain():
    s = Session(datasets.load("karate"))
    run(s, iterations=10)
    s.handle({"op": "communities", "cutoff": 1e9})
    assert s.state.analysis["communities"]["count"] == 1
    s.handle({"op": "communities", "cutoff": None})
    assert s.state.analysis["communities"]["count"] > 1
    s.handle({"op": "louvain"})
    assert s.state.analysis["louvain"]["count"] > 1


def test_reset_lengths():
    s = Session(datasets.load("karate"))
    run(s, iterations=3)
    s.handle({"op": "reset_lengths", "mode": "uniform"})
    assert {l["length"] for l in s.state.graph["links"]} == {1.0}
    assert s.state.analysis["iteration"] == 0


def test_demo_limits():
    s = Session(None, None, {"methods": ["forman"], "max_nodes": 50, "datasets": ["karate", "sbm"]})
    s.handle({"op": "load_dataset", "name": "karate"})
    with pytest.raises(GraphError):
        s.handle({"op": "flow_settings", "settings": {"method": "ollivier"}})
    with pytest.raises(GraphError):
        s.handle({"op": "load_dataset", "name": "sbm", "params": {"blocks": 5, "block_size": 20}})
    with pytest.raises(GraphError):
        s.handle({"op": "load_dataset", "name": "football"})


def test_forman_is_used_for_big_graphs():
    s = Session()
    s.handle({"op": "load_dataset", "name": "sbm", "params": {"blocks": 7, "block_size": 900, "p_in": 0.002, "p_out": 0.0001}})
    assert s.settings["method"] == "forman"
    with pytest.raises(GraphError):
        s.handle({"op": "flow_start", "settings": {"method": "ollivier"}})


def test_lfr_and_import_round_trip(tmp_path):
    g = datasets.load("lfr", {"n": 200, "mu": 0.1, "average_degree": 8, "min_community": 20})
    assert g["meta"]["truth_names"] and all("truth" in n for n in g["nodes"])

    csv = b"source,target,capacity\nA,B,5\nB,C,1\nA,C,2\nB,A,1\nC,C,9\n"
    graph, summary = importer.read("net.csv", csv)
    assert len(graph["nodes"]) == 3 and len(graph["links"]) == 3
    ab = next(l for l in graph["links"] if {graph["nodes"][l["source"]]["label"], graph["nodes"][l["target"]]["label"]} == {"A", "B"})
    assert ab["capacity"] == 6  # A-B and B-A merged
    assert "1 repeated" in summary and "1 self-loops" in summary

    graph, _ = importer.read("plain.txt", b"# comment\n1 2\n2 3\n3 1\n")
    assert len(graph["links"]) == 3 and "capacity" not in graph["links"][0]

    import networkx as nx
    nxg = nx.karate_club_graph()
    for u, v in nxg.edges():
        nxg[u][v]["capacity"] = 2.0
    path = tmp_path / "k.graphml"
    nx.write_graphml(nxg, path)
    graph, summary = importer.read("k.graphml", path.read_bytes())
    assert len(graph["nodes"]) == 34 and graph["links"][0]["capacity"] == 2.0
    assert graph["meta"]["truth_names"] == ["Mr. Hi", "Officer"]


def test_unpin_all_keeps_lengths():
    s = Session(datasets.load("karate"))
    links = s.state.graph["links"]
    s.handle({"op": "scale", "links": [[l["source"], l["target"]] for l in links[:5]], "factor": 2})
    lengths = [l["length"] for l in s.state.graph["links"]]
    s.handle({"op": "flow_start", "settings": {"iterations": 3}})
    s.handle({"op": "unpin_all"})  # allowed while the flow runs
    s.handle({"op": "flow_stop"})
    assert not any(l.get("pinned") for l in s.state.graph["links"])
    assert [l["length"] for l in s.state.graph["links"]] == lengths
    s.handle({"op": "undo"})
    assert sum(1 for l in s.state.graph["links"] if l.get("pinned")) == 5
