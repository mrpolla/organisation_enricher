"""Email extraction from HTML pages.

Handles:
  - mailto: href links
  - Visible text (standard email regex)
  - Common obfuscation patterns: (at), [at], (dot), [dot], " at ", " dot "
  - JSON-LD 'email' fields

All discovered emails are returned as (normalised_lowercase, evidence_snippet)
pairs.  The caller is responsible for selecting the best candidate.

Public API
----------
extract_emails(html, page_url)            -> list[tuple[str, str]]
deduplicate_emails(pairs)                 -> list[tuple[str, str]]
"""

from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from _html_extract import extract_json_ld, flatten_json_ld, snippet

# ── Regexes ───────────────────────────────────────────────────────────────────

# Standard email address
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Obfuscation variants — normalised to @/. before validation
_OBFUSCATION_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+"          # local part
    r"\s*"
    r"(?:\(at\)|\[at\]|\bat\b)"     # @ substitute
    r"\s*"
    r"[a-zA-Z0-9.\-]+"              # domain without TLD (before dot substitute)
    r"\s*"
    r"(?:\(dot\)|\[dot\]|\bdot\b)"  # . substitute (at least one)
    r"\s*"
    r"[a-zA-Z]{2,}",                # TLD
    re.IGNORECASE,
)

_EVIDENCE_WIDTH = 120  # chars around each email

# Domains that appear in examples / privacy notices — not real contact emails
_EXAMPLE_DOMAINS = {
    "example.com", "example.org", "example.net",
    "domain.com", "yourdomain.com", "mustermann.de",
    "yourcompany.com",
}

# Generic local-parts that are very likely not personal addresses but are still
# valid org contacts — we keep them but track that they're generic
_GENERIC_LOCALS = {
    "info", "contact", "kontakt", "hello", "hallo", "mail",
    "office", "admin", "support", "service", "team", "post",
    "press", "presse", "redaktion", "anfrage", "anfragen",
    "welcome", "willkommen", "noreply", "no-reply",
}


def _normalise_obfuscated(raw: str) -> str | None:
    """Convert an obfuscated email-like string to a proper email, or None."""
    s = raw.strip()
    # Replace (at) / [at] / \bat\b → @
    s = re.sub(r"\s*(?:\(at\)|\[at\]|\bat\b)\s*", "@", s, flags=re.IGNORECASE)
    # Replace (dot) / [dot] / \bdot\b → .
    s = re.sub(r"\s*(?:\(dot\)|\[dot\]|\bdot\b)\s*", ".", s, flags=re.IGNORECASE)
    # Remove remaining whitespace
    s = re.sub(r"\s+", "", s)
    # Validate
    if _EMAIL_RE.fullmatch(s):
        return s.lower()
    return None


def _is_example(email: str) -> bool:
    domain = email.split("@", 1)[-1].lower()
    local = email.split("@", 1)[0].lower()
    return (
        domain in _EXAMPLE_DOMAINS
        or local in {"noreply", "no-reply", "donotreply", "do-not-reply"}
        or domain.startswith("example.")
    )


def _collect_from_mailto(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[tuple[str, str]] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if not href.lower().startswith("mailto:"):
            continue
        raw = href[7:]  # strip "mailto:"
        # Remove query string (subject=…, body=…)
        raw = raw.split("?")[0]
        # URL-decode
        raw = urllib.parse.unquote(raw).strip().lower()
        if _EMAIL_RE.fullmatch(raw) and not _is_example(raw):
            label = tag.get_text(strip=True) or raw
            results.append((raw, f"[mailto] {label}"))
    return results


def _collect_from_json_ld(html: str) -> list[tuple[str, str]]:
    blocks = flatten_json_ld(extract_json_ld(html))
    results: list[tuple[str, str]] = []
    for block in blocks:
        email_val = block.get("email")
        if not email_val:
            continue
        for raw in (email_val if isinstance(email_val, list) else [email_val]):
            raw = str(raw).strip().lower()
            if raw.startswith("mailto:"):
                raw = raw[7:]
            if _EMAIL_RE.fullmatch(raw) and not _is_example(raw):
                btype = block.get("@type", "structured data")
                results.append((raw, f"[JSON-LD {btype}] {raw}"))
    return results


def _collect_from_text(html: str) -> list[tuple[str, str]]:
    """Find emails in visible page text (plain regex + obfuscation patterns)."""
    from _html_extract import page_text as _page_text
    text = _page_text(html)
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Plain emails
    for m in _EMAIL_RE.finditer(text):
        email = m.group().lower()
        if email in seen or _is_example(email):
            continue
        seen.add(email)
        ev = snippet(text, m.start(), width=_EVIDENCE_WIDTH)
        results.append((email, ev))

    # Obfuscated
    for m in _OBFUSCATION_RE.finditer(text):
        email = _normalise_obfuscated(m.group())
        if email and email not in seen and not _is_example(email):
            seen.add(email)
            ev = snippet(text, m.start(), width=_EVIDENCE_WIDTH)
            results.append((email, ev))

    return results


# ── Public API ────────────────────────────────────────────────────────────────

def extract_emails(html: str, page_url: str = "") -> list[tuple[str, str]]:
    """Return all discovered (email, evidence) pairs from *html*.

    Sources tried (in order, all results merged):
      1. mailto: links
      2. JSON-LD email fields
      3. Visible text + obfuscation patterns
    """
    results: list[tuple[str, str]] = []
    results.extend(_collect_from_mailto(html))
    results.extend(_collect_from_json_ld(html))
    results.extend(_collect_from_text(html))
    return results


def deduplicate_emails(
    pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Remove duplicate emails (case-insensitive), preserving first occurrence."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for email, evidence in pairs:
        key = email.lower()
        if key not in seen:
            seen.add(key)
            out.append((email.lower(), evidence))
    return out
