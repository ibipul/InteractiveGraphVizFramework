// Graph editor front end. Every edit is sent to the backend as one small
// message; the backend answers with the full new state, which is drawn here.
// The backend is the Python server over a WebSocket, or, when no server
// answers (e.g. on GitHub Pages), LocalBackend running in this page.

import { LocalBackend } from "./local.js";

const color = d3.scaleOrdinal(d3.schemeTableau10);
const radius = (d) => Math.min(32, 10 + 2.2 * Math.sqrt(Math.max(0, d.total)));
const width = (d) => Math.min(12, 1.5 + Math.sqrt(Math.max(0, d.total)));
const linkKey = (l) => `${l.source.id ?? l.source}-${l.target.id ?? l.target}`;
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ state

let backend = null;
let nodes = [];          // simulation node objects, kept across updates so positions survive
let links = [];
let snap = null;         // last state from the backend
let selected = null;     // {kind: "node", id} or {kind: "link", source, target}
let pendingSelect = null;
let fitNext = true;

// ------------------------------------------------------------------ drawing

const svg = d3.select("#graph");
const view = svg.append("g");
const linkLayer = view.append("g");
const dragLine = view.append("line").attr("class", "dragline").style("display", "none");
const nodeLayer = view.append("g");

const zoom = d3.zoom()
  .scaleExtent([0.15, 4])
  .clickDistance(4)
  .on("zoom", (e) => view.attr("transform", e.transform));
svg.call(zoom).on("dblclick.zoom", null);

function centerOrigin() {
  const { width: w, height: h } = svg.node().getBoundingClientRect();
  svg.call(zoom.transform, d3.zoomIdentity.translate(w / 2, h / 2));
}
centerOrigin();

const sim = d3.forceSimulation()
  .force("link", d3.forceLink().id((d) => d.id).distance(55))
  .force("charge", d3.forceManyBody().strength(-140))
  .force("x", d3.forceX(0).strength(0.04))
  .force("y", d3.forceY(0).strength(0.04))
  .force("collide", d3.forceCollide((d) => radius(d) + 3))
  .on("tick", tick);

function tick() {
  linkLayer.selectAll("line")
    .attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
    .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
  nodeLayer.selectAll("g.node").attr("transform", (d) => `translate(${d.x},${d.y})`);
}

function isSelected(kind, d) {
  if (!selected || selected.kind !== kind) return false;
  if (kind === "node") return selected.id === d.id;
  return selected.source === d.source.id && selected.target === d.target.id;
}

function render() {
  linkLayer.selectAll("g.l")
    .data(links, linkKey)
    .join((enter) => {
      const g = enter.append("g").attr("class", "l");
      g.append("line").attr("class", "link");
      g.append("line").attr("class", "link-hit")
        .on("click", (e, d) => select({ kind: "link", source: d.source.id, target: d.target.id }));
      return g;
    })
    .each(function (d) {
      // Child lines keep the datum they were created with; refresh it.
      d3.select(this).selectAll("line").datum(d);
    })
    .select(".link")
    .attr("stroke-width", width)
    .classed("selected", (d) => isSelected("link", d));

  const g = nodeLayer.selectAll("g.node")
    .data(nodes, (d) => d.id)
    .join((enter) => {
      const g = enter.append("g").attr("class", "node").call(drag);
      g.append("circle");
      g.append("text");
      g.append("title");
      g.on("click", (e, d) => select({ kind: "node", id: d.id }))
        .on("dblclick", (e, d) => { d.fx = d.fy = null; sim.alpha(0.2).restart(); });
      return g;
    })
    .classed("selected", (d) => isSelected("node", d));
  g.select("circle").attr("r", radius).attr("fill", (d) => color(d.nval));
  g.select("text").text((d) => d.id);
  g.select("title").text((d) => `Node ${d.id} · group ${d.nval} · load ${d.current}/${d.total}`);
  tick();
}

function fitView() {
  if (!nodes.length) return centerOrigin();
  const box = svg.node().getBoundingClientRect();
  const [x0, x1] = d3.extent(nodes, (d) => d.x);
  const [y0, y1] = d3.extent(nodes, (d) => d.y);
  const k = Math.min(1.5, 0.9 / Math.max((x1 - x0 + 80) / box.width, (y1 - y0 + 80) / box.height));
  svg.transition().duration(400).call(zoom.transform,
    d3.zoomIdentity.translate(box.width / 2, box.height / 2).scale(k)
      .translate(-(x0 + x1) / 2, -(y0 + y1) / 2));
}

// ------------------------------------------------------------------ gestures

// Drag from a node to another node to link them; with Shift held, move the node.
let dragMode = null;
let dropTarget = null;

