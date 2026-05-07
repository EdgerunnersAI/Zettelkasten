"""Unit tests for ops/scripts/burst_pressure_probe.py pure helpers."""
from __future__ import annotations

import pytest

from ops.scripts.burst_pressure_probe import (
    _parse_status_distribution,
    _summarize,
    _verdict,
)


class TestParseStatusDistribution:
    def test_mixed_codes(self):
        result = _parse_status_distribution([200, 200, 502, 503, 200])
        assert result == {200: 3, 502: 1, 503: 1}

    def test_all_same(self):
        assert _parse_status_distribution([200, 200, 200]) == {200: 3}

    def test_empty(self):
        assert _parse_status_distribution([]) == {}


class TestSummarize:
    def test_basic(self):
        stats = _summarize([12, 35, 80, 120, 12, 25])
        assert "p50" in stats
        assert "p95" in stats
        assert "p99" in stats
        assert "max" in stats
        # sorted: [12, 12, 25, 35, 80, 120]
        assert stats["max"] == 120.0
        assert stats["p50"] <= stats["p95"] <= stats["p99"] <= stats["max"]

    def test_empty(self):
        stats = _summarize([])
        assert stats == {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}

    def test_single_value(self):
        stats = _summarize([42.0])
        assert stats["p50"] == 42.0
        assert stats["max"] == 42.0


class TestVerdict:
    def test_pass(self):
        assert _verdict(r502=0.0, lag_p95=10.0) == "PASS"

    def test_fail_on_502(self):
        assert _verdict(r502=0.01, lag_p95=10.0) == "FAIL"

    def test_concerns_high_lag(self):
        assert _verdict(r502=0.0, lag_p95=200.0) == "CONCERNS"

    def test_fail_beats_concerns(self):
        # 502 present even with high lag => FAIL not CONCERNS
        assert _verdict(r502=0.05, lag_p95=200.0) == "FAIL"

    def test_boundary_lag_exactly_50(self):
        # p95 == 50 is not < 50, so CONCERNS
        assert _verdict(r502=0.0, lag_p95=50.0) == "CONCERNS"

    def test_boundary_lag_just_under_50(self):
        assert _verdict(r502=0.0, lag_p95=49.9) == "PASS"
