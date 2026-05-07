"""iter-12 Task 35: empirical-gate audit script tests."""


def test_gate_activates_when_spread_high_and_min_low():
    from ops.scripts.audit_ce_distribution import iter_13_a1_gate
    samples = {
        "kasten_a": [0.45] * 50,
        "kasten_b": [0.75] * 50,
        "kasten_c": [0.70] * 50,
    }
    assert iter_13_a1_gate(samples) == "ACTIVATE_A1_PER_KASTEN_FLOOR"


def test_gate_closes_when_clustered():
    from ops.scripts.audit_ce_distribution import iter_13_a1_gate
    samples = {
        "kasten_a": [0.70] * 50,
        "kasten_b": [0.71] * 50,
        "kasten_c": [0.69] * 50,
    }
    assert iter_13_a1_gate(samples) == "CLOSE_CARRY_OVER_STATIC_FLOOR_CORRECT"


def test_gate_defers_when_insufficient_data():
    from ops.scripts.audit_ce_distribution import iter_13_a1_gate
    samples = {"kasten_a": [0.70] * 50, "kasten_b": [0.70] * 49}
    assert iter_13_a1_gate(samples) == "DEFER_TO_ITER_14_INSUFFICIENT_DATA"
