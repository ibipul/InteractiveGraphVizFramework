"""Ricci curvature, discrete Ricci flow and Ricci-flow community detection.

Two curvatures are available.

Ollivier-Ricci (Ollivier 2009, in the form used by Ni et al. 2019). Each
node x gets a probability distribution m_x: it keeps `alpha` of the mass
and spreads the rest over its neighbours v in proportion to
exp(-len(x, v)**2). For an edge x-y,

    curvature(x, y) = 1 - W1(m_x, m_y) / d(x, y)

where W1 is the optimal-transport (earth mover's) cost of turning m_x into
m_y, with shortest-path lengths as the cost of moving mass, and d(x, y) is
the shortest-path length from x to y (usually the edge itself). Edges
inside tightly knit groups come out positive, bridges between groups
negative. It needs all shortest-path lengths, so it is limited to graphs
of up to OLLIVIER_MAX_NODES nodes. Values match the GraphRicciCurvature
package (OllivierRicci, method "OTD").

Forman-Ricci, augmented with triangles (Sreejith et al. 2016; Samal et al.
2018), as in GraphRicciCurvature's FormanRicci(method="augmented"). With
node weights 1, for an edge e = x-y of length w_e with t triangles,

    F(e) = w_e * (t * w_e + 2 / w_e
                  - sum over other edges x-v not in a triangle with e: 1 / sqrt(w_e * w_xv)
                  - the same for y)

On an unweighted graph this is 4 - deg(x) - deg(y) + 3t. It is a local
formula, so it scales to large graphs, but it is a rougher signal.

Ricci flow (Ni et al. 2019, "Community detection on networks with Ricci
flow", Sci. Rep. 9:9984). Each iteration recomputes the curvature and sets

    len(x, y) <- len(x, y) - step * curvature(x, y) * len(x, y)

then rescales all lengths so they sum to the number of edges. Bridges grow
long and in-group edges shrink, so cutting the long edges ("surgery")
splits the network into communities.

Forman in the flow. Forman's edge weights mean connection strength, while
the flow evolves lengths (distances), so the flow computes Forman with
weight = 1 / length. Feeding it lengths instead is a feedback loop: a link
the flow stretches looks even more negatively curved, stretches further,
and after ~100 iterations every length collapses to one community. Forman
curvature is also unbounded and grows with the endpoints' degrees, so for
the flow it is divided by deg(x) + deg(y), then by its largest absolute
value, putting it in [-1, 1] like Ollivier's, and lengths are kept within
FORMAN_LENGTHS times the mean. These choices are made here, not taken from
the papers above; with them, use a smaller step (0.3). Forman remains a
much rougher signal than Ollivier: it finds no communities in noisier block
models or LFR graphs where the Ollivier flow does.

Edge lengths are distances. A capacity means the opposite (a stronger
connection), so by default the starting length is 1 / capacity.
"""

from __future__ import annotations

import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

try:  # POT's network simplex is much faster; the browser build (Pyodide) lacks it
    import ot
except ImportError:
    ot = None

THREADS = sys.platform != "emscripten"  # no threads in the browser

MIN_LENGTH = 1e-9
OLLIVIER_MAX_NODES = 6000
INIT_MODES = ("inverse", "capacity", "uniform", "keep")
METHODS = ("ollivier", "forman")


class TooLarge(ValueError):
    pass


def emd2(a: np.ndarray, b: np.ndarray, cost: np.ndarray) -> float:
    """Optimal-transport cost of moving distribution a onto b."""
    if ot is not None:
        return float(ot.emd2(a, b, cost))
    # Transport plan P (len(a) x len(b)): rows sum to a, columns to b. The two
    # sets of sums must agree exactly, and one column constraint is implied
    # by the others; with it, rounding error can make the problem infeasible.
    # Masses on stretched links can be ~1e-8, below HiGHS's feasibility
    # tolerance, so solve with both scaled up by SCALE and scale the cost back.
    SCALE = 1e6
    na, nb = cost.shape
    a = a * SCALE
    b = b * (a.sum() / b.sum())
    rows = np.kron(np.eye(na), np.ones(nb))
    cols = np.kron(np.ones(na), np.eye(nb))[:-1]
    res = linprog(cost.ravel(), A_eq=np.vstack([rows, cols]), b_eq=np.concatenate([a, b[:-1]]),
                  bounds=(0, None), method="highs")
    if res.status != 0:
        raise RuntimeError(f"optimal-transport solver failed: {res.message}")
    return float(res.fun) / SCALE


