"""Pydantic models for evaluator output and composite score calculation."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class GEvalCriterion(BaseModel):
    """One anchored-ternary G-Eval criterion (score in {1, 2, 3})."""

    score: int = Field(ge=1, le=3)
    anchor: str = ""
    reasoning: str = ""

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_ternary(cls, value):
        # Tolerate float emissions from the judge by rounding into the ternary
        # band, then clamping. Anything below 1 -> 1, anything above 3 -> 3.
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value
        numeric = round(numeric)
        return max(1, min(3, int(numeric)))


class GEvalScores(BaseModel):
    """G-Eval (coherence + fluency only). consistency/relevance removed in
    evaluator.v4 — they overlapped with finesure.faithfulness/completeness."""

    coherence: GEvalCriterion
    fluency: GEvalCriterion

    @field_validator("coherence", "fluency", mode="before")
    @classmethod
    def _coerce_criterion(cls, value):
        # Back-compat: accept a bare numeric (legacy 0-5 float) by mapping
        # into the new ternary band so old fixtures parse without crashing.
        if isinstance(value, (int, float)):
            n = float(value)
            if n <= 1.5:
                s = 1
            elif n <= 3.5:
                s = 2
            else:
                s = 3
            return {"score": s, "anchor": "", "reasoning": ""}
        return value


class FineSurEItem(BaseModel):
    claim: str | None = None
    fact: str | None = None
    sentence: str | None = None
    span: str | None = None
    importance: int | None = None

    @classmethod
    def _coerce(cls, value):
        """Accept a bare string → treat as {sentence: <str>}."""
        if isinstance(value, str):
            return {"sentence": value}
        return value


class FineSurEDimension(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    items: list[FineSurEItem] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _coerce_items(cls, value):
        """Accept a list with mixed strings/dicts; strings → {sentence: <str>}."""
        if not isinstance(value, list):
            return value
        return [FineSurEItem._coerce(item) for item in value]

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, value):
        """Accept 0-1 or 0-100 scale; clamp into [0,1]."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return value
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))


class FineSurEScores(BaseModel):
    faithfulness: FineSurEDimension
    completeness: FineSurEDimension
    conciseness: FineSurEDimension

    @field_validator("faithfulness", "completeness", "conciseness", mode="before")
    @classmethod
    def _coerce_dimension(cls, value):
        """Accept bare float (coerce to {score: v, items: []}) or dict."""
        if isinstance(value, (int, float)):
            return {"score": value, "items": []}
        return value


class SummaCLiteSentence(BaseModel):
    sentence: str
    reason: str = ""


class SummaCLite(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    contradicted_sentences: list[SummaCLiteSentence] = Field(default_factory=list)
    neutral_sentences: list[SummaCLiteSentence] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_summac_score(cls, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return value
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))

    @field_validator("contradicted_sentences", "neutral_sentences", mode="before")
    @classmethod
    def _coerce_sentences(cls, value):
        if not isinstance(value, list):
            return value
        return [{"sentence": x} if isinstance(x, str) else x for x in value]


class RubricComponent(BaseModel):
    id: str
    score: float
    max_points: int
    criteria_fired: list[str] = Field(default_factory=list)
    criteria_missed: list[str] = Field(default_factory=list)

    @field_validator("criteria_fired", "criteria_missed", mode="before")
    @classmethod
    def _coerce_criteria(cls, value):
        if not isinstance(value, list):
            return value
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                # Accept {"id": "...", "score": ...} richer form from evaluator
                out.append(str(item.get("id") or item.get("name") or item))
            else:
                out.append(str(item))
        return out


class AntiPatternTrigger(BaseModel):
    id: str
    source_region: str = ""
    auto_cap: int | None = None

    @field_validator("source_region", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return "" if value is None else value


class RubricBreakdown(BaseModel):
    components: list[RubricComponent]
    caps_applied: dict[str, int | None] = Field(
        default_factory=lambda: {
            "hallucination_cap": None,
            "omission_cap": None,
            "generic_cap": None,
        }
    )
    anti_patterns_triggered: list[AntiPatternTrigger] = Field(default_factory=list)

    @field_validator("caps_applied", mode="before")
    @classmethod
    def _fill_caps(cls, value):
        base = {"hallucination_cap": None, "omission_cap": None, "generic_cap": None}
        if isinstance(value, dict):
            for k in base:
                if k in value and value[k] is not None:
                    try:
                        base[k] = int(value[k])
                    except (TypeError, ValueError):
                        base[k] = None
        return base

    @property
    def total_of_100(self) -> float:
        return sum(component.score for component in self.components)


class EditorializationFlag(BaseModel):
    sentence: str
    flag_type: str = "added_stance"
    explanation: str = ""

    @field_validator("flag_type", mode="before")
    @classmethod
    def _normalize_flag(cls, value):
        """Permit any string; normalize to one of the 3 known types when close."""
        if value is None:
            return "added_stance"
        v = str(value).lower().replace(" ", "_")
        for known in ("added_stance", "added_judgment", "added_framing"):
            if known in v:
                return known
        return "added_stance"


class EvalResult(BaseModel):
    g_eval: GEvalScores
    finesure: FineSurEScores
    summac_lite: SummaCLite
    rubric: RubricBreakdown
    maps_to_metric_summary: dict[str, float]
    editorialization_flags: list[EditorializationFlag] = Field(default_factory=list)
    # Advisory flag — true when ingest-side metadata (author/issue_date/etc.) is
    # missing but the summary body itself is well-formed. NEVER feeds the
    # composite cap; surfacing only. Paired with `missing_meta` listing keys.
    metadata_partial: bool = False
    missing_meta: list[str] = Field(default_factory=list)
    evaluator_metadata: dict


def apply_caps(score: float, caps: dict[str, int | None]) -> float:
    if caps.get("hallucination_cap") is not None:
        return min(score, float(caps["hallucination_cap"]))
    if caps.get("omission_cap") is not None:
        return min(score, float(caps["omission_cap"]))
    if caps.get("generic_cap") is not None:
        return min(score, float(caps["generic_cap"]))
    return score


def g_eval_aggregate(scores: GEvalScores) -> float:
    """Anchored-ternary aggregate -> 0-100. (1+1)=33, (2+2)=67, (3+3)=100."""
    return ((scores.coherence.score + scores.fluency.score) / 6.0) * 100.0


def composite_score(result: EvalResult) -> float:
    base = (
        0.60 * result.rubric.total_of_100
        + 0.20 * result.finesure.faithfulness.score * 100
        + 0.10 * result.finesure.completeness.score * 100
        + 0.10 * g_eval_aggregate(result.g_eval)
    )
    return round(apply_caps(base, result.rubric.caps_applied), 2)
