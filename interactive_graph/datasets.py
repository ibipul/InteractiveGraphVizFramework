"""Graphs to load from the side panel, each with ground-truth communities.

Synthetic graphs come from networkx generators. Real networks are
downloaded on first use and cached in data/datasets/.
"""

from __future__ import annotations

import gzip
import io
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

import networkx as nx

from .graph import Graph

CACHE = Path(__file__).resolve().parent.parent / "data" / "datasets"
USER_AGENT = "Mozilla/5.0 (InteractiveGraphViz dataset loader)"  # Newman's site refuses Python's default


def to_graph(g: nx.Graph, truth: dict[Any, Any] | None, meta: dict[str, Any],
             capacity: str | None = None) -> Graph:
    """Turn a networkx graph into this app's graph dict.

    Node ids become 0..n-1; the original names go in `label`. Ground-truth
    values (any hashable) become integers 0..k-1, with the names kept in
    meta["truth_names"].
    """
    g = nx.Graph(g)
    g.remove_edges_from(nx.selfloop_edges(g))
    index = {node: i for i, node in enumerate(g.nodes())}
    names: list[str] = []
    codes: dict[Any, int] = {}
    if truth:
        for value in sorted({truth[n] for n in g if n in truth}, key=str):
            codes[value] = len(names)
            names.append(str(value))

    nodes = []
    for node, i in index.items():
        item = {"id": i, "label": str(g.nodes[node].get("label", node))}
        if truth and node in truth:
            item["truth"] = codes[truth[node]]
        nodes.append(item)
    links = []
    for u, v, data in g.edges(data=True):
        link = {"source": index[u], "target": index[v]}
        if capacity and isinstance(data.get(capacity), (int, float)):
            link["capacity"] = float(data[capacity])
        links.append(link)

    meta = dict(meta, nodes=len(nodes), links=len(links))
    if names:
        meta["truth_names"] = names
    return {"nodes": nodes, "links": links, "meta": meta}


def _fetch(url: str, name: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        path.write_bytes(data)
    return path.read_bytes()


def _newman_gml(zip_name: str) -> nx.Graph:
    data = _fetch(f"https://public.websites.umich.edu/~mejn/netdata/{zip_name}", zip_name)
    zf = zipfile.ZipFile(io.BytesIO(data))
    text = zf.read(zip_name.replace(".zip", ".gml")).decode()
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip().startswith("graph"))
    return nx.parse_gml("\n".join(lines[start:]), label="id")


# ---------------------------------------------------------------- loaders

def karate(params: dict[str, Any]) -> Graph:
    g = nx.karate_club_graph()
    truth = {n: d["club"] for n, d in g.nodes(data=True)}
    for n in g:
        g.nodes[n]["label"] = str(n)
    return to_graph(g, truth, {"name": "Karate club", "truth_label": "club after the split"})


def football(params: dict[str, Any]) -> Graph:
    g = _newman_gml("football.zip")
    truth = {n: d["value"] for n, d in g.nodes(data=True)}
    return to_graph(g, truth, {"name": "College football", "truth_label": "conference"})


def polbooks(params: dict[str, Any]) -> Graph:
    g = _newman_gml("polbooks.zip")
    leaning = {"l": "liberal", "n": "neutral", "c": "conservative"}
    truth = {n: leaning.get(d["value"], d["value"]) for n, d in g.nodes(data=True)}
    return to_graph(g, truth, {"name": "Political books", "truth_label": "political leaning"})


def email_eu(params: dict[str, Any]) -> Graph:
    base = "https://snap.stanford.edu/data/"
    edges = gzip.decompress(_fetch(base + "email-Eu-core.txt.gz", "email-Eu-core.txt.gz")).decode()
    labels = gzip.decompress(_fetch(base + "email-Eu-core-department-labels.txt.gz",
                                    "email-Eu-core-department-labels.txt.gz")).decode()
    g = nx.Graph()
    truth = {}
    for line in labels.split("\n"):
        if line.strip():
            node, dept = map(int, line.split())
            g.add_node(node)
            truth[node] = dept
    for line in edges.split("\n"):
        if line.strip():
            u, v = map(int, line.split())
            g.add_edge(u, v)
    return to_graph(g, truth, {"name": "email-Eu-core", "truth_label": "department"})


def cora(params: dict[str, Any]) -> Graph:
    base = "https://raw.githubusercontent.com/tkipf/pygcn/master/data/cora/"
    content = _fetch(base + "cora.content", "cora.content").decode()
    cites = _fetch(base + "cora.cites", "cora.cites").decode()
    g = nx.Graph()
    truth = {}
    for line in content.split("\n"):
        parts = line.split()
        if parts:
            g.add_node(parts[0])
            truth[parts[0]] = parts[-1]
    for line in cites.split("\n"):
        parts = line.split()
        if len(parts) == 2 and parts[0] in truth and parts[1] in truth:
            g.add_edge(parts[0], parts[1])
    return to_graph(g, truth, {"name": "Cora", "truth_label": "paper topic"})