@dataclass
class EdgeArrays:
    """The graph as arrays: edge i joins nodes src[i] and dst[i]."""

    ids: list[int]                 # node index -> node id
    src: np.ndarray
    dst: np.ndarray
    capacity: np.ndarray
    length: np.ndarray | None = None   # lengths stored on the links, if every link has one
    n: int = field(init=False)

    def __post_init__(self):
        self.n = len(self.ids)


def edge_arrays(graph: dict[str, Any]) -> EdgeArrays:
    ids = [node["id"] for node in graph["nodes"]]
    index = {nid: i for i, nid in enumerate(ids)}
    links = graph["links"]
    src = np.array([index[l["source"]] for l in links], dtype=np.int64)
    dst = np.array([index[l["target"]] for l in links], dtype=np.int64)
    capacity = np.array([_number(l.get("capacity"), 1.0) for l in links], dtype=float)
    has_length = bool(links) and all(isinstance(l.get("length"), (int, float)) for l in links)
    length = np.array([l["length"] for l in links], dtype=float) if has_length else None
    return EdgeArrays(ids, src, dst, capacity, length)


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else default


def initial_lengths(arrays: EdgeArrays, mode: str = "inverse") -> np.ndarray:
    """Starting edge lengths, scaled so their mean is 1.

    inverse: 1 / capacity (default; missing or non-positive capacities count as 1)
    capacity: the capacity itself
    uniform: every edge 1
    keep: the lengths already on the links (falls back to inverse)
    """
    if mode not in INIT_MODES:
        raise ValueError(f"starting length must be one of {', '.join(INIT_MODES)}")
    cap = np.where(arrays.capacity > 0, arrays.capacity, 1.0)
    if mode == "keep" and arrays.length is not None:
        length = arrays.length.copy()
    elif mode == "capacity":
        length = cap.copy()
    elif mode == "uniform":
        length = np.ones(len(cap))
    else:
        length = 1.0 / cap
    length = np.maximum(length, MIN_LENGTH)
    return length * (len(length) / length.sum()) if len(length) else length


def _adjacency(arrays: EdgeArrays, values: np.ndarray) -> csr_matrix:
    adj = csr_matrix((np.concatenate([values, values]),
                      (np.concatenate([arrays.src, arrays.dst]),
                       np.concatenate([arrays.dst, arrays.src]))), shape=(arrays.n, arrays.n))
    adj.sum_duplicates()
    return adj


# ---------------------------------------------------------------- Ollivier

