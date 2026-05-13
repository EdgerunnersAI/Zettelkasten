"""Newsletter per-source summarizer (3-call DenseVerify pipeline).

Call budget (<=3 per zettel):
  1. DenseVerifier (pro) — dense + verify + stance signal (from DV's
     ``stance`` hint field; the source-ingest ``detect_stance`` heuristic
     remains authoritative because it reads substack/beehiiv tone cues
     the LLM misses).
  2. StructuredExtractor via direct flash generate — schema-shaped payload.
  3. Optional flash template-artifact repair — when the first payload
     leaks ``**ID:**`` / ``**Title:**`` / ``#substack`` scaffolding, one
     repair call rewrites it cleanly. The DV-brief patch path is NOT
     used here because newsletter failures are artifact-driven, not
     omission-driven.
"""
from __future__ import annotations

import re
import time

from website.features.summarization_engine.core.budget import get_budget
from website.features.summarization_engine.core.models import SummaryMetadata
from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import (
    IngestResult,
    SourceType,
)
from website.features.summarization_engine.evaluator.numeric_grounding import (
    extract_numeric_tokens,
    ground_numeric_claims,
)
from website.features.summarization_engine.summarization import register_summarizer
from website.features.summarization_engine.summarization.base import BaseSummarizer
from website.features.summarization_engine.summarization.common.dense_verify_runner import (
    run_dense_verify,
)
from website.features.summarization_engine.summarization.common.json_utils import (
    parse_json_object,
)
from website.features.summarization_engine.summarization.common.model_trace import (
    aggregate_fallback_reason,
    make_call_entry,
)
from website.features.summarization_engine.summarization.common.prompts import (
    SYSTEM_PROMPT,
)
from website.features.summarization_engine.summarization.common.structured import (
    _date_or_none,
    _normalize_tags,
)
from website.features.summarization_engine.summarization.newsletter.archetype import (
    archetype_from_signals as _newsletter_archetype_impl,
)
from website.features.summarization_engine.summarization.newsletter.guards import (
    apply_newsletter_guards,
)
from website.features.summarization_engine.summarization.newsletter.prompts import (
    STRUCTURED_EXTRACT_INSTRUCTION,
)
from website.features.summarization_engine.summarization.newsletter.shapes import (
    NO_STANCE_PENALTY,
)
from website.features.summarization_engine.summarization.newsletter.templates import (
    template_for as shape_template_for,
)
from website.features.summarization_engine.summarization.newsletter.schema import (
    NewsletterDetailedPayload,
    NewsletterSection,
    NewsletterStructuredPayload,
    NewsletterSummaryResult,
)


