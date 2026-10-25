"""Curvature, flow and scoring checks.

Reference values were computed with the GraphRicciCurvature package
(v0.6.1: OllivierRicci with alpha=0.5, method "OTD"; FormanRicci with
method "augmented") on Zachary's karate club with all edge weights 1.
"""

import networkx as nx
import numpy as np
import pytest

from interactive_graph import ricci

KARATE_OLLIVIER = {(0, 1): 0.111111, (0, 31): -0.427083, (2, 32): -0.233333,
                   (32, 33): 0.267157, (5, 16): 0.375}
KARATE_FORMAN = {(0, 1): 0.0, (0, 31): -18.0, (2, 32): -15.0, (32, 33): 5.0, (5, 16): 1.0}
# After 10 flow iterations from uniform lengths: (length, curvature).
KARATE_FLOW = {(0, 1): (0.063991, 0.186083), (0, 31): (3.432189, -0.027886),
               (32, 33): (0.014072, 0.368237)}


def karate():
    g = nx.karate_club_graph()
    edges = np.array(g.edges())
    arrays = ricci.EdgeArrays(list(g.nodes()), edges[:, 0], edges[:, 1], np.ones(len(edges)))
    index = {tuple(e): i for i, e in enumerate(edges.tolist())}
    return arrays, index


def test_ollivier_matches_reference():
    arrays, index = karate()
    k = ricci.ollivier_ricci(arrays, np.ones(len(index)))
    for edge, want in KARATE_OLLIVIER.items():
        assert k[index[edge]] == pytest.approx(want, abs=1e-6)


def test_forman_matches_reference():
    arrays, index = karate()
    k = ricci.forman_ricci(arrays, np.ones(len(index)))
    for edge, want in KARATE_FORMAN.items():
        assert k[index[edge]] == pytest.approx(want, abs=1e-9)


def test_flow_matches_reference():
    arrays, index = karate()
    result = ricci.ricci_flow(arrays, iterations=10, init="uniform", tolerance=0)
    for edge, (length, curvature) in KARATE_FLOW.items():
        assert result.length[index[edge]] == pytest.approx(length, abs=1e-6)
        assert result.curvature[index[edge]] == pytest.approx(curvature, abs=1e-6)
    assert result.length.sum() == pytest.approx(len(index))


def test_linprog_fallback_matches_pot(monkeypatch):
    ot = pytest.importorskip("ot")
    rng = np.random.default_rng(0)
    for _ in range(50):
        na, nb = rng.integers(1, 15, 2)
        a, b = rng.random(na), rng.random(nb)
        a, b, cost = a / a.sum(), b / b.sum(), rng.random((na, nb))
        want = float(ot.emd2(a, b, cost))
        monkeypatch.setattr(ricci, "ot", None)
        assert ricci.emd2(a, b, cost) == pytest.approx(want, abs=1e-8)
        monkeypatch.setattr(ricci, "ot", ot)


def test_initial_lengths_from_capacity():
    arrays = ricci.EdgeArrays([0, 1, 2], np.array([0, 1]), np.array([1, 2]), np.array([1.0, 4.0]))
    inverse = ricci.initial_lengths(arrays, "inverse")
    assert inverse.mean() == pytest.approx(1)
    assert inverse[0] / inverse[1] == pytest.approx(4)
    assert ricci.initial_lengths(arrays, "capacity")[1] / ricci.initial_lengths(arrays, "capacity")[0] == pytest.approx(4)
    assert list(ricci.initial_lengths(arrays, "uniform")) == [1, 1]


def test_ollivier_size_limit(monkeypatch):
    arrays, index = karate()
    monkeypatch.setattr(ricci, "OLLIVIER_MAX_NODES", 10)
    with pytest.raises(ricci.TooLarge):
        ricci.ollivier_ricci(arrays, np.ones(len(index)))


