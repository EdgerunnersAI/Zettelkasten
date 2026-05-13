"""CF-2 R2 tests: academic-roundup signals (DOI / journal / et al.)."""
from __future__ import annotations

from website.features.summarization_engine.source_ingest.newsletter.shape_classifier import (
    classify,
    classify_tier1,
)
from website.features.summarization_engine.summarization.newsletter.shapes import (
    NewsletterShape,
)


def _filler(n: int) -> str:
    return " ".join(["word"] * n)


def test_doi_count_triggers_academic_roundup():
    md = (
        "Paper one explores something.\n"
        "doi: 10.1021/jacs.5b00001 and another 10.1038/nature12345 here.\n"
        + _filler(100)
    )
    cls = classify_tier1(md, "")
    assert cls is not None
    assert cls.shape == NewsletterShape.ACADEMIC_ROUNDUP
    assert cls.confidence == 0.90
    assert cls.method == "tier1_academic_signals"


def test_journal_hits_plus_et_al_triggers_academic_roundup():
    md = (
        "Reported in Nature Chemistry, with follow-ups in JACS and Angew. Chem.\n"
        "Smith et al. demonstrate the synthesis.\n"
        + _filler(120)
    )
    cls = classify_tier1(md, "")
    assert cls is not None
    assert cls.shape == NewsletterShape.ACADEMIC_ROUNDUP
    assert cls.method == "tier1_academic_signals"


def test_author_list_pattern_triggers_academic_roundup():
    md = (
        "Authors: Smith, J. A.; Jones, B. C.; Patel, K. L.; Wei, X.\n"
        + _filler(120)
    )
    cls = classify_tier1(md, "")
    assert cls is not None
    assert cls.shape == NewsletterShape.ACADEMIC_ROUNDUP
    assert cls.method == "tier1_academic_signals"


def test_pseudo_headers_count_toward_effective_headers():
    # No `##` markdown, but bold-wrapped pseudo-headers -> effective_headers>=5.
    body = "\n\n".join(
        [f"**Paper {i}: A Synthesis Result**\n\n" + _filler(60) for i in range(6)]
    )
    cls = classify_tier1(body, "")
    assert cls is not None
    # No DOI / journal / author signals fire, so we exercise the effective_headers
    # branch added in CF-2 R2.
    assert cls.shape == NewsletterShape.ACADEMIC_ROUNDUP
    assert cls.signals["pseudo_headers"] >= 5


def test_organicsynthesis_beehiiv_style_academic_roundup():
    # Mimic an organicsynthesis.beehiiv.com newsletter shape: a handful of
    # papers cited with DOIs and "et al." bylines, no markdown headers.
    md = (
        "Weekly roundup of organic synthesis papers.\n\n"
        "Chen et al. report a new method (10.1021/jacs.5b00210).\n"
        "Patel et al. extend the scope (10.1002/anie.202112345).\n"
        + _filler(150)
    )
    cls = classify(md, title="Weekly Organic Synthesis Roundup")
    assert cls.shape == NewsletterShape.ACADEMIC_ROUNDUP


def test_pure_commentary_essay_no_false_positive():
    md = (
        "Why product strategy is hard. "
        "Teams confuse vision with execution. "
        "The right move is to align on outcomes first, then iterate. "
        + _filler(200)
    )
    cls = classify(md, title="On Product Strategy")
    assert cls.shape != NewsletterShape.ACADEMIC_ROUNDUP
