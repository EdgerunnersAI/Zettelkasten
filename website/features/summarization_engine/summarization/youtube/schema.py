"""Pydantic schema for YouTube-specific structured summary payload."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator
from typing_extensions import Annotated


MiniTitle = Annotated[str, StringConstraints(max_length=50)]
_TITLE_STOPWORDS = {"a", "an", "and", "for", "in", "of", "the", "to", "with"}
_SPEAKER_PLACEHOLDERS = frozenset({
    "narrator", "host", "speaker", "analyst", "commentator",
    "voiceover", "voice over", "author of the source",
    "the host", "the speaker", "the narrator", "author",
    "presenter", "the presenter", "source", "the source",
    "channel", "the channel", "uploader", "the uploader",
    "creator", "the creator", "interviewer", "the interviewer",
    "interviewee", "the interviewee", "participant", "the participant",
    "guest", "the guest", "youtuber", "the youtuber",
    "unidentified", "unidentified speaker", "unknown", "unknown speaker",
    "the author", "the video", "video", "lecture", "the lecture",
    "anonymous", "n/a", "na",
})


class ChapterBullet(BaseModel):
    timestamp: str
    title: str
    bullets: list[str] = Field(..., min_length=1)


class YouTubeDetailedPayload(BaseModel):
    thesis: str
    format: Literal[
        "tutorial",
        "interview",
        "commentary",
        "lecture",
        "review",
        "debate",
        "walkthrough",
        "reaction",
        "vlog",
        "other",
    ]
    chapters_or_segments: list[ChapterBullet] = Field(..., min_length=1)
    demonstrations: list[str] = Field(default_factory=list)
    closing_takeaway: str


class YouTubeStructuredPayload(BaseModel):
    mini_title: MiniTitle
    brief_summary: str
    tags: list[str] = Field(..., min_length=7, max_length=10)
    speakers: list[str] = Field(..., min_length=1)
    guests: list[str] | None = None
    entities_discussed: list[str] = Field(default_factory=list)
    detailed_summary: YouTubeDetailedPayload
    # H2/C2: first-class attribution confidence (mirrors OpenAI structured-outputs
    # `refusal` placement). "high" = all real names, "low" = mixed real+placeholder,
    # "missing" = all-placeholder or fell back to channel/neutral label.
    attribution_confidence: Literal["high", "low", "missing"] = "high"

    @model_validator(mode="after")
    def _normalize_note_facing_fields(self) -> "YouTubeStructuredPayload":
        self.detailed_summary.format = _normalize_format_name(
            self.detailed_summary.format,
            brief=self.brief_summary,
            thesis=self.detailed_summary.thesis,
            chapter_titles=[item.title for item in self.detailed_summary.chapters_or_segments],
            speakers=list(self.speakers or []),
        )
        self.mini_title = _normalize_mini_title(self.mini_title)
        self.tags = _ensure_format_tag(self.tags, self.detailed_summary.format)
        self.brief_summary = _repair_brief_summary(
            brief=self.brief_summary,
            format_name=self.detailed_summary.format,
            thesis=self.detailed_summary.thesis,
            speakers=self.speakers,
            entities=self.entities_discussed,
            chapter_titles=[item.title for item in self.detailed_summary.chapters_or_segments],
            demonstrations=list(self.detailed_summary.demonstrations or []),
            closing_takeaway=self.detailed_summary.closing_takeaway,
        )
        return self

    @model_validator(mode="after")
    def _sanitize_speakers(self) -> "YouTubeStructuredPayload":
        """4-step speaker fallback (see docs/summary_eval/_synthesis.md P5).

        Step 1: strip placeholder tokens (``unidentified host``, etc.) AND
                geographic/landmark entities (``Strait of Hormuz``,
                ``Gulf of Aden``). A place name is never a speaker; if the
                model emitted one, treat it as a hallucinated slot fill
                and drop it so the fallback chain engages instead.
        Step 2: if any real names survive, use them as-is.
        Step 3: fall back to the first named human in ``entities_discussed``
                (a speaker-like entity is almost always a transcript named
                subject; this catches channels where the uploader name
                doubled up in entities). Geographic entities are skipped
                here too.
        Step 4: if nothing plausible is found, use the neutral label
                ``"The speaker"`` rather than dropping the structured
                payload.

        Rationale (iter-20 regression): YouTube iter-20 output
        ``speakers: ["Strait of Hormuz"]`` on the Petrodollar lecture
        because the model borrowed a prominent location from the brief.
        ``_is_placeholder_speaker`` only rejected role nouns, so the
        string survived, the evaluator fired ``speakers_absent``, and
        the rubric clamped the composite to 75 despite a raw score of
        90. Filtering geographic entities at step 1 lets the fallback
        chain surface a real human (via ``entities_discussed``) or
        degrade cleanly to the neutral label, which the evaluator
        scores as "no speakers identified" rather than "wrong speaker".
        """
        raw_speakers = [s for s in (self.speakers or []) if isinstance(s, str) and s.strip()]
        real = [
            s.strip()
            for s in raw_speakers
            if not _is_placeholder_speaker(s.strip())
            and not _is_geographic_entity(s.strip())
            and not _is_non_human_speaker_entity(s.strip())
        ]
        # H2/C2: detect mixed real+placeholder for the "low" tier.
        had_placeholder = any(
            _is_placeholder_speaker(s.strip()) or _is_geographic_entity(s.strip())
            for s in raw_speakers
        )
        if real:
            self.speakers = real
            self.attribution_confidence = "low" if had_placeholder else "high"
            return self

        # Step 3: first named human entity in entities_discussed.
        for entity in (self.entities_discussed or []):
            if (
                isinstance(entity, str)
                and _looks_like_named_human(entity)
                and not _is_geographic_entity(entity)
                and not _is_non_human_speaker_entity(entity)
            ):
                self.speakers = [entity.strip()]
                # Coerced from entities — original speakers were all placeholders.
                self.attribution_confidence = "missing"
                return self

        # Step 4: deterministic neutral label.
        self.speakers = ["The speaker"]
        self.attribution_confidence = "missing"
        return self


_PLACEHOLDER_ADJECTIVES = frozenset({
    "unidentified", "unknown", "anonymous", "unnamed", "generic",
})


_GEOGRAPHIC_HEAD_NOUNS = frozenset({
    "strait", "straits", "gulf", "bay", "peninsula", "river", "sea",
    "ocean", "lake", "mount", "mountain", "mountains", "valley",
    "desert", "island", "islands", "archipelago", "cape", "channel",
    "fjord", "harbor", "harbour", "delta", "plateau", "basin",
    "canyon", "forest", "plain", "plains", "coast", "coastline",
    "shore", "province", "state", "county", "district", "territory",
    "region", "republic", "kingdom", "empire", "federation", "country",
    "continent", "city", "town", "village", "capital", "borough",
    "street", "avenue", "road", "highway", "boulevard", "lane",
    "bridge", "tunnel", "square", "plaza", "park", "stadium",
})

_GEOGRAPHIC_PHRASE_RE = re.compile(
    r"(?i)^\s*(the\s+)?("
    r"strait|straits|gulf|bay|peninsula|river|sea|ocean|lake|mount|"
    r"mountain|mountains|valley|desert|island|islands|archipelago|cape|"
    r"channel|fjord|harbor|harbour|delta|plateau|basin|canyon|forest|"
    r"plain|plains|coast|coastline|shore|province|state|county|"
    r"district|territory|region|republic|kingdom|empire|federation|"
    r"country|continent|city|town|village|capital|borough|street|"
    r"avenue|road|highway|boulevard|lane|bridge|tunnel|square|plaza|"
    r"park|stadium"
    r")\b[\s\w\-,']*$"
)

# "X of Y" / "X of the Y" pattern where the head noun is a geographic
# generic (e.g., "Strait of Hormuz", "Gulf of Aden", "Bay of Bengal",
# "Republic of Korea"). Matching is anchored so partial hits inside a
# legit name do not fire.
_GEOGRAPHIC_OF_PHRASE_RE = re.compile(
    r"(?i)^\s*(the\s+)?(?P<head>\w+)\s+of\s+(the\s+)?\w[\w\s\-']*$"
)

_NON_HUMAN_SPEAKER_TERMS = frozenset({
    "anti-chess",
    "chataranga",
    "chess",
    "declined",
    "defence",
    "defense",
    "fischer",
    "gambit",
    "opening",
    "shatranj",
})


def _is_geographic_entity(name: str) -> bool:
    """Return True if ``name`` looks like a location, landmark, or
    geopolitical entity rather than a human speaker.

    Heuristic (pure regex; no NER dependency):
      1. Leading "<head-noun> ..." where head-noun is a geographic
         generic — matches "Strait of Hormuz", "Gulf of Aden",
         "Mount Everest", "River Thames", "Lake Tahoe".
      2. "<something> of <something>" where the head-noun token is a
         geographic generic — matches the same set and also catches
         "Kingdom of Saudi Arabia", "Republic of Korea".
      3. Single-token lowercase place name — already caught by the
         named-human filter but re-asserted here for clarity.

    This is defense-in-depth on top of the prompt instruction. False
    positives are benign: if a real human happens to be named "Bay
    Jordan", step 2/3 will drop them but step 4 supplies ``"The
    speaker"`` — the evaluator scores that as a neutral miss, not a
    hallucination. False negatives are the real risk (see residual
    risks in the PR description).
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    # Short-circuit: if any whitespace-separated token in the phrase is a
    # geographic generic noun, treat the whole string as a location. This
    # catches leading-head ("Strait of Hormuz", "Mount Everest"),
    # trailing-head ("Persian Gulf", "Arabian Sea"), and embedded-head
    # ("Kingdom of Saudi Arabia") forms in one pass. Tokens are stripped
    # of trailing punctuation so "Gulf," matches too.
    tokens = [re.sub(r"[^\w]", "", tok).lower() for tok in lowered.split()]
    for tok in tokens:
        if tok and tok in _GEOGRAPHIC_HEAD_NOUNS:
            return True
    # "X of Y" form (e.g., "Republic of Korea") where the head is a
    # geographic generic — already covered by the token loop above, kept
    # here as a guard against future head-noun list changes.
    of_match = _GEOGRAPHIC_OF_PHRASE_RE.match(cleaned)
    if of_match and of_match.group("head").lower() in _GEOGRAPHIC_HEAD_NOUNS:
        return True
    # Full-phrase regex (belt-and-suspenders for multi-token forms).
    if _GEOGRAPHIC_PHRASE_RE.match(cleaned):
        return True
    return False


