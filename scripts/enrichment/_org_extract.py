"""Organisation metadata extraction from HTML pages.

Extracts:
  - Legal form   (GmbH, gGmbH, e.V., Stiftung, …)
  - Organisation name
  - Organisation type  (Company, Association, Foundation, …)
  - Nonprofit status   (Yes / No / Unclear)

All functions are deterministic: no LLM, no external calls.

Public API
----------
extract_legal_form(text)                   -> tuple[str, str] | None
extract_org_name(html, page_type)          -> tuple[str, str] | None
infer_org_type(legal_form, text)           -> str | None
infer_nonprofit_status(legal_form, text)   -> Literal["Yes", "No", "Unclear"]
"""

from __future__ import annotations

import re
from typing import Literal

from bs4 import BeautifulSoup

from _html_extract import extract_json_ld, flatten_json_ld, snippet

# ── Legal form patterns ───────────────────────────────────────────────────────
# Ordered most-specific → least-specific to prevent partial matches.
# Each entry: (regex_pattern, normalised_display_value)
_LEGAL_FORM_PATTERNS: list[tuple[str, str]] = [
    (r"GmbH\s*&\s*Co\.?\s*KG",     "GmbH & Co. KG"),
    (r"UG\s*\(haftungsbeschr[äa]nkt\)", "UG (haftungsbeschränkt)"),
    (r"gGmbH",                      "gGmbH"),
    (r"gUG",                        "gUG"),
    (r"GmbH",                       "GmbH"),
    (r"UG\b",                       "UG"),
    (r"\bAG\b",                     "AG"),
    (r"e\.?\s*V\.?(?:\b|$)",        "e.V."),
    (r"\bGbR\b",                    "GbR"),
    (r"\bOHG\b",                    "OHG"),
    (r"\bKG\b",                     "KG"),
    (r"\bA[öo]R\b",                 "AöR"),
    (r"\bKd[öo]R\b",                "KdöR"),
    (r"\bStiftung\b",               "Stiftung"),
    (r"\bLimited\b",                "Limited"),
    (r"\bLtd\.?\b",                 "Ltd"),
    (r"\bInc\.?\b",                 "Inc"),
    (r"\bLLC\b",                    "LLC"),
]

# Compiled: (compiled_re, normalised_display)
_LEGAL_FORM_RE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat, re.IGNORECASE), display)
    for pat, display in _LEGAL_FORM_PATTERNS
]

_EVIDENCE_WINDOW = 80  # chars on each side of the match


def extract_legal_form(text: str) -> tuple[str, str] | None:
    """Return (normalised_display, evidence_snippet) for the first legal form
    found in *text*, or None if none is found."""
    for pattern, display in _LEGAL_FORM_RE:
        m = pattern.search(text)
        if m:
            ev = snippet(text, m.start(), width=_EVIDENCE_WINDOW * 2)
            return display, ev
    return None


# ── Organisation name ─────────────────────────────────────────────────────────

_GENERIC_TITLES = {
    "home", "welcome", "homepage", "start", "startseite",
    "index", "untitled", "willkommen", "news", "impressum", "imprint",
    "legal notice", "contact", "kontakt", "about", "about us",
    "seite nicht gefunden", "page not found", "not found",
}

_ERROR_NAME_RE = re.compile(
    r"(?:\b(?:error\s*)?404\b|not found|seite nicht gefunden|access denied|"
    r"zugriff verweigert|service unavailable)", re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.|linkedin\.com|facebook\.com)", re.IGNORECASE)
_DOMAIN_ONLY_RE = re.compile(r"^[\w-]+(?:\.[\w-]+)+/?$", re.IGNORECASE)

_TITLE_SEPARATORS = [" | ", " - ", " – ", " — ", " : ", " · "]


def is_plausible_org_name(name: str) -> bool:
    """Return whether *name* is safe enough for automatic assignment."""
    clean = re.sub(r"\s+", " ", name).strip(" \t\r\n|,;:-")
    if not 3 <= len(clean) <= 140:
        return False
    if clean.lower() in _GENERIC_TITLES or _ERROR_NAME_RE.search(clean):
        return False
    if _URL_RE.search(clean) or _DOMAIN_ONLY_RE.fullmatch(clean):
        return False
    if not re.search(r"[A-Za-zÄÖÜäöüß]", clean):
        return False
    # A legal form by itself is not an organisation name.
    if any(re.fullmatch(pat, clean, re.IGNORECASE) for pat, _ in _LEGAL_FORM_PATTERNS):
        return False
    return True


def _name_from_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("title")
    if not tag or not tag.text.strip():
        return None
    text = tag.text.strip()
    for sep in _TITLE_SEPARATORS:
        if sep in text:
            segment = text.split(sep)[0].strip()
            if is_plausible_org_name(segment):
                return segment
    if is_plausible_org_name(text):
        return text
    return None


