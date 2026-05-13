"""Newsletter-specific prompt templates."""
from __future__ import annotations

SOURCE_CONTEXT = (
    "You are summarizing a newsletter issue. Preserve publication identity, "
    "issue thesis, section structure, and the author's apparent stance "
    "(optimistic/skeptical/cautionary/neutral/mixed). Distinguish "
    "conclusions/recommendations from descriptive background. Never "
    "editorialize: if the source is neutral, your summary must NOT use "
    "'bullish' or 'bearish' framing. Ignore footer boilerplate, unsubscribe "
    "language, and house style unless materially meaningful. If the source "
    "contains explicit criticism, caveats, or counterarguments, preserve them."
)

STRUCTURED_EXTRACT_INSTRUCTION = (
    f"{SOURCE_CONTEXT}\n\n"
    "Return a JSON object with these exact keys:\n"
    '- "mini_title": include publication name + thesis when publication identity is available; for branded sources (Stratechery, Platformer, etc.) publication name is mandatory (max 60 chars)\n'
    '- "brief_summary": 5-7 complete sentences, <=380 characters, covering publication identity, issue thesis, audience, major sections, CTA\n'
    '- "tags": array of 7-10 lowercase hyphenated tags; include one type/intent tag such as opinion, analysis, how-to, case-study, research-summary, interview, or announcement\n'
    '- "detailed_summary": object with keys "publication_identity", "issue_thesis", "sections" (array of objects like {{"heading": "...", "bullets": ["..."]}}), "conclusions_or_recommendations" (array with concrete recommendations/action items when present), "stance" (enum optimistic|skeptical|cautionary|neutral|mixed), "cta" (string OR null), "caveats" (array of 0-5 short verbatim or near-verbatim author-stated disclaimers, hedges, conflicts-of-interest disclosures, or scope limits — look for markers: "not paid", "no affiliate", "I should note", "caveat:", "disclosure:", "however", "that said", "to be clear", parentheticals beginning with "though", "although"; return [] if none present)\n\n'
    "Each sections[*].bullets[] array should contain 5-7 complete sentences covering "
    "the section's main claim, supporting evidence, and any caveat or counterpoint. "
    "Fewer than 5 loses nuance; more than 7 dilutes the issue. Terminate each bullet "
    "with a period.\n\n"
    "CRITICAL — Brief / caveat consistency: if you populate "
    "detailed_summary.caveats with one or more entries, the brief_summary MUST "
    "acknowledge at least one of those caveats (typically as a parenthetical or "
    "a final clause). If detailed_summary.caveats is empty, do not invent one.\n\n"
    "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
    "SUMMARY:\n{summary_text}"
)