def _is_non_human_speaker_entity(name: str) -> bool:
    """Return True for titled concepts that look person-like but are topics.

    Some YouTube transcripts mention capitalized game openings, works, or
    concepts often enough that the model tries to use them as speakers. A
    neutral/no-speaker fallback is better than rendering a topic as a human.
    """
    cleaned = (name or "").strip().lower()
    if not cleaned:
        return False
    tokens = {re.sub(r"[^\w-]", "", tok) for tok in cleaned.split()}
    return any(tok in _NON_HUMAN_SPEAKER_TERMS for tok in tokens)


def _looks_like_named_human(name: str) -> bool:
    """Heuristic check: does ``name`` look like a real person's name?

    Used as step 3 of the speaker fallback in ``_sanitize_speakers``. A
    named human usually has two or more whitespace-separated tokens with
    leading capitals, and must not trip the placeholder-speaker detector.
    Organization names ("MAPS", "Google") are filtered by requiring at
    least one token longer than 2 characters that is NOT fully uppercase.
    """
    cleaned = (name or "").strip()
    if not cleaned or _is_placeholder_speaker(cleaned) or _is_non_human_speaker_entity(cleaned):
        return False
    tokens = cleaned.split()
    if len(tokens) < 2:
        return False
    capitalized = [t for t in tokens if t[:1].isupper()]
    if len(capitalized) < 2:
        return False
    # Reject all-caps acronyms like "MAPS" or "NIH" even in a pair.
    if all(t.isupper() for t in tokens):
        return False
    return True