class NewsletterSummarizer(BaseSummarizer):
    source_type = SourceType.NEWSLETTER

    def __init__(self, gemini_client: TieredGeminiClient, config):
        super().__init__(gemini_client, config)
        from website.features.summarization_engine.core.config import load_config

        self._engine_config = load_config()

    async def summarize(self, ingest: IngestResult) -> NewsletterSummaryResult:
        start = time.perf_counter()

        # Call 1 — DenseVerify (pro).
        dv = await run_dense_verify(client=self._client, ingest=ingest)
        pro_tokens = 0  # DV tokens are tracked elsewhere; run_dense_verify has no accessor here
        call_trace: list[dict] = [{
            "role": "dense_verify",
            "model": dv.model_used,
            "starting_model": dv.starting_model,
            "fallback_reason": dv.fallback_reason,
        }]

        # Shape classification (Tier-1 deterministic; 0 LLM calls). Per
        # R-Newsletter-Coverage 2026-05-13: dispatch a shape-specific
        # template tail so academic-roundup/link-digest/news-aggregator/
        # product-announcement don't get penalized as commentary-essay.
        # Lazy import breaks the source_ingest -> summarization.newsletter
        # circular load order at module-collection time.
        from website.features.summarization_engine.source_ingest.newsletter.shape_classifier import (
            classify as classify_shape,
        )
        shape_classification = classify_shape(
            markdown=ingest.raw_text or "",
            title=str(
                ingest.sections.get("Title")
                or ingest.metadata.get("title")
                or ""
            ),
            llm_classifier=None,  # Tier-2 deferred; Tier-1 only for now
        )
        shape_template = shape_template_for(shape_classification.shape)
        suppress_stance_penalty = (
            shape_classification.shape in NO_STANCE_PENALTY
        )

        # Compose the structured prompt with DV hints. Newsletter retains its
        # bespoke template-artifact repair loop because the failure mode is
        # leaked Substack/beehiiv scaffolding, which the generic DV patch
        # substring probe cannot detect.
        prompt = STRUCTURED_EXTRACT_INSTRUCTION.format(summary_text=dv.dense_text or ingest.raw_text or "")
        prompt = f"{prompt}\n\n{shape_template}"
        if dv.missing_facts:
            joined = "; ".join(f.strip() for f in dv.missing_facts if f.strip())
            if joined:
                prompt = f"{prompt}\n\nEnsure these facts are covered: {joined}"

        # Call 2 — structured extraction (flash).
        get_budget().consume(role="summarizer")  # C6: 3-call budget
        result = await self._client.generate(
            prompt,
            tier="flash",
            response_schema=NewsletterStructuredPayload,
            system_instruction=SYSTEM_PROMPT,
            role="summarizer",
        )
        call_trace.append(make_call_entry(role="summarizer", result=result))
        flash_tokens = result.input_tokens + result.output_tokens
        is_schema_fallback = False
        repair_fired = False
        try:
            payload = _parse_payload_with_ingest(result.text, ingest)
            # Call 3 (optional) — flash template-artifact repair.
            if _payload_contains_template_artifacts(payload):
                get_budget().consume(role="repair")  # C6: 3-call budget
                repair = await self._client.generate(
                    prompt
                    + "\n\nThe previous output contained metadata-template artifacts "
                    "(such as **ID:**/**Title:** or hash-tag bundles). "
                    "Regenerate clean JSON that follows the schema only.",
                    tier="flash",
                    response_schema=NewsletterStructuredPayload,
                    system_instruction=SYSTEM_PROMPT,
                    role="repair",
                )
                call_trace.append(make_call_entry(role="repair", result=repair))
                flash_tokens += repair.input_tokens + repair.output_tokens
                repair_fired = True
                payload = _parse_payload_with_ingest(repair.text, ingest)
                if _payload_contains_template_artifacts(payload):
                    raise ValueError(
                        "newsletter payload still contains template artifacts"
                    )
        except Exception:
            payload = _fallback_payload(ingest, dv.dense_text or ingest.raw_text or "", self._engine_config)
            is_schema_fallback = True
        payload = _apply_ingest_guardrails(payload, ingest)

        # Deterministic hallucination guards: banned-adjective strip +
        # numeric verbatim verifier. Zero LLM cost. Conditional repair
        # stays MUTUALLY EXCLUSIVE with template-artifact repair — combined
        # ceiling: <=1 conditional Flash call (3-call cap preserved).
        guard_audit = _run_hallucination_guards(
            payload,
            ingest.raw_text or "",
            suppress_evaluative_strip=suppress_stance_penalty,
        )
        if guard_audit["requires_repair"] and not repair_fired and not is_schema_fallback:
            get_budget().consume(role="repair")  # C6: 3-call budget
            numerals_blob = ", ".join(guard_audit["unverified_numerals"])
            repair = await self._client.generate(
                prompt
                + "\n\nThe previous output contained numeric tokens not present "
                "in the source: "
                + numerals_blob
                + ". Regenerate JSON; omit any numeral, code, identifier, or "
                "proper-noun token that does not appear verbatim in the source.",
                tier="flash",
                response_schema=NewsletterStructuredPayload,
                system_instruction=SYSTEM_PROMPT,
                role="repair",
            )
            call_trace.append(make_call_entry(role="repair", result=repair))
            flash_tokens += repair.input_tokens + repair.output_tokens
            try:
                payload = _parse_payload_with_ingest(repair.text, ingest)
                payload = _apply_ingest_guardrails(payload, ingest)
            except Exception:
                pass  # keep prior payload — guards below still scrub adjectives
            # Re-run guards on the repaired payload to refresh audit
            guard_audit = _run_hallucination_guards(
                payload,
                ingest.raw_text or "",
                suppress_evaluative_strip=suppress_stance_penalty,
            )
        latency_ms = int((time.perf_counter() - start) * 1000)

        # Compute a lightweight newsletter archetype label so the _dense_verify
        # extras block reaches cross-source parity with YouTube (format_label)
        # and GitHub (archetype). Heuristic (no extra LLM call) because the
        # 3-call budget is already saturated. Falls back to "engineering_essay"
        # as the dominant Substack/Beehiiv default.
        archetype = _newsletter_archetype(
            payload=payload,
            ingest=ingest,
        )
        structured_payload_extras = payload.model_dump(mode="json")
        structured_payload_extras["_dense_verify"] = {
            "archetype": archetype,
            "stance": dv.stance,
            "missing_fact_count": len(dv.missing_facts),
        }
        structured_payload_extras["_guard_audit"] = guard_audit
        structured_payload_extras["_shape"] = shape_classification.shape.value
        structured_payload_extras["_shape_confidence"] = shape_classification.confidence
        structured_payload_extras["_shape_method"] = shape_classification.method
        # Newsletter personalization: byline author comes from the ingest
        # metadata (extractor parsed the Substack/beehiiv author field — not
        # LLM-inferred). Surface it on the payload so the frontend can render
        # a consistent "Written by <author>" line across sources.
        byline = str(ingest.metadata.get("author") or "").strip()
        if byline:
            structured_payload_extras["byline_author"] = byline

        return NewsletterSummaryResult(
            mini_title=payload.mini_title[
                : self._engine_config.structured_extract.mini_title_max_chars
            ],
            brief_summary=_trim_at_sentence_boundary(
                payload.brief_summary,
                self._engine_config.structured_extract.brief_summary_max_chars,
            ),
            tags=_normalize_tags(
                payload.tags,
                self._engine_config.structured_extract.tags_min,
                self._engine_config.structured_extract.tags_max,
                reserved=_brand_reserved(payload),
            ),
            detailed_summary=payload.detailed_summary,
            metadata=SummaryMetadata(
                source_type=ingest.source_type,
                url=ingest.url,
                author=ingest.metadata.get("author"),
                date=_date_or_none(
                    ingest.metadata.get("published") or ingest.metadata.get("date")
                ),
                extraction_confidence=ingest.extraction_confidence,
                confidence_reason=ingest.confidence_reason,
                total_tokens_used=pro_tokens + flash_tokens,
                gemini_pro_tokens=pro_tokens,
                gemini_flash_tokens=flash_tokens,
                total_latency_ms=latency_ms,
                cod_iterations_used=0,
                self_check_missing_count=len(dv.missing_facts),
                patch_applied=False,
                structured_payload=structured_payload_extras,
                is_schema_fallback=is_schema_fallback,
                model_used=call_trace,
                fallback_reason=aggregate_fallback_reason(call_trace),
            ),
        )


