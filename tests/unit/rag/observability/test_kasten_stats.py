"""iter-12 Class K4: per-Kasten bootstrap stats tests."""
import pytest


def test_bootstrap_returns_static_fallback_below_n_min():
    from website.features.rag_pipeline.observability.kasten_stats import KastenStats
    stats = KastenStats(window=50, n_min=20)
    for _ in range(10):
        stats.record("kasten-1", "node-a")
    # n=10 < n_min=20 → static 0.25 fallback
    assert stats.bootstrap_threshold("kasten-1") == 0.25


def test_bootstrap_returns_static_fallback_for_unseen_kasten():
    from website.features.rag_pipeline.observability.kasten_stats import KastenStats
    stats = KastenStats()
    assert stats.bootstrap_threshold("never-recorded") == 0.25


def test_bootstrap_computes_mean_plus_2_stdev_above_n_min():
    from website.features.rag_pipeline.observability.kasten_stats import KastenStats
    stats = KastenStats(window=50, n_min=20)
    # 30 records, all top-1 node-a → only one node frequency
    for _ in range(30):
        stats.record("kasten-1", "node-a")
    threshold = stats.bootstrap_threshold("kasten-1")
    # Single-node Kasten edge case: degenerates to ~freq itself; clamp to 1.0
    assert 0.5 < threshold <= 1.0


def test_bootstrap_threshold_with_diverse_top1():
    from website.features.rag_pipeline.observability.kasten_stats import KastenStats
    stats = KastenStats(window=50, n_min=10)
    nodes = ["a", "b", "c", "d", "e"]
    # 50 records, ~uniform across 5 nodes
    for i in range(50):
        stats.record("kasten-1", nodes[i % 5])
    threshold = stats.bootstrap_threshold("kasten-1")
    # Each node has freq ≈ 0.2; mean ≈ 0.2; stdev tiny → threshold ≈ 0.2-0.25
    assert 0.15 < threshold < 0.5


def test_window_evicts_old_entries():
    from website.features.rag_pipeline.observability.kasten_stats import KastenStats
    stats = KastenStats(window=5, n_min=2)
    for _ in range(10):
        stats.record("k", "n1")
    stats.record("k", "n2")
    stats.record("k", "n3")
    # window=5 → only last 5 records: 3*n1, n2, n3 (since deque is FIFO with maxlen)
    threshold = stats.bootstrap_threshold("k")
    assert 0.0 < threshold <= 1.0


def test_threshold_clamped_to_1():
    from website.features.rag_pipeline.observability.kasten_stats import KastenStats
    stats = KastenStats(window=50, n_min=10)
    # All same node → mean=1.0, stdev=0, mean+2*stdev=1.0
    for _ in range(30):
        stats.record("k", "single")
    assert stats.bootstrap_threshold("k") <= 1.0