def _is_placeholder_speaker(name: str) -> bool:
    """Return True if ``name`` is a role noun, placeholder label, or empty."""
    lowered = (name or "").strip().lower()
    if not lowered or lowered in _SPEAKER_PLACEHOLDERS:
        return True
    tokens = lowered.split()
    if tokens and tokens[0] in _PLACEHOLDER_ADJECTIVES:
        return True
    # "The <role>" pattern already covered by set; also catch bare role nouns
    role_nouns = {
        "narrator", "host", "speaker", "presenter", "commentator",
        "interviewer", "interviewee", "guest", "participant", "creator",
        "uploader", "channel", "youtuber", "author", "analyst", "source",
        "lecturer", "instructor", "teacher",
    }
    if len(tokens) == 1 and tokens[0] in role_nouns:
        return True
    if len(tokens) == 2 and tokens[0] in {"the", "a", "an"} and tokens[1] in role_nouns:
        return True
    return False


_SCAFFOLD_SENTENCE_PREFIXES = (
    r"^the closing takeaway[:\-]\s*",
    r"^closing takeaway[:\-]\s*",
    r"^in conclusion[,:\-]\s*",
    r"^to summarize[,:\-]\s*",
    r"^overall[,:\-]\s*",
)
_PLACEHOLDER_SPEAKER_PHRASE = re.compile(
    r"\b("
    r"(?:the\s+)?unidentified\s+(?:speaker|presenter|narrator|host|interviewer|lecturer|author)"
    r"|(?:the\s+)?unknown\s+(?:speaker|presenter|narrator|host|author)"
    r"|(?:the\s+)?anonymous\s+(?:speaker|presenter|narrator|host|author)"
    r")\b",
    re.IGNORECASE,
)


