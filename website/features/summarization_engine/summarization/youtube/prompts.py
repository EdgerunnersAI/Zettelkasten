"""YouTube-specific prompt templates.

The base ``STRUCTURED_EXTRACT_INSTRUCTION`` is format-agnostic. When upstream
routing has classified the video format (documentary, commentary, lecture,
explainer, interview), :func:`select_youtube_prompt` returns a prompt that
prepends a short format-specific guidance block to the base instruction so the
model emphasises the right structural beats (chapters for lectures, the
guest's central claim for interviews, etc.) without changing the JSON schema
the structured extractor expects.
"""
from __future__ import annotations

SOURCE_CONTEXT = (
    "You are summarizing a YouTube video. Write in natural, complete sentences "
    "— never templated phrasing. Always identify the speaker or host BY NAME "
    "(e.g. 'Steve Jobs at Stanford', not 'the speaker'). Preserve chronological "
    "structure and chapter order. Identify the central thesis, the speaker and "
    "any guests, named products/libraries/tools/datasets referenced, and the "
    "closing takeaway. When examples or analogies are used, summarize their "
    "PURPOSE, not their verbatim content. Do not retain clickbait phrasing."
)


_FORMAT_GUIDANCE: dict[str, str] = {
    "documentary": (
        "FORMAT: documentary. Emphasize the narrative arc, the central "
        "subject under investigation, named on-screen sources, and the "
        "narrator's framing. Treat archival footage and interview clips as "
        "supporting evidence; cite the on-camera speaker by name when given."
    ),
    "commentary": (
        "FORMAT: commentary / review / reaction. Emphasize the host's "
        "central opinion or verdict, the artefact being commented on, and "
        "the strongest 2-3 supporting arguments. Do not present commentary "
        "as factual reporting."
    ),
    "lecture": (
        "FORMAT: lecture / talk. Emphasize the structural outline (sections, "
        "chapters, slides), the lecturer's central thesis, and the named "
        "concepts / definitions / theorems introduced. Preserve order — a "
        "lecture's argument depends on it."
    ),
    "explainer": (
        "FORMAT: explainer / tutorial / walkthrough. Emphasize the problem "
        "being solved, the step-by-step procedure, and the named tools or "
        "commands shown. The reader should be able to reproduce the gist of "
        "the workflow from the summary alone."
    ),
    "interview": (
        "FORMAT: interview / podcast / Q&A. Identify the host AND the "
        "guest(s) by name in every relevant field. Emphasize the guest's "
        "central claims, their named projects/companies/papers, and the "
        "questions the host actually asked rather than the host's own views."
    ),
}

STRUCTURED_EXTRACT_INSTRUCTION = (
    f"{SOURCE_CONTEXT}\n\n"
    "Return a JSON object with these exact keys:\n"
    '- "mini_title": 3-5 word content-first title (max 50 chars); NO clickbait\n'
    '- "brief_summary": EXACTLY 5-7 COMPLETE sentences (<=490 chars). Natural prose. '
    'MUST name the primary speaker/host. Cover: (1) what the video is + format, '
    '(2) who delivers it, (3) the central thesis, (4) the main segments walked '
    'through, (5) named tools/entities/demos, (6) the closing takeaway. Every '
    'sentence must end properly — never trail off mid-clause.\n'
    '- "tags": 7-10 lowercase hyphenated tags (topic/domain, creator or channel, '
    'format, named tools/concepts, audience)\n'
    '- "speakers": array of strings — ONLY named humans, organizational '
    'roles (e.g., "Narrator", "Host"), or named organizations '
    '(e.g., "CNBC", "The Economist"). NEVER locations, landmarks, '
    'rivers, straits, gulfs, peninsulas, waterways, countries, cities, '
    'or any geographic entity. If the speaker is not explicitly named '
    'in the transcript, use "The speaker" — do not invent a name and '
    'do not reuse a prominent entity from the content as a speaker. '
    'At least one entry required.\n'
    '- "guests": array of strings OR null\n'
    '- "entities_discussed": array of named products/libraries/datasets/tools\n'
    '- "detailed_summary": object with keys "thesis" (1-2 sentences), "format" '
    '(enum tutorial|interview|commentary|lecture|review|debate|walkthrough|'
    'reaction|vlog|other), "chapters_or_segments" (array of {timestamp, title, '
    'bullets}). Emit 3-7 chapters for videos 15+ minutes, 2-4 for shorter '
    'videos. EACH chapter needs 5-7 bullets of concrete, grounded claims from '
    'that segment (no verbatim quotes, no speculation). If you cannot support '
    '5 bullets for a chapter, drop the chapter entirely. Bullets are complete '
    'sentences ending in terminal punctuation, no trailing fragments, no JSON. '
    'Chapter titles must be short noun phrases. If the source has no explicit '
    'chapter headings, use neutral labels like "Segment 1", "Segment 2" '
    'instead of inventing editorial chapter names. Prefer real timestamps from '
    'the transcript (e.g. "04:12"). If no timestamp is known, set "timestamp" '
    'to an empty string — NEVER emit "00:00" as filler. "demonstrations" '
    '(array of strings describing any hands-on demos/examples), '
    '"closing_takeaway" (1-2 sentences).\n\n'
    "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
    "SUMMARY:\n{summary_text}"
)


def select_youtube_prompt(format_label: str | None) -> str:
    """Return a structured-extract prompt tuned for ``format_label``.

    ``format_label`` is one of the labels emitted by
    :func:`...youtube.format_classifier.classify_format`. Unknown / empty
    labels fall through to the base format-agnostic prompt so behaviour is
    backward-compatible with call sites that do not yet route by format.
    """
    guidance = _FORMAT_GUIDANCE.get((format_label or "").strip().lower())
    if not guidance:
        return STRUCTURED_EXTRACT_INSTRUCTION
    return f"{guidance}\n\n{STRUCTURED_EXTRACT_INSTRUCTION}"
