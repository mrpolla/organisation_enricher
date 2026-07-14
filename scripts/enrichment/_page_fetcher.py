"""HTTP page fetching with disk cache, page discovery, safety limits, and
per-domain page manifests.

Cache layout: data/cache/pages/{domain}/_pages_v2/{slug}__{url_hash}.html
Manifest:     data/cache/pages/{domain}/_sites_v2/{site_hash}/_manifest.json

Public API
----------
fetch_page(url, *, cache_dir, timeout, max_bytes, max_retries, force) -> str | None
discover_pages(base_url, homepage_html, *, max_pages)               -> list[(url, page_type)]
load_cached_pages(website_normalized, cache_dir)                    -> list[(html, page_type, url)]
write_manifest(website_normalized, pages, cache_dir)
read_manifest(website_normalized, cache_dir)                        -> list[dict] | None
remove_manifest(website_normalized, cache_dir)
normalise_url(url)                                                  -> str
base_url(url)                                                       -> str
same_domain(url_a, url_b)                                           -> bool
"""

from __future__ import annotations

import json
import hashlib
import pathlib
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException

from _utils import DIRECTORY_DOMAINS

# ── Constants ─────────────────────────────────────────────────────────────────

PAGES_CACHE_DIR   = pathlib.Path("data/cache/pages")
REQUEST_TIMEOUT   = 10
MAX_RESPONSE_BYTES = 500_000
MAX_RETRIES       = 2
MAX_PAGES_PER_SITE = 5
RETRY_DELAY       = 1.5
_MANIFEST_FILENAME = "_manifest.json"
_CACHE_VERSION = 2

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; enrichment-bot/1.0)"}

_PAGE_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("Impressum", ["impressum", "imprint", "legal-notice", "legal_notice"]),
    ("Contact",   ["kontakt", "contact", "kontaktiere", "reach-us", "get-in-touch"]),
    ("About",     ["ueber-uns", "uber-uns", "about-us", "about_us", "about",
                   "wer-wir-sind", "who-we-are", "unternehmen", "company",
                   "über-uns"]),
    ("Team",      ["team", "people", "staff", "our-team", "mitarbeiter"]),
]

_COMMON_SUFFIXES: list[tuple[str, str]] = [
    ("/impressum",  "Impressum"),
    ("/imprint",    "Impressum"),
    ("/kontakt",    "Contact"),
    ("/contact",    "Contact"),
    ("/ueber-uns",  "About"),
    ("/uber-uns",   "About"),
    ("/about-us",   "About"),
    ("/about",      "About"),
    ("/team",       "Team"),
]

_SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".mp3", ".mp4", ".avi", ".mov",
    ".css", ".js",
}


# ── URL helpers ───────────────────────────────────────────────────────────────

def normalise_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path   = parsed.path.rstrip("/")
    query  = ("?" + parsed.query) if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def base_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return f"{(parsed.scheme or 'https').lower()}://{parsed.netloc.lower()}"


def same_domain(url_a: str, url_b: str) -> bool:
    def _bare(u: str) -> str:
        return urlparse(u).netloc.lower().removeprefix("www.")
    return _bare(url_a) == _bare(url_b)


def _is_directory_domain(url: str) -> bool:
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    return any(domain == d or domain.endswith("." + d) for d in DIRECTORY_DOMAINS)


def _has_skippable_extension(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)


# ── Disk cache ────────────────────────────────────────────────────────────────

def _cache_path(url: str, cache_dir: pathlib.Path) -> pathlib.Path:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    digest = hashlib.sha256(normalise_url(url).encode("utf-8")).hexdigest()[:20]
    slug = re.sub(r"[^\w]", "_", parsed.path.strip("/")) or "index"
    return cache_dir / domain / "_pages_v2" / f"{slug[:50]}__{digest}.html"


def _manifest_path(website_normalized: str, cache_dir: pathlib.Path) -> pathlib.Path:
    domain = urlparse(website_normalized).netloc.lower().removeprefix("www.")
    digest = hashlib.sha256(
        normalise_url(website_normalized).encode("utf-8")
    ).hexdigest()[:20]
    return cache_dir / domain / "_sites_v2" / digest / _MANIFEST_FILENAME


_SOFT_ERROR_TITLE_RE = re.compile(
    r"(?:\b404\b|not found|seite nicht gefunden|page not found|"
    r"access denied|zugriff verweigert|service unavailable)",
    re.IGNORECASE,
)