def _strip_scaffold_phrases(text: str) -> str:
    """Remove LLM scaffolding artifacts from the brief.

    Targets two recurring regressions:
      1. ``"The closing takeaway:"`` / ``"In conclusion:"`` framing prefixes
         that announce the summary's structure rather than speaking
         naturally.
      2. Placeholder speaker phrases like ``"Unidentified Presenter"`` that
         leak into the brief when the LLM cannot identify the speaker.
    """
    if not text:
        return text
    cleaned = text
    # Strip markdown heading prefixes ("### Zettelkasten Note: ...") and
    # inline bold markers ("**word**" -> "word") that the model sometimes
    # echoes back from its system prompt framing.
    cleaned = re.sub(r"^#{1,6}\s+[^\n]+?:\s*", "", cleaned).strip()
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned).strip()
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", cleaned)
    cleaned = _PLACEHOLDER_SPEAKER_PHRASE.sub("The speaker", cleaned)
    sentences = _split_sentences(cleaned)
    out: list[str] = []
    for sentence in sentences:
        s = sentence.strip()
        for pattern in _SCAFFOLD_SENTENCE_PREFIXES:
            s = re.sub(pattern, "", s, flags=re.IGNORECASE).strip()
        if s:
            if s[:1].islower():
                s = s[:1].upper() + s[1:]
            out.append(s)
    rebuilt = " ".join(out).strip()
    return rebuilt or cleaned


def _normalize_mini_title(title: str) -> str:
    # Allow intra-word apostrophes (ASCII ``'`` and curly ``\u2019``) and
    # hyphens so ``Petrodollar's`` stays one token instead of splitting
    # into ``Petrodollar`` + ``s`` (iter-20 regression: the stopword
    # filter then dropped the stray ``s`` and the mini_title read
    # ``"Petrodollar s Decline US Dominance"``).
    raw_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\u2019\-]*", title or "")
    # Strip leading/trailing apostrophes/hyphens from each token; a bare
    # apostrophe left over from odd punctuation ("'" -> "") is dropped.
    tokens = [t.strip("'\u2019-") for t in raw_tokens]
    tokens = [t for t in tokens if t]
    preferred = [token for token in tokens if token.lower() not in _TITLE_STOPWORDS]
    words = preferred if len(preferred) >= 3 else tokens
    normalized = " ".join(words[:5]).strip()
    return normalized[:50] or "YouTube Summary"