def _name_from_meta(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content", "").strip():
        name = og["content"].strip()
        return name if is_plausible_org_name(name) else None
    app = soup.find("meta", attrs={"name": "application-name"})
    if app and app.get("content", "").strip():
        name = app["content"].strip()
        return name if is_plausible_org_name(name) else None
    return None


def _name_from_json_ld(html: str) -> str | None:
    blocks = flatten_json_ld(extract_json_ld(html))
    org_types = {"Organization", "Corporation", "LocalBusiness", "NGO",
                 "GovernmentOrganization", "EducationalOrganization",
                 "ResearchOrganization", "Nonprofit"}
    for block in blocks:
        btype = block.get("@type", "")
        if isinstance(btype, list):
            match = any(t in org_types for t in btype)
        else:
            match = btype in org_types
        if match:
            name = block.get("name", "")
            if name and isinstance(name, str) and is_plausible_org_name(name):
                return name.strip()
    return None


def _name_from_impressum(text: str) -> str | None:
    """Heuristic: in Impressum text the org name + legal form often appear in
    the first 600 chars."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text[:1500].splitlines()]
    for line in lines:
        if not line or len(line) > 180:
            continue
        for pattern, display in _LEGAL_FORM_RE:
            m = pattern.search(line)
            if not m:
                continue
            candidate = re.sub(r"^(?:anbieter|betreiber|inhaltlich verantwortlich)\s*:?\s*", "", line, flags=re.IGNORECASE)
            candidate = re.sub(pattern, display, candidate, count=1).strip(" ,;:-")
            if is_plausible_org_name(candidate):
                return candidate
    return None


def extract_org_name(
    html: str,
    page_type: str = "Homepage",
) -> tuple[str, str] | None:
    """Return (name, evidence_snippet) or None.

    Tries sources in priority order, varying by page_type:
      Impressum  → JSON-LD → Impressum heuristic → meta → title
      Other      → JSON-LD → meta → title
    """
    sources: list[tuple[str | None, str]] = []

    if page_type == "Impressum":
        text = BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
        sources = [
            (_name_from_json_ld(html),    "JSON-LD"),
            (_name_from_impressum(text),  "Impressum text"),
        ]
    elif page_type == "Homepage":
        sources = [
            (_name_from_json_ld(html),    "JSON-LD"),
            (_name_from_meta(html),       "meta tag"),
            (_name_from_title(html),      "title tag"),
        ]
    else:
        sources = [
            (_name_from_json_ld(html),    "JSON-LD"),
            (_name_from_meta(html),       "meta tag"),
        ]

    for name, source in sources:
        if name and is_plausible_org_name(name):
            return name, f"[{source}] {name}"

    return None


# ── Organisation type ─────────────────────────────────────────────────────────

# Legal form → Organisation_Type (deterministic)
_LEGAL_FORM_TO_TYPE: dict[str, str] = {
    "gGmbH":              "Company",
    "gUG":                "Company",
    "GmbH":               "Company",
    "GmbH & Co. KG":      "Company",
    "UG":                 "Company",
    "UG (haftungsbeschränkt)": "Company",
    "AG":                 "Company",
    "GbR":                "Company",
    "KG":                 "Company",
    "OHG":                "Company",
    "Ltd":                "Company",
    "Limited":            "Company",
    "Inc":                "Company",
    "LLC":                "Company",
    "e.V.":               "Association",
    "Stiftung":           "Foundation",
    "AöR":                "Public institution",
    "KdöR":               "Public institution",
}

# Text keywords for type inference (only when no legal form is available)
# Each entry: (compiled_re, type)
_TYPE_KEYWORD_RE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(Hochschule|Universit[äa]t|Fachhochschule|TU\b|FH\b|Institut(?:e)? f[üu]r)\b", re.IGNORECASE),
     "University / research institution"),
    (re.compile(r"\b(research\s+institute|research\s+center|research\s+centre)\b", re.IGNORECASE),
     "University / research institution"),
    (re.compile(r"\b(Stadtwerke|Bezirksamt|Senatsverwaltung|Landesamt|Bundesministerium|Ministerium)\b", re.IGNORECASE),
     "Public institution"),
    (re.compile(r"\b(Initiative|Projekt|Netzwerk)\b", re.IGNORECASE),
     "Initiative / project"),
]


def infer_org_type(legal_form: str | None, text: str) -> str | None:
    """Return an Organisation_Type string or None if evidence is insufficient."""
    if legal_form:
        return _LEGAL_FORM_TO_TYPE.get(legal_form)

    # Text keywords — only accept if exactly one type matches to be unambiguous
    matches: set[str] = set()
    for pattern, org_type in _TYPE_KEYWORD_RE:
        if pattern.search(text):
            matches.add(org_type)

    if len(matches) == 1:
        return matches.pop()
    return None


# ── Nonprofit status ──────────────────────────────────────────────────────────

_NONPROFIT_YES_FORMS = {"gGmbH", "gUG"}

_NONPROFIT_NO_FORMS = {"AG", "Ltd", "Limited", "Inc", "LLC",
                       "GmbH", "GmbH & Co. KG", "UG",
                       "UG (haftungsbeschränkt)", "GbR", "KG", "OHG"}

_NONPROFIT_YES_RE = re.compile(
    r"\b(gemeinn[üu]tzig|non-profit|nonprofit|not-for-profit|"
    r"charitable\s+organisation?|charitable\s+organization|"
    r"tax.exempt\s+charitable|wohlt[äa]tig)\b",
    re.IGNORECASE,
)

_NONPROFIT_NO_RE = re.compile(
    r"\b(commercial|for-profit|profit.making|gewinnorientiert)\b",
    re.IGNORECASE,
)


def infer_nonprofit_status(
    legal_form: str | None,
    text: str,
) -> Literal["Yes", "No", "Unclear"]:
    """Return 'Yes', 'No', or 'Unclear'.

    Strong evidence required for 'Yes' or 'No'.
    e.V. and Stiftung alone are NOT sufficient for 'Yes'.
    """
    if legal_form in _NONPROFIT_YES_FORMS:
        return "Yes"

    if _NONPROFIT_YES_RE.search(text):
        return "Yes"

    if legal_form in _NONPROFIT_NO_FORMS and not _NONPROFIT_YES_RE.search(text):
        return "No"

    return "Unclear"