def sbm(params: dict[str, Any]) -> Graph:
    k = int(params.get("blocks", 5))
    size = int(params.get("block_size", 40))
    p_in, p_out = float(params.get("p_in", 0.3)), float(params.get("p_out", 0.02))
    seed = int(params.get("seed", 1))
    if not (2 <= k <= 50 and 3 <= size <= 1000 and k * size <= 20000):
        raise ValueError("use 2-50 blocks of 3-1000 nodes (at most 20,000 nodes)")
    probs = [[p_in if i == j else p_out for j in range(k)] for i in range(k)]
    g = nx.stochastic_block_model([size] * k, probs, seed=seed)
    truth = {n: d["block"] for n, d in g.nodes(data=True)}
    return to_graph(g, truth, {
        "name": f"SBM {k}×{size}",
        "truth_label": "block",
        "description": f"Stochastic block model: {k} blocks of {size} nodes, "
                       f"p_in={p_in}, p_out={p_out}, seed={seed}"})


def lfr(params: dict[str, Any]) -> Graph:
    n = int(params.get("n", 500))
    mu = float(params.get("mu", 0.2))
    avg = float(params.get("average_degree", 10))
    min_c = int(params.get("min_community", 20))
    seed = int(params.get("seed", 1))
    if not (50 <= n <= 2000):
        raise ValueError("LFR needs 50-2000 nodes (networkx's generator rarely succeeds above that)")
    error: Exception | None = None
    # networkx's LFR generator often gives up for a given seed; try the next ones.
    for s in range(seed, seed + 25):
        try:
            g = nx.LFR_benchmark_graph(n, 3, 1.5, mu, average_degree=avg, min_community=min_c,
                                       max_community=max(min_c * 5, n // 5), seed=s, max_iters=500)
            break
        except (nx.ExceededMaxIterations, nx.NetworkXError) as e:
            error = e
    else:
        raise ValueError(f"networkx could not build this LFR graph ({error}); "
                         "try a larger minimum community or a different seed")
    truth: dict[int, int] = {}
    for i, comm in enumerate({frozenset(d["community"]) for _, d in g.nodes(data=True)}):
        for node in comm:
            truth[node] = i
    return to_graph(g, truth, {
        "name": f"LFR n={n} μ={mu}",
        "truth_label": "planted community",
        "description": f"LFR benchmark: n={n}, mixing μ={mu}, average degree {avg}, "
                       f"min community {min_c}, seed {s}"})


# ---------------------------------------------------------------- registry

def _num(name: str, label: str, default: float, lo: float, hi: float, step: float = 1) -> dict:
    return {"name": name, "label": label, "default": default, "min": lo, "max": hi, "step": step}


DATASETS: dict[str, dict[str, Any]] = {
    "karate": {"title": "Karate club", "size": "34 nodes, 78 edges", "load": karate,
               "about": "Zachary's karate club; truth is which club each member joined after the split."},
    "polbooks": {"title": "Political books", "size": "105 nodes, 441 edges", "load": polbooks,
                 "about": "Books on US politics bought together on Amazon; truth is the political leaning (Newman)."},
    "football": {"title": "College football", "size": "115 nodes, 613 edges", "load": football,
                 "about": "US college football games in 2000; truth is the conference (Girvan and Newman)."},
    "email_eu": {"title": "email-Eu-core", "size": "1,005 nodes, 16,064 edges", "load": email_eu,
                 "about": "Emails in a European research institution (SNAP); truth is one of 42 departments."},
    "cora": {"title": "Cora citations", "size": "2,708 nodes, 5,278 edges", "load": cora,
             "about": "Citations between machine-learning papers; truth is one of 7 topics, which follows the links only loosely."},
    "sbm": {"title": "Stochastic block model", "size": "synthetic", "load": sbm,
            "about": "Planted blocks: each pair of nodes links with p_in inside a block and p_out across.",
            "params": [_num("blocks", "Blocks", 5, 2, 50), _num("block_size", "Block size", 40, 3, 1000),
                       _num("p_in", "p in", 0.3, 0, 1, 0.01), _num("p_out", "p out", 0.02, 0, 1, 0.005),
                       _num("seed", "Seed", 1, 0, 10**6)]},
    "lfr": {"title": "LFR benchmark", "size": "synthetic", "load": lfr,
            "about": "Lancichinetti-Fortunato-Radicchi graph: power-law degrees and community sizes; "
                     "μ is the share of each node's links that leave its community.",
            "params": [_num("n", "Nodes", 500, 50, 2000), _num("mu", "Mixing μ", 0.2, 0, 0.8, 0.05),
                       _num("average_degree", "Average degree", 10, 2, 50),
                       _num("min_community", "Min community", 20, 5, 500),
                       _num("seed", "Seed", 1, 0, 10**6)]},
}


def catalog() -> list[dict[str, Any]]:
    return [{"id": key, **{k: v for k, v in d.items() if k != "load"}} for key, d in DATASETS.items()]


def load(name: str, params: dict[str, Any] | None = None) -> Graph:
    if name not in DATASETS:
        raise ValueError(f"unknown dataset {name!r}")
    loader: Callable[[dict[str, Any]], Graph] = DATASETS[name]["load"]
    graph = loader(params or {})
    graph["meta"]["dataset"] = name
    graph["meta"].setdefault("about", DATASETS[name]["about"])
    return graph