register_summarizer(NewsletterSummarizer)


def _run_hallucination_guards(
    payload: NewsletterStructuredPayload,
    source_text: str,
    *,
    suppress_evaluative_strip: bool = False,
) -> dict:
    """Apply deterministic guards to brief + each detailed bullet in place.

    Aggregates banned-adjective strips and unverified-numeral flags across
    the brief and every detailed bullet. Mutates the payload (strips
    evaluative adjectives silently); numeric flags are reported only — the
    caller decides whether to invoke conditional Flash repair.
    """
    aggregate: dict = {
        "banned_adjectives_stripped": [],
        "unverified_numerals": [],
        "requires_repair": False,
    }

    guarded_brief, b_audit = apply_newsletter_guards(
        summary_text=payload.brief_summary,
        source_text=source_text,
        suppress_evaluative_strip=suppress_evaluative_strip,
    )
    payload.brief_summary = guarded_brief
    aggregate["banned_adjectives_stripped"].extend(b_audit["banned_adjectives_stripped"])
    aggregate["unverified_numerals"].extend(b_audit["unverified_numerals"])

    for section in payload.detailed_summary.sections:
        for i, bullet in enumerate(section.bullets):
            guarded_bullet, audit = apply_newsletter_guards(
                summary_text=bullet, source_text=source_text,
                suppress_evaluative_strip=suppress_evaluative_strip,
            )
            section.bullets[i] = guarded_bullet
            aggregate["banned_adjectives_stripped"].extend(
                audit["banned_adjectives_stripped"]
            )
            aggregate["unverified_numerals"].extend(audit["unverified_numerals"])

    aggregate["requires_repair"] = bool(aggregate["unverified_numerals"])
    return aggregate


def _parse_payload_with_ingest(text: str, ingest: IngestResult) -> NewsletterStructuredPayload:
    raw = parse_json_object(text)
    publication = _publication_identity_hint(ingest)
    if publication:
        raw.setdefault("detailed_summary", {})
        raw["detailed_summary"]["publication_identity"] = publication
        title = str(raw.get("mini_title") or "")
        if publication.lower() not in title.lower():
            raw["mini_title"] = f"{publication}: {title}"
    return NewsletterStructuredPayload(**raw)


