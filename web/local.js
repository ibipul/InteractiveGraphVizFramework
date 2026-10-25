// In-browser backend for the static demo (e.g. GitHub Pages), used when no
// server answers on /ws. It mirrors interactive_graph/graph.py and the
// example step in interactive_graph/model.py; keep them in step.

const HISTORY_LIMIT = 200;
const STORAGE_KEY = "interactive-graph-demo";

const clone = (g) => JSON.parse(JSON.stringify(g));
const int = (v, fallback) => {
  const n = Number.parseInt(v, 10);
  return Number.isNaN(n) ? fallback : n;
};
const endpoint = (v) => int(v && typeof v === "object" ? v.id : v);

export function normalize(data) {
  if (!data || !Array.isArray(data.nodes)) throw new Error("a graph needs a 'nodes' list");
  const nodes = new Map();
  for (const raw of data.nodes) {
    const node = {
      id: int(raw.id),
      nval: int(raw.nval ?? raw.group, 1),
      current: int(raw.current, 0),
      total: int(raw.total, 1),
    };
    for (const k of ["x", "y"]) if (typeof raw[k] === "number") node[k] = Math.round(raw[k] * 10) / 10;
    nodes.set(node.id, node);
  }
  const links = new Map();
  for (const raw of data.links || []) {
    let s = endpoint(raw.source), t = endpoint(raw.target);
    if (s === t || !nodes.has(s) || !nodes.has(t)) continue;
    [s, t] = [Math.min(s, t), Math.max(s, t)];
    const key = `${s}-${t}`;
    if (!links.has(key)) {
      links.set(key, {
        source: s, target: t,
        lval: int(raw.lval ?? raw.weight, 1),
        current: int(raw.current, 0),
        total: int(raw.total, 1),
      });
    }
  }
  const byPair = (a, b) => a.source - b.source || a.target - b.target;
  return {
    nodes: [...nodes.values()].sort((a, b) => a.id - b.id),
    links: [...links.values()].sort(byPair),
  };
}

export function exampleStep(graph) {
  graph.nodes.forEach((node, i) => {
    node.nval = (node.nval % 2) + (i % 2 === 0 ? 2 : 1);
    node.current = 0;
  });
  return graph;
}

export class LocalBackend {
  constructor(initialGraph) {
    this.listeners = [];
    this.graph = normalize(initialGraph);
    this.autoStep = false;
    this.version = 0;
    this.undo = [];
    this.redo = [];
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved) this.graph = normalize(saved);
    } catch { /* no storage: start from the example */ }
  }

  onMessage(fn) {
    this.listeners.push(fn);
    queueMicrotask(() => fn({ type: "state", ...this.snapshot() }));
  }

  emit(msg) {
    for (const fn of this.listeners) fn(msg);
  }

  snapshot() {
    return {
      graph: clone(this.graph),
      version: this.version,
      auto_step: this.autoStep,
      can_undo: this.undo.length > 0,
      can_redo: this.redo.length > 0,
      has_model: true,
    };
  }

  send(op) {
    try {
      const added = this.apply(op);
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(this.graph)); } catch { /* ignore */ }
      if (added) this.emit({ type: "added", node: added });
      this.emit({ type: "state", ...this.snapshot() });
    } catch (e) {
      this.emit({ type: "error", message: e.message });
    }
  }

  // ---------------------------------------------------------------- ops

  apply(op) {
    const name = op.op;
    if (name === "undo" || name === "redo") {
      const [from, to] = name === "undo" ? [this.undo, this.redo] : [this.redo, this.undo];
      if (from.length) { to.push(this.graph); this.graph = from.pop(); }
      this.version++;
      return null;
    }
    if (name === "set_auto_step") {
      this.autoStep = Boolean(op.value);
      this.version++;
      return null;
    }
    const handler = this[`op_${name}`];
    if (!handler) throw new Error(`unknown op '${name}'`);

    const before = clone(this.graph);
    let result;
    try {
      result = handler.call(this, op);
      if (this.autoStep && name !== "step" && name !== "load") this.op_step();
    } catch (e) {
      this.graph = before;
      throw e;
    }
    if (JSON.stringify(before) !== JSON.stringify(this.graph)) {
      this.undo.push(before);
      if (this.undo.length > HISTORY_LIMIT) this.undo.shift();
      this.redo = [];
    }
    this.version++;
    return result;
  }

  node(id) {
    const n = this.graph.nodes.find((n) => n.id === int(id));
    if (!n) throw new Error(`no node ${id}`);
    return n;
  }

  link(source, target) {
    const [s, t] = [int(source), int(target)].sort((a, b) => a - b);
    const l = this.graph.links.find((l) => l.source === s && l.target === t);
    if (!l) throw new Error(`no link ${s}-${t}`);
    return l;
  }

  item(op) {
    return op.kind === "link" ? this.link(op.source, op.target) : this.node(op.id);
  }

  op_add_node(op) {
    const id = Math.max(-1, ...this.graph.nodes.map((n) => n.id)) + 1;
    const node = { id, nval: int(op.nval, 1), current: 0, total: 1 };
    for (const k of ["x", "y"]) if (typeof op[k] === "number") node[k] = Math.round(op[k] * 10) / 10;
    this.graph.nodes.push(node);
    return node;
  }

  op_add_link(op) {
    const [s, t] = [int(op.source), int(op.target)].sort((a, b) => a - b);
    if (s === t) throw new Error("a node cannot link to itself");
    this.node(s); this.node(t);
    if (this.graph.links.some((l) => l.source === s && l.target === t)) return null;
    this.graph.links.push({ source: s, target: t, lval: int(op.lval, 1), current: 0, total: 1 });
    this.graph.links.sort((a, b) => a.source - b.source || a.target - b.target);
    return null;
  }

  op_delete(op) {
    if (op.kind === "link") {
      const l = this.link(op.source, op.target);
      this.graph.links = this.graph.links.filter((x) => x !== l);
      return null;
    }
    const n = this.node(op.id);
    this.graph.nodes = this.graph.nodes.filter((x) => x !== n);
    this.graph.links = this.graph.links.filter((l) => l.source !== n.id && l.target !== n.id);
    return null;
  }

  op_adjust(op) {
    const item = this.item(op);
    const delta = int(op.delta, 1);
    item.current += delta;
    item.total += delta;
    return null;
  }

  op_set(op) {
    const item = this.item(op);
    const allowed = "id" in item ? ["nval", "current", "total"] : ["lval", "current", "total"];
    if (!allowed.includes(op.field)) throw new Error(`cannot set '${op.field}'`);
    const value = int(op.value);
    if (Number.isNaN(value)) throw new Error("value must be a whole number");
    item[op.field] = value;
    return null;
  }

  op_step() {
    this.graph = normalize(exampleStep(clone(this.graph)));
    return null;
  }

  op_load(op) {
    this.graph = normalize(op.graph);
    return null;
  }
}