def test_flow_separates_two_cliques():
    g = nx.barbell_graph(8, 0)  # two 8-cliques joined by one edge
    edges = np.array(g.edges())
    arrays = ricci.EdgeArrays(list(g.nodes()), edges[:, 0], edges[:, 1], np.ones(len(edges)))
    truth = np.array([0] * 8 + [1] * 8)
    for method in ricci.METHODS:
        result = ricci.ricci_flow(arrays, iterations=10, method=method, init="uniform")
        comms = ricci.find_communities(arrays, result.length)
        assert comms.count == 2, method
        assert ricci.nmi(comms.labels, truth) == pytest.approx(1)


def test_scores():
    a = np.array([0, 0, 1, 1, 2])
    assert ricci.nmi(a, np.array([1, 1, 0, 0, 2])) == pytest.approx(1)
    assert ricci.ari(a, np.array([1, 1, 0, 0, 2])) == pytest.approx(1)
    assert ricci.nmi(np.zeros(10, int), np.arange(10)) == pytest.approx(0)
    # Values from scikit-learn 1.8 (normalized_mutual_info_score, adjusted_rand_score).
    rng = np.random.default_rng(0)
    x, y = rng.integers(0, 5, 300), rng.integers(0, 7, 300)
    assert ricci.nmi(x, y) == pytest.approx(0.017269, abs=1e-6)
    assert ricci.ari(x, y) == pytest.approx(-0.004150, abs=1e-6)


def test_modularity_matches_networkx():
    arrays, _ = karate()
    labels = ricci.louvain(arrays)
    g = nx.karate_club_graph()
    parts = [set(np.flatnonzero(labels == c).tolist()) for c in range(labels.max() + 1)]
    assert ricci.modularity(arrays, labels) == pytest.approx(nx.community.modularity(g, parts, weight=None))


def test_compare_to_truth():
    labels = np.array([0, 0, 0, 1, 1, 2, 2])
    truth = np.array([1, 1, 0, 0, 0, 0, 1])
    c = ricci.compare_to_truth(labels, truth, 2)
    assert c["table"] == [[1, 2], [2, 0], [1, 1]]
    assert c["main"][:2] == [1, 0]
    assert c["colour"][0] == 1 and c["colour"][1] == 0 and c["colour"][2] == 2  # unmatched gets a new colour
    assert c["mismatches"] == 2 and c["purity"] == pytest.approx(5 / 7)


def test_linprog_fallback_handles_tiny_masses(monkeypatch):
    # Stretched links give neighbours masses ~1e-8, below HiGHS's tolerance.
    ot = pytest.importorskip("ot")
    rng = np.random.default_rng(1)
    for _ in range(20):
        a = np.concatenate([[0.5], rng.random(6) * 0.5 / 6, [1e-8, 7e-8]])
        b = np.concatenate([[0.5], rng.random(5) * 0.5 / 5, [2e-8]])
        a, b = a / a.sum(), b / b.sum()
        cost = rng.random((len(a), len(b))) * 6
        want = float(ot.emd2(a, b, cost))
        monkeypatch.setattr(ricci, "ot", None)
        assert ricci.emd2(a, b, cost) == pytest.approx(want, abs=1e-8)
        monkeypatch.setattr(ricci, "ot", ot)


def test_forman_flow_does_not_collapse():
    # With lengths fed as Forman weights, every length collapsed after ~100 iterations.
    arrays, _ = karate()
    short = ricci.ricci_flow(arrays, iterations=20, method="forman", init="uniform", tolerance=0)
    long = ricci.ricci_flow(arrays, iterations=200, method="forman", init="uniform", tolerance=0)
    assert ricci.find_communities(arrays, long.length).count > 1
    assert ricci.find_communities(arrays, long.length).count == ricci.find_communities(arrays, short.length).count
    assert long.length.min() > 1e-3  # no link shrinks towards zero


def test_forman_flow_uses_strengths():
    arrays, index = karate()
    lengths = np.linspace(0.5, 2.0, len(index))
    assert np.allclose(ricci.curvature(arrays, lengths, "forman"), ricci.forman_ricci(arrays, 1 / lengths))
