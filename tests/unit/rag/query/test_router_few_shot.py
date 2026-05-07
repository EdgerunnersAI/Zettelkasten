"""iter-12 Phase 6 / Task 12: A1 balanced few-shot router prompt."""
from website.features.rag_pipeline.query.router import _ROUTER_PROMPT


def test_router_prompt_has_balanced_few_shot_examples():
    """A1: 8-12 balanced examples (>=2 per class, total in [8,12])."""
    for label in ("lookup", "vague", "thematic", "multi_hop", "step_back"):
        # Examples appear as `... => label` lines; tolerate alternative arrows
        # but require >=2 per class regardless of separator.
        count = 0
        for sep in ("=> %s" % label, "->%s" % label, ": %s" % label):
            count = max(count, _ROUTER_PROMPT.count(sep))
        assert count >= 2, f"<2 examples for {label} in router prompt"


def test_router_prompt_total_examples_in_range():
    """8-12 total -- past 12 hurts accuracy per arXiv 2509.13196."""
    n = _ROUTER_PROMPT.count("=>")
    assert 8 <= n <= 12, f"router prompt has {n} examples; need [8,12]"