def _ensure_format_tag(tags: list[str], format_name: str) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        cleaned = str(tag).strip().lower().replace(" ", "-")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    format_tag = format_name.strip().lower()
    if format_tag and format_tag not in normalized:
        if len(normalized) >= 10:
            normalized = normalized[:-1]
        normalized.append(format_tag)
    return normalized[:10]


def _repair_brief_summary(
    *,
    brief: str,
    format_name: str,
    thesis: str,
    speakers: list[str],
    entities: list[str],
    chapter_titles: list[str],
    demonstrations: list[str],
    closing_takeaway: str,
) -> str:
    """Accept the LLM brief as-is when it already looks natural and
    hits the rubric target length. Otherwise extend or rebuild from
    structured fields so the brief always lands in the 5–7 sentence
    window without truncating mid-clause.

    Regression note (iter-06 → iter-09):
      * iter-07 dropped brief.length_5_to_7_sentences to 0/2 because a
        2-sentence LLM brief passed the prior ``>= 2`` gate and was
        returned verbatim. The gate now accepts 5–7 sentences; shorter
        complete briefs are *extended* with structured facts (never
        truncated).
      * iter-08 cratered when placeholder speakers raised a
        ValidationError → schema_fallback → raw output → hallucination
        cap. That is handled by ``_sanitize_speakers`` coercion.
    """
    from website.features.summarization_engine.summarization.common.text_guards import (
        ends_with_dangling_word,
        repair_or_drop,
    )

    cleaned = re.sub(r"\s+", " ", brief or "").strip()
    cleaned = _strip_scaffold_phrases(cleaned)
    sentences = _split_sentences(cleaned)
    tail_ok = bool(sentences) and not ends_with_dangling_word(sentences[-1])

    # Path 1: LLM brief already hits the 5–7 sentence target window.
    if (
        cleaned
        and cleaned[-1] in ".!?"
        and 5 <= len(sentences) <= 7
        and len(cleaned) <= 500
        and tail_ok
    ):
        return cleaned

    # Path 2: brief is coherent (2–4 complete sentences) but short of
    # the rubric target — extend with structured sentences rather than
    # discard the natural paragraph.
    if (
        cleaned
        and cleaned[-1] in ".!?"
        and 2 <= len(sentences) < 5
        and len(cleaned) <= 500
        and tail_ok
    ):
        extended = _extend_brief_with_structured(
            sentences=sentences,
            format_name=format_name,
            speakers=speakers,
            entities=entities,
            chapter_titles=chapter_titles,
            demonstrations=demonstrations,
            closing_takeaway=closing_takeaway,
        )
        if extended:
            return extended

    # Path 3: brief has too many sentences — clip to 7 whole ones.
    if (
        cleaned
        and cleaned[-1] in ".!?"
        and len(sentences) > 7
        and tail_ok
    ):
        clipped = " ".join(sentences[:7]).strip()
        if 0 < len(clipped) <= 500:
            return clipped

    # Path 4: brief looks dangling but is terminated — last-chance repair.
    already_terminated = bool(cleaned) and cleaned[-1] in ".!?"
    if already_terminated:
        repaired = repair_or_drop(cleaned)
        if repaired:
            repaired_sentences = _split_sentences(repaired)
            if (
                5 <= len(repaired_sentences) <= 7
                and len(repaired) <= 500
                and not ends_with_dangling_word(repaired_sentences[-1])
            ):
                return repaired

    # Path 5: full rebuild from structured fields (broken/empty LLM brief).
    return _compose_structured_brief(
        format_name=format_name,
        thesis=thesis,
        speakers=speakers,
        entities=entities,
        chapter_titles=chapter_titles,
        demonstrations=demonstrations,
        closing_takeaway=closing_takeaway,
    )