const drag = d3.drag()
  .clickDistance(4)
  .on("start", (e, d) => {
    dragMode = e.sourceEvent.shiftKey ? "move" : "link";
    if (dragMode === "move") {
      if (!e.active) sim.alphaTarget(0.2).restart();
      d.fx = d.x; d.fy = d.y;
    }
  })
  .on("drag", (e, d) => {
    if (dragMode === "move") { d.fx = e.x; d.fy = e.y; return; }
    dragLine.style("display", null)
      .attr("x1", d.x).attr("y1", d.y).attr("x2", e.x).attr("y2", e.y);
    const hit = sim.find(e.x, e.y, 40);
    dropTarget = hit && hit !== d && Math.hypot(hit.x - e.x, hit.y - e.y) <= radius(hit) + 6 ? hit : null;
    nodeLayer.selectAll("g.node").classed("target", (n) => n === dropTarget);
  })
  .on("end", (e, d) => {
    if (dragMode === "move") {
      if (!e.active) sim.alphaTarget(0);  // node stays pinned; double-click to release
    } else {
      dragLine.style("display", "none");
      nodeLayer.selectAll("g.node").classed("target", false);
      if (dropTarget) {
        pendingSelect = { kind: "link", source: Math.min(d.id, dropTarget.id), target: Math.max(d.id, dropTarget.id) };
        send({ op: "add_link", source: d.id, target: dropTarget.id });
      }
    }
    dragMode = dropTarget = null;
  });

svg.on("click", (e) => {
  if (e.target !== svg.node()) return;  // only clicks on empty canvas
  const [x, y] = d3.zoomTransform(svg.node()).invert(d3.pointer(e));
  send({ op: "add_node", x, y });
});

window.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select")) return;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); send({ op: e.shiftKey ? "redo" : "undo" }); return; }
  if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); send({ op: "redo" }); return; }
  if (mod || e.altKey) return;

  switch (e.key.toLowerCase()) {
    case "s": send({ op: "step" }); break;
    case "escape": select(null); break;
    case "a": case "+": case "=": if (selected) send({ op: "adjust", ...selected, delta: 1 }); break;
    case "d": case "-": if (selected) send({ op: "adjust", ...selected, delta: -1 }); break;
    case "delete": case "backspace":
      if (selected) { send({ op: "delete", ...selected }); select(null); }
      break;
    default: return;
  }
  e.preventDefault();
});

// ------------------------------------------------------------------ state from backend

function onMessage(msg) {
  if (msg.type === "error") return toast(msg.message);
  if (msg.type === "added") { pendingSelect = { kind: "node", id: msg.node.id }; return; }
  if (msg.type === "state") applyState(msg);
}

function applyState(s) {
  snap = s;
  const old = new Map(nodes.map((n) => [n.id, n]));
  const oldKeys = nodes.map((n) => n.id).join() + "|" + links.map(linkKey).join();

  nodes = s.graph.nodes.map((n) => {
    const o = old.get(n.id);
    if (o) return Object.assign(o, { nval: n.nval, current: n.current, total: n.total });
    const fresh = { ...n };
    if (fresh.x == null) { fresh.x = (Math.random() - 0.5) * 40; fresh.y = (Math.random() - 0.5) * 40; }
    return fresh;
  });
  links = s.graph.links.map((l) => ({ ...l }));
  sim.nodes(nodes);
  sim.force("link").links(links);

  const changed = oldKeys !== nodes.map((n) => n.id).join() + "|" + links.map(linkKey).join();
  if (fitNext) {
    sim.alpha(1);
    for (let i = 0; i < 200; i++) sim.tick();
    sim.alpha(0.05).restart();
  } else if (changed) {
    sim.alpha(0.3).restart();
  }

  if (pendingSelect) { selected = pendingSelect; pendingSelect = null; }
  if (selected && !findSelected()) selected = null;

  render();
  if (fitNext) { fitView(); fitNext = false; }
  renderInspector();
  renderControls();
}

function findSelected() {
  if (!selected) return null;
  if (selected.kind === "node") return nodes.find((n) => n.id === selected.id);
  return links.find((l) => l.source.id === selected.source && l.target.id === selected.target);
}

function select(sel) {
  selected = sel;
  render();
  renderInspector();
}

function send(op) {
  if (backend) backend.send(op);
}

// ------------------------------------------------------------------ side panel

function renderControls() {
  $("undo").disabled = !snap.can_undo;
  $("redo").disabled = !snap.can_redo;
  $("step").disabled = !snap.has_model;
  $("auto-step").checked = snap.auto_step;
  $("auto-step").disabled = !snap.has_model;
  $("auto-step").parentElement.classList.toggle("disabled", !snap.has_model);
  $("counts").textContent = `${nodes.length} nodes · ${links.length} links`;
}