def ollivier_ricci(arrays: EdgeArrays, length: np.ndarray, alpha: float = 0.5,
                   should_stop: Callable[[], bool] | None = None) -> np.ndarray:
    """Ollivier-Ricci curvature of every edge, given the edge lengths."""
    n, m = arrays.n, len(length)
    if m == 0:
        return np.zeros(0)
    if n > OLLIVIER_MAX_NODES:
        raise TooLarge(f"Ollivier-Ricci is limited to {OLLIVIER_MAX_NODES:,} nodes "
                       f"(this graph has {n:,}); use Forman-Ricci instead")
    if not 0 <= alpha < 1:
        raise ValueError("alpha must be at least 0 and below 1")

    adj = _adjacency(arrays, length)
    dist = dijkstra(adj, directed=False)
    indptr, nbr, nlen = adj.indptr, adj.indices, adj.data

    support: list[np.ndarray] = []
    mass: list[np.ndarray] = []
    for x in range(n):
        nb, nl = nbr[indptr[x]:indptr[x + 1]], nlen[indptr[x]:indptr[x + 1]]
        w = np.exp(-nl ** 2)
        if len(nb):
            w = w / w.sum() if w.sum() > 0 else np.full(len(nb), 1 / len(nb))
        support.append(np.concatenate([[x], nb]).astype(np.int64))
        mass.append(np.concatenate([[alpha if len(nb) else 1.0], (1 - alpha) * w]))

    def chunk(edges: np.ndarray) -> np.ndarray:
        if should_stop and should_stop():
            raise InterruptedError
        out = np.empty(len(edges))
        for i, e in enumerate(edges):
            x, y = arrays.src[e], arrays.dst[e]
            w1 = emd2(mass[x], mass[y], dist[np.ix_(support[x], support[y])])
            out[i] = 1 - w1 / dist[x, y]
        return out

    parts = np.array_split(np.arange(m), max(1, m // 500))
    if not THREADS:
        return np.concatenate([chunk(p) for p in parts])
    with ThreadPoolExecutor(min(8, os.cpu_count() or 1)) as pool:
        return np.concatenate(list(pool.map(chunk, parts)))


# ---------------------------------------------------------------- Forman

def forman_ricci(arrays: EdgeArrays, length: np.ndarray) -> np.ndarray:
    """Augmented Forman-Ricci curvature of every edge (node weights 1)."""
    if len(length) == 0:
        return np.zeros(0)
    a = _adjacency(arrays, np.ones(len(length)))
    inv = _adjacency(arrays, 1 / np.sqrt(length))       # 1 / sqrt(w) on every edge
    s, d = arrays.src, arrays.dst

    # Triangles through each edge, and the 1/sqrt(w) sums over the triangle
    # edges at each end, read off the products at the edge's position.
    tri = a.multiply(a @ a).tocsr()
    tri_inv = a.multiply(inv @ a).tocsr()     # [x, y] = sum over common v of 1/sqrt(w_xv)
    t = np.asarray(tri[s, d]).ravel()
    tri_x = np.asarray(tri_inv[s, d]).ravel()
    tri_y = np.asarray(tri_inv[d, s]).ravel()

    total_inv = np.asarray(inv.sum(axis=1)).ravel()
    edge_inv = 1 / np.sqrt(length)
    rest_x = total_inv[s] - edge_inv - tri_x      # other edges at x, not in a triangle with e
    rest_y = total_inv[d] - edge_inv - tri_y
    w = length
    return w * (t * w + 2 / w - (rest_x + rest_y) / np.sqrt(w))


def flow_signal(arrays: EdgeArrays, kappa: np.ndarray, method: str) -> np.ndarray:
    """The curvature the flow uses: Ollivier as is, Forman normalized (see top)."""
    if method != "forman" or len(kappa) == 0:
        return kappa
    degree = np.bincount(np.concatenate([arrays.src, arrays.dst]), minlength=arrays.n)
    k = kappa / (degree[arrays.src] + degree[arrays.dst])
    return k / max(np.abs(k).max(), 1e-12)


DEFAULT_STEP = {"ollivier": 1.0, "forman": 0.3}
FORMAN_LENGTHS = (0.01, 100.0)   # flow lengths stay within this range times the mean


def bound_lengths(length: np.ndarray, method: str) -> np.ndarray:
    """Keep updated lengths positive; for Forman, also within FORMAN_LENGTHS."""
    if method == "forman":
        lo, hi = FORMAN_LENGTHS
        return np.clip(length, lo, hi)
    return np.maximum(length, MIN_LENGTH)


def curvature(arrays: EdgeArrays, length: np.ndarray, method: str = "ollivier",
              alpha: float = 0.5, should_stop: Callable[[], bool] | None = None) -> np.ndarray:
    if method == "ollivier":
        return ollivier_ricci(arrays, length, alpha, should_stop)
    if method == "forman":
        return forman_ricci(arrays, 1.0 / length)  # Forman weights are strengths (see top)
    raise ValueError(f"method must be one of {', '.join(METHODS)}")


# ---------------------------------------------------------------- flow

@dataclass
class FlowUpdate:
    iteration: int          # 0 = curvature of the starting lengths
    iterations: int
    length: np.ndarray
    curvature: np.ndarray
    change: float           # largest change in any edge's curvature since the last iteration
    done: bool = False
    converged: bool = False


def ricci_flow(arrays: EdgeArrays, iterations: int = 20, method: str = "ollivier",
               alpha: float = 0.5, step: float | None = None, init: str = "inverse",
               tolerance: float = 1e-4,
               on_update: Callable[[FlowUpdate], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> FlowUpdate:
    """Run the discrete Ricci flow. Calls on_update after every iteration.

    Stops early when no edge's curvature changes by more than `tolerance`,
    or when should_stop() returns True (the last finished iteration is kept).
    """
    if iterations < 0:
        raise ValueError("iterations cannot be negative")
    step = DEFAULT_STEP.get(method, 1.0) if step is None else step
    if not 0 < step <= 1:
        raise ValueError("step must be above 0 and at most 1")

    def flow_curvature(length: np.ndarray) -> np.ndarray:
        return flow_signal(arrays, curvature(arrays, length, method, alpha, should_stop), method)

    length = initial_lengths(arrays, init)
    kappa = flow_curvature(length)
    update = FlowUpdate(0, iterations, length, kappa, float("nan"), done=iterations == 0)
    if on_update:
        on_update(update)

    for it in range(1, iterations + 1):
        if should_stop and should_stop():
            break
        new_length = bound_lengths(length - step * kappa * length, method)
        new_length *= len(new_length) / new_length.sum()
        try:
            new_kappa = flow_curvature(new_length)
        except InterruptedError:
            break
        change = float(np.abs(new_kappa - kappa).max())
        length, kappa = new_length, new_kappa
        converged = change < tolerance
        update = FlowUpdate(it, iterations, length, kappa, change,
                            done=converged or it == iterations, converged=converged)
        if on_update:
            on_update(update)
        if converged:
            break
    update.done = True
    return update


# ---------------------------------------------------------------- communities

@dataclass
class Communities:
    labels: np.ndarray       # community of each node, 0 = largest
    cutoff: float            # edges longer than this were cut
    modularity: float
    count: int


def modularity(arrays: EdgeArrays, labels: np.ndarray) -> float:
    """Newman modularity of a node labelling, on the unweighted graph."""
    m = len(arrays.src)
    if m == 0:
        return 0.0
    inside = labels[arrays.src] == labels[arrays.dst]
    degree = np.bincount(np.concatenate([arrays.src, arrays.dst]), minlength=arrays.n)
    per_comm = np.bincount(labels, weights=degree)
    return float(inside.sum() / m - ((per_comm / (2 * m)) ** 2).sum())


def _rank_by_size(labels: np.ndarray) -> np.ndarray:
    sizes = np.bincount(labels)
    rank = np.empty_like(sizes)
    rank[np.argsort(-sizes, kind="stable")] = np.arange(len(sizes))
    return rank[labels]


def cut_communities(arrays: EdgeArrays, length: np.ndarray, cutoff: float) -> np.ndarray:
    """Connected pieces left after removing edges longer than cutoff."""
    keep = length <= cutoff
    g = csr_matrix((np.ones(int(keep.sum())), (arrays.src[keep], arrays.dst[keep])),
                   shape=(arrays.n, arrays.n))
    _, labels = connected_components(g, directed=False)
    return _rank_by_size(labels)


def find_communities(arrays: EdgeArrays, length: np.ndarray, cutoff: float | None = None,
                     candidates: int = 80) -> Communities:
    """Cut edges longer than `cutoff` and label the pieces.

    With no cutoff, tries cutoffs between the median and the longest edge
    length and keeps the one with the highest modularity.
    """
    if len(length) == 0:
        labels = np.arange(arrays.n)
        return Communities(labels, 0.0, 0.0, arrays.n)
    if cutoff is None:
        lo, hi = float(np.median(length)), float(length.max())
        tries = np.unique(np.concatenate([np.linspace(lo, hi, candidates),
                                          np.quantile(length, np.linspace(0.5, 1, candidates))]))
        scored = [(modularity(arrays, cut_communities(arrays, length, c)), -c) for c in tries]
        cutoff = -max(scored)[1]
    labels = cut_communities(arrays, length, cutoff)
    return Communities(labels, float(cutoff), modularity(arrays, labels), int(labels.max()) + 1)


def louvain(arrays: EdgeArrays, seed: int = 0) -> np.ndarray:
    """Louvain communities (networkx), as a baseline to compare with."""
    import networkx as nx

    g = nx.Graph()
    g.add_nodes_from(range(arrays.n))
    g.add_edges_from(zip(arrays.src.tolist(), arrays.dst.tolist()))
    labels = np.zeros(arrays.n, dtype=np.int64)
    for i, comm in enumerate(nx.community.louvain_communities(g, seed=seed)):
        labels[list(comm)] = i
    return _rank_by_size(labels)


# ---------------------------------------------------------------- compare with ground truth

def compare_to_truth(labels: np.ndarray, truth: np.ndarray, n_truth: int,
                     n_found: int | None = None, max_rows: int = 300) -> dict[str, Any]:
    """How found communities line up with ground-truth classes.

    labels and truth cover the nodes that have a ground truth; n_found is the
    total number of communities (some may have no such nodes). Returns:
      table      overlap counts, found community x truth class (first max_rows communities)
      main       each community's most common truth class (-1 if it has no such nodes)
      colour     a palette index per community: the truth class it is matched to one-to-one
                 (best total overlap), or n_truth + k for communities left unmatched
      purity     share of nodes that belong to their community's main class
      mismatches number of nodes that don't
    """
    from scipy.optimize import linear_sum_assignment

    n_found = max(n_found or 0, int(labels.max()) + 1 if len(labels) else 0)
    n_truth = max(n_truth, int(truth.max()) + 1 if len(truth) else 0)
    table = np.zeros((n_found, n_truth), dtype=np.int64)
    np.add.at(table, (labels, truth), 1)
    main = np.where(table.sum(1) > 0, table.argmax(1), -1)
    agree = int(table.max(1).sum()) if n_found else 0

    colour = np.full(n_found, -1)
    rows, cols = linear_sum_assignment(-table) if n_found and n_truth else ([], [])
    for r, c in zip(rows, cols):
        if table[r, c] > 0:
            colour[r] = c
    extra = n_truth
    for r in range(n_found):
        if colour[r] < 0:
            colour[r] = extra
            extra += 1

    return {"table": table[:max_rows].tolist(), "main": main.tolist(), "colour": colour.tolist(),
            "purity": agree / len(labels) if len(labels) else 0.0,
            "mismatches": len(labels) - agree, "known": int(len(labels))}


# ---------------------------------------------------------------- scores

def _contingency(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    _, ai = np.unique(a, return_inverse=True)
    _, bi = np.unique(b, return_inverse=True)
    table = np.zeros((ai.max() + 1, bi.max() + 1))
    np.add.at(table, (ai, bi), 1)
    return table


def nmi(a: np.ndarray, b: np.ndarray) -> float:
    """Normalized mutual information (arithmetic-mean normalization, as in scikit-learn)."""
    table = _contingency(a, b)
    n = table.sum()
    pa, pb = table.sum(1) / n, table.sum(0) / n
    nz = table > 0
    pij = table[nz] / n
    mi = float((pij * np.log(pij / np.outer(pa, pb)[nz])).sum())
    ha = float(-(pa[pa > 0] * np.log(pa[pa > 0])).sum())
    hb = float(-(pb[pb > 0] * np.log(pb[pb > 0])).sum())
    if ha == 0 and hb == 0:
        return 1.0
    return mi / ((ha + hb) / 2) if ha + hb > 0 else 0.0


def ari(a: np.ndarray, b: np.ndarray) -> float:
    """Adjusted Rand index."""
    table = _contingency(a, b)
    comb = lambda x: x * (x - 1) / 2
    n = table.sum()
    sum_ij = comb(table).sum()
    sum_a, sum_b = comb(table.sum(1)).sum(), comb(table.sum(0)).sum()
    expected = sum_a * sum_b / comb(n) if n > 1 else 0.0
    max_index = (sum_a + sum_b) / 2
    if max_index == expected:
        return 1.0
    return float((sum_ij - expected) / (max_index - expected))
