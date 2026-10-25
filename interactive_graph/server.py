"""Web server: serves the page and keeps every open tab in sync.

Each browser opens one WebSocket to /ws. An edit is sent as a small JSON
message ({"op": "add_node", "x": 120, "y": 80}); the server applies it,
saves the graph to disk and sends the new state to every connected tab.

Settings (environment variables):
    GRAPH_FILE  where the graph is saved (default: data/graph.json)
    MODEL       the step function, as module:function
                (default: interactive_graph.model:step; "none" disables it)
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .graph import GraphError, GraphState, StepFn, normalize

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web"
EXAMPLE = STATIC / "examples" / "default.json"

log = logging.getLogger("interactive_graph")


def load_step(spec: str) -> StepFn | None:
    if spec.lower() == "none":
        return None
    module, _, func = spec.partition(":")
    return getattr(importlib.import_module(module), func or "step")


def read_graph(path: Path) -> dict[str, Any]:
    source = path if path.exists() else EXAMPLE
    return normalize(json.loads(source.read_text()))


def write_graph(path: Path, graph: dict[str, Any]) -> None:
    # Write to a temp file first so a crash never leaves half a file.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(graph, indent=2) + "\n")
    tmp.replace(path)


def create_app(graph_file: str | Path | None = None, model: str | None = None) -> FastAPI:
    path = Path(graph_file or os.environ.get("GRAPH_FILE", ROOT / "data" / "graph.json"))
    state = GraphState(read_graph(path),
                       load_step(model or os.environ.get("MODEL", "interactive_graph.model:step")))
    clients: set[WebSocket] = set()

    app = FastAPI(title="Interactive Graph Viz")
    app.state.graph = state

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/graph")
    def get_graph() -> JSONResponse:
        """The current graph, e.g. for downloading or scripting."""
        return JSONResponse(state.graph)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"app": "interactive-graph", "version": state.version}

    async def broadcast(message: dict[str, Any]) -> None:
        for ws in list(clients):
            try:
                await ws.send_json(message)
            except Exception:
                clients.discard(ws)

    @app.websocket("/ws")
    async def socket(ws: WebSocket) -> None:
        await ws.accept()
        clients.add(ws)
        await ws.send_json({"type": "state", **state.snapshot()})
        try:
            while True:
                op = await ws.receive_json()
                try:
                    result = state.apply(op)
                except (GraphError, KeyError, TypeError, ValueError) as e:
                    await ws.send_json({"type": "error", "message": str(e)})
                    continue
                except Exception as e:  # a bug in the model step
                    log.exception("op failed: %s", op)
                    await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
                    continue
                write_graph(path, state.graph)
                if result is not None:
                    await ws.send_json({"type": "added", "node": result})
                await broadcast({"type": "state", **state.snapshot()})
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    app.mount("/", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