function renderInspector() {
  const box = $("inspector");
  const item = findSelected();
  if (!item) {
    box.innerHTML = '<p class="muted">Click a node or link to see and edit it.</p>';
    return;
  }
  const isNode = selected.kind === "node";
  const fields = isNode
    ? [["nval", "Group"], ["current", "Current"], ["total", "Total"]]
    : [["lval", "Weight"], ["current", "Current"], ["total", "Total"]];

  box.replaceChildren();
  const title = el("div", "title");
  if (isNode) {
    const sw = el("span", "swatch");
    sw.style.background = color(item.nval);
    title.append(sw, `Node ${item.id}`);
  } else {
    title.append(`Link ${item.source.id} – ${item.target.id}`);
  }
  box.append(title);

  for (const [field, label] of fields) {
    const row = el("div", "field");
    const id = `f-${field}`;
    const lab = el("label");
    lab.htmlFor = id;
    lab.textContent = label;
    const input = el("input");
    Object.assign(input, { id, type: "number", step: 1, value: item[field] });
    input.addEventListener("change", () => send({ op: "set", ...selected, field, value: input.value }));
    const stepper = el("div", "stepper");
    stepper.append(
      button("−", () => send({ op: "set", ...selected, field, value: item[field] - 1 }), "small"),
      input,
      button("+", () => send({ op: "set", ...selected, field, value: item[field] + 1 }), "small"),
    );
    row.append(lab, stepper);
    box.append(row);
  }

  const actions = el("div", "row");
  actions.append(
    button("Load −1 (D)", () => send({ op: "adjust", ...selected, delta: -1 })),
    button("Load +1 (A)", () => send({ op: "adjust", ...selected, delta: 1 })),
  );
  box.append(actions);

  const footer = el("div", "row");
  if (isNode) {
    const degree = links.filter((l) => l.source.id === item.id || l.target.id === item.id).length;
    footer.append(el("span", "muted", `${degree} link${degree === 1 ? "" : "s"}`));
  }
  footer.append(button("Delete", () => { send({ op: "delete", ...selected }); select(null); }, "danger"));
  box.append(footer);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

function button(text, onClick, cls) {
  const b = el("button", cls, text);
  b.addEventListener("click", onClick);
  return b;
}

let toastTimer;
function toast(message) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

function setStatus(kind, text, title) {
  const s = $("status");
  s.className = `status ${kind}`;
  s.textContent = text;
  s.title = title || text;
}

// ------------------------------------------------------------------ toolbar

$("undo").onclick = () => send({ op: "undo" });
$("redo").onclick = () => send({ op: "redo" });
$("step").onclick = () => send({ op: "step" });
$("auto-step").onchange = (e) => send({ op: "set_auto_step", value: e.target.checked });

$("download").onclick = () => {
  const blob = new Blob([JSON.stringify(snap.graph, null, 2) + "\n"], { type: "application/json" });
  const a = el("a");
  a.href = URL.createObjectURL(blob);
  a.download = "graph.json";
  a.click();
  URL.revokeObjectURL(a.href);
};

$("load").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    fitNext = true;
    send({ op: "load", graph: JSON.parse(await file.text()) });
  } catch (err) {
    toast(`Could not read ${file.name}: ${err.message}`);
  }
};

$("example").onclick = async () => {
  fitNext = true;
  send({ op: "load", graph: await fetchExample() });
};
$("clear").onclick = () => send({ op: "load", graph: { nodes: [], links: [] } });

window.addEventListener("resize", () => { if (!nodes.length) centerOrigin(); });

// ------------------------------------------------------------------ backends

async function fetchExample() {
  const r = await fetch("examples/default.json");
  return r.json();
}

class SocketBackend {
  constructor(url) {
    this.url = url;
    this.listeners = [];
    this.ws = null;
    this.retry = 500;
  }

  onMessage(fn) { this.listeners.push(fn); }

  // Resolves true once connected, or false if the first attempt fails.
  connect() {
    return new Promise((resolve) => {
      let opened = false;
      const ws = new WebSocket(this.url);
      const timer = setTimeout(() => { if (!opened) { ws.close(); resolve(false); } }, 2500);
      ws.onopen = () => {
        opened = true;
        clearTimeout(timer);
        this.ws = ws;
        this.retry = 500;
        setStatus("live", "Live · saved on server", "Connected. Every edit is saved and shared with other open tabs.");
        resolve(true);
      };
      ws.onmessage = (e) => { const msg = JSON.parse(e.data); this.listeners.forEach((fn) => fn(msg)); };
      ws.onclose = () => {
        clearTimeout(timer);
        if (!opened) return resolve(false);
        this.ws = null;
        setStatus("down", "Reconnecting…", "Lost the connection to the server. Retrying.");
        setTimeout(() => this.connect(), this.retry);
        this.retry = Math.min(this.retry * 2, 5000);
      };
    });
  }

  send(op) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(op));
    else toast("Not connected to the server; the edit was not saved.");
  }
}

async function start() {
  const params = new URLSearchParams(location.search);
  if (!params.has("demo") && location.protocol.startsWith("http")) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const sock = new SocketBackend(`${proto}//${location.host}${location.pathname.replace(/[^/]*$/, "")}ws`);
    sock.onMessage(onMessage);
    if (await sock.connect()) { backend = sock; return; }
  }
  backend = new LocalBackend(await fetchExample());
  backend.onMessage(onMessage);
  setStatus("demo", "Demo · runs in your browser",
    "No server found, so edits and the example model step run in this page and are kept in this browser only.");
}

start();