def is_error_page(html: str) -> bool:
    """Detect common soft HTTP errors that incorrectly return status 200."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if _SOFT_ERROR_TITLE_RE.search(title):
        return True
    heading = " ".join(
        tag.get_text(" ", strip=True) for tag in soup.find_all(["h1", "h2"], limit=3)
    )
    return bool(_SOFT_ERROR_TITLE_RE.fullmatch(heading.strip()))


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_page(
    url: str,
    *,
    cache_dir: pathlib.Path = PAGES_CACHE_DIR,
    timeout: int = REQUEST_TIMEOUT,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_retries: int = MAX_RETRIES,
    force: bool = False,
) -> str | None:
    """Fetch *url* returning validated HTTP-200 HTML, or ``None``.

    Valid cache files are reused unless *force* is true. Error responses,
    redirects to unrelated domains, non-HTML responses, and soft-404 pages are
    never written to disk.
    """
    if _has_skippable_extension(url) or _is_directory_domain(url):
        return None
    cache = _cache_path(url, cache_dir)
    if not force and cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, headers=HEADERS,
                                allow_redirects=True, stream=True)
            if resp.status_code != 200:
                if resp.status_code >= 500 and attempt < max_retries:
                    time.sleep(RETRY_DELAY)
                    continue
                return None
            if not same_domain(resp.url, url):
                return None
            if "text/html" not in resp.headers.get("Content-Type", ""):
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=32_768):
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            html = b"".join(chunks).decode("utf-8", errors="replace")
            if is_error_page(html):
                return None
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(html, encoding="utf-8", errors="replace")
            return html
        except RequestException:
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
    return None


# ── Page discovery ────────────────────────────────────────────────────────────

def _classify_path(path: str) -> str | None:
    lower = path.lower()
    for page_type, patterns in _PAGE_TYPE_PATTERNS:
        if any(pat in lower for pat in patterns):
            return page_type
    return None


def discover_pages(
    website_url: str,
    homepage_html: str,
    *,
    max_pages: int = MAX_PAGES_PER_SITE,
) -> list[tuple[str, str]]:
    """Return up to *max_pages* (url, page_type) same-domain pairs."""
    found: dict[str, str] = {}

    soup = BeautifulSoup(homepage_html, "html.parser")
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(website_url.rstrip("/") + "/", href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if not same_domain(absolute, website_url) or _has_skippable_extension(absolute):
            continue
        page_type = _classify_path(parsed.path)
        if page_type and page_type not in found.values():
            found[normalise_url(absolute)] = page_type
        if len(found) >= max_pages:
            break

    if len(found) < max_pages:
        already = set(found.values())
        for suffix, page_type in _COMMON_SUFFIXES:
            if page_type in already:
                continue
            norm = normalise_url(website_url.rstrip("/") + suffix)
            if norm not in found:
                found[norm] = page_type
                already.add(page_type)
            if len(found) >= max_pages:
                break

    return list(found.items())


# ── Manifest ──────────────────────────────────────────────────────────────────

def write_manifest(
    website_normalized: str,
    pages: list[dict],
    cache_dir: pathlib.Path = PAGES_CACHE_DIR,
) -> None:
    """Write page manifest atomically after ALL pages for a site are fetched.

    Each entry: {"page_type": str, "url": str}
    """
    path = _manifest_path(website_normalized, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": _CACHE_VERSION,
        "website_normalized": normalise_url(website_normalized),
        "pages": pages,
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def read_manifest(
    website_normalized: str,
    cache_dir: pathlib.Path = PAGES_CACHE_DIR,
) -> list[dict] | None:
    path = _manifest_path(website_normalized, cache_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("cache_version") != _CACHE_VERSION:
            return None
        pages = data.get("pages")
        return pages if isinstance(pages, list) else None
    except (json.JSONDecodeError, OSError):
        return None


def remove_manifest(
    website_normalized: str,
    cache_dir: pathlib.Path = PAGES_CACHE_DIR,
) -> None:
    """Remove the valid-site marker before a forced refresh."""
    path = _manifest_path(website_normalized, cache_dir)
    if path.exists():
        path.unlink()


def load_cached_pages(
    website_normalized: str,
    cache_dir: pathlib.Path = PAGES_CACHE_DIR,
) -> list[tuple[str, str, str]]:
    """Return [(html, page_type, page_url), …] from the on-disk cache.

    Reads the manifest; loads each cached HTML file. No network calls.
    """
    manifest = read_manifest(website_normalized, cache_dir)
    if not manifest:
        return []
    result: list[tuple[str, str, str]] = []
    for entry in manifest:
        url       = entry.get("url", "")
        page_type = entry.get("page_type", "Unknown")
        path = _cache_path(url, cache_dir)
        if not path.exists():
            return []
        html = path.read_text(encoding="utf-8", errors="replace")
        if html and not is_error_page(html):
            result.append((html, page_type, url))
    return result
