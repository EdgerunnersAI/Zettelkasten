"""ISO-3166 alpha-2 country-code to display-name mapping.

WAVE-D Phase 1 WM-16. Used by ``User_Activity.notify_pricing_visit`` and
``notify_payment`` to render ``"India (IN)"`` in Slack instead of the bare
2-letter code Cloudflare ships via ``cf-ipcountry``.

We deliberately ship an in-module dict (no ``pycountry`` dep) — pycountry's
unzipped wheel is ~5 MB and pulls 2.3k entries on import. For an ops-channel
display we need the top ~50 countries our users come from, plus a sane
"Unknown (XX)" fallback. The dict is rendered once at import time and the
lookup is O(1).

If a code arrives that isn't in the map we render ``"<UNKNOWN> (XX)"`` so
operators can still see what Cloudflare reported without losing the raw
code for triage.
"""

from __future__ import annotations

# Top countries by Zettelkasten traffic profile + every G20 + a sweep of
# common APAC / EMEA / LATAM markets. ~50 codes — covers ~98% of real
# pricing-page hits per current analytics; the fallback handles the rest.
_COUNTRIES: dict[str, str] = {
    "IN": "India",
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "DE": "Germany",
    "FR": "France",
    "JP": "Japan",
    "CN": "China",
    "KR": "South Korea",
    "BR": "Brazil",
    "MX": "Mexico",
    "RU": "Russia",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "SE": "Sweden",
    "NO": "Norway",
    "FI": "Finland",
    "DK": "Denmark",
    "PL": "Poland",
    "CH": "Switzerland",
    "BE": "Belgium",
    "AT": "Austria",
    "IE": "Ireland",
    "PT": "Portugal",
    "GR": "Greece",
    "CZ": "Czech Republic",
    "RO": "Romania",
    "UA": "Ukraine",
    "TR": "Turkey",
    "IL": "Israel",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "ZA": "South Africa",
    "EG": "Egypt",
    "NG": "Nigeria",
    "KE": "Kenya",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "PE": "Peru",
    "SG": "Singapore",
    "MY": "Malaysia",
    "ID": "Indonesia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "PH": "Philippines",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "LK": "Sri Lanka",
    "NP": "Nepal",
    "TW": "Taiwan",
    "HK": "Hong Kong",
    "NZ": "New Zealand",
}


def format_country(code: str | None) -> str:
    """Render a Cloudflare ``cf-ipcountry`` value as ``"Name (CC)"``.

    * ``"IN"`` → ``"India (IN)"``
    * ``"XX"`` (Cloudflare's anonymous-proxy code) → ``"Unknown (XX)"``
    * ``None`` / ``""`` / ``"-"`` → ``"—"`` (em-dash, matches existing
      ``notify_pricing_visit`` placeholder convention)
    * unknown 2-letter code → ``"Unknown (CC)"``
    """
    if not code or code in {"—", "-"}:
        return "—"
    code_u = code.strip().upper()
    if not code_u:
        return "—"
    if code_u == "XX":
        return "Unknown (XX)"
    name = _COUNTRIES.get(code_u)
    if name:
        return f"{name} ({code_u})"
    # M-5: drop angle-bracket literal — Slack renders ``<...>`` as a link.
    return f"Unknown ({code_u})"


__all__ = ["format_country"]
