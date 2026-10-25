"""Web server: serves the page and keeps every open tab in sync.

Each browser opens one WebSocket to /ws. An edit is sent as a small JSON
message ({"op": "add_node", "x": 120, "y": 80}); the server applies it,
saves the graph to disk and sends the new state to every connected tab.
While the Ricci flow runs, it runs on a background thread and every tab
gets the state after each iteration; edits from any tab steer it.

Settings (environment variables):
    GRAPH_FILE  where the graph is saved (default: data/graph.json)
    MODEL       the step function, as module:function
                (default: interactive_graph.model:step; "none" disables it)
"""

from __future__ import annotations

import asyncio
import csv
import importlib
import io
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import datasets, importer
from .graph import GraphError, StepFn, normalize
from .session import Session

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web"
PACKAGE = Path(__file__).resolve().parent
PUBLISH_EVERY = 0.25   # seconds between state messages while the flow runs
SAVE_EVERY = 5.0       # seconds between saves while the flow runs

log = logging.getLogger("interactive_graph")


def load_step(spec: str) -> StepFn | None:
    if spec.lower() == "none":
        return None
    module, _, func = spec.partition(":")
    return getattr(importlib.import_module(module), func or "step")


def read_graph(path: Path) -> dict[str, Any]:
    """The saved graph, or the karate club on the first run."""
    if path.exists():
        return normalize(json.loads(path.read_text()))
    return datasets.load("karate")


def write_graph(path: Path, graph: dict[str, Any]) -> None:
    # Write to a temp file first so a crash never leaves half a file.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    indent = 2 if len(graph["links"]) < 5000 else None
    tmp.write_text(json.dumps(graph, indent=indent) + "\n")
    tmp.replace(path)


def results_tables(graph: dict[str, Any]) -> tuple[str, str]:
    """Nodes and edges as CSV text."""
    nodes, edges = io.StringIO(), io.StringIO()
    names = graph.get("meta", {}).get("truth_names", [])
    w = csv.writer(nodes)
    w.writerow(["id", "label", "community", "truth", "truth_name"])
    for n in graph["nodes"]:
        t = n.get("truth")
        w.writerow([n["id"], n.get("label", ""), n.get("community", ""),
                    "" if t is None else t, names[t] if t is not None and t < len(names) else ""])
    w = csv.writer(edges)
    w.writerow(["source", "target", "capacity", "length", "curvature", "pinned"])
    for l in graph["links"]:
        w.writerow([l["source"], l["target"], l.get("capacity", ""), l.get("length", ""),
                    l.get("curvature", ""), int(bool(l.get("pinned")))])
    return nodes.getvalue(), edges.getvalue()


def create_app(graph_file: str | Path | None = None, model: str | None = None) -> FastAPI:
    path = Path(graph_file or os.environ.get("GRAPH_FILE", ROOT / "data" / "graph.json"))
    session = Session(read_graph(path),
                      load_step(model or os.environ.get("MODEL", "interactive_graph.model:step")))
    state = session.state
    clients: set[WebSocket] = set()
    flow_thread: dict[str, threading.Thread | None] = {"t": None}

    app = FastAPI(title="Interactive Ricci Flow")
    app.state.session = session

    async def broadcast(message: dict[str, Any]) -> None:
        text = json.dumps(message)
        for ws in list(clients):
            try:
                await ws.send_text(text)
            except Exception:
                clients.discard(ws)

    async def publish() -> None:
        with state.lock:
            message = session.snapshot()
            text = json.dumps(message)
        for ws in list(clients):
            try:
                await ws.send_text(text)
            except Exception:
                clients.discard(ws)

    def save() -> None:
        with state.lock:
            write_graph(path, state.graph)

    def start_flow_thread(loop: asyncio.AbstractEventLoop) -> None:
        if flow_thread["t"] and flow_thread["t"].is_alive():
            return

        def run() -> None:
            last_publish = last_save = 0.0
            try:
                while session.running:
                    go_on = session.iterate()
                    now = time.monotonic()
                    if not go_on or now - last_publish >= PUBLISH_EVERY:
                        asyncio.run_coroutine_threadsafe(publish(), loop)
                        last_publish = now
                    if not go_on or now - last_save >= SAVE_EVERY:
                        save()
                        last_save = now
            except Exception as e:  # report and stop rather than die silently
                log.exception("Ricci flow failed")
                session.stop()
                asyncio.run_coroutine_threadsafe(
                    broadcast({"type": "error", "message": f"Ricci flow stopped: {e}"}), loop)
            asyncio.run_coroutine_threadsafe(publish(), loop)
            save()

        flow_thread["t"] = threading.Thread(target=run, name="ricci-flow", daemon=True)
        flow_thread["t"].start()

    async def handle(op: dict[str, Any]) -> dict[str, Any] | None:
        reply = await asyncio.to_thread(session.handle, op)
        if session.running:
            start_flow_thread(asyncio.get_running_loop())
        else:
            await asyncio.to_thread(save)
        await publish()
        return reply

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/graph")
    def get_graph() -> JSONResponse:
        """The current graph, e.g. for downloading or scripting."""
        with state.lock:
            return JSONResponse(state.graph)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"app": "interactive-graph", "version": state.version}

    @app.get("/api/datasets")
    def get_datasets() -> list[dict[str, Any]]:
        return datasets.catalog()

    @app.post("/api/import")
    async def import_file(request: Request, name: str) -> JSONResponse:
        """Upload a network file as the raw request body; ?name= gives its file name."""
        data = await request.body()
        try:
            graph, summary = await asyncio.to_thread(importer.read, name, data)
            await handle({"op": "load_graph", "graph": graph, "summary": summary})
        except (GraphError, ValueError, KeyError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            log.exception("import failed")
            return JSONResponse({"error": f"could not read {name}: {e}"}, status_code=400)
        return JSONResponse({"message": f"Imported {name}: {summary}"})

    @app.post("/api/save")
    def save_results() -> dict[str, Any]:
        """Write the graph, node/edge tables and the run history to data/results/."""
        with state.lock:
            graph = json.loads(json.dumps(state.graph))
            analysis = json.loads(json.dumps(state.analysis))
        name = "".join(c if c.isalnum() or c in "-_" else "-" for c in
                       graph.get("meta", {}).get("name", "graph"))[:40]
        folder = ROOT / "data" / "results" / f"{datetime.now():%Y%m%d-%H%M%S}-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        nodes_csv, edges_csv = results_tables(graph)
        (folder / "graph.json").write_text(json.dumps(graph) + "\n")
        (folder / "nodes.csv").write_text(nodes_csv)
        (folder / "edges.csv").write_text(edges_csv)
        (folder / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")
        return {"message": f"Saved to {folder.relative_to(ROOT)}/"}

    @app.websocket("/ws")
    async def socket(ws: WebSocket) -> None:
        await ws.accept()
        clients.add(ws)
        with state.lock:
            await ws.send_text(json.dumps(session.snapshot()))
        try:
            while True:
                op = await ws.receive_json()
                try:
                    reply = await handle(op)
                except (GraphError, KeyError, TypeError, ValueError) as e:
                    await ws.send_json({"type": "error", "message": str(e)})
                    continue
                except Exception as e:  # e.g. a bug in a model step or a failed download
                    log.exception("op failed: %s", op.get("op"))
                    await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
                    continue
                if reply:
                    await ws.send_json(reply)
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    # The Python package, for the in-browser build (web/py-worker.js).
    app.mount("/py/interactive_graph", StaticFiles(directory=PACKAGE), name="py")
    app.mount("/", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
