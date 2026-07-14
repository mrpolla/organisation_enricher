"""HTML content extraction helpers.

Provides clean text, JSON-LD blocks, footer text, and main content
extraction from raw HTML strings.

Public API
----------
page_text(html)          -> str
extract_json_ld(html)    -> list[dict]
extract_footer_text(html)-> str
extract_main_text(html)  -> str
snippet(text, around, width) -> str
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

# Tags whose content should be stripped from visible text
_NOISE_TAGS = {"script", "style", "noscript", "template", "head"}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ── Text extraction ───────────────────────────────────────────────────────────

def page_text(html: str) -> str:
    """Return all visible text from an HTML page, with noise tags removed."""
    soup = _soup(html)
    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def extract_footer_text(html: str) -> str:
    """Return text from <footer> or elements whose class/id contains 'footer'."""
    soup = _soup(html)
    parts: list[str] = []

    # Semantic <footer> elements
    for footer in soup.find_all("footer"):
        text = footer.get_text(separator=" ", strip=True)
        if text:
            parts.append(text)

    # Divs/sections with footer in class or id
    if not parts:
        for el in soup.find_all(True):
            if not isinstance(el, Tag):
                continue
            attrs_combined = " ".join(
                str(v) for v in (el.get("class", []) + [el.get("id", "") or ""])
            ).lower()
            if "footer" in attrs_combined:
                text = el.get_text(separator=" ", strip=True)
                if text and text not in parts:
                    parts.append(text)

    return " ".join(parts)


def extract_main_text(html: str) -> str:
    """Return text from <main>, <article>, or full body as fallback."""
    soup = _soup(html)

    for tag_name in ("main", "article"):
        tag = soup.find(tag_name)
        if tag:
            return tag.get_text(separator=" ", strip=True)

    body = soup.find("body")
    if body:
        return body.get_text(separator=" ", strip=True)

    return page_text(html)


# ── JSON-LD ───────────────────────────────────────────────────────────────────

def extract_json_ld(html: str) -> list[dict[str, Any]]:
    """Parse and return all JSON-LD blocks found in the page."""
    soup = _soup(html)
    results: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                results.extend(d for d in data if isinstance(d, dict))
            elif isinstance(data, dict):
                results.append(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return results


def flatten_json_ld(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand @graph containers so callers get a flat list of objects."""
    flat: list[dict[str, Any]] = []
    for block in blocks:
        if "@graph" in block and isinstance(block["@graph"], list):
            flat.extend(b for b in block["@graph"] if isinstance(b, dict))
        else:
            flat.append(block)
    return flat


# ── Snippet helper ────────────────────────────────────────────────────────────

def snippet(text: str, around: int, width: int = 120) -> str:
    """Return a ±(width//2) character window centred on *around* in *text*."""
    half = width // 2
    start = max(0, around - half)
    end = min(len(text), around + half)
    raw = text[start:end].strip()
    # Collapse whitespace
    return re.sub(r"\s+", " ", raw)
