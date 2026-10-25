// 3D view: the same graph drawn in WebGL with 3d-force-graph (three.js),
// for looking at community structure from any angle. It shares colours,
// selection and steering with the 2D view; editing gestures (Add mode, box
// select, drag-to-link) stay in 2D. The library loads on first use.

const LIB = "https://cdn.jsdelivr.net/npm/3d-force-graph@1.80.0/dist/3d-force-graph.min.js";

let loading = null;
function loadLibrary() {
  if (window.ForceGraph3D) return Promise.resolve();
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = LIB;
    s.onload = resolve;
    s.onerror = () => { loading = null; reject(new Error("could not load the 3D library (check the internet connection)")); };
    document.head.append(s);
  });
  return loading;
}

// `api` supplies the styling and the click handlers from app.js:
//   label(d), nodeColor(d), nodeSize(d), linkColor(l), linkWidth(l),
//   linkDistance(l), linkStrength(l), charge(), background(),
//   clickNode(id, shift), clickLink(s, t, shift), clickBackground(shift)
export async function createView3D(container, api) {
  await loadLibrary();
  const byId = new Map();
  let linkByKey = new Map();
  const key = (s, t) => (s < t ? `${s}-${t}` : `${t}-${s}`);
  const id = (end) => (typeof end === "object" ? end.id : end);

  const graph = window.ForceGraph3D()(container)
    .backgroundColor(api.background())
    .showNavInfo(false)
    .nodeId("id")
    .nodeLabel((d) => api.label(d))
    .nodeColor((d) => api.nodeColor(d))
    .nodeVal((d) => api.nodeSize(d))
    .nodeOpacity(0.95)
    .nodeResolution(12)
    .linkColor((l) => api.linkColor(l))
    .linkWidth((l) => api.linkWidth(l))
    .linkOpacity(0.6)
    .onNodeClick((d, e) => api.clickNode(d.id, e.shiftKey))
    .onLinkClick((l, e) => api.clickLink(id(l.source), id(l.target), e.shiftKey))
    .onBackgroundClick((e) => api.clickBackground(e.shiftKey))
    .warmupTicks(40)
    .cooldownTime(8000);

  const resize = () => graph.width(container.clientWidth).height(container.clientHeight);
  const observer = new ResizeObserver(resize);
  observer.observe(container);

  function forces() {
    graph.d3Force("link").distance((l) => api.linkDistance(l)).strength((l) => api.linkStrength(l));
    graph.d3Force("charge").strength(api.charge());
  }

  // Data fields copied from the app's node objects (not their 2D x / y).
  const NODE_FIELDS = ["id", "label", "community", "truth", "nval", "total", "current"];
  const LINK_FIELDS = ["length", "curvature", "pinned", "capacity", "total", "lval"];

  function setData(nodes, links, structural) {
    if (structural) {
      const next = nodes.map((n) => {
        const o = byId.get(n.id) || {};
        for (const f of NODE_FIELDS) o[f] = n[f];
        byId.set(n.id, o);
        return o;
      });
      const keep = new Set(nodes.map((n) => n.id));
      for (const k of [...byId.keys()]) if (!keep.has(k)) byId.delete(k);
      const nextLinks = links.map((l) => {
        const o = { source: id(l.source), target: id(l.target) };
        for (const f of LINK_FIELDS) o[f] = l[f];
        return o;
      });
      linkByKey = new Map(nextLinks.map((l) => [key(l.source, l.target), l]));
      graph.graphData({ nodes: next, links: nextLinks });
      forces();
    } else {
      for (const n of nodes) {
        const o = byId.get(n.id);
        if (o) for (const f of NODE_FIELDS) o[f] = n[f];
      }
      let moved = false;
      for (const l of links) {
        const o = linkByKey.get(key(id(l.source), id(l.target)));
        if (!o) continue;
        if (o.length !== l.length) moved = true;
        for (const f of LINK_FIELDS) o[f] = l[f];
      }
      if (moved) { forces(); graph.d3ReheatSimulation(); }
      restyle();
    }
  }

  // Re-evaluate colours and sizes (selection, colour mode, flow results).
  function restyle() {
    graph.nodeColor(graph.nodeColor()).nodeVal(graph.nodeVal())
      .linkColor(graph.linkColor()).linkWidth(graph.linkWidth())
      .backgroundColor(api.background());
  }

  return {
    setData,
    restyle,
    relayout() { forces(); graph.d3ReheatSimulation(); },
    fit() { graph.zoomToFit(600, 30); },
    pause() { graph.pauseAnimation(); },
    resume() { resize(); graph.resumeAnimation(); },
  };
}
