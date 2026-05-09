"""Unit tests for the Phase 2.4.0 typed Candidate discriminated union + ACL adapters.

Covers contract guarantees the rest of Phase 2.4.x relies on:
1. Pydantic discriminator selection by ``kind``.
2. Per-kind required field enforcement.
3. ``node_id`` back-compat alias correctness.
4. ``extra="forbid"`` catches typos / silent contract drift.
5. ``frozen=True`` immutability.
6. ``candidate_to_legacy_dict`` shape per kind.
7. ``score_kind`` set from CALL-SITE arg, not derived from row.
8. ``fts_text`` synthesis only when ``score_kind == "fts"``.
9. ``default_rrf_score`` override for RRF fusion call-sites.
10. ``score_kind`` Literal validation.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import TypeAdapter, ValidationError

from website.features.rag_pipeline.retrieval.candidate_model import (
    Candidate,
    ChunkCandidate,
    DocCandidate,
    EntityCandidate,
    candidate_to_legacy_dict,
    chunk_from_v2_row,
    doc_from_v2_row,
    entity_from_v2_row,
)

CANDIDATE_ADAPTER: TypeAdapter[Candidate] = TypeAdapter(Candidate)


# ---------------------------------------------------------------------------
# 1. Discriminator selection
# ---------------------------------------------------------------------------
class TestDiscriminator:
    def test_chunk_kind_returns_chunk_candidate(self):
        chunk_id = uuid.uuid4()
        zet_id = uuid.uuid4()
        c = CANDIDATE_ADAPTER.validate_python(
            {
                "kind": "chunk",
                "canonical_chunk_id": chunk_id,
                "canonical_zettel_id": zet_id,
                "score": 0.9,
                "rrf_score": 0.9,
                "score_kind": "dense",
            }
        )
        assert isinstance(c, ChunkCandidate)
        assert c.canonical_chunk_id == chunk_id
        assert c.canonical_zettel_id == zet_id

    def test_entity_kind_returns_entity_candidate(self):
        c = CANDIDATE_ADAPTER.validate_python(
            {
                "kind": "entity",
                "kg_node_id": 42,
                "score": 0.5,
                "rrf_score": 0.5,
                "score_kind": "graph",
            }
        )
        assert isinstance(c, EntityCandidate)
        assert c.kg_node_id == 42

    def test_doc_kind_returns_doc_candidate(self):
        zet_id = uuid.uuid4()
        c = CANDIDATE_ADAPTER.validate_python(
            {
                "kind": "doc",
                "canonical_zettel_id": zet_id,
                "score": 0.7,
                "rrf_score": 0.7,
                "score_kind": "rerank",
            }
        )
        assert isinstance(c, DocCandidate)
        assert c.canonical_zettel_id == zet_id


# ---------------------------------------------------------------------------
# 2. Required-fields-per-kind
# ---------------------------------------------------------------------------
class TestRequiredFieldsPerKind:
    def test_chunk_requires_canonical_chunk_id(self):
        with pytest.raises(ValidationError):
            ChunkCandidate(
                # canonical_chunk_id missing
                canonical_zettel_id=uuid.uuid4(),
                score=0.5,
                rrf_score=0.5,
                score_kind="dense",
            )

    def test_chunk_requires_canonical_zettel_id(self):
        with pytest.raises(ValidationError):
            ChunkCandidate(
                canonical_chunk_id=uuid.uuid4(),
                # canonical_zettel_id missing
                score=0.5,
                rrf_score=0.5,
                score_kind="dense",
            )

    def test_entity_requires_kg_node_id(self):
        with pytest.raises(ValidationError):
            EntityCandidate(
                # kg_node_id missing
                score=0.5,
                rrf_score=0.5,
                score_kind="graph",
            )

    def test_doc_requires_canonical_zettel_id(self):
        with pytest.raises(ValidationError):
            DocCandidate(
                # canonical_zettel_id missing
                score=0.5,
                rrf_score=0.5,
                score_kind="dense",
            )


# ---------------------------------------------------------------------------
# 3. node_id back-compat alias
# ---------------------------------------------------------------------------
class TestNodeIdAlias:
    def test_chunk_node_id_is_canonical_chunk_id_str(self):
        chunk_id = uuid.uuid4()
        c = ChunkCandidate(
            canonical_chunk_id=chunk_id,
            canonical_zettel_id=uuid.uuid4(),
            score=0.1,
            rrf_score=0.1,
            score_kind="dense",
        )
        assert c.node_id == str(chunk_id)

    def test_entity_node_id_is_kg_node_id_str(self):
        c = EntityCandidate(
            kg_node_id=987,
            score=0.1,
            rrf_score=0.1,
            score_kind="graph",
        )
        assert c.node_id == "987"

    def test_doc_node_id_is_canonical_zettel_id_str(self):
        zet_id = uuid.uuid4()
        c = DocCandidate(
            canonical_zettel_id=zet_id,
            score=0.1,
            rrf_score=0.1,
            score_kind="dense",
        )
        assert c.node_id == str(zet_id)


# ---------------------------------------------------------------------------
# 4. extra="forbid"
# ---------------------------------------------------------------------------
class TestExtraForbid:
    def test_unknown_field_chunk_raises(self):
        with pytest.raises(ValidationError):
            ChunkCandidate(
                canonical_chunk_id=uuid.uuid4(),
                canonical_zettel_id=uuid.uuid4(),
                score=0.1,
                rrf_score=0.1,
                score_kind="dense",
                bogus_field="oops",
            )

    def test_unknown_field_entity_raises(self):
        with pytest.raises(ValidationError):
            EntityCandidate(
                kg_node_id=1,
                score=0.1,
                rrf_score=0.1,
                score_kind="graph",
                typo_field=True,
            )

    def test_unknown_field_doc_raises(self):
        with pytest.raises(ValidationError):
            DocCandidate(
                canonical_zettel_id=uuid.uuid4(),
                score=0.1,
                rrf_score=0.1,
                score_kind="dense",
                another_typo="x",
            )


# ---------------------------------------------------------------------------
# 5. frozen=True (immutability)
# ---------------------------------------------------------------------------
class TestFrozen:
    def test_chunk_is_frozen(self):
        c = ChunkCandidate(
            canonical_chunk_id=uuid.uuid4(),
            canonical_zettel_id=uuid.uuid4(),
            score=0.1,
            rrf_score=0.1,
            score_kind="dense",
        )
        with pytest.raises(ValidationError):
            c.score = 0.9  # type: ignore[misc]

    def test_entity_is_frozen(self):
        c = EntityCandidate(
            kg_node_id=1,
            score=0.1,
            rrf_score=0.1,
            score_kind="graph",
        )
        with pytest.raises(ValidationError):
            c.kg_node_id = 99  # type: ignore[misc]

    def test_doc_is_frozen(self):
        c = DocCandidate(
            canonical_zettel_id=uuid.uuid4(),
            score=0.1,
            rrf_score=0.1,
            score_kind="dense",
        )
        with pytest.raises(ValidationError):
            c.title = "new"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 6. candidate_to_legacy_dict shape
# ---------------------------------------------------------------------------
class TestLegacyDictProjection:
    def test_chunk_legacy_dict_has_expected_keys(self):
        chunk_id = uuid.uuid4()
        zet_id = uuid.uuid4()
        c = ChunkCandidate(
            canonical_chunk_id=chunk_id,
            canonical_zettel_id=zet_id,
            chunk_idx=3,
            content="hello",
            score=0.7,
            rrf_score=0.7,
            score_kind="dense",
        )
        d = candidate_to_legacy_dict(c)
        assert d["node_id"] == str(chunk_id)
        assert d["score"] == 0.7
        assert d["rrf_score"] == 0.7
        assert d["score_kind"] == "dense"
        assert d["kind"] == "chunk"
        assert d["canonical_chunk_id"] == str(chunk_id)
        assert d["canonical_zettel_id"] == str(zet_id)
        assert d["kg_node_id"] is None
        assert d["chunk_idx"] == 3
        assert d["content"] == "hello"

    def test_entity_legacy_dict_has_expected_keys(self):
        c = EntityCandidate(
            kg_node_id=42,
            title="Topic",
            entity_type="concept",
            score=0.4,
            rrf_score=0.4,
            score_kind="graph",
        )
        d = candidate_to_legacy_dict(c)
        assert d["node_id"] == "42"
        assert d["kind"] == "entity"
        assert d["kg_node_id"] == 42
        assert d["canonical_chunk_id"] is None
        assert d["canonical_zettel_id"] is None
        assert d["title"] == "Topic"
        assert d["entity_type"] == "concept"
        assert d["score_kind"] == "graph"

    def test_doc_legacy_dict_has_expected_keys(self):
        zet_id = uuid.uuid4()
        c = DocCandidate(
            canonical_zettel_id=zet_id,
            title="Doc title",
            score=0.6,
            rrf_score=0.6,
            score_kind="rerank",
        )
        d = candidate_to_legacy_dict(c)
        assert d["node_id"] == str(zet_id)
        assert d["kind"] == "doc"
        assert d["canonical_zettel_id"] == str(zet_id)
        assert d["canonical_chunk_id"] is None
        assert d["kg_node_id"] is None
        assert d["title"] == "Doc title"
        assert d["score_kind"] == "rerank"


# ---------------------------------------------------------------------------
# 7. chunk_from_v2_row honors caller-supplied score_kind
# ---------------------------------------------------------------------------
class TestScoreKindIsCallSiteSet:
    @pytest.fixture
    def base_row(self):
        return {
            "canonical_chunk_id": str(uuid.uuid4()),
            "canonical_zettel_id": str(uuid.uuid4()),
            "chunk_idx": 0,
            "content": "lorem ipsum",
            "score": 0.5,
        }

    def test_dense_score_kind(self, base_row):
        c = chunk_from_v2_row(base_row, score_kind="dense")
        assert c.score_kind == "dense"

    def test_fts_score_kind_same_row(self, base_row):
        c = chunk_from_v2_row(base_row, score_kind="fts")
        assert c.score_kind == "fts"

    def test_score_kind_not_derived_from_row(self, base_row):
        # Same row, different score_kinds must yield different Candidates.
        a = chunk_from_v2_row(base_row, score_kind="dense")
        b = chunk_from_v2_row(base_row, score_kind="rerank")
        assert a.score_kind == "dense"
        assert b.score_kind == "rerank"


# ---------------------------------------------------------------------------
# 8. fts_text synthesis only when score_kind == "fts"
# ---------------------------------------------------------------------------
class TestFtsTextSynthesis:
    def test_fts_text_filled_when_score_kind_is_fts_from_fts_text_field(self):
        row = {
            "canonical_chunk_id": str(uuid.uuid4()),
            "canonical_zettel_id": str(uuid.uuid4()),
            "score": 0.4,
            "fts_text": "tsvector matched terms",
            "content": "full content",
        }
        c = chunk_from_v2_row(row, score_kind="fts")
        assert c.fts_text == "tsvector matched terms"

    def test_fts_text_falls_back_to_content_when_fts_text_missing(self):
        row = {
            "canonical_chunk_id": str(uuid.uuid4()),
            "canonical_zettel_id": str(uuid.uuid4()),
            "score": 0.4,
            "content": "full content",
        }
        c = chunk_from_v2_row(row, score_kind="fts")
        assert c.fts_text == "full content"

    def test_fts_text_empty_when_score_kind_not_fts(self):
        row = {
            "canonical_chunk_id": str(uuid.uuid4()),
            "canonical_zettel_id": str(uuid.uuid4()),
            "score": 0.4,
            "fts_text": "should be ignored",
            "content": "also ignored",
        }
        c = chunk_from_v2_row(row, score_kind="dense")
        assert c.fts_text == ""


# ---------------------------------------------------------------------------
# 9. default_rrf_score override
# ---------------------------------------------------------------------------
class TestDefaultRrfScoreOverride:
    def test_rrf_score_defaults_to_score(self):
        row = {
            "canonical_chunk_id": str(uuid.uuid4()),
            "canonical_zettel_id": str(uuid.uuid4()),
            "score": 0.5,
        }
        c = chunk_from_v2_row(row, score_kind="dense")
        assert c.rrf_score == 0.5

    def test_default_rrf_score_overrides_score(self):
        row = {
            "canonical_chunk_id": str(uuid.uuid4()),
            "canonical_zettel_id": str(uuid.uuid4()),
            "score": 0.5,
        }
        c = chunk_from_v2_row(row, score_kind="dense", default_rrf_score=0.0123)
        assert c.score == 0.5
        assert c.rrf_score == 0.0123

    def test_default_rrf_score_override_for_entity(self):
        row = {"kg_node_id": 7, "score": 0.9}
        c = entity_from_v2_row(row, score_kind="graph", default_rrf_score=0.001)
        assert c.score == 0.9
        assert c.rrf_score == 0.001

    def test_default_rrf_score_override_for_doc(self):
        row = {"canonical_zettel_id": str(uuid.uuid4()), "score": 0.3}
        c = doc_from_v2_row(row, score_kind="rerank", default_rrf_score=0.5)
        assert c.score == 0.3
        assert c.rrf_score == 0.5


# ---------------------------------------------------------------------------
# 10. score_kind Literal enforcement
# ---------------------------------------------------------------------------
class TestScoreKindLiteralEnforcement:
    def test_invalid_score_kind_chunk_raises(self):
        with pytest.raises(ValidationError):
            ChunkCandidate(
                canonical_chunk_id=uuid.uuid4(),
                canonical_zettel_id=uuid.uuid4(),
                score=0.1,
                rrf_score=0.1,
                score_kind="bogus_kind",  # type: ignore[arg-type]
            )

    def test_invalid_score_kind_entity_raises(self):
        with pytest.raises(ValidationError):
            EntityCandidate(
                kg_node_id=1,
                score=0.1,
                rrf_score=0.1,
                score_kind="not_a_kind",  # type: ignore[arg-type]
            )

    def test_invalid_score_kind_doc_raises(self):
        with pytest.raises(ValidationError):
            DocCandidate(
                canonical_zettel_id=uuid.uuid4(),
                score=0.1,
                rrf_score=0.1,
                score_kind="invalid",  # type: ignore[arg-type]
            )

    def test_all_documented_score_kinds_accepted(self):
        # Ensure the Literal stays in sync with the documented values.
        for kind_value in ("dense", "fts", "graph", "graph_seed", "rerank", "anchor_seed"):
            c = EntityCandidate(
                kg_node_id=1,
                score=0.1,
                rrf_score=0.1,
                score_kind=kind_value,  # type: ignore[arg-type]
            )
            assert c.score_kind == kind_value