def _compose_structured_brief(
    *,
    format_name: str,
    thesis: str,
    speakers: list[str],
    entities: list[str],
    chapter_titles: list[str],
    demonstrations: list[str],
    closing_takeaway: str,
) -> str:
    speaker = _primary_speaker(speakers) or "The speaker"
    parts: list[str] = []

    thesis_sentence = _first_sentence(thesis)
    if thesis_sentence:
        parts.append(
            f"In this {format_name}, {speaker} argues that "
            f"{thesis_sentence.lower().rstrip('.')}."
        )
    else:
        parts.append(f"This {format_name} is delivered by {speaker}.")

    titles = [t for t in (chapter_titles or []) if t and t.strip()][:3]
    if titles:
        parts.append(f"The {format_name} moves through {_join_series(titles)}.")

    demos = [d for d in (demonstrations or []) if d and d.strip()][:2]
    if demos:
        parts.append(f"It walks through {_join_series(demos)}.")

    entity_text = [e for e in (entities or []) if e and e.strip()][:3]
    if entity_text:
        parts.append(f"Along the way {speaker} references {_join_series(entity_text)}.")

    closing_sentence = _first_sentence(closing_takeaway)
    if closing_sentence:
        parts.append(f"The closing point is that {closing_sentence.lower().rstrip('.')}.")

    return _fit_parts_to_budget(parts)


def _extend_brief_with_structured(
    *,
    sentences: list[str],
    format_name: str,
    speakers: list[str],
    entities: list[str],
    chapter_titles: list[str],
    demonstrations: list[str],
    closing_takeaway: str,
) -> str:
    """Append structured sentences until the brief reaches 5 sentences.

    Only adds content that is not already mentioned in the natural
    brief, so the extension reads as continuation rather than
    duplication.
    """
    existing = " ".join(sentences).lower()
    extensions: list[str] = []
    target = 5 - len(sentences)
    if target <= 0:
        return " ".join(sentences).strip()

    def already_mentioned(snippet: str) -> bool:
        token = snippet.strip().lower().rstrip(".")
        return bool(token) and token in existing

    titles = [t for t in (chapter_titles or []) if t and t.strip()][:3]
    if titles and len(extensions) < target:
        unseen = [t for t in titles if not already_mentioned(t)]
        if unseen:
            extensions.append(
                f"The {format_name} moves through {_join_series(unseen)}."
            )

    demos = [d for d in (demonstrations or []) if d and d.strip()][:2]
    if demos and len(extensions) < target:
        unseen = [d for d in demos if not already_mentioned(d)]
        if unseen:
            extensions.append(f"It walks through {_join_series(unseen)}.")

    speaker = _primary_speaker(speakers)
    entity_text = [e for e in (entities or []) if e and e.strip()][:3]
    if entity_text and len(extensions) < target:
        unseen = [e for e in entity_text if not already_mentioned(e)]
        if unseen:
            subject = speaker or "the speaker"
            extensions.append(f"Along the way {subject} references {_join_series(unseen)}.")

    closing_sentence = _first_sentence(closing_takeaway)
    if closing_sentence and len(extensions) < target and not already_mentioned(closing_sentence):
        extensions.append(
            f"The closing point is that {closing_sentence.lower().rstrip('.')}."
        )

    if not extensions:
        return ""

    combined = (" ".join(sentences) + " " + " ".join(extensions)).strip()
    if len(combined) <= 500:
        return combined

    # Budget overflow — keep original brief plus as many extensions as fit.
    acc = " ".join(sentences).strip()
    for ext in extensions:
        candidate = (acc + " " + ext).strip()
        if len(candidate) > 500:
            break
        acc = candidate
    return acc


def _fit_parts_to_budget(parts: list[str]) -> str:
    rebuilt = " ".join(parts).strip()
    if rebuilt and len(rebuilt) <= 500:
        return rebuilt
    # Drop the least load-bearing sentences first (entity ref, then walkthrough).
    for drop_index in (3, 2, 1):
        if drop_index < len(parts):
            trimmed = " ".join(parts[:drop_index] + parts[drop_index + 1 :]).strip()
            if len(trimmed) <= 500:
                return trimmed
    fallback = " ".join([p for p in (parts[0], parts[-1]) if p]) if parts else ""
    return fallback[:500].rstrip()


def _primary_speaker(speakers: list[str]) -> str:
    for speaker in speakers or []:
        name = re.sub(r"\s+", " ", (speaker or "")).strip()
        if name and not _is_placeholder_speaker(name):
            return name
    return ""


