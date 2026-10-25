# Interactive Graph Viz Framework

> ### *draw a graph, change it live, run your model on it*

A browser editor for weighted graphs, backed by a small Python server. You add nodes and links with the mouse, change node and link loads from the keyboard, and run a model step (your own Python function) on the graph. Every edit is saved right away and shows up in every open tab.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.161.1-009688?logo=fastapi&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-0.54.0-499848)
![D3.js](https://img.shields.io/badge/D3.js-7.9.0-F9A03C?logo=d3dotjs&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

![The editor with the example graph loaded and node 1 selected](docs/screenshot.png)
<sub>The editor with the example graph loaded and node 1 selected.</sub>

## Quick start

```bash
git clone https://github.com/ibipul/InteractiveGraphVizFramework.git
cd InteractiveGraphVizFramework
./run.sh
```

Requires Python 3.10 or newer. The first run creates a virtual environment in `.venv` and installs the packages from `requirements.txt`. The page then opens at **http://localhost:8070**, or the next free port; the terminal prints the address. Keep the terminal open while you use the page, and press Ctrl+C there to stop.

The graph is saved to `data/graph.json` after every edit, so you can stop the server and pick up where you left off.
