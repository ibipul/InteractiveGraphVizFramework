// Graph editor and Ricci-flow front end. Every edit is sent to the backend as
// one small message; the backend answers with the full new state, which is
// drawn here on a canvas. The backend is the Python server over a WebSocket,
// or, when no server answers (e.g. on GitHub Pages), the same Python package
// running in this browser through Pyodide (py-worker.js).

const $ = (id) => document.getElementById(id);
const fmt = (x, d = 3) => (x == null || Number.isNaN(x) ? "–" : Number(x).toFixed(d));
const keyOf = (s, t) => (s < t ? `${s}-${t}` : `${t}-${s}`);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ------------------------------------------------------------------ state

let backend = null;
let snap = null;               // last state from the backend
let structure = -1;            // snap.structure the arrays below were built for
let nodes = [];                // simulation node objects, kept across updates so positions survive
let links = [];
let nodeById = new Map();
let linkByKey = new Map();
let degree = new Map();
let catalog = [];
let isDemo = false;
let mode = "select";
let colorBy = "community";
const sel = { nodes: new Set(), links: new Set() };
let pendingSelect = null;
let fitNext = true;
let medianLength = 1;
let curvScale = 1;             // |curvature| that maps to the darkest colour
const view = { curv: true, dots: true, layout: true, labels: false };

// ------------------------------------------------------------------ canvas

const canvas = $("graph");
const ctx = canvas.getContext("2d");
let width = 0, height = 0, dpr = 1;
let transform = d3.zoomIdentity;
let dirty = false;

function resize() {
  const r = canvas.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  width = r.width; height = r.height;
  canvas.width = Math.max(1, Math.round(width * dpr));
  canvas.height = Math.max(1, Math.round(height * dpr));
  if (fitNext && snap && fitView(false)) fitNext = false;
  redraw();
}
new ResizeObserver(resize).observe(canvas);

function redraw() {
  if (!dirty) { dirty = true; requestAnimationFrame(draw); }
}

// Colours
const palette = [...d3.schemeTableau10, "#86bcb6", "#d37295", "#a0cbe8", "#ffbe7d", "#8cd17d",
  "#b6992d", "#499894", "#f1ce63", "#d4a6c8", "#9d7660"];
const colorAt = (i) => (i < palette.length ? palette[i] : d3.interpolateRainbow((i * 0.618034) % 1));
const curvColors = d3.range(11).map((i) => d3.interpolateRdBu(i / 10));
let commSizes = [];

function nodeColor(d) {
  if (colorBy === "truth") return d.truth == null ? "#aaa" : colorAt(d.truth);
  if (colorBy === "group") return colorAt(d.nval);
  if (d.community == null) return "#aaa";
  return commSizes[d.community] === 1 ? "#b8bcc4" : colorAt(d.community);
}

function linkColorIndex(l) {
  if (!view.curv || l.curvature == null) return -1;
  const t = Math.max(-1, Math.min(1, l.curvature / curvScale));
  return Math.round((t + 1) * 5);
}

// 0 = solid ... 4 = sparse dots, from the link's length relative to the median.
const DASHES = [[], [7, 3], [4, 4], [2, 4], [1, 6]];
function dashIndex(l) {
  if (!view.dots || l.length == null) return 0;
  const r = l.length / medianLength;
  return r < 1.6 ? 0 : r < 2.5 ? 1 : r < 4 ? 2 : r < 7 ? 3 : 4;
}

function radius(d) {
  const n = nodes.length;
  const base = n > 2000 ? 2.5 : n > 600 ? 4 : n > 150 ? 6 : 9;
  return base + 1.6 * Math.sqrt(Math.max(0, (d.total ?? 1) - 1));
}

