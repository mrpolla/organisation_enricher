"""Postal address extraction from HTML pages.

Targets German postal addresses (street + house number + 5-digit postcode
+ city) and international addresses via JSON-LD PostalAddress.

Public API
----------
extract_addresses(html, json_ld, page_url) -> list[tuple[str, str]]
completeness_score(address)                -> int   (0–4)
is_more_complete(addr_a, addr_b)           -> bool
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from _html_extract import extract_json_ld, flatten_json_ld, snippet

# ── German address regex ──────────────────────────────────────────────────────
# Matches:  Street Name <house number>, <postcode> <city>
# Postcode must be exactly 5 digits (German standard).
# House number: digits optionally followed by letter(s) and/or / and digits.

_STREET_PAT = (
    r"[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-\.]{1,30}"
    r"(?:\s+[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-\.]{1,30}){0,3}\s*"
    r"(?:stra(?:ße|sse)|str\.|weg|gasse|platz|allee|ring|damm|ufer|steig|berg|chaussee)"
    r"\.?"
)
_HOUSENR_PAT = r"\s*\d{1,4}\s*[a-zA-Z]?(?:\s*/\s*\d{1,4}\s*[a-zA-Z]?)?"
_POSTCODE_PAT = r"\d{5}"
_CITY_PAT = r"[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-]{1,30}(?:\s+[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-]{1,30}){0,2}"

_GERMAN_ADDRESS_RE = re.compile(
    r"(?P<street>" + _STREET_PAT + r")"
    r"(?P<housenr>" + _HOUSENR_PAT + r")"
    r"[,\n\r]?\s*"
    r"(?P<postcode>" + _POSTCODE_PAT + r")"
    r"\s+"
    r"(?P<city>" + _CITY_PAT + r")",
    re.IGNORECASE | re.UNICODE,
)

# Also match compact form: "Musterstr. 12, 10115 Berlin"
_COMPACT_ADDRESS_RE = re.compile(
    r"(?P<street>[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-\.]{2,40}(?:str(?:aße|\.)?|weg|gasse|platz|allee|ring|damm|ufer|steig|berg|chaussee)\.?)"
    r"\s+(?P<housenr>\d{1,4}\s*[a-zA-Z]?)"
    r"(?:[,\s]+)"
    r"(?P<postcode>\d{5})"
    r"\s+"
    r"(?P<city>[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-\s]{1,40})",
    re.IGNORECASE | re.UNICODE,
)

_EVIDENCE_WIDTH = 160


def _normalise_address(match: re.Match[str]) -> str:
    street = match.group("street").strip().rstrip(",")
    tokens = street.split()
    if len(tokens) > 1:
        suffix_only = re.fullmatch(
            r"(?:stra(?:ße|sse)|str\.|weg|gasse|platz|allee|ring|damm|ufer|steig|berg|chaussee)\.?",
            tokens[-1], re.IGNORECASE,
        )
        street = " ".join(tokens[-2:]) if suffix_only else tokens[-1]
    housenr = match.group("housenr").strip()
    postcode = match.group("postcode").strip()
    city = match.group("city").strip().rstrip(".,;")
    city = re.split(
        r"\s+(?:Telefon|Tel\.?|Phone|E-?Mail|Fax)\b",
        city,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    return f"{street} {housenr}, {postcode} {city}"


# ── JSON-LD PostalAddress ─────────────────────────────────────────────────────

def _addresses_from_json_ld(json_ld: list[dict[str, Any]]) -> list[tuple[str, str]]:
    blocks = flatten_json_ld(json_ld)
    results: list[tuple[str, str]] = []

    for block in blocks:
        # PostalAddress may be nested under address or contactPoint
        candidates: list[Any] = []
        for key in ("address", "contactPoint", "location"):
            val = block.get(key)
            if val:
                candidates.extend(val if isinstance(val, list) else [val])
        if block.get("@type") == "PostalAddress":
            candidates.append(block)

        for c in candidates:
            if not isinstance(c, dict):
                continue
            # Unwrap if it's a contactPoint containing address
            if c.get("@type") == "ContactPoint" and "address" in c:
                addr_obj = c["address"]
                candidates.append(addr_obj)
                continue
            if c.get("@type") != "PostalAddress":
                continue
            street = str(c.get("streetAddress", "")).strip()
            postcode = str(c.get("postalCode", "")).strip()
            city = str(c.get("addressLocality", "")).strip()
            country = str(c.get("addressCountry", "")).strip()

            if not (street and postcode and city and re.search(r"\d", street)):
                continue

            parts = [p for p in [street, postcode + (" " + city if city else ""), country] if p]
            address = ", ".join(parts)
            evidence = f"[JSON-LD PostalAddress] {address}"
            results.append((address, evidence))

    return results


# ── Text-based extraction ─────────────────────────────────────────────────────

def _addresses_from_text(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for pattern in (_GERMAN_ADDRESS_RE, _COMPACT_ADDRESS_RE):
        for m in pattern.finditer(text):
            try:
                addr = _normalise_address(m)
            except IndexError:
                continue
            if addr in seen:
                continue
            seen.add(addr)
            ev = snippet(text, m.start(), width=_EVIDENCE_WIDTH)
            results.append((addr, ev))

    return results


def _address_blocks(html: str) -> list[str]:
    """Return short DOM blocks likely to contain one coherent address."""
    soup = BeautifulSoup(html, "html.parser")
    blocks: list[str] = []
    for tag in soup.find_all(["address", "p", "li", "td", "div"]):
        text = tag.get_text(" ", strip=True)
        if 10 <= len(text) <= 240 and re.search(r"\b\d{5}\b", text):
            blocks.append(text)
    return list(dict.fromkeys(blocks))


# ── Completeness scoring ──────────────────────────────────────────────────────

_HAS_POSTCODE_RE = re.compile(r"\b\d{5}\b")
_HAS_HOUSENR_RE = re.compile(r"\b\d{1,4}\s*[a-zA-Z]?\b")
_HAS_STREET_RE = re.compile(
    r"(?:[A-ZÄÖÜa-zäöüß]{3,}(?:stra(?:ße|sse)|str\.|weg|gasse|platz|allee|ring|damm|ufer|steig|berg)|"
    r"[A-ZÄÖÜa-zäöüß]{3,}\s+(?:Straße|Str\.|Weg|Gasse|Platz|Allee|Ring|Damm|Ufer|Steig|Berg))",
    re.IGNORECASE,
)
_HAS_CITY_RE = re.compile(r"(?:Berlin|Hamburg|München|Köln|\b[A-ZÄÖÜ][a-zäöüß]{3,}\b)")


def completeness_score(address: str) -> int:
    """Score 0–4: presence of street name, house number, postcode, city."""
    score = 0
    if _HAS_STREET_RE.search(address):
        score += 1
    if _HAS_HOUSENR_RE.search(address):
        score += 1
    if _HAS_POSTCODE_RE.search(address):
        score += 1
    if _HAS_CITY_RE.search(address):
        score += 1
    return score


def is_more_complete(addr_a: str, addr_b: str) -> bool:
    """Return True if addr_a is strictly more complete than addr_b."""
    return completeness_score(addr_a) > completeness_score(addr_b)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_addresses(
    html: str,
    json_ld: list[dict[str, Any]] | None = None,
    page_url: str = "",
) -> list[tuple[str, str]]:
    """Return all discovered (address, evidence) pairs.

    Sources tried in order:
      1. JSON-LD PostalAddress (if *json_ld* provided or extracted from *html*)
            2. Short DOM blocks containing complete German postal addresses

    Duplicate addresses (case-insensitive normalisation) are removed.
    """
    if json_ld is None:
        json_ld = extract_json_ld(html)

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(pairs: list[tuple[str, str]]) -> None:
        for addr, ev in pairs:
            key = re.sub(r"\s+", " ", addr).strip().lower()
            if key not in seen:
                seen.add(key)
                results.append((addr, ev))

    _add(_addresses_from_json_ld(json_ld))
    for block in _address_blocks(html):
        _add(_addresses_from_text(block))

    return [(addr, ev) for addr, ev in results if completeness_score(addr) == 4]