def _fallback_payload(ingest: IngestResult, summary_text: str, config) -> NewsletterStructuredPayload:
    title = (
        ingest.sections.get("Title")
        or ingest.metadata.get("title")
        or ingest.metadata.get("publication_identity")
        or "Newsletter issue"
    )
    publication = str(ingest.metadata.get("publication_identity") or "").strip()
    brief = _safe_fallback_brief(ingest, summary_text)
    conclusions_section = ingest.sections.get("Conclusions", "")
    conclusions = [
        line.lstrip("- ").strip()
        for line in conclusions_section.splitlines()
        if line.strip()
    ]
    cta_section = ingest.sections.get("CTAs", "")
    cta = ""
    if cta_section:
        cta = cta_section.splitlines()[0].lstrip("- ").strip()
    if cta and re.match(r"^(subscribe|sign up|sign in)\b", cta.lower()):
        cta = ""
    payload = NewsletterStructuredPayload(
        mini_title=_fallback_title(title=str(title), publication=publication),
        brief_summary=brief,
        tags=["newsletter", "publication", "analysis", "issue", "author", "stance", "cta"],
        detailed_summary=NewsletterDetailedPayload(
            publication_identity=publication,
            issue_thesis=str(title),
            sections=[NewsletterSection(heading="Summary", bullets=[brief])],
            conclusions_or_recommendations=conclusions,
            stance=str(ingest.metadata.get("detected_stance") or "neutral"),
            cta=cta or None,
        ),
    )
    payload.tags = _normalize_tags(
        payload.tags,
        config.structured_extract.tags_min,
        config.structured_extract.tags_max,
        reserved=_brand_reserved(payload),
    )
    return payload


def _safe_fallback_brief(ingest: IngestResult, summary_text: str) -> str:
    base_parts = [
        str(ingest.sections.get("Title") or "").strip(),
        str(ingest.sections.get("Subtitle") or "").strip(),
        str(ingest.sections.get("Preheader") or "").strip(),
    ]
    prose = " ".join(part for part in base_parts if part)
    if not prose:
        prose = " ".join((summary_text or "").split())
    prose = re.sub(r"\*\*[^*]+:\*\*\s*", "", prose)
    prose = re.sub(r"\s*#\w[\w-]*", "", prose)
    prose = prose.strip() or "No summary text was available."
    return _trim_at_sentence_boundary(prose, 380)


def _apply_ingest_guardrails(
    payload: NewsletterStructuredPayload,
    ingest: IngestResult,
) -> NewsletterStructuredPayload:
    publication = _publication_identity_hint(ingest)
    if publication:
        payload.detailed_summary.publication_identity = publication
        if publication.lower() not in payload.mini_title.lower():
            payload.mini_title = f"{publication}: {payload.mini_title}"
    _remove_unsupported_numeric_claims(payload, ingest.raw_text)
    return payload


def _brand_reserved(payload: NewsletterStructuredPayload) -> list[str]:
    """Build the reserved-tag list for a newsletter payload from the
    publication identity. Returns ``[]`` when no publication is set so the
    caller falls back to legacy normalization (no forced reservation)."""
    publication = (payload.detailed_summary.publication_identity or "").strip()
    if not publication:
        return []
    slug = re.sub(r"[^a-z0-9+-]+", "-", publication.lower()).strip("-")
    return [slug] if slug else []


def _publication_identity_hint(ingest: IngestResult) -> str:
    url = ingest.url.lower()
    title = str(ingest.sections.get("Title") or ingest.metadata.get("title") or "")
    if "platformer.news" in url:
        return "Platformer"
    if "product.beehiiv.com" in url:
        return "beehiiv"
    if "organicsynthesis.beehiiv.com" in url or "organic synthesis" in title.lower():
        return "Organic Synthesis"
    if "pragmaticengineer.com" in url:
        return "Pragmatic Engineer"
    if "beehiiv.com" in url:
        return "beehiiv"
    return str(ingest.metadata.get("publication_identity") or "").strip()


# Newsletter uses the stricter (all-integer) bare-integer mode so small
# fabricated counts like "42 teams" are also flagged as unsupported. The
# evaluator default (3-digit minimum) is deliberately looser to avoid
# flagging incidental small counts in cross-source scoring.
_NL_MIN_BARE_INT_DIGITS = 1