function draw() {
  dirty = false;
  const k = transform.k;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.translate(transform.x, transform.y);
  ctx.scale(k, k);

  // Links, batched by colour and dash so a big graph is a few dozen strokes.
  const plain = css("--link");
  const batches = new Map();
  for (const l of links) {
    const key = (l.pinned ? -2 : linkColorIndex(l)) * 10 + dashIndex(l);
    let b = batches.get(key);
    if (!b) batches.set(key, (b = []));
    b.push(l);
  }
  const lw = (links.length > 5000 ? 0.7 : 1.3) / k;
  for (const [key, batch] of batches) {
    const ci = Math.floor((key + 20) / 10) - 2, di = ((key % 10) + 10) % 10;
    ctx.strokeStyle = ci === -2 ? css("--pin") : ci < 0 ? plain : curvColors[ci];
    ctx.globalAlpha = di >= 3 ? 0.45 : 0.85;
    ctx.lineWidth = ci === -2 ? lw * 1.6 : lw;
    ctx.setLineDash(DASHES[di].map((v) => v / k));
    ctx.beginPath();
    for (const l of batch) {
      ctx.moveTo(l.source.x, l.source.y);
      ctx.lineTo(l.target.x, l.target.y);
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  // The selection on top.
  const inside = selectedLinks("inside");
  if (inside.length) {
    ctx.strokeStyle = css("--select");
    ctx.lineWidth = 2.5 / k;
    strokeLinks(inside);
  }

  // Nodes, batched by colour.
  const byColor = new Map();
  for (const d of nodes) {
    const c = nodeColor(d);
    let b = byColor.get(c);
    if (!b) byColor.set(c, (b = []));
    b.push(d);
  }
  const outline = nodes.length <= 3000;
  ctx.lineWidth = 1.2 / k;
  ctx.strokeStyle = css("--canvas");
  for (const [c, batch] of byColor) {
    ctx.fillStyle = c;
    ctx.beginPath();
    for (const d of batch) {
      const r = radius(d);
      ctx.moveTo(d.x + r, d.y);
      ctx.arc(d.x, d.y, r, 0, 2 * Math.PI);
    }
    ctx.fill();
    if (outline) ctx.stroke();
  }
  if (sel.nodes.size) {
    ctx.strokeStyle = css("--select");
    ctx.lineWidth = 3 / k;
    ctx.beginPath();
    for (const id of sel.nodes) {
      const d = nodeById.get(id);
      if (!d) continue;
      const r = radius(d) + 2 / k;
      ctx.moveTo(d.x + r, d.y);
      ctx.arc(d.x, d.y, r, 0, 2 * Math.PI);
    }
    ctx.stroke();
  }
  if (dropTarget) {
    ctx.strokeStyle = css("--accent");
    ctx.lineWidth = 3 / k;
    ctx.beginPath();
    ctx.arc(dropTarget.x, dropTarget.y, radius(dropTarget) + 3 / k, 0, 2 * Math.PI);
    ctx.stroke();
  }

  // Labels when there is room.
  if (view.labels || nodes.length <= 60) {
    ctx.font = `${11 / k}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = css("--text");
    const small = nodes.length <= 60 && !view.labels;
    for (const d of nodes) {
      const text = small ? String(d.id) : d.label ?? String(d.id);
      if (small) {
        ctx.fillStyle = "#fff";
        ctx.fillText(text, d.x, d.y);
      } else {
        ctx.fillText(text, d.x, d.y - radius(d) - 7 / k);
      }
    }
  }

  // Gesture overlays (screen space).
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (box) {
    ctx.strokeStyle = css("--accent");
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1;
    ctx.strokeRect(box.x0, box.y0, box.x1 - box.x0, box.y1 - box.y0);
    ctx.setLineDash([]);
  }
  if (dragLine) {
    ctx.strokeStyle = css("--accent");
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(...transform.apply([dragLine.from.x, dragLine.from.y]));
    ctx.lineTo(dragLine.x, dragLine.y);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function strokeLinks(list) {
  ctx.beginPath();
  for (const l of list) {
    ctx.moveTo(l.source.x, l.source.y);
    ctx.lineTo(l.target.x, l.target.y);
  }
  ctx.stroke();
}

// ------------------------------------------------------------------ layout

const linkForce = d3.forceLink().id((d) => d.id);
const sim = d3.forceSimulation()
  .force("link", linkForce)
  .force("charge", d3.forceManyBody())
  .force("x", d3.forceX(0).strength(0.03))
  .force("y", d3.forceY(0).strength(0.03))
  .on("tick", redraw);

function configureForces() {
  const n = nodes.length;
  const base = n > 1000 ? 22 : n > 200 ? 32 : 45;
  const len = (l) => (view.layout && l.length != null ? Math.max(0.3, Math.min(10, l.length / medianLength)) : 1);
  linkForce
    .distance((l) => base * len(l))
    .strength((l) => (1 / Math.min(degree.get(l.source.id ?? l.source) || 1, degree.get(l.target.id ?? l.target) || 1)) / len(l) ** 2);
  sim.force("charge").strength(n > 1500 ? -12 : n > 300 ? -25 : -90).distanceMax(400).theta(n > 2000 ? 1.2 : 0.9);
  sim.force("collide", n <= 400 ? d3.forceCollide((d) => radius(d) + 1.5) : null);
  sim.alphaDecay(n > 2000 ? 0.04 : 0.0228);
}

function fitView(animate = true) {
  if (!width || !height) return false;  // not laid out yet; resize() fits later
  if (!nodes.length) {
    setTransform(d3.zoomIdentity.translate(width / 2, height / 2), animate);
    return true;
  }
  const [x0, x1] = d3.extent(nodes, (d) => d.x);
  const [y0, y1] = d3.extent(nodes, (d) => d.y);
  const k = Math.min(2, 0.9 / Math.max((x1 - x0 + 40) / width, (y1 - y0 + 40) / height));
  setTransform(d3.zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2), animate);
  return true;
}

// ------------------------------------------------------------------ gestures

const zoom = d3.zoom()
  .scaleExtent([0.02, 8])
  .clickDistance(4)
  .filter((e) => (e.type === "wheel" || !e.shiftKey) && !e.button)
  .on("zoom", (e) => { transform = e.transform; if (e.sourceEvent) userMovedView = true; redraw(); });
let userMovedView = false;
let wasRunning = false;

function setTransform(t, animate) {
  const s = d3.select(canvas);
  (animate ? s.transition().duration(450) : s).call(zoom.transform, t);
}

function hitNode(px, py) {
  const [x, y] = transform.invert([px, py]);
  const d = sim.find(x, y, 30 / transform.k + 30);
  return d && Math.hypot(d.x - x, d.y - y) <= radius(d) + 4 / transform.k ? d : null;
}

function hitLink(px, py) {
  const [x, y] = transform.invert([px, py]);
  const tol = 5 / transform.k;
  let best = null, bestDist = tol;
  for (const l of links) {
    const { x: x1, y: y1 } = l.source, { x: x2, y: y2 } = l.target;
    const dx = x2 - x1, dy = y2 - y1;
    const len2 = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / len2));
    const dist = Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy));
    if (dist < bestDist) { best = l; bestDist = dist; }
  }
  return best;
}

let dragLine = null, dropTarget = null, box = null, dragMoved = null;

const drag = d3.drag()
  .container(canvas)
  .clickDistance(3)
  .filter((e) => !e.button)
  .subject((e) => {
    const d = hitNode(e.x, e.y);
    if (d) return { node: d, x: e.x, y: e.y };
    if (e.sourceEvent?.shiftKey && mode === "select") return { box: true, x: e.x, y: e.y };
    return null;  // let zoom pan
  })
  .on("start", (e) => {
    const s = e.subject;
    if (s.box) { box = { x0: e.x, y0: e.y, x1: e.x, y1: e.y }; return; }
    if (mode === "add") { dragLine = { from: s.node, x: e.x, y: e.y }; return; }
    // Move the node, or the whole selection if the node is part of it.
    dragMoved = sel.nodes.has(s.node.id) ? [...sel.nodes].map((id) => nodeById.get(id)).filter(Boolean) : [s.node];
    for (const d of dragMoved) { d.fx = d.x; d.fy = d.y; }
    sim.alphaTarget(0.15).restart();
  })
  .on("drag", (e) => {
    const s = e.subject;
    if (box) { box.x1 = e.x; box.y1 = e.y; redraw(); return; }
    if (dragLine) {
      dragLine.x = e.x; dragLine.y = e.y;
      const hit = hitNode(e.x, e.y);
      dropTarget = hit && hit !== s.node ? hit : null;
      redraw();
      return;
    }
    const dx = e.dx / transform.k, dy = e.dy / transform.k;
    for (const d of dragMoved) { d.fx += dx; d.fy += dy; }
  })
  .on("end", (e) => {
    if (box) {
      const [ax, ay] = transform.invert([Math.min(box.x0, box.x1), Math.min(box.y0, box.y1)]);
      const [bx, by] = transform.invert([Math.max(box.x0, box.x1), Math.max(box.y0, box.y1)]);
      for (const d of nodes) if (d.x >= ax && d.x <= bx && d.y >= ay && d.y <= by) sel.nodes.add(d.id);
      box = null;
      selectionChanged();
      return;
    }
    if (dragLine) {
      const from = e.subject.node;
      if (dropTarget) {
        pendingSelect = { links: [keyOf(from.id, dropTarget.id)] };
        send({ op: "add_link", source: from.id, target: dropTarget.id });
      }
      dragLine = dropTarget = null;
      redraw();
      return;
    }
    sim.alphaTarget(0);  // moved nodes stay pinned in place; double-click to release
    dragMoved = null;
  });

d3.select(canvas).call(drag).call(zoom).on("dblclick.zoom", null);

canvas.addEventListener("click", (e) => {
  const [px, py] = [e.offsetX, e.offsetY];
  const d = hitNode(px, py);
  const l = d ? null : hitLink(px, py);
  if (!d && !l) {
    if (mode === "add") {
      const [x, y] = transform.invert([px, py]);
      send({ op: "add_node", x, y });
    } else if (!e.shiftKey) {
      clearSelection();
    }
    return;
  }
  if (!e.shiftKey) { sel.nodes.clear(); sel.links.clear(); }
  const set = d ? sel.nodes : sel.links;
  const id = d ? d.id : keyOf(l.source.id, l.target.id);
  if (e.shiftKey && set.has(id)) set.delete(id); else set.add(id);
  selectionChanged();
});

canvas.addEventListener("dblclick", (e) => {
  const d = hitNode(e.offsetX, e.offsetY);
  if (d) { d.fx = d.fy = null; sim.alpha(0.2).restart(); }
});

function setMode(m) {
  mode = m;
  $("mode-select").classList.toggle("on", m === "select");
  $("mode-add").classList.toggle("on", m === "add");
  canvas.classList.toggle("add", m === "add");
}
$("mode-select").onclick = () => setMode("select");
$("mode-add").onclick = () => setMode("add");

window.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select")) return;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); send({ op: e.shiftKey ? "redo" : "undo" }); return; }
  if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); send({ op: "redo" }); return; }
  if (mod || e.altKey) return;
  const hasSel = sel.nodes.size + sel.links.size > 0;
  switch (e.key) {
    case "v": case "V": setMode("select"); break;
    case "n": case "N": setMode("add"); break;
    case "f": case "F": fitView(); break;
    case " ": toggleRun(); break;
    case "s": case "S": flowOnce(); break;
    case "Escape": clearSelection(); break;
    case "[": if (hasSel) steer("pull"); break;
    case "]": if (hasSel) steer("push"); break;
    case "p": case "P": if (hasSel) steer("pin"); break;
    case "a": case "A": case "+": case "=": adjustSelection(1); break;
    case "d": case "D": case "-": adjustSelection(-1); break;
    case "Delete": case "Backspace": if (hasSel) deleteSelection(); break;
    default: return;
  }
  e.preventDefault();
});

// ------------------------------------------------------------------ selection and steering

function clearSelection() {
  sel.nodes.clear(); sel.links.clear();
  selectionChanged();
}

function selectionChanged() {
  for (const id of [...sel.nodes]) if (!nodeById.has(id)) sel.nodes.delete(id);
  for (const k of [...sel.links]) if (!linkByKey.has(k)) sel.links.delete(k);
  inspectorShape = null;
  redraw();
  renderInspector();
}

// Links a steering action applies to:
//   inside: selected links, plus links with both ends in the selected nodes
//   boundary: links with exactly one end in the selected nodes
function selectedLinks(kind) {
  if (!sel.nodes.size && !sel.links.size) return [];
  const out = [];
  for (const l of links) {
    const a = sel.nodes.has(l.source.id), b = sel.nodes.has(l.target.id);
    const picked = sel.links.has(keyOf(l.source.id, l.target.id));
    if (kind === "inside" ? picked || (a && b) : a !== b) out.push(l);
  }
  return out;
}

const pairs = (list) => list.map((l) => [l.source.id, l.target.id]);

function steer(action) {
  const inside = selectedLinks("inside");
  if (action === "pull") {
    if (!inside.length) return toast("No links inside the selection to pull together");
    send({ op: "scale", links: pairs(inside), factor: 0.5 });
  } else if (action === "push") {
    let edges = selectedLinks("boundary");
    if (!edges.length) edges = inside;  // a selection of links only: lengthen them
    if (!edges.length) return toast("No links to push apart");
    send({ op: "scale", links: pairs(edges), factor: 2 });
  } else if (action === "pin" || action === "unpin") {
    if (!inside.length) return toast("No links in the selection to pin");
    const value = action === "pin" ? !inside.every((l) => l.pinned) : false;
    send({ op: "pin", links: pairs(inside), value });
  }
}

function adjustSelection(delta) {
  for (const id of sel.nodes) send({ op: "adjust", kind: "node", id, delta });
  for (const k of sel.links) {
    const [s, t] = k.split("-").map(Number);
    send({ op: "adjust", kind: "link", source: s, target: t, delta });
  }
}

function deleteSelection() {
  send({ op: "delete_many", nodes: [...sel.nodes], links: [...sel.links].map((k) => k.split("-").map(Number)) });
  clearSelection();
}

// The inspector is rebuilt only when the selection or its make-up changes;
// otherwise (e.g. every flow iteration) its numbers are updated in place, so
// buttons don't get replaced under the mouse.
let inspectorShape = null;
let inspectorUpdaters = [];

function renderInspector() {
  const nSel = sel.nodes.size, lSel = sel.links.size;
  const inside = selectedLinks("inside");
  const shape = [structure, [...sel.nodes].join(), [...sel.links].join(),
    inside.map((l) => (l.pinned ? 1 : 0)).join(""), colorBy].join("|");
  if (shape === inspectorShape) {
    for (const update of inspectorUpdaters) update();
    return;
  }
  inspectorShape = shape;
  inspectorUpdaters = [];
  buildInspector(nSel, lSel);
}

function live(element, compute) {
  const update = () => {
    if (document.activeElement === element) return;
    const v = compute();
    if (element.tagName === "INPUT") element.value = v; else element.textContent = v;
  };
  update();
  inspectorUpdaters.push(update);
  return element;
}

function buildInspector(nSel, lSel) {
  const box = $("inspector");
  box.replaceChildren();
  if (!nSel && !lSel) {
    box.append(el("p", "muted", "Click a node or link, Shift+click to add more, or Shift+drag a box. " +
      "Click a community below to select all of it. Steering edits apply to the next flow iteration."));
    return;
  }

  if (nSel === 1 && !lSel) {
    const d = nodeById.get([...sel.nodes][0]);
    const title = el("div", "title");
    const sw = el("span", "swatch"); sw.style.background = nodeColor(d);
    title.append(sw, d.label && d.label !== String(d.id) ? `${d.label} (node ${d.id})` : `Node ${d.id}`);
    box.append(title);
    const names = snap.graph.meta?.truth_names || [];
    box.append(live(el("p", "facts"), () => {
      const facts = [`${degree.get(d.id) || 0} links`];
      if (d.community != null) facts.push(`community ${d.community}`);
      if (d.truth != null) facts.push(`truth: ${names[d.truth] ?? d.truth}`);
      return facts.join(" · ");
    }));
    box.append(numberField("Group", () => d.nval, (v) => send({ op: "set", kind: "node", id: d.id, field: "nval", value: v }), 1));
    box.append(numberField("Load", () => d.total, null, 1, (delta) => send({ op: "adjust", kind: "node", id: d.id, delta })));
  } else if (lSel === 1 && !nSel) {
    const l = linkByKey.get([...sel.links][0]);
    const name = (id) => nodeById.get(id)?.label ?? id;
    box.append(el("div", "title", `Link ${name(l.source.id)} – ${name(l.target.id)}`));
    box.append(live(el("p", "facts"), () => `curvature ${fmt(l.curvature)}${l.pinned ? " · pinned" : ""}`));
    const ref = { kind: "link", source: l.source.id, target: l.target.id };
    box.append(numberField("Length", () => l.length, (v) => send({ op: "set", ...ref, field: "length", value: v }), 0.1,
      null, (f) => send({ op: "scale", links: [[l.source.id, l.target.id]], factor: f })));
    box.append(numberField("Capacity", () => l.capacity ?? 1, (v) => send({ op: "set", ...ref, field: "capacity", value: v }), 0.1));
    box.append(numberField("Load", () => l.total, null, 1, (delta) => send({ op: "adjust", ...ref, delta })));
    const pin = el("label", "toggle");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = !!l.pinned;
    cb.onchange = () => send({ op: "set", ...ref, field: "pinned", value: cb.checked });
    pin.append(cb, "Pinned (the flow keeps this length)");
    box.append(pin);
  } else {
    box.append(el("div", "title", [nSel && `${nSel} nodes`, lSel && `${lSel} links`].filter(Boolean).join(", ")));
  }

  const inside = selectedLinks("inside"), boundary = selectedLinks("boundary");
  const pinnedAll = inside.length && inside.every((l) => l.pinned);
  box.append(el("p", "facts", `${inside.length} links inside, ${boundary.length} leaving the selection`));
  if (nSel > 1) {
    const c = snap.analysis?.communities;
    box.append(live(el("p", "facts"), () => {
      const lens = inside.map((l) => l.length), out = boundary.map((l) => l.length);
      const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
      return `mean length inside ${fmt(mean(lens), 2)}, leaving ${fmt(mean(out), 2)}` + (c ? ` (cut at ${fmt(c.cutoff, 2)})` : "");
    }));
  }
  const actions = el("div", "actions");
  actions.append(
    button(`Pull together [`, () => steer("pull"), null, `Halve the length of the ${inside.length} inside links and pin them`),
    button(`Push apart ]`, () => steer("push"), null, `Double the length of the ${boundary.length || inside.length} links leaving the selection and pin them`),
    button(pinnedAll ? "Unpin links P" : "Pin links P", () => steer("pin"), null, "The flow leaves pinned lengths alone"),
    button("Delete", deleteSelection, "danger"),
  );
  box.append(actions);
}

function numberField(label, get, onSet, step, onDelta, onScale) {
  const row = el("div", "field");
  const lab = el("label", null, label);
  const stepper = el("div", "stepper");
  const input = el("input");
  Object.assign(input, { type: "number", step });
  live(input, () => (get() == null ? "" : +Number(get()).toPrecision(4)));
  if (onSet) input.addEventListener("change", () => onSet(input.value)); else input.readOnly = true;
  const minus = onScale ? () => onScale(0.8) : onDelta ? () => onDelta(-1) : () => onSet(Number(get()) - step);
  const plus = onScale ? () => onScale(1.25) : onDelta ? () => onDelta(1) : () => onSet(Number(get()) + step);
  stepper.append(button("−", minus, "small"), input, button("+", plus, "small"));
  row.append(lab, stepper);
  return row;
}

// ------------------------------------------------------------------ state from backend

function onMessage(msg) {
  if (msg.type === "error") return toast(msg.message, 6000);
  if (msg.type === "added") { pendingSelect = { nodes: [msg.node.id] }; return; }
  if (msg.type === "loaded") { fitNext = true; clearSelectionSoon(); return toast(msg.message); }
  if (msg.type === "progress") return showBusy(msg.message);
  if (msg.type === "ready") return showBusy(null);
  if (msg.type === "catalog") { catalog = msg.catalog; return renderCatalog(); }
  if (msg.type === "state") applyState(msg);
}

let clearPending = false;
function clearSelectionSoon() { clearPending = true; }

function applyState(s) {
  snap = s;
  const g = s.graph;
  if (s.structure !== structure) {
    structure = s.structure;
    const old = new Map(nodes.map((n) => [n.id, n]));
    const spread = Math.sqrt(g.nodes.length) * 12;
    nodes = g.nodes.map((n) => {
      const o = old.get(n.id);
      if (o) return Object.assign(o, n, { x: o.x, y: o.y });
      const fresh = { ...n };
      if (fresh.x == null) { fresh.x = (Math.random() - 0.5) * spread; fresh.y = (Math.random() - 0.5) * spread; }
      return fresh;
    });
    nodeById = new Map(nodes.map((n) => [n.id, n]));
    links = g.links.map((l) => ({ ...l }));
    linkByKey = new Map(links.map((l) => [keyOf(l.source, l.target), l]));
    degree = new Map();
    for (const l of links) {
      degree.set(l.source, (degree.get(l.source) || 0) + 1);
      degree.set(l.target, (degree.get(l.target) || 0) + 1);
    }
    sim.nodes(nodes);
    linkForce.links(links);
    updateScales();
    configureForces();
    if (fitNext) {
      const budget = performance.now() + (nodes.length > 1500 ? 1200 : 600);
      sim.alpha(1);
      for (let i = 0; i < 300 && performance.now() < budget; i++) sim.tick();
      sim.alpha(0.3).restart();
    } else {
      sim.alpha(0.3).restart();
    }
  } else {
    // Same nodes and links: just refresh their values (the flow's fast path).
    for (const n of g.nodes) {
      const o = nodeById.get(n.id);
      if (o) { o.nval = n.nval; o.total = n.total; o.current = n.current; o.community = n.community; o.truth = n.truth; o.label = n.label; }
    }
    for (const l of g.links) {
      const o = linkByKey.get(keyOf(l.source, l.target));
      if (!o) continue;
      const lengthChanged = o.length !== l.length;
      Object.assign(o, { length: l.length, curvature: l.curvature, pinned: l.pinned, capacity: l.capacity,
        lval: l.lval, total: l.total, current: l.current });
      if (lengthChanged) o._moved = true;
    }
    updateScales();
    if (view.layout) {
      configureForces();
      sim.alpha(Math.max(sim.alpha(), 0.25)).restart();
    }
  }

  if (clearPending) { sel.nodes.clear(); sel.links.clear(); clearPending = false; }
  if (pendingSelect) {
    sel.nodes = new Set(pendingSelect.nodes || []);
    sel.links = new Set(pendingSelect.links || []);
    pendingSelect = null;
  }
  for (const id of [...sel.nodes]) if (!nodeById.has(id)) sel.nodes.delete(id);
  for (const k of [...sel.links]) if (!linkByKey.has(k)) sel.links.delete(k);

  const running = !!s.analysis?.running;
  if (running && !wasRunning) userMovedView = false;
  if (!running && wasRunning && !userMovedView) setTimeout(() => fitView(), 600);  // after the layout settles
  wasRunning = running;
  if (fitNext && fitView(false)) fitNext = false;
  redraw();
  renderPanels();
}

function updateScales() {
  const lengths = links.map((l) => l.length).filter((x) => x != null).sort((a, b) => a - b);
  medianLength = lengths.length ? lengths[Math.floor(lengths.length / 2)] : 1;
  const method = snap?.analysis?.settings?.method;
  const abs = links.map((l) => Math.abs(l.curvature ?? 0)).sort((a, b) => a - b);
  curvScale = method === "forman" ? Math.max(1e-9, abs[Math.floor(abs.length * 0.95)] || 1) : 1;
  commSizes = snap?.analysis?.communities?.sizes || [];
}

// ------------------------------------------------------------------ panels

function renderPanels() {
  const a = snap.analysis || {};
  const g = snap.graph;
  const meta = g.meta || {};
  const running = !!a.running;

  $("undo").disabled = !snap.can_undo;
  $("redo").disabled = !snap.can_redo;

  // Graph info
  const truthN = meta.truth_names?.length;
  $("graph-info").innerHTML = `<b>${escape(meta.name || "Untitled graph")}</b> · ${nodes.length.toLocaleString()} nodes · ` +
    `${links.length.toLocaleString()} links` + (truthN ? ` · ground truth: ${truthN} ${escape(meta.truth_label || "classes")}` : "");
  $("save-server").hidden = isDemo;

  // Flow settings
  const s = a.settings || {};
  const limits = a.limits || {};
  const methods = limits.methods || ["ollivier", "forman"];
  const tooBig = nodes.length > (a.ollivier_max_nodes || 6000);
  for (const b of document.querySelectorAll("[data-method]")) {
    b.classList.toggle("on", b.dataset.method === s.method);
    b.disabled = running || !methods.includes(b.dataset.method) || (b.dataset.method === "ollivier" && tooBig);
  }
  $("method-note").textContent = s.method === "forman"
    ? "Forman-Ricci: a fast local formula (degrees and triangles). Rough, but scales to large graphs."
    : tooBig ? "" : "Ollivier-Ricci: optimal transport between neighbourhoods. Accurate, slower on big graphs." +
      (isDemo ? " In the browser it uses a slower solver." : "");
  setValue("alpha", s.alpha);
  setValue("step", s.step);
  setValue("iterations", s.iterations);
  $("alpha").disabled = s.method !== "ollivier";
  $("run").textContent = running ? "Stop" : "Run";
  $("run").classList.toggle("danger", running);
  $("once").disabled = running;
  $("reset").disabled = running;
  for (const id of ["load-dataset", "import"]) $(id).disabled = false;

  const last = a.history?.[a.history.length - 1];
  const parts = [`<span class="k">Iteration</span> <b>${a.iteration ?? 0}</b>`];
  if (a.last_seconds != null) parts.push(`${fmt(a.last_seconds, 2)} s each`);
  if (last?.curvature_min != null) parts.push(`curvature ${fmt(last.curvature_min, 2)} … ${fmt(last.curvature_max, 2)}`);
  if (a.pinned) parts.push(`${a.pinned} pinned`);
  if (running) parts.push("<b>running</b>");
  $("flow-status").innerHTML = parts.join(" · ");
  drawChart(a.history || []);

  // Communities
  const c = a.communities;
  if (c) {
    const truth = c.nmi != null ? ` · NMI <b>${fmt(c.nmi)}</b> · ARI <b>${fmt(c.ari)}</b>` : "";
    $("comm-summary").innerHTML = `<b>${c.count}</b> communities · modularity <b>${fmt(c.modularity)}</b>${truth}`;
    const [lo, hi] = a.length_range || [0, 1];
    const slider = $("cutoff");
    slider.min = lo; slider.max = hi; slider.step = (hi - lo) / 500 || 0.001;
    if (document.activeElement !== slider) slider.value = c.cutoff;
    $("cutoff-value").textContent = `cut > ${fmt(c.cutoff, 2)}`;
    $("cut-auto").checked = s.cutoff == null;
    slider.disabled = s.cutoff == null;
  } else {
    $("comm-summary").textContent = "";
  }
  const lv = a.louvain;
  $("louvain-result").innerHTML = lv
    ? `Louvain: ${lv.count} communities · modularity ${fmt(lv.modularity)}` + (lv.nmi != null ? ` · NMI ${fmt(lv.nmi)} · ARI ${fmt(lv.ari)}` : "")
    : "";
  for (const b of document.querySelectorAll("[data-color]")) {
    b.classList.toggle("on", b.dataset.color === colorBy);
    if (b.dataset.color === "truth") b.disabled = !truthN;
  }
  if (colorBy === "truth" && !truthN) colorBy = "community";
  renderCommunityList();
  renderLegend();

  $("model-section").hidden = !snap.has_model;
  $("auto-step").checked = snap.auto_step;

  renderInspector();
}

function renderCommunityList() {
  const list = $("comm-list");
  list.replaceChildren();
  const groups = new Map();
  const field = colorBy === "truth" ? "truth" : colorBy === "group" ? "nval" : "community";
  for (const d of nodes) {
    const v = d[field];
    if (v == null) continue;
    groups.set(v, (groups.get(v) || 0) + 1);
  }
  const names = field === "truth" ? snap.graph.meta?.truth_names || [] : [];
  const sorted = [...groups].sort((a, b) => b[1] - a[1]).slice(0, 40);
  for (const [v, size] of sorted) {
    const chip = el("span", "chip");
    const sw = el("span", "swatch");
    sw.style.background = field === "community" && size === 1 ? "#b8bcc4" : colorAt(v);
    chip.append(sw, `${names[v] ?? v} · ${size}`);
    chip.title = "Click to select these nodes (Shift+click to add)";
    chip.onclick = (e) => {
      if (!e.shiftKey) { sel.nodes.clear(); sel.links.clear(); }
      for (const d of nodes) if (d[field] === v) sel.nodes.add(d.id);
      selectionChanged();
    };
    list.append(chip);
  }
  if (groups.size > sorted.length) list.append(el("span", "muted small", `+${groups.size - sorted.length} more`));
}

function renderLegend() {
  const lg = $("legend");
  const hasCurv = view.curv && links.some((l) => l.curvature != null);
  lg.hidden = !hasCurv;
  if (!hasCurv) return;
  const grad = `linear-gradient(to right, ${curvColors.join(",")})`;
  lg.innerHTML = `Link curvature<div class="bar-grad" style="background:${grad}"></div>` +
    `<div class="ends"><span>−${fmt(curvScale, curvScale >= 10 ? 0 : 1)}</span><span>0</span><span>+${fmt(curvScale, curvScale >= 10 ? 0 : 1)}</span></div>`;
}

function drawChart(history) {
  const c = $("chart");
  const show = history.length > 1;
  c.hidden = !show;
  $("chart-legend").hidden = !show;
  if (!show) return;
  const w = c.clientWidth, h = c.clientHeight, r = window.devicePixelRatio || 1;
  c.width = w * r; c.height = h * r;
  const g = c.getContext("2d");
  g.setTransform(r, 0, 0, r, 0, 0);
  g.clearRect(0, 0, w, h);
  const pad = { l: 26, r: 6, t: 6, b: 16 };
  const xs = d3.scaleLinear().domain(d3.extent(history, (d) => d.iteration)).range([pad.l, w - pad.r]);
  const ys = d3.scaleLinear().domain([0, 1]).range([h - pad.b, pad.t]);
  g.strokeStyle = css("--border"); g.lineWidth = 1;
  g.fillStyle = css("--muted"); g.font = "10px system-ui"; g.textAlign = "right"; g.textBaseline = "middle";
  for (const v of [0, 0.5, 1]) {
    g.beginPath(); g.moveTo(pad.l, ys(v)); g.lineTo(w - pad.r, ys(v)); g.stroke();
    g.fillText(v.toFixed(1), pad.l - 4, ys(v));
  }
  g.textAlign = "center"; g.textBaseline = "top";
  const [i0, i1] = xs.domain();
  g.fillText(i0, xs(i0), h - pad.b + 3); g.fillText(i1, xs(i1), h - pad.b + 3);
  const series = [["modularity", "#59a14f", "modularity"], ["nmi", "#4e79a7", "NMI"], ["ari", "#e15759", "ARI"]];
  const shown = [];
  for (const [key, color, label] of series) {
    const pts = history.filter((d) => d[key] != null);
    if (pts.length < 2) continue;
    shown.push(`<span style="color:${color}">━</span> ${label}`);
    g.strokeStyle = color; g.lineWidth = 1.8;
    g.beginPath();
    pts.forEach((d, i) => (i ? g.lineTo : g.moveTo).call(g, xs(d.iteration), ys(d[key])));
    g.stroke();
  }
  $("chart-legend").innerHTML = shown.join(" &nbsp; ") + " &nbsp; by iteration";
}

function renderCatalog() {
  const s = $("dataset");
  const current = s.value;
  s.replaceChildren();
  for (const d of catalog) {
    const o = el("option", null, `${d.title} (${d.size})`);
    o.value = d.id;
    s.append(o);
  }
  if (current && catalog.some((d) => d.id === current)) s.value = current;
  renderParams();
}

function renderParams() {
  const d = catalog.find((x) => x.id === $("dataset").value);
  $("dataset-about").textContent = d?.about || "";
  const box = $("dataset-params");
  box.replaceChildren();
  for (const p of d?.params || []) {
    const lab = el("label", null, p.label);
    const input = el("input");
    Object.assign(input, { type: "number", name: p.name, value: p.default, min: p.min, max: p.max, step: p.step });
    lab.append(input);
    box.append(lab);
  }
}

function setValue(id, v) {
  const e = $(id);
  if (document.activeElement !== e && v != null) e.value = v;
}

// ------------------------------------------------------------------ controls

const flowSettings = () => ({
  method: document.querySelector("[data-method].on")?.dataset.method,
  alpha: $("alpha").value, step: $("step").value, iterations: $("iterations").value,
});

function toggleRun() {
  if (snap?.analysis?.running) send({ op: "flow_stop" });
  else send({ op: "flow_start", settings: flowSettings() });
}
function flowOnce() {
  if (!snap?.analysis?.running) send({ op: "flow_once", settings: flowSettings() });
}

$("run").onclick = toggleRun;
$("once").onclick = flowOnce;
$("reset").onclick = () => send({ op: "reset_lengths", mode: $("reset-mode").value });
for (const b of document.querySelectorAll("[data-method]")) {
  b.onclick = () => send({ op: "flow_settings", settings: { method: b.dataset.method } });
}
for (const id of ["alpha", "step", "iterations"]) {
  $(id).addEventListener("change", () => send({ op: "flow_settings", settings: { [id]: $(id).value } }));
}
$("cut-auto").onchange = (e) => send({ op: "communities", cutoff: e.target.checked ? null : $("cutoff").value });
$("cutoff").oninput = (e) => { $("cutoff-value").textContent = `cut > ${fmt(e.target.value, 2)}`; };
$("cutoff").onchange = (e) => send({ op: "communities", cutoff: e.target.value });
$("louvain").onclick = () => send({ op: "louvain" });
for (const b of document.querySelectorAll("[data-color]")) {
  b.onclick = () => { colorBy = b.dataset.color; renderPanels(); redraw(); };
}

$("dataset").onchange = renderParams;
$("load-dataset").onclick = () => {
  const params = {};
  for (const input of $("dataset-params").querySelectorAll("input")) params[input.name] = Number(input.value);
  showBusy("Loading…");
  send({ op: "load_dataset", name: $("dataset").value, params });
};

$("import").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  showBusy(`Reading ${file.name}…`);
  try {
    const msg = await backend.importFile(file);
    if (msg) toast(msg);
  } catch (err) {
    toast(err.message, 8000);
  } finally {
    showBusy(null);
  }
};

$("download-results").onclick = () => {
  const name = (snap.graph.meta?.name || "graph").replace(/[^\w-]+/g, "-");
  download(`${name}-results.json`, JSON.stringify({ graph: snap.graph, analysis: snap.analysis }, null, 1));
  const names = snap.graph.meta?.truth_names || [];
  const q = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v);
  const nodesCsv = ["id,label,community,truth,truth_name",
    ...snap.graph.nodes.map((n) => [n.id, n.label, n.community, n.truth, names[n.truth]].map(q).join(","))].join("\n");
  const edgesCsv = ["source,target,capacity,length,curvature,pinned",
    ...snap.graph.links.map((l) => [l.source, l.target, l.capacity, l.length, l.curvature, l.pinned ? 1 : 0].map(q).join(","))].join("\n");
  download(`${name}-nodes.csv`, nodesCsv + "\n");
  download(`${name}-edges.csv`, edgesCsv + "\n");
};

$("save-server").onclick = async () => {
  const r = await fetch("api/save", { method: "POST" });
  const body = await r.json();
  toast(body.message || body.error);
};

$("undo").onclick = () => send({ op: "undo" });
$("redo").onclick = () => send({ op: "redo" });
$("fit").onclick = () => fitView();
$("model-step").onclick = () => send({ op: "step" });
$("auto-step").onchange = (e) => send({ op: "set_auto_step", value: e.target.checked });
$("v-curv").onchange = (e) => { view.curv = e.target.checked; renderLegend(); redraw(); };
$("v-dots").onchange = (e) => { view.dots = e.target.checked; redraw(); };
$("v-labels").onchange = (e) => { view.labels = e.target.checked; redraw(); };
$("v-layout").onchange = (e) => { view.layout = e.target.checked; configureForces(); sim.alpha(0.5).restart(); };

function download(name, text) {
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type: name.endsWith(".csv") ? "text/csv" : "application/json" }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------------ small helpers

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function button(text, onClick, cls, title) {
  const b = el("button", cls, text);
  b.addEventListener("click", onClick);
  if (title) b.title = title;
  return b;
}

function escape(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

let toastTimer;
function toast(message, ms = 3500) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
  showBusy(null);
}

function showBusy(message) {
  $("busy").hidden = !message;
  if (message) $("busy").textContent = message;
}

function setStatus(kind, text, title) {
  const s = $("status");
  s.className = `status ${kind}`;
  s.textContent = text;
  s.title = title || text;
}

function send(op) {
  if (backend) backend.send(op);
}

// ------------------------------------------------------------------ backends

class SocketBackend {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.retry = 500;
  }

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
      ws.onmessage = (e) => onMessage(JSON.parse(e.data));
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

  async importFile(file) {
    const r = await fetch(`api/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    return body.message;
  }
}

class WorkerBackend {
  constructor() {
    this.worker = new Worker(new URL("py-worker.js", import.meta.url), { type: "module" });
    this.waiting = new Map();
    this.next = 1;
    this.worker.onmessage = (e) => {
      const msg = e.data;
      if (msg.reply != null && this.waiting.has(msg.reply)) {
        const { resolve, reject } = this.waiting.get(msg.reply);
        this.waiting.delete(msg.reply);
        msg.error ? reject(new Error(msg.error)) : resolve(msg.message);
        return;
      }
      onMessage(msg);
    };
    this.worker.onerror = (e) => { showBusy(null); toast(`The in-browser Python failed to start: ${e.message}`, 10000); };
    const base = new URL(".", location.href).href;
    this.worker.postMessage({ type: "init", base });
  }

  send(op) {
    this.worker.postMessage({ type: "op", op });
  }

  importFile(file) {
    return file.arrayBuffer().then((data) => new Promise((resolve, reject) => {
      const id = this.next++;
      this.waiting.set(id, { resolve, reject });
      this.worker.postMessage({ type: "import", id, name: file.name, data }, [data]);
    }));
  }
}

async function start() {
  const params = new URLSearchParams(location.search);
  if (!params.has("demo") && location.protocol.startsWith("http")) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const sock = new SocketBackend(`${proto}//${location.host}${location.pathname.replace(/[^/]*$/, "")}ws`);
    if (await sock.connect()) {
      backend = sock;
      try {
        catalog = await (await fetch("api/datasets")).json();
        renderCatalog();
      } catch { /* the panel stays empty */ }
      return;
    }
  }
  isDemo = true;
  $("demo-note").hidden = false;
  setStatus("demo", "Web demo · Python in your browser",
    "No server found, so the Python code runs in this page (Pyodide). Results stay in this browser unless you download them.");
  showBusy("Starting Python in your browser…");
  backend = new WorkerBackend();
}

start();
