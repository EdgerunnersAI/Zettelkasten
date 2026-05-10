"""Airtight summary normalization at the API boundary.

The zettel modal reads ``node.summary`` and expects a predictable shape. Over
the life of this project three writer paths have landed rows in the
canonical-summary column (v1 ``kg_nodes.summary`` jsonb, dropped in Phase 6;
now ``content.workspace_zettels.ai_summary`` text per Phase 3.1 of the v2
purge — see ``website/features/summarization_engine/writers/supabase.py``)
with four different shapes:

1. Canonical JSON envelope with ``detailed_summary`` as a list of structured
   sections (``[{heading, bullets, sub_sections}, ...]``) — the post-fix
   shape from ``website.core.persist`` and the new ``SupabaseWriter``.
2. Canonical envelope but ``detailed_summary`` is a **source-shaped dict**
   (e.g. newsletter ``{publication_identity, issue_thesis, sections, ...}``
   or youtube ``{thesis, format, chapters_or_segments, closing_takeaway}``).
3. Canonical envelope but ``detailed_summary`` is a **markdown string**
   with raw schema-key headings (``## thesis`` / ``## chapters_or_segments``)
   and chapter bullets serialized as ``{"timestamp": "...", "title": "...",
   "bullets": [...]}`` JSON strings.
4. A plain string that isn't JSON at all (legacy pre-envelope rows).

Rather than teaching every frontend renderer to defend against all four, we
normalize once — server-side — so the wire always carries shape (1).

This module exposes ``normalize_summary_for_wire(raw, source_type)`` which
returns the envelope's canonical JSON string back, plus
``normalize_graph_nodes(graph_dict)`` which mutates every node in place.

No LLM calls. Pure dict/string transforms. Idempotent — running it twice on
a canonical row is a no-op.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

from website.core.text_polish import polish, polish_envelope, rewrite_tags


# Raw pipeline schema keys → human-facing labels rendered in the zettel modal.
_RAW_HEADING_MAP: dict[str, str] = {
    "thesis": "Core argument",
    "core_argument": "Core argument",
    "issue_thesis": "Core argument",
    "overview": "Overview",
    "format": "Format",
    "format_and_speakers": "Format and speakers",
    "chapters_or_segments": "Chapter walkthrough",
    "chapter_walkthrough": "Chapter walkthrough",
    "demonstrations": "Demonstrations",
    "closing_takeaway": "Closing remarks",
    "closing remarks": "Closing remarks",
    "closing_remarks": "Closing remarks",
    "publication_identity": "Publication identity",
    "sections": "Sections",
    "conclusions_or_recommendations": "Conclusions & recommendations",
    "cta": "Call to action",
    "stance": "Stance",
    "op_thesis": "OP thesis",
    "dissent_cluster": "Dissent",
    "agreement_cluster": "Agreement",
}

_TIMESTAMP_PATTERNS = [
    re.compile(r"^\s*\[?\d{1,2}(?::\d{2}){1,2}\]?\s*[—\-:]\s*"),
    re.compile(r"^\s*\[?\d{1,2}(?::\d{2}){1,2}\]?\s+"),
    re.compile(r"^\s*\d{4}\s*[—\-]\s*"),  # "1852 — Title"
    # Drop placeholder timestamps that survive when the source has no
    # real timing data: "N/A — Title", "n/a: Title", "- Title", "none — Title"
    re.compile(r"^\s*(?:n\s*/?\s*a|none|null|-{1,3}|–|—)\s*[—\-:]\s+", re.IGNORECASE),
    re.compile(r"^\s*(?:n\s*/?\s*a|none|null)\s+", re.IGNORECASE),
]

_DROP_HEADING_LOWER = {
    "format",              # surfaced as a bullet, never a section
    "moderation context",  # internal ingest signal — users don't need to see it
    "moderation_context",
}

# Sub-section headings that should never appear nested under any parent
# section, regardless of parent name. ``Overview`` and ``Summary`` are the
# anchors of the modal — they exist as top-level h2s, not as children.
_DROP_SUB_HEADING_LOWER = {"overview", "summary"}

# Lead-in fragments writers attach to a thesis bullet:
#   "In this lecture, Andrej Karpathy argues that <thesis>"
#   "In this commentary, the speaker posits that <thesis>"
# These are stripped before redundancy comparison so the substring check
# can match the lifted thesis against the same thesis re-emitted as a
# "Core argument" sub. Also stripped from the rendered top-level bullet
# itself so the Format-and-speakers sub isn't redundant.
_LEAD_IN_RE = re.compile(
    r"^\s*in\s+this\s+(?:lecture|commentary|video|talk|episode|interview|"
    r"paper|article|piece|issue|post|thread|essay|newsletter|review|"
    r"discussion|panel|address|speech|sermon|debate|keynote|tutorial|"
    r"podcast|stream|broadcast|segment|presentation|chapter|session)"
    r"[^,]{0,80},\s*"
    r"(?:the\s+(?:speaker|author|host|presenter|panel|interviewer|"
    r"interviewee|moderator|guest|narrator|writer)|[^,]{0,60}?)\s+"
    r"(?:argues?|posits?|claims?|states?|explains?|shows?|demonstrates?|"
    r"reports?|highlights?|examines?|asserts?|contends?|maintains?|"
    r"reveals?|describes?|outlines?|presents?|discusses?|reflects?)\s+that\s+",
    re.IGNORECASE,
)

# Tokens that indicate a "speaker" string is actually a User-Agent / OS /
# browser leak rather than a human name. Matched case-insensitively against
# either the full string or any whitespace token within it.
_NON_HUMAN_SPEAKER_TOKENS = frozenset({
    "mac", "macos", "macintosh", "windows", "linux", "ubuntu", "debian",
    "android", "ios", "iphone", "ipad", "chrome", "chromium", "safari",
    "firefox", "edge", "opera", "mozilla", "webkit", "gecko", "blink",
    "x11", "wow64", "win32", "win64", "x86_64", "amd64", "arm64",
})

_NON_HUMAN_SPEAKER_PHRASE_RE = re.compile(
    r"(?i)\b(?:mac\s*os\s*x?|os\s*x|windows\s+(?:nt|10|11|7|xp|vista)|"
    r"iphone\s*os|android\s*\d|chrome\s*os|user[\s\-]agent)\b"
)

# "Apple", "Google", etc. attached as people in "Key voices and figures"
# brief composers — narrow company deny-list only when the surrounding
# brief context tags them as people.
_KNOWN_COMPANY_NAMES = frozenset({
    "apple", "google", "microsoft", "meta", "facebook", "amazon", "netflix",
    "tesla", "twitter", "x", "openai", "anthropic", "nvidia", "intel", "amd",
    "ibm", "oracle", "salesforce", "adobe", "spotify", "uber", "airbnb",
    "github", "gitlab", "stripe", "shopify", "samsung", "sony", "nintendo",
})


def _pretty_heading(raw: str | None) -> str:
    if not raw:
        return ""
    key = str(raw).strip().lower()
    if key in _RAW_HEADING_MAP:
        return _RAW_HEADING_MAP[key]
    # Preserve original casing for human-authored headings (e.g. proper nouns,
    # book titles, ALLCAPS acronyms). Only desnake when the raw heading is
    # purely lowercase + underscores (i.e. came from the schema, not the LLM).
    text = re.sub(r"_+", " ", str(raw)).strip()
    if text and text == text.lower() and ("_" in str(raw) or " " not in str(raw).strip()):
        return text[:1].upper() + text[1:]
    return text


def _strip_timestamp(label: str | None) -> str:
    if not label:
        return ""
    out = str(label)
    # Apply repeatedly until stable — handles "N/A — 12:34 — Title" stacks.
    for _ in range(3):
        before = out
        for pat in _TIMESTAMP_PATTERNS:
            out = pat.sub("", out)
        if out == before:
            break
    return out.strip()


def _try_parse_dict(text: str) -> dict | None:
    """Try ``json.loads`` first, then ``ast.literal_eval`` for Python-repr
    style (single-quoted) dicts. Returns None when neither yields a dict.

    ``ast.literal_eval`` is safe: it only parses literals, never executes
    arbitrary code. This rescues chapter bullets that arrived as
    ``"{'timestamp': 'N/A', 'title': '...', 'bullets': [...]}"`` instead of
    proper JSON.
    """
    s = text.strip()
    # Strip surrounding quotes/whitespace that sometimes wrap the blob.
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    try:
        parsed = ast.literal_eval(s)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _try_parse_list_of_dicts(text: str) -> list[dict] | None:
    s = text.strip()
    try:
        parsed = json.loads(s)
    except Exception:
        try:
            parsed = ast.literal_eval(s)
        except Exception:
            return None
    if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
        return parsed  # type: ignore[return-value]
    return None


_CHAPTER_DICT_REGEX = re.compile(
    r'(?is)["\']?\s*title["\']?\s*:\s*["\']([^"\']+?)["\']\s*,'
    r'\s*["\']?\s*bullets["\']?\s*:\s*\[([^\]]*?)\]'
)


def _split_concatenated_dicts(text: str) -> list[str]:
    """Split a string that may contain multiple back-to-back dict literals
    (``"{...}{...}"`` or ``"{...}, {...}"``) into individual dict candidates.

    Walks the string with brace balance tracking — does not naively split on
    ``}{`` because that breaks when bullets contain nested braces.
    """
    s = text.strip()
    if not s:
        return []
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    quote_ch = ""
    escape = False
    for i, ch in enumerate(s):
        if in_str:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == quote_ch:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote_ch = ch
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    out.append(s[start:i + 1])
                    start = -1
    return out


def _regex_extract_chapter(text: str) -> dict | None:
    """Last-ditch chapter extraction: regex out ``"title": "..."`` and
    ``"bullets": [...]`` when JSON / literal_eval both fail (typically
    because of unescaped quotes in the bullet bodies, e.g. ``print("hello")``).

    Returns a synthetic dict with ``title`` and ``bullets`` fields so the
    caller can ingest it identically to a parsed dict.
    """
    m = _CHAPTER_DICT_REGEX.search(text)
    if not m:
        return None
    title = m.group(1).strip()
    bullets_blob = m.group(2)
    # Split bullet items on quote-comma-quote boundaries, then strip quotes.
    raw_items = re.findall(r'["\']((?:\\.|[^"\\\'])+)["\']', bullets_blob)
    bullets = [b.strip() for b in raw_items if b.strip()]
    if not title:
        return None
    return {"title": title, "bullets": bullets}


def _expand_json_string_bullets(section: dict[str, Any]) -> dict[str, Any]:
    """Chapter bullets sometimes arrive as JSON strings of
    ``{"timestamp": "...", "title": "...", "bullets": [...]}``. Expand each
    into a proper sub-section so the renderer never shows a JSON blob.

    Also handles:
      * Python-repr (single-quoted) dict bullets via ``ast.literal_eval``.
      * A single-element list whose only entry is a JSON string carrying
        multiple chapter dicts (``"[{...}, {...}]"``).
      * A single bullet string carrying multiple back-to-back dicts
        (``"{...}{...}"``).
      * Sub-section values whose entries are dict-shaped JSON strings — the
        function recurses into ``sub_sections`` and rebuilds them too, so a
        ``Chapter walkthrough`` sub whose value is a list of stringified
        dicts gets unwrapped into a proper ``{title: bullets}`` mapping.
      * Surrounding whitespace / wrapping quotes around the blob.
      * Last-ditch regex extraction of ``title``/``bullets`` when JSON +
        ``ast.literal_eval`` both fail (broken-quote payloads).
    """
    subs_acc: dict[str, list[str]] = {}
    leftover: list[str] = []

    def _ingest_into(target: dict[str, list[str]], parsed: dict) -> bool:
        title = _strip_timestamp(str(parsed.get("title") or "").strip())
        if not title:
            return False
        nested_bullets = parsed.get("bullets") or []
        if not isinstance(nested_bullets, list):
            nested_bullets = [str(nested_bullets)]
        sub_list = [str(x).strip() for x in nested_bullets if str(x).strip()]
        if not sub_list and parsed.get("summary"):
            sub_list = [str(parsed["summary"]).strip()]
        key, idx = title, 2
        while key in target:
            key = f"{title} ({idx})"
            idx += 1
        target[key] = sub_list
        return True

    def _try_expand_one(bullet_text: str, target: dict[str, list[str]]) -> bool:
        """Attempt to parse ``bullet_text`` as one or more dict literals and
        ingest into ``target``. Returns True if any ingest succeeded."""
        t = bullet_text.strip()
        # Strip surrounding quotes that sometimes wrap the blob.
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
            t = t[1:-1].strip()
        if not (t.startswith("{") or t.startswith("[")):
            return False
        ok_any = False
        # Try list-of-dicts first.
        if t.startswith("["):
            lst = _try_parse_list_of_dicts(t)
            if lst:
                for item in lst:
                    if _ingest_into(target, item):
                        ok_any = True
                if ok_any:
                    return True
        # Try single dict.
        parsed = _try_parse_dict(t)
        if parsed and _ingest_into(target, parsed):
            return True
        # Try splitting concatenated dicts.
        chunks = _split_concatenated_dicts(t)
        if len(chunks) > 1:
            for chunk in chunks:
                p = _try_parse_dict(chunk)
                if p and _ingest_into(target, p):
                    ok_any = True
                else:
                    rx = _regex_extract_chapter(chunk)
                    if rx and _ingest_into(target, rx):
                        ok_any = True
            if ok_any:
                return True
        # Last-ditch regex.
        rx = _regex_extract_chapter(t)
        if rx and _ingest_into(target, rx):
            return True
        return False

    bullets = section.get("bullets") or []
    for b in bullets:
        if not isinstance(b, str):
            leftover.append(str(b))
            continue
        t = b.strip()
        # Quick-reject: only attempt parse on bullets that look dict-shaped.
        if not (t.startswith("{") or t.startswith("[")
                or t.startswith("'{") or t.startswith('"{')
                or t.startswith("'[") or t.startswith('"[')):
            leftover.append(b)
            continue
        if not _try_expand_one(t, subs_acc):
            leftover.append(b)

    # Recurse into sub_sections — convert any dict-shaped string entries into
    # proper sub-sections, replacing the original key with the unwrapped
    # chapter titles when at least one entry expanded.
    existing = section.get("sub_sections") or {}
    rebuilt_subs: dict[str, list[str]] = {}
    if isinstance(existing, dict):
        for k, v in existing.items():
            values = list(v) if isinstance(v, list) else [str(v)]
            inner_subs: dict[str, list[str]] = {}
            kept_values: list[str] = []
            for item in values:
                if not isinstance(item, str):
                    kept_values.append(str(item))
                    continue
                t = item.strip()
                if (t.startswith("{") or t.startswith("[")
                        or t.startswith("'{") or t.startswith('"{')
                        or t.startswith("'[") or t.startswith('"[')):
                    if _try_expand_one(t, inner_subs):
                        continue
                kept_values.append(item)
            if inner_subs:
                # Drop the original key entirely if it was a chapter-walkthrough
                # placeholder (its content was structured chapter dicts, not
                # narrative bullets). Inject the expanded chapter titles as
                # peer sub-sections.
                if kept_values:
                    rebuilt_subs[str(k)] = kept_values
                for ck, cv in inner_subs.items():
                    target_key, idx = ck, 2
                    while target_key in rebuilt_subs:
                        target_key = f"{ck} ({idx})"
                        idx += 1
                    rebuilt_subs[target_key] = cv
            else:
                rebuilt_subs[str(k)] = values

    # Merge top-level chapter expansions into the rebuilt sub-sections map.
    for k, v in subs_acc.items():
        target_key, idx = k, 2
        while target_key in rebuilt_subs:
            target_key = f"{k} ({idx})"
            idx += 1
        rebuilt_subs[target_key] = v

    if not subs_acc and rebuilt_subs == existing:
        # No-op fast path: nothing changed.
        return section

    return {
        "heading": section.get("heading"),
        "bullets": leftover,
        "sub_sections": rebuilt_subs,
    }


def _strip_lead_in(text: str) -> str:
    """Remove a "In this <format>, <speaker> argues that " preface.

    After stripping, re-capitalize the first letter so the residual sentence
    reads as a proper sentence (e.g. ``"the market for zero-day..."`` ->
    ``"The market for zero-day..."``). Idempotent on already-capitalized
    text.
    """
    if not text:
        return ""
    out = _LEAD_IN_RE.sub("", text, count=1).strip()
    if not out:
        return ""
    # Only re-capitalize if the strip actually fired (output != input).
    if out != text.strip() and out[0].isalpha() and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def _norm_compare(text: str) -> str:
    """Lowercase + collapse whitespace + strip lead-in + strip trailing
    punctuation for fuzzy containment comparisons. Pure helper, no side
    effects."""
    out = re.sub(r"\s+", " ", str(text or "").strip().lower())
    out = _LEAD_IN_RE.sub("", out, count=1).strip()
    return out.rstrip(".!? ").strip()


def _token_set(text: str) -> set[str]:
    """Word-token set (>3 chars, lowercased). Used for Jaccard fallback."""
    if not text:
        return set()
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 3}


def _bullet_is_redundant(top_bullet: str, sub_bullets: list[str]) -> bool:
    """True if the sub-section's content is effectively a duplicate of the
    top-level bullet. Used to drop a "Core argument" sub whose single bullet
    repeats the Overview thesis.

    Strategies (in order):
      1. Strip "In this <format>, <speaker> argues that " lead-in from both
         sides, then exact / containment / first-60-char compare.
      2. Token-set Jaccard similarity ≥ 0.55 on lowercased word tokens >3
         chars — handles capitalization swaps and minor edits where neither
         is a substring of the other.
    """
    if not top_bullet or not sub_bullets:
        return False
    top = _norm_compare(top_bullet)
    if not top:
        return False
    joined = _norm_compare(" ".join(sub_bullets))
    if not joined:
        return False
    if top == joined:
        return True
    if top in joined or joined in top:
        return True
    prefix = min(60, len(top), len(joined))
    if prefix >= 30 and top[:prefix] == joined[:prefix]:
        return True
    # Token-set Jaccard fallback.
    a, b = _token_set(top), _token_set(joined)
    if a and b:
        inter = len(a & b)
        union = len(a | b)
        if union and (inter / union) >= 0.55:
            return True
    return False


def _is_non_human_speaker(name: str) -> bool:
    """True when ``name`` looks like a User-Agent / OS / browser string
    rather than a human speaker."""
    cleaned = (name or "").strip()
    if not cleaned:
        return True
    if _NON_HUMAN_SPEAKER_PHRASE_RE.search(cleaned):
        return True
    tokens = re.findall(r"[A-Za-z]+", cleaned.lower())
    if not tokens:
        return True
    # If the entire string is a single token from the deny-list, drop it.
    if len(tokens) == 1 and tokens[0] in _NON_HUMAN_SPEAKER_TOKENS:
        return True
    # If a majority of tokens are deny-listed UA fragments, drop it.
    bad = sum(1 for t in tokens if t in _NON_HUMAN_SPEAKER_TOKENS)
    if bad >= max(2, len(tokens) - 1):
        return True
    return False


_SPEAKERS_LINE_RE = re.compile(
    r"(?i)(speakers?\s*:\s*)(.+?)(?=(?:\.\s|$))",
)


def _sanitize_speakers_in_text(text: str) -> str:
    """Filter UA / OS leaks out of an inline ``Speakers: X, Y.`` clause.
    When all candidates are filtered, replace the clause with
    ``Speakers: not identified``. When only some are filtered, keep the rest.
    Idempotent and conservative — only fires on text containing
    ``Speakers:`` (case-insensitive)."""
    if not text or "speaker" not in text.lower():
        return text

    def _repl(m: re.Match[str]) -> str:
        prefix = m.group(1)
        body = m.group(2)
        # Split on commas / "and" while preserving order.
        parts = re.split(r"\s*,\s*|\s+and\s+", body)
        kept = [p.strip() for p in parts if p.strip() and not _is_non_human_speaker(p.strip())]
        if not kept:
            return f"{prefix}not identified"
        return prefix + ", ".join(kept)

    return _SPEAKERS_LINE_RE.sub(_repl, text)


def _sanitize_format_and_speakers(values: list[str]) -> list[str]:
    """Apply UA/OS filtering to every bullet in a Format-and-speakers sub."""
    out: list[str] = []
    for v in values:
        cleaned = _sanitize_speakers_in_text(str(v))
        if cleaned.strip():
            out.append(cleaned.strip())
    return out


# Brief-summary sanitization — drop trailing incomplete-sentence fragments
# ("requiring trust in.", "The main takeaway is The speech concludes with the.")
# so old stored rows self-heal at the wire boundary. Conservative: only
# strips clearly-truncated trailing sentences; never touches the leading
# ones.
_INCOMPLETE_TAIL_TOKENS = frozenset({
    "in", "of", "to", "for", "with", "by", "on", "at", "from", "as", "into",
    "the", "a", "an", "and", "or", "but", "that", "which", "who", "whom",
    "is", "are", "was", "were", "be", "being", "been", "has", "have", "had",
})


def _sentence_looks_complete(sentence: str) -> bool:
    """A sentence is 'complete' when it ends with terminal punctuation AND
    the last meaningful word is not a connective / preposition / article."""
    s = sentence.strip()
    if not s:
        return False
    if s[-1] not in ".!?":
        return False
    # Look at the last non-trivial word.
    words = re.findall(r"[A-Za-z']+", s)
    if not words:
        return False
    last = words[-1].lower()
    if last in _INCOMPLETE_TAIL_TOKENS:
        return False
    # Detect "The main takeaway is The speech concludes with the." — ends
    # with "the." which is in the deny-list above and will be caught.
    return True


_UA_INLINE_LIST_RE = re.compile(
    r"(?i)("
    r"mac\s*os\s*x?|os\s*x|windows\s+(?:nt|xp|vista)|"
    r"iphone\s*os|android\s+\d+(?:\.\d+)*|chrome\s*os|user[\s\-]agent|"
    r"webkit|gecko|chromium"
    r")"
)


def _is_ua_list_item(item: str) -> bool:
    """True when the candidate list item is a UA / OS leak rather than a
    legitimate platform reference. Conservative: full-string match only —
    "Windows 10" alone is allowed (it's a real OS users may discuss), but
    "Mac OS X" is always a UA-string artifact in our pipeline and so are
    "WebKit"/"Gecko"/"Mozilla"/"Chrome OS" / browser engine names.
    """
    cleaned = (item or "").strip().rstrip(".!?")
    if not cleaned:
        return False
    return bool(_UA_INLINE_LIST_RE.fullmatch(cleaned))


def _scrub_ua_inline_list(sentence: str) -> str:
    """Remove UA / OS / browser entries from a comma-separated list inside a
    sentence, e.g. ``"references Windows 10, Mac OS X, and Bugtraq."`` ->
    ``"references Windows 10 and Bugtraq."``.

    Conservative algorithm:
      1. Identify the list region: the longest tail of the sentence formed by
         comma-separated items joined with a final " and " or ", and ".
      2. Each item in the list is independently checked against
         ``_NON_HUMAN_SPEAKER_PHRASE_RE``. Items containing a UA phrase are
         dropped; others are preserved verbatim (including any lead-in
         words on the first item, e.g. ``"references Windows 10"``).
      3. The lead-in / preface (everything BEFORE the list region) is never
         modified — that's where the verb of the sentence lives.

    The list region is detected by walking commas from the end of the
    sentence. We start with the final clause (after the last " and " /
    "; and " / ", and ") plus any preceding comma-joined siblings as long as
    each sibling is short (<=12 words) — that's the heuristic for a list
    rather than a clause. If the heuristic fails we leave the sentence alone.
    """
    if not sentence:
        return sentence
    # Only scan when the sentence contains a strict UA artifact — a full
    # "Mac OS X" / "WebKit" / "Chrome OS" style token, not a casual mention
    # of "Windows 10" which a user may legitimately discuss.
    if not _UA_INLINE_LIST_RE.search(sentence):
        return sentence
    # Strip trailing punctuation; we'll re-attach it.
    s = sentence.rstrip()
    trailing = ""
    if s and s[-1] in ".!?":
        trailing = s[-1]
        s = s[:-1]
    # Find ", and " or " and " as the final-list connector.
    m = re.search(r"(?:,\s+and\s+|\s+and\s+)([^,]+)$", s)
    if not m:
        return sentence  # no detectable list — bail
    last_item = m.group(1).strip()
    head_region = s[:m.start()]  # everything up to ", and " / " and "
    # Split the head region by commas into possible siblings.
    head_parts = [p.strip() for p in head_region.split(",")]
    list_verb_re_top = re.compile(
        r"\b(references?|mentions?|cites?|includes?|lists?|names?|"
        r"covers?|discusses?|features?)\s+",
        re.IGNORECASE,
    )
    if len(head_parts) < 2:
        # Only "X and Y" with no commas — split on a list-introducing verb
        # if present so the lead-in clause is preserved as ``prefix``.
        first = head_parts[0]
        vb0 = list_verb_re_top.search(first)
        if vb0:
            prefix = first[:vb0.end()]
            item1 = first[vb0.end():].strip()
            items = [item1, last_item]
        else:
            if len(first.split()) > 12:
                return sentence
            items = [first, last_item]
            prefix = ""
    else:
        # The first comma-split chunk contains the lead-in + first list item.
        # Detect a list-introducing verb (references, mentions, includes,
        # cites, lists, names, covers, discusses, features) — when present,
        # split the chunk at the verb boundary so the lead-in is preserved as
        # ``prefix`` and only the trailing item participates in UA filtering.
        first = head_parts[0]
        list_verb_re = re.compile(
            r"\b(references?|mentions?|cites?|includes?|lists?|names?|"
            r"covers?|discusses?|features?)\s+",
            re.IGNORECASE,
        )
        vb_top = list_verb_re.search(first)
        if vb_top:
            prefix = first[:vb_top.end()]
            item1 = first[vb_top.end():].strip()
            items = [item1] + head_parts[1:] + [last_item]
        elif len(first.split()) > 12:
            # First chunk is a clause containing the lead-in. We split off
            # the trailing list item by walking back to the verb boundary —
            # heuristic: if first chunk ends with a UA phrase or a known
            # list entry style (capitalized phrase), keep it as item;
            # otherwise treat full chunk as prefix and list begins at chunk 2.
            # Simplest: if first chunk doesn't itself contain a UA phrase
            # AND has >12 words, treat it entirely as prefix.
            if not _UA_INLINE_LIST_RE.search(first):
                prefix = first + ", "
                items = head_parts[1:] + [last_item]
            else:
                # Split first chunk into "lead-in" + "first list item" by
                # scanning for the UA phrase position.
                ua_match = _UA_INLINE_LIST_RE.search(first)
                # Walk back from ua_match.start() to find a space — anything
                # from that space to end-of-chunk is item #1.
                start = ua_match.start()
                space_idx = first.rfind(" ", 0, start)
                # Look for a verb-like token before that. Conservative:
                # treat everything up to the LAST capitalized word that
                # starts a phrase as prefix.
                # Simpler still: use a known set of words to detect the
                # boundary. Verbs that introduce lists: references, mentions,
                # cites, includes, lists, names, covers, discusses.
                vb = re.search(
                    r"\b(references?|mentions?|cites?|includes?|lists?|names?|covers?|discusses?|features?)\s+",
                    first,
                    re.IGNORECASE,
                )
                if vb:
                    prefix = first[:vb.end()]
                    item1 = first[vb.end():].strip()
                    items = [item1] + head_parts[1:] + [last_item]
                else:
                    prefix = first[:space_idx + 1] if space_idx >= 0 else ""
                    item1 = first[space_idx + 1:] if space_idx >= 0 else first
                    items = [item1] + head_parts[1:] + [last_item]
        else:
            items = head_parts + [last_item]
            prefix = ""
    items = [it.strip() for it in items if it.strip()]
    kept = [it for it in items if not _is_ua_list_item(it)]
    if not kept:
        return sentence  # don't zero out — caller decides
    if len(kept) == len(items):
        return sentence  # nothing removed
    if len(kept) == 1:
        rebuilt = prefix + kept[0]
    elif len(kept) == 2:
        rebuilt = prefix + f"{kept[0]} and {kept[1]}"
    else:
        rebuilt = prefix + ", ".join(kept[:-1]) + ", and " + kept[-1]
    rebuilt = re.sub(r"\s{2,}", " ", rebuilt).strip()
    return rebuilt + trailing


def _sanitize_brief(text: str) -> str:
    """Drop trailing incomplete-sentence fragments from a brief summary.

    Splits on sentence boundaries, then walks from the end stripping any
    sentence that fails ``_sentence_looks_complete``. If the entire text is
    one incomplete sentence, leave it intact (we never zero out content —
    callers can decide to render placeholder text).

    Also removes obvious company names from a "Key voices and figures
    include X and Y." sentence when at least one human-looking entity
    survives. If all candidates are companies, the sentence is dropped.

    Additionally scrubs mid-sentence UA / OS / browser leaks from each
    sentence (e.g. ``"references Windows 10, Mac OS X, and Bugtraq"`` ->
    ``"references Windows 10 and Bugtraq"``) and re-stitches the surrounding
    list cleanly.
    """
    if not text:
        return ""
    # Strip pipeline caveats / "Note to ingester:" leaks before any further
    # processing — these are internal metadata, not user-facing prose.
    from website.core.text_polish import strip_caveats as _strip_caveats
    cleaned = _strip_caveats(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    # Split into sentences preserving terminator.
    sentences = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", cleaned)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return cleaned
    # Scrub mid-sentence UA / OS / browser leaks (e.g. "Mac OS X" sandwiched
    # in a comma list) before any further processing.
    sentences = [_scrub_ua_inline_list(s) for s in sentences]
    sentences = [s for s in sentences if s.strip()]
    # Filter "Key voices and figures include ..." sentences.
    filtered: list[str] = []
    for s in sentences:
        m = re.match(
            r"(?i)^(key voices(?:\s+and\s+figures)?\s+include\s+)(.+?)([.!?])\s*$",
            s,
        )
        if m:
            prefix, body, term = m.group(1), m.group(2), m.group(3)
            parts = re.split(r"\s*,\s*|\s+and\s+", body)
            kept = []
            for p in parts:
                p_clean = p.strip().rstrip(".")
                if not p_clean:
                    continue
                if p_clean.lower() in _KNOWN_COMPANY_NAMES:
                    continue
                kept.append(p_clean)
            if not kept:
                continue  # drop the whole sentence
            if len(kept) == 1:
                rebuilt = f"{prefix}{kept[0]}{term}"
            else:
                rebuilt = f"{prefix}{', '.join(kept[:-1])} and {kept[-1]}{term}"
            filtered.append(rebuilt)
            continue
        filtered.append(s)
    if not filtered:
        # Everything filtered — fall back to the original input (better than
        # an empty brief).
        return cleaned
    # Walk back from the end dropping incomplete tails.
    while len(filtered) > 1 and not _sentence_looks_complete(filtered[-1]):
        filtered.pop()
    # If we're left with a single incomplete sentence, leave it — never
    # zero out the brief.
    return " ".join(filtered).strip()


def _wrap_chapter_like_h2s(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If there are >5 top-level sections that look like chapter-style
    headings AND no ``Chapter walkthrough`` h2 already exists, fold them
    into sub-sections under a synthetic ``Chapter walkthrough`` h2.

    Heuristic for "chapter-like": short title (≤8 words), not one of the
    canonical anchor headings (Overview / Core argument / Closing remarks /
    Demonstrations / Conclusions / Call to action / Stance / etc.), and
    has at least one bullet. This catches the iter-08 path where chapter
    sections were emitted as flat h2s instead of nested under
    ``Chapter walkthrough``.
    """
    if not sections:
        return sections
    has_walkthrough = any(
        (s.get("heading") or "").strip().lower() == "chapter walkthrough"
        for s in sections
    )
    if has_walkthrough:
        return sections
    anchor_headings = {
        "overview", "core argument", "closing remarks", "closing takeaway",
        "demonstrations", "conclusions & recommendations", "conclusions",
        "conclusion", "recommendations", "call to action", "stance",
        "publication identity", "format and speakers", "format",
        "op thesis", "dissent", "agreement", "summary",
    }
    chapter_like_idx: list[int] = []
    for i, s in enumerate(sections):
        h = (s.get("heading") or "").strip()
        if not h:
            continue
        if h.lower() in anchor_headings:
            continue
        # Allow chapter-style titles up to 12 words — talk titles like
        # "Common Pitfalls and How to Avoid Them" easily exceed 8.
        if len(h.split()) > 12:
            continue
        # Must have content (bullets or sub_sections) so we don't wrap
        # empty/divider headings.
        if not (s.get("bullets") or s.get("sub_sections")):
            continue
        chapter_like_idx.append(i)
    if len(chapter_like_idx) < 4:
        return sections
    # Build the wrapped section, preserving order.
    chapter_subs: dict[str, list[str]] = {}
    for i in chapter_like_idx:
        s = sections[i]
        title = str(s.get("heading") or "").strip()
        bullets = s.get("bullets") or []
        if not isinstance(bullets, list):
            bullets = [str(bullets)]
        # Flatten any sub_sections back into bullets prefixed with "—".
        nested = s.get("sub_sections") or {}
        flat = [str(b).strip() for b in bullets if str(b).strip()]
        if isinstance(nested, dict):
            for k, v in nested.items():
                if isinstance(v, list):
                    flat.extend(str(x).strip() for x in v if str(x).strip())
                elif v:
                    flat.append(str(v).strip())
        key, idx = title, 2
        while key in chapter_subs:
            key = f"{title} ({idx})"
            idx += 1
        chapter_subs[key] = flat
    # Replace the chapter-like indices with a single Chapter walkthrough
    # section inserted at the position of the first chapter-like h2.
    drop_set = set(chapter_like_idx)
    out: list[dict[str, Any]] = []
    inserted = False
    for i, s in enumerate(sections):
        if i in drop_set:
            if not inserted:
                out.append({
                    "heading": "Chapter walkthrough",
                    "bullets": [],
                    "sub_sections": chapter_subs,
                })
                inserted = True
            continue
        out.append(s)
    return out


def _normalize_section(section: dict[str, Any]) -> dict[str, Any] | None:
    """One pipeline-shape section → one pretty section. Returns None to drop."""
    if not isinstance(section, dict):
        return None
    raw_heading = str(section.get("heading") or "").strip()
    if raw_heading.lower() in _DROP_HEADING_LOWER:
        return None
    section = _expand_json_string_bullets(section)
    pretty_subs: dict[str, list[str]] = {}
    subs = section.get("sub_sections") or {}
    if isinstance(subs, dict):
        for k, v in subs.items():
            if not isinstance(v, list):
                v = [str(v)]
            cleaned_key = _strip_timestamp(_pretty_heading(k)) or str(k)
            cleaned_vals = [str(x).strip() for x in v if str(x).strip()]
            # Sanitize Format-and-speakers UA leaks.
            if cleaned_key.strip().lower() == "format and speakers":
                cleaned_vals = _sanitize_format_and_speakers(cleaned_vals)
            pretty_subs[cleaned_key] = cleaned_vals
    bullets = section.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    bullets = [str(x).strip() for x in bullets if str(x).strip()]
    pretty_heading = _strip_timestamp(raw_heading) or raw_heading
    pretty_heading = _pretty_heading(pretty_heading) or pretty_heading

    # Drop sub-sections that should never nest under any parent — Overview
    # and Summary are top-level anchors, never children.
    pretty_subs = {
        k: v for k, v in pretty_subs.items()
        if k.strip().lower() not in _DROP_SUB_HEADING_LOWER
    }

    # Drop a sub-section whose key (case-insensitive) duplicates the parent
    # heading — guards against a layout path that injects e.g. a "Core
    # argument" sub inside a "Core argument" parent. Idempotent.
    parent_key = pretty_heading.strip().lower()
    if parent_key:
        pretty_subs = {
            k: v for k, v in pretty_subs.items() if k.strip().lower() != parent_key
        }

    # On Overview specifically, strip the "In this <format>, X argues that "
    # lead-in from the lifted top bullet so the Format-and-speakers sub
    # isn't redundant with the bullet, AND so dedupe against a Core-argument
    # sub catches semantic duplicates that differ only in the lead-in.
    if parent_key == "overview" and bullets:
        bullets = [_strip_lead_in(b) or b for b in bullets]

    # Dedupe pass: when the top-level bullet (typically the lifted thesis on
    # Overview) is effectively repeated by a sub-section's content, drop the
    # sub. Applies to any sub key, but in practice catches "Core argument"
    # and "Thesis" subs that the source layouts still emit alongside the
    # promoted top-level Overview bullet.
    #
    # IMPORTANT: strip lead-in from every sub-section's bullets BEFORE the
    # redundancy compare. Without this, a top bullet that was already
    # lead-in-stripped will not match a sub bullet that still carries the
    # ``"In this lecture, Andrej Karpathy argues that ..."`` preface — the
    # containment check fails because the preface dominates the prefix.
    if bullets and pretty_subs:
        top_bullet = bullets[0]
        survivors: dict[str, list[str]] = {}
        for k, v in pretty_subs.items():
            stripped_sub = [_strip_lead_in(x) or x for x in v]
            if _bullet_is_redundant(top_bullet, stripped_sub):
                continue
            # Preserve the lead-in-stripped form for the surviving sub so the
            # rendered text matches the dedupe-time text (otherwise users see
            # the preface in the sub but not in the top bullet).
            survivors[k] = stripped_sub if parent_key == "overview" else v
        pretty_subs = survivors

    return {
        "heading": pretty_heading,
        "bullets": bullets,
        "sub_sections": pretty_subs,
    }


def _from_list(detailed: list) -> list[dict[str, Any]]:
    """Canonical list shape — normalize each section, then promote a leading
    "Core argument"/"Thesis" section into a proper Overview wrapper so every
    detailed summary opens on the same anchor regardless of upstream shape."""
    out: list[dict[str, Any]] = []
    for s in detailed:
        ns = _normalize_section(s) if isinstance(s, dict) else None
        if ns is not None:
            out.append(ns)
    if out and out[0].get("heading") == "Core argument":
        first = out[0]
        bullets = first.get("bullets") or []
        out[0] = {
            "heading": "Overview",
            "bullets": [_strip_lead_in(b) or b for b in bullets],
            "sub_sections": dict(first.get("sub_sections") or {}),
        }
    out = _wrap_chapter_like_h2s(out)
    return out


def _from_newsletter_dict(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Newsletter detailed_summary dict → structured sections.

    Newsletter shape: ``{publication_identity, issue_thesis, sections, stance,
    conclusions_or_recommendations, cta}``. We emit:
      * Overview section with publication + thesis as sub-sections
      * One section per entry in ``sections``
      * Conclusions section when recommendations exist
    """
    out: list[dict[str, Any]] = []
    overview_subs: dict[str, list[str]] = {}
    overview_bullets: list[str] = []
    if d.get("issue_thesis"):
        overview_bullets.append(_strip_lead_in(str(d["issue_thesis"]).strip()) or str(d["issue_thesis"]).strip())
    if d.get("publication_identity"):
        overview_subs["Publication"] = [str(d["publication_identity"]).strip()]
    if overview_bullets or overview_subs:
        out.append({"heading": "Overview", "bullets": overview_bullets, "sub_sections": overview_subs})
    for s in d.get("sections") or []:
        if not isinstance(s, dict):
            continue
        ns = _normalize_section(s)
        if ns:
            out.append(ns)
    recs = d.get("conclusions_or_recommendations") or []
    if recs and isinstance(recs, list):
        out.append({
            "heading": "Conclusions & recommendations",
            "bullets": [str(r).strip() for r in recs if str(r).strip()],
            "sub_sections": {},
        })
    if d.get("cta") and isinstance(d.get("cta"), str):
        out.append({"heading": "Call to action", "bullets": [d["cta"].strip()], "sub_sections": {}})
    return out


def _from_youtube_dict(d: dict[str, Any]) -> list[dict[str, Any]]:
    """YouTube detailed_summary dict → structured sections.

    Shape: ``{thesis, format, chapters_or_segments: [{timestamp, title,
    bullets}], demonstrations, closing_takeaway}``. Timestamps are dropped
    per product decision.
    """
    out: list[dict[str, Any]] = []
    overview_bullets: list[str] = []
    if d.get("thesis"):
        thesis = str(d["thesis"]).strip()
        overview_bullets.append(_strip_lead_in(thesis) or thesis)
    if overview_bullets:
        out.append({"heading": "Overview", "bullets": overview_bullets, "sub_sections": {}})
    chapter_subs: dict[str, list[str]] = {}
    for chap in d.get("chapters_or_segments") or []:
        if not isinstance(chap, dict):
            continue
        title = _strip_timestamp(str(chap.get("title") or "").strip())
        if not title:
            continue
        bullets = chap.get("bullets") or []
        if not isinstance(bullets, list):
            bullets = [str(bullets)]
        clean = [str(b).strip() for b in bullets if str(b).strip()]
        key, idx = title, 2
        while key in chapter_subs:
            key = f"{title} ({idx})"
            idx += 1
        chapter_subs[key] = clean
    if chapter_subs:
        out.append({"heading": "Chapter walkthrough", "bullets": [], "sub_sections": chapter_subs})
    demos = d.get("demonstrations") or []
    if demos:
        out.append({
            "heading": "Demonstrations",
            "bullets": [str(x).strip() for x in demos if str(x).strip()],
            "sub_sections": {},
        })
    if d.get("closing_takeaway"):
        takeaway = str(d["closing_takeaway"]).strip()
        bullet = takeaway if takeaway.lower().startswith("recap") else f"Recap: {takeaway}"
        out.append({"heading": "Closing remarks", "bullets": [bullet], "sub_sections": {}})
    return out


def _from_generic_dict(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Unknown dict shape — convert each top-level key into a section."""
    out: list[dict[str, Any]] = []
    for k, v in d.items():
        if v is None:
            continue
        heading = _pretty_heading(k)
        if isinstance(v, str):
            out.append({"heading": heading, "bullets": [v.strip()], "sub_sections": {}})
        elif isinstance(v, list):
            bullets: list[str] = []
            subs: dict[str, list[str]] = {}
            for item in v:
                if isinstance(item, str):
                    bullets.append(item.strip())
                elif isinstance(item, dict):
                    ns = _normalize_section(item)
                    if ns:
                        subs[ns["heading"] or f"Item {len(subs)+1}"] = ns["bullets"]
            section: dict[str, Any] = {"heading": heading, "bullets": bullets, "sub_sections": subs}
            out.append(section)
        elif isinstance(v, dict):
            nested = _from_generic_dict(v)
            if nested:
                # Fold children into sub_sections so we don't explode the outline
                subs = {n["heading"] or "—": n["bullets"] for n in nested}
                out.append({"heading": heading, "bullets": [], "sub_sections": subs})
        else:
            out.append({"heading": heading, "bullets": [str(v)], "sub_sections": {}})
    return out


def _from_markdown(md: str) -> list[dict[str, Any]]:
    """Parse ``## heading`` / ``### sub`` / ``- bullet`` markdown into sections."""
    lines = md.split("\n")
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    sub_heading: str | None = None

    def ensure() -> dict[str, Any]:
        nonlocal current
        if current is None:
            current = {"heading": "Overview", "bullets": [], "sub_sections": {}}
            sections.append(current)
        return current

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        m2 = re.match(r"^##\s+(.*)$", line)
        m3 = re.match(r"^###\s+(.*)$", line)
        mb = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m2:
            current = {"heading": m2.group(1).strip(), "bullets": [], "sub_sections": {}}
            sections.append(current)
            sub_heading = None
            continue
        if m3:
            sub_heading = m3.group(1).strip()
            ensure()
            continue
        sec = ensure()
        text = mb.group(1).strip() if mb else line.strip()
        if sub_heading is not None:
            sec["sub_sections"].setdefault(sub_heading, []).append(text)
        else:
            sec["bullets"].append(text)
    # Normalize each section (heading remap, timestamp strip, JSON-bullet expand)
    normalized: list[dict[str, Any]] = []
    for s in sections:
        ns = _normalize_section(s)
        if ns is not None:
            normalized.append(ns)
    return normalized


def _promote_core_argument_to_overview(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If the first section is a bare "Core argument" / "Thesis", relabel it
    "Overview" so the modal always opens on the same anchor heading. Idempotent."""
    if sections and sections[0].get("heading") == "Core argument":
        first = sections[0]
        bullets = list(first.get("bullets") or [])
        sections[0] = {
            "heading": "Overview",
            "bullets": [_strip_lead_in(b) or b for b in bullets],
            "sub_sections": dict(first.get("sub_sections") or {}),
        }
    return sections


def _detect_detailed_shape(detailed: Any) -> str:
    if isinstance(detailed, list):
        return "list"
    if isinstance(detailed, dict):
        keys = set(detailed.keys())
        if {"publication_identity", "issue_thesis"} & keys:
            return "newsletter_dict"
        if {"thesis", "chapters_or_segments"} & keys or {"thesis", "format"} & keys:
            return "youtube_dict"
        return "generic_dict"
    if isinstance(detailed, str):
        trimmed = detailed.strip()
        if trimmed.startswith("[") or trimmed.startswith("{"):
            try:
                parsed = json.loads(trimmed)
                # Recurse into the parsed value
                return f"json_{_detect_detailed_shape(parsed)}"
            except Exception:
                pass
        if "## " in trimmed or "### " in trimmed:
            return "markdown"
        return "plain_string"
    return "unknown"


def _from_plain_string(text: str) -> list[dict[str, Any]]:
    """Plain (non-markdown, non-JSON) detailed_summary — wrap as a single
    Overview section split into one bullet per sentence. Guarantees the modal
    always shows substantive top-level content for legacy rows where the
    pipeline only produced a single prose paragraph."""
    cleaned = text.strip()
    if not cleaned:
        return []
    # Split on sentence boundaries; fall back to the whole string if there
    # are no terminators (so we never lose content).
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", cleaned)
    bullets = [p.strip() for p in parts if p.strip()]
    if not bullets:
        bullets = [cleaned]
    return [{"heading": "Overview", "bullets": bullets, "sub_sections": {}}]


def _normalize_detailed(detailed: Any) -> list[dict[str, Any]]:
    shape = _detect_detailed_shape(detailed)
    if shape == "list":
        return _wrap_chapter_like_h2s(
            _promote_core_argument_to_overview(_from_list(detailed))
        )
    if shape == "newsletter_dict":
        return _from_newsletter_dict(detailed)
    if shape == "youtube_dict":
        return _from_youtube_dict(detailed)
    if shape == "generic_dict":
        return _from_generic_dict(detailed)
    if shape == "markdown":
        return _wrap_chapter_like_h2s(
            _promote_core_argument_to_overview(_from_markdown(detailed))
        )
    if shape == "plain_string":
        return _from_plain_string(detailed)
    if shape.startswith("json_"):
        # detailed is a JSON string of a known shape — parse and recurse
        try:
            parsed = json.loads(detailed.strip())
            return _normalize_detailed(parsed)
        except Exception:
            return []
    return []


def normalize_summary_for_wire(raw: Any, source_type: str | None = None) -> str:
    """Return the canonical JSON envelope string for ``raw``.

    Input can be any historical shape (string, dict, JSON-in-string). Output
    is a JSON string with keys ``mini_title``, ``brief_summary``,
    ``detailed_summary`` (always a list of structured sections),
    ``closing_remarks``. Safe to run on already-canonical rows.
    """
    del source_type  # reserved for future per-source wording tweaks
    envelope: dict[str, Any] = {
        "mini_title": "",
        "brief_summary": "",
        "detailed_summary": [],
        "closing_remarks": "",
    }
    if raw is None:
        return json.dumps(envelope, ensure_ascii=False)
    # Attempt to parse an envelope from ``raw``
    source: dict[str, Any] | None = None
    if isinstance(raw, dict):
        source = raw
    elif isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed.startswith("{"):
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, dict):
                    source = parsed
            except Exception:
                source = None
        if source is None:
            # Legacy plain-text brief — no detailed content available
            envelope["brief_summary"] = _sanitize_brief(raw.strip())
            return json.dumps(envelope, ensure_ascii=False)
    else:
        envelope["brief_summary"] = _sanitize_brief(str(raw))
        return json.dumps(envelope, ensure_ascii=False)

    envelope["mini_title"] = str(source.get("mini_title") or "").strip()
    raw_brief = str(source.get("brief_summary") or source.get("summary") or "").strip()
    envelope["brief_summary"] = _sanitize_brief(raw_brief)
    raw_close = str(source.get("closing_remarks") or source.get("closing_takeaway") or "").strip()
    envelope["closing_remarks"] = _sanitize_speakers_in_text(raw_close)
    detailed_raw = source.get("detailed_summary")
    envelope["detailed_summary"] = _normalize_detailed(detailed_raw) if detailed_raw is not None else []

    # Lift a closing-remark equivalent from inside the detailed dict when the
    # top-level envelope didn't carry one. Newsletter writers stash CTA inside
    # ``detailed_summary``, YouTube writers stash ``closing_takeaway`` there.
    # Without this lift the modal renders an empty closing-remarks section.
    if not envelope["closing_remarks"] and isinstance(detailed_raw, dict):
        nested_close = (
            detailed_raw.get("closing_remarks")
            or detailed_raw.get("closing_takeaway")
            or detailed_raw.get("cta")
            or ""
        )
        if isinstance(nested_close, str) and nested_close.strip():
            envelope["closing_remarks"] = _sanitize_speakers_in_text(nested_close.strip())

    # Defensive: if the detailed list is non-empty but has no Overview anchor,
    # synthesize one from brief_summary so the modal opens on a stable heading.
    detailed_list = envelope["detailed_summary"]
    if detailed_list and isinstance(detailed_list, list):
        has_overview = any(
            isinstance(s, dict) and s.get("heading") == "Overview" for s in detailed_list
        )
        if not has_overview and envelope["brief_summary"]:
            detailed_list.insert(
                0,
                {
                    "heading": "Overview",
                    "bullets": [envelope["brief_summary"]],
                    "sub_sections": {},
                },
            )

    # Final wire-boundary polish: deterministic grammar/typography pass
    # plus caveat stripping. Idempotent — safe on already-polished rows.
    envelope = polish_envelope(envelope)

    return json.dumps(envelope, ensure_ascii=False)


def normalize_graph_nodes(graph_dict: dict[str, Any]) -> dict[str, Any]:
    """Mutate each node's ``summary`` to the canonical envelope string.

    Also rewrites Reddit ``r-foo`` tags to ``r/foo`` for display parity.
    """
    for node in graph_dict.get("nodes") or []:
        raw = node.get("summary")
        source_type = node.get("source_type")
        try:
            node["summary"] = normalize_summary_for_wire(raw, source_type)
        except Exception:
            # Never fail graph fetch because of one bad row — keep raw form
            continue
        if "tags" in node:
            try:
                node["tags"] = rewrite_tags(node.get("tags"))
            except Exception:
                pass
        if isinstance(node.get("name"), str) and node["name"]:
            try:
                node["name"] = polish(node["name"])
            except Exception:
                pass
    return graph_dict
