"""WM-09: DO _severity classifier — table-driven."""
from __future__ import annotations

import pytest

from website.features.web_monitor.DO_Alerts import _severity


@pytest.mark.parametrize(
    "metric,status_,value,expected",
    [
        # resolved trumps everything — always "info"
        ("cpu", "resolved", 99.0, "info"),
        ("memory", "resolved", None, "info"),
        ("unknown", "resolved", 0.0, "info"),
        # None value or None metric → warning
        (None, "alert", 99.0, "warning"),
        ("cpu", "alert", None, "warning"),
        (None, None, None, "warning"),
        # Below critical threshold → warning
        ("cpu", "alert", 80.0, "warning"),
        ("memory", "alert", 94.9, "warning"),
        ("mem", "alert", 50.0, "warning"),
        ("disk", "alert", 89.999, "warning"),
        # Boundary: exactly 95 IS critical (`>= 95`)
        ("cpu", "alert", 95.0, "critical"),
        ("memory", "alert", 95.0, "critical"),
        ("mem", "alert", 95.0, "critical"),
        ("disk", "alert", 95.0, "critical"),
        # Above 95 → critical
        ("cpu", "alert", 99.5, "critical"),
        ("disk", "alert", 100.0, "critical"),
        # Unknown metric stays warning even at 99 (we don't escalate things
        # we can't classify — safer to under-page than over-page)
        ("network", "alert", 99.0, "warning"),
    ],
)
def test_severity_table(metric, status_, value, expected):
    assert _severity(metric, status_, value) == expected