def _remove_unsupported_numeric_claims(
    payload: NewsletterStructuredPayload,
    source_text: str,
) -> None:
    """Strip any numeric claim in the structured payload that is not
    grounded in ``source_text``. Delegates token extraction / grounding
    to the shared evaluator module so production stripping and
    post-hoc grounding scoring cannot drift apart.

    Behavior contract (preserved from the pre-delegation implementation):
      - No-op when ``source_text`` is empty / whitespace-only.
      - brief_summary: drop each sentence that contains any ungrounded
        token; fall back to the original brief if every sentence gets
        dropped (never return an empty brief).
      - section.bullets: drop ungrounded bullets; if that empties the
        section, keep one number-free bullet as a placeholder.
      - conclusions_or_recommendations: drop any item with an
        ungrounded token.
    """
    if not source_text.strip():
        return

    payload.brief_summary = _filter_unsupported_number_sentences(
        payload.brief_summary, source_text
    )
    for section in payload.detailed_summary.sections:
        filtered = [
            bullet
            for bullet in section.bullets
            if not _has_unsupported_number(bullet, source_text)
        ]
        if filtered:
            section.bullets = filtered
        else:
            number_free = [
                bullet
                for bullet in section.bullets
                if not extract_numeric_tokens(
                    bullet, min_bare_integer_digits=_NL_MIN_BARE_INT_DIGITS
                )
            ]
            section.bullets = number_free[:1]
    payload.detailed_summary.conclusions_or_recommendations = [
        item
        for item in payload.detailed_summary.conclusions_or_recommendations
        if not _has_unsupported_number(item, source_text)
    ]


def _filter_unsupported_number_sentences(text: str, source_text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    kept = [
        sentence
        for sentence in sentences
        if sentence and not _has_unsupported_number(sentence, source_text)
    ]
    return " ".join(kept) or text


def _has_unsupported_number(text: str, source_text: str) -> bool:
    _, ungrounded = ground_numeric_claims(
        text, source_text, min_bare_integer_digits=_NL_MIN_BARE_INT_DIGITS
    )
    return bool(ungrounded)


def _payload_contains_template_artifacts(payload: NewsletterStructuredPayload) -> bool:
    snippets = [payload.mini_title, payload.brief_summary]
    snippets.extend(section.heading for section in payload.detailed_summary.sections)
    for section in payload.detailed_summary.sections:
        snippets.extend(section.bullets)
    joined = " ".join(snippets).lower()
    return bool(
        "**id:**" in joined
        or "**title:**" in joined
        or "**tags:**" in joined
        or "#substack" in joined
    )


def _trim_at_sentence_boundary(text: str, max_chars: int) -> str:
    """Trim public brief text without cutting through words or sentences."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned

    candidate = cleaned[:max_chars].rstrip()
    matches = list(re.finditer(r"[.!?](?=\s|$)", candidate))
    if matches:
        last = matches[-1].end()
        if last >= max_chars * 0.55:
            return candidate[:last].strip()

    boundary = candidate.rfind(" ")
    if boundary >= max_chars * 0.55:
        return candidate[:boundary].rstrip(" ,;:")
    return candidate.rstrip(" ,;:")


def _newsletter_archetype(
    *,
    payload: NewsletterStructuredPayload,
    ingest: IngestResult,
) -> str:
    """Bridge ``archetype_from_signals`` to the summarizer call shape.

    Pulls the title, brief, and detailed bullets out of the structured
    payload (the surface the reviewer sees) plus the URL from the ingest
    so URL-path heuristics can fire. Falls through to the archetype
    module's default on anything it cannot classify.
    """
    bullets: list[str] = []
    try:
        for section in payload.detailed_summary.sections:
            for b in section.bullets:
                if b:
                    bullets.append(str(b))
        for item in payload.detailed_summary.conclusions_or_recommendations:
            if item:
                bullets.append(str(item))
    except Exception:  # noqa: BLE001 — schema drift should not crash summarizer
        bullets = []

    return _newsletter_archetype_impl(
        title=str(payload.mini_title or ""),
        brief_summary=str(payload.brief_summary or ""),
        detailed_bullets=bullets,
        url=ingest.url or "",
    )


def _fallback_title(*, title: str, publication: str) -> str:
    title = title.strip() or "Newsletter issue"
    publication = publication.strip()
    if publication and publication.lower() not in title.lower():
        return f"{publication}: {title}"[:60]
    return title[:60]
