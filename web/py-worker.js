// Copyright 2018-2026 Bipul Islam
// Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
// SPDX-License-Identifier: Apache-2.0

// In-browser backend for the web demo: runs the interactive_graph Python
// package under Pyodide (CPython compiled to WebAssembly) in this worker,
// and answers the page's messages the way the server does.
//
// While the Ricci flow runs, the worker does one iteration, posts the state,
// then yields so queued messages (steering edits, Stop) are handled before
// the next iteration.

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const FILES = ["__init__.py", "graph.py", "ricci.py", "datasets.py", "importer.py", "session.py"];
const LIMITS = { methods: ["ollivier", "forman"], max_nodes: 600, datasets: ["karate", "football", "sbm", "lfr"] };
// Real networks shipped with the page (web/datasets/), since the browser can't
// download them from their original sites.
const BUNDLED = ["football"];

let py = null;
let api = null;
let looping = false;
const queue = [];

const post = (msg) => self.postMessage(msg);

async function init(base) {
  post({ type: "progress", message: "Starting Python in your browser (first visit downloads about 30 MB)…" });
  py = await loadPyodide();
  post({ type: "progress", message: "Loading numpy, scipy and networkx…" });
  await py.loadPackage(["numpy", "scipy", "networkx"]);
  py.FS.mkdirTree("/home/pyodide/interactive_graph");
  for (const f of FILES) {
    const r = await fetch(`${base}py/interactive_graph/${f}`);
    if (!r.ok) throw new Error(`could not fetch ${f} (${r.status})`);
    py.FS.writeFile(`/home/pyodide/interactive_graph/${f}`, await r.text());
  }
  py.FS.mkdirTree("/home/pyodide/web/datasets");
  for (const name of BUNDLED) {
    const r = await fetch(`${base}datasets/${name}.json`);
    if (r.ok) py.FS.writeFile(`/home/pyodide/web/datasets/${name}.json`, await r.text());
  }
  py.globals.set("LIMITS_JSON", JSON.stringify(LIMITS));
  api = py.runPython(`
import json, sys
sys.path.insert(0, "/home/pyodide")
from interactive_graph import datasets, importer
from interactive_graph.session import Session

LIMITS = json.loads(LIMITS_JSON)
session = Session(None, LIMITS)

def handle(text):
    reply = session.handle(json.loads(text))
    return json.dumps(reply) if reply else ""

def import_file(name, data):
    raw = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    graph, summary = importer.read(name, raw)
    session.handle({"op": "load_graph", "graph": graph, "summary": summary})
    return f"Imported {name}: {summary}"

def catalog():
    for d in datasets.catalog():
        if d["id"] == "lfr":
            for p in d["params"]:
                if p["name"] == "n":
                    p["max"] = LIMITS["max_nodes"]
    return json.dumps([d for d in datasets.catalog() if d["id"] in LIMITS["datasets"]])

{"handle": handle, "state": lambda: json.dumps(session.snapshot()),
 "iterate": lambda: session.iterate(), "running": lambda: session.running,
 "import_file": import_file, "catalog": catalog}
`).toJs({ dict_converter: Object.fromEntries });

  post({ type: "catalog", catalog: JSON.parse(api.catalog()) });
  handle({ op: "load_dataset", name: "karate" });
  post({ type: "ready" });
}

function postState() {
  post(JSON.parse(api.state()));
}

function handle(op) {
  try {
    const reply = api.handle(JSON.stringify(op));
    if (reply) post(JSON.parse(reply));
  } catch (e) {
    post({ type: "error", message: cleanError(e) });
  }
  postState();
  if (api.running()) startLoop();
}

function startLoop() {
  if (looping) return;
  looping = true;
  const tick = () => {
    if (!api.running()) { looping = false; postState(); return; }
    try {
      api.iterate();
    } catch (e) {
      post({ type: "error", message: `Ricci flow stopped: ${cleanError(e)}` });
      api.handle(JSON.stringify({ op: "flow_stop" }));
    }
    postState();
    setTimeout(tick, 0);  // let queued messages (steering, Stop) in first
  };
  setTimeout(tick, 0);
}

// Python errors arrive with the whole traceback; keep the last line.
function cleanError(e) {
  const lines = String(e.message || e).trim().split("\n");
  return lines[lines.length - 1].replace(/^[\w.]+(Error|Exception): /, "");
}

self.onmessage = async (e) => {
  const msg = e.data;
  if (msg.type === "init") {
    try {
      await init(msg.base);
      for (const m of queue.splice(0)) self.onmessage({ data: m });
    } catch (err) {
      post({ type: "progress", message: `Could not start Python in this browser: ${err.message}` });
    }
    return;
  }
  if (!api) { queue.push(msg); return; }
  if (msg.type === "op") handle(msg.op);
  if (msg.type === "import") {
    try {
      const message = api.import_file(msg.name, new Uint8Array(msg.data));
      post({ reply: msg.id, message });
    } catch (err) {
      post({ reply: msg.id, error: cleanError(err) });
    }
    postState();
  }
};
