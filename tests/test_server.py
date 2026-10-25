import json

from fastapi.testclient import TestClient

from interactive_graph.server import create_app


def until(ws, pred, limit=500):
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("message never arrived")


def test_edits_are_saved_and_broadcast(tmp_path):
    path = tmp_path / "graph.json"
    client = TestClient(create_app(graph_file=path))

    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        first = a.receive_json()
        assert first["type"] == "state" and first["graph"]["meta"]["name"] == "Karate club"
        b.receive_json()

        a.send_json({"op": "add_node", "x": 1, "y": 2})
        assert len(a.receive_json()["graph"]["nodes"]) == 35
        assert a.receive_json()["type"] == "added"
        assert len(b.receive_json()["graph"]["nodes"]) == 35  # the other tab sees it

        a.send_json({"op": "add_link", "source": 0, "target": 0})
        assert until(a, lambda m: m["type"] == "error")

    saved = json.loads(path.read_text())
    assert len(saved["nodes"]) == 35
    assert client.get("/api/graph").json()["nodes"] == saved["nodes"]

    # A restarted server picks up where it left off.
    again = TestClient(create_app(graph_file=path))
    assert len(again.get("/api/graph").json()["nodes"]) == 35


def test_flow_runs_on_a_thread_and_streams(tmp_path):
    client = TestClient(create_app(graph_file=tmp_path / "g.json"))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"op": "flow_start", "settings": {"method": "ollivier", "iterations": 8}})
        done = until(ws, lambda m: m["type"] == "state" and m["analysis"]["iteration"] == 8
                     and not m["analysis"]["running"])
        assert done["analysis"]["communities"]["nmi"] is not None


def test_import_datasets_and_save(tmp_path, monkeypatch):
    import interactive_graph.server as server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    client = TestClient(create_app(graph_file=tmp_path / "g.json"))
    assert {d["id"] for d in client.get("/api/datasets").json()} >= {"karate", "sbm", "lfr", "cora"}
    r = client.post("/api/import?name=x.csv", content=b"a,b,2\nb,c,3\nc,a,1\n")
    assert r.status_code == 200 and "3 nodes, 3 edges" in r.json()["message"]
    assert len(client.get("/api/graph").json()["links"]) == 3
    assert client.post("/api/import?name=x.xyz", content=b"?").status_code == 400
    saved = client.post("/api/save").json()["message"]
    folder = tmp_path / saved.removeprefix("Saved to ").rstrip("/")
    assert {p.name for p in folder.iterdir()} == {"graph.json", "nodes.csv", "edges.csv", "analysis.json"}


def test_page_and_static_files(tmp_path):
    client = TestClient(create_app(graph_file=tmp_path / "g.json"))
    assert "Interactive Ricci Flow" in client.get("/").text
    for path in ("/app.js", "/py-worker.js", "/py/interactive_graph/session.py"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/health").json()["app"] == "interactive-graph"