def _split_sentences(text: str) -> list[str]:
    # Delegate to the shared abbreviation-aware splitter so ``U.S.``,
    # ``Dr.``, ``e.g.``, ``3.14`` etc. never produce spurious fragments.
    from website.features.summarization_engine.summarization.common.text_guards import (
        split_sentences as _shared_split,
    )
    return _shared_split(text or "")


def _first_sentence(text: str) -> str:
    sentences = _split_sentences(text)
    if sentences:
        return sentences[0]
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    return cleaned


def _join_series(items: list[str]) -> str:
    vals = [re.sub(r"\s+", " ", i).strip() for i in items if i and i.strip()]
    if not vals:
        return ""
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2:
        return f"{vals[0]} and {vals[1]}"
    return ", ".join(vals[:-1]) + f", and {vals[-1]}"


def _normalize_format_name(
    format_name: str,
    *,
    brief: str,
    thesis: str,
    chapter_titles: list[str],
    speakers: list[str] | None = None,
) -> str:
    """Preserve any valid LLM-supplied label verbatim; otherwise defer
    to the confidence-scored :func:`classify_format` heuristic so
    downstream never sees ``"other"``.
    """
    cleaned = (format_name or "").strip().lower()
    if cleaned and cleaned != "other":
        return cleaned

    # Lazy import keeps the module import graph small and avoids a
    # circular dependency if the classifier ever needs schema types.
    from website.features.summarization_engine.summarization.youtube.format_classifier import (
        classify_format,
    )

    label, _confidence = classify_format(
        title=thesis or "",
        description=brief or "",
        chapter_titles=list(chapter_titles or []),
        speakers=list(speakers or []),
    )
    return label


def _join_items(items: list[str]) -> str:
    values = [re.sub(r"\s+", " ", item).strip() for item in items if item and item.strip()]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return f"{values[0]} and {values[1]}"


def _select_figures_for_brief(speakers: list[str], entities: list[str]) -> list[str]:
    values = [re.sub(r"\s+", " ", item).strip() for item in speakers if item and item.strip()]
    meaningful = [
        value
        for value in values
        if value.lower() not in {"narrator", "author of the source"}
    ]
    if len(meaningful) >= 2:
        return meaningful[-2:]
    if len(meaningful) == 1:
        fallback_entities = [
            re.sub(r"\s+", " ", item).strip()
            for item in entities
            if item and item.strip() and item.strip() != meaningful[0]
        ]
        if fallback_entities:
            return [meaningful[0], fallback_entities[0]]
        return meaningful
    fallback_entities = [
        re.sub(r"\s+", " ", item).strip()
        for item in entities
        if item and item.strip()
    ]
    return fallback_entities[:2] or values[:2] or ["the source material"]


def _as_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip().rstrip(",;:")
    if not cleaned:
        return ""
    if cleaned.endswith((".", "!", "?")):
        return cleaned
    return f"{cleaned}."


def _trim_fragment(text: str, max_words: int) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip().rstrip(",;:")
    for marker in ("; ", ", but ", ", while ", ", which ", ", and "):
        if marker in cleaned:
            head = cleaned.split(marker, 1)[0].strip().rstrip(",;:")
            if 4 <= len(re.findall(r"\S+", head)) <= max_words:
                return head
    words = re.findall(r"\S+", cleaned)
    if len(words) <= max_words:
        return " ".join(words).rstrip(",;:")
    return " ".join(words[:max_words]).rstrip(",;:")


def _fit_brief_sentences(sentences: list[str], *, max_chars: int) -> str:
    fitted: list[str] = []
    for sentence in sentences:
        candidate = _as_sentence(sentence)
        if not candidate:
            continue
        joined = " ".join([*fitted, candidate]).strip()
        if len(joined) <= max_chars:
            fitted.append(candidate)
            continue

        remaining = max_chars - len(" ".join(fitted)) - (1 if fitted else 0)
        if remaining <= 8:
            break
        trimmed = _as_sentence(_trim_fragment(candidate, max(3, remaining // 7)))
        joined = " ".join([*fitted, trimmed]).strip()
        if len(joined) <= max_chars:
            fitted.append(trimmed)
        break
    return " ".join(fitted).strip()
