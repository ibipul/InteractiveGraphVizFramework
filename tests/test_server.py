import json

from fastapi.testclient import TestClient

from interactive_graph.server import create_app


def test_edits_are_saved_and_broadcast(tmp_path):
    path = tmp_path / "graph.json"
    client = TestClient(create_app(graph_file=path))

    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        first = a.receive_json()
        assert first["type"] == "state" and len(first["graph"]["nodes"]) == 107  # the example
        next_id = max(n["id"] for n in first["graph"]["nodes"]) + 1
        b.receive_json()

        a.send_json({"op": "add_node", "x": 1, "y": 2})
        assert a.receive_json() == {"type": "added",
                                    "node": {"id": next_id, "nval": 1, "current": 0, "total": 1,
                                             "x": 1.0, "y": 2.0}}
        for ws in (a, b):  # both tabs get the new state
            msg = ws.receive_json()
            assert msg["type"] == "state" and len(msg["graph"]["nodes"]) == 108

        a.send_json({"op": "add_link", "source": 0, "target": 0})
        assert a.receive_json()["type"] == "error"

    saved = json.loads(path.read_text())
    assert len(saved["nodes"]) == 108
    assert client.get("/api/graph").json() == saved

    # A restarted server picks up where it left off.
    again = TestClient(create_app(graph_file=path))
    assert len(again.get("/api/graph").json()["nodes"]) == 108


def test_page_and_static_files(tmp_path):
    client = TestClient(create_app(graph_file=tmp_path / "g.json", model="none"))
    assert "Interactive Graph Viz" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/examples/default.json").status_code == 200
    assert client.get("/api/health").json()["app"] == "interactive-graph"
