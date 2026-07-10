#!/usr/bin/env python3
"""Step 04: Find missing email addresses for named contacts.

Applies to rows where Email is missing, Contact_Name_First_Name +
Contact_Name_Last_Name are available, and a website is known.

Three strategies, tried in order — stops at the first success:
  1. Scrape internal pages: /impressum, /kontakt, /contact, /team, /about, …
  2. Site-specific Brave search: site:{domain} "First Last"
  3. General Brave search: "First Last" "Organisation Name" email

Email is matched to the contact by finding the address closest in the page
text to any occurrence of the contact's first or last name (within 500 chars).

Scraped pages are cached under data/cache/pages/{domain}/{page}.html.
Brave results are cached under data/cache/brave/{slug}.json.

Sets Email_Source = "Website scrape" or "Brave search".

Usage:
    python scripts/04_scrape_emails.py

Requires:
    BRAVE_API_KEY in .env

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
        data/cache/pages/…
        data/cache/brave/…
"""

import json
import os
import pathlib
import re
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.exceptions import RequestException

from _utils import DIRECTORY_DOMAINS, load as load_csv, save, slugify

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_CACHE_DIR = pathlib.Path("data/cache/brave")
PAGES_CACHE_DIR = pathlib.Path("data/cache/pages")
BRAVE_RESULTS = 5

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; enrichment-bot/1.0)"}
REQUEST_TIMEOUT = 10

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
NAME_PROXIMITY = 500  # max chars between email and name to count as a match

CONTACT_PAGES = [
    "/impressum",
    "/kontakt",
    "/contact",
    "/team",
    "/about",
    "/ueber-uns",
    "/about-us",
    "",  # homepage — last resort
]


# ── Page cache ────────────────────────────────────────────────────────────────

def _page_cache_path(url: str) -> pathlib.Path:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    slug = re.sub(r"[^\w]", "_", parsed.path.strip("/")) or "index"
    return PAGES_CACHE_DIR / domain / f"{slug}.html"


def fetch_page(url: str) -> str | None:
    cache = _page_cache_path(url)
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")
    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT, headers=HEADERS, allow_redirects=True
        )
        if resp.ok and "text/html" in resp.headers.get("Content-Type", ""):
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(resp.text, encoding="utf-8", errors="replace")
            return resp.text
    except RequestException:
        pass
    return None


# ── Text / email helpers ──────────────────────────────────────────────────────

def html_to_text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


def find_email_near_name(text: str, first: str, last: str) -> str | None:
    """Return the email address closest to any occurrence of first or last name,
    provided the email's local part also contains at least one name fragment.
    Generic addresses like contact@, info@, hello@ are rejected."""
    emails = [(m.start(), m.group()) for m in EMAIL_RE.finditer(text)]
    if not emails:
        return None

    terms = [t.lower() for t in (first, last) if t and len(t) > 1]
    if not terms:
        return None

    text_lower = text.lower()
    name_positions: list[int] = []
    for term in terms:
        pos = 0
        while (idx := text_lower.find(term, pos)) != -1:
            name_positions.append(idx)
            pos = idx + 1

    if not name_positions:
        return None

    # Sort emails by proximity to any name occurrence
    ranked = sorted(
        emails,
        key=lambda ep: min(abs(ep[0] - np) for np in name_positions),
    )

    for email_pos, email in ranked:
        dist = min(abs(email_pos - np) for np in name_positions)
        if dist > NAME_PROXIMITY:
            break  # remaining are even further away
        # The local part must contain at least one name fragment
        local = email.split("@")[0].lower()
        if any(term in local for term in terms):
            return email

    return None


# ── Brave helpers ─────────────────────────────────────────────────────────────

def get_api_key() -> str:
    load_dotenv()
    key = os.getenv("BRAVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BRAVE_API_KEY not set in .env")
    return key


def brave_search(query: str, api_key: str) -> list[dict]:
    """Brave search with JSON cache. Returns list of result dicts."""
    cache_path = BRAVE_CACHE_DIR / f"{slugify(query)}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8")).get("web", {}).get("results", [])

    resp = requests.get(
        BRAVE_API_URL,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": BRAVE_RESULTS},
        timeout=15,
    )
    content_type = resp.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        raise RuntimeError(
            f"Brave API returned {content_type!r} — check key at "
            "https://api.search.brave.com/app/keys"
        )
    data = resp.json()
    BRAVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data.get("web", {}).get("results", [])


def _is_directory(url: str) -> bool:
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    return any(domain == d or domain.endswith("." + d) for d in DIRECTORY_DOMAINS)


# ── Strategies ────────────────────────────────────────────────────────────────

def _base_url(row: pd.Series) -> str | None:
    for col in ("Website_Normalized", "Website"):
        val = row.get(col)
        if pd.notna(val) and str(val).strip():
            p = urlparse(str(val).strip())
            return f"{p.scheme}://{p.netloc}"
    return None


def _scrape_pages(first: str, last: str, base: str) -> str | None:
    for path in CONTACT_PAGES:
        url = (base + path) if path else base
        html = fetch_page(url)
        if html:
            email = find_email_near_name(html_to_text(html), first, last)
            if email:
                print(f"      ✓ page scrape  → {url}")
                return email
    return None


def _site_brave(first: str, last: str, domain: str, api_key: str) -> str | None:
    for query in (f'site:{domain} "{first} {last}"', f'site:{domain} "{last} {first}"'):
        for r in brave_search(query, api_key)[:3]:
            html = fetch_page(r.get("url", ""))
            if html:
                email = find_email_near_name(html_to_text(html), first, last)
                if email:
                    print(f"      ✓ site search  → {r['url']}")
                    return email
    return None


def _general_brave(first: str, last: str, org: str, api_key: str) -> str | None:
    if not org:
        return None
    query = f'"{first} {last}" "{org}" email'
    for r in brave_search(query, api_key)[:3]:
        url = r.get("url", "")
        if _is_directory(url):
            continue
        html = fetch_page(url)
        if html:
            email = find_email_near_name(html_to_text(html), first, last)
            if email:
                print(f"      ✓ general search → {url}")
                return email
    return None


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, api_key: str) -> pd.DataFrame:
    if "Email_Source" not in df.columns:
        df["Email_Source"] = pd.NA

    no_first = "Contact_Name_First_Name" not in df.columns
    has_name = (
        pd.Series(False, index=df.index) if no_first
        else df["Contact_Name_First_Name"].notna() & df["Contact_Name_Last_Name"].notna()
    )
    has_site = df["Website"].notna() | (
        df["Website_Normalized"].notna() if "Website_Normalized" in df.columns
        else pd.Series(False, index=df.index)
    )
    mask = df["Email"].isna() & has_name & has_site

    if not mask.any():
        print("  No candidates (need missing Email + parsed name + website).")
        return df

    print(f"  Processing {int(mask.sum())} row(s)...")

    for idx, row in df[mask].iterrows():
        first = str(row["Contact_Name_First_Name"])
        last = str(row["Contact_Name_Last_Name"])
        org = str(row.get("Organisation_Name") or "")
        base = _base_url(row)
        domain = urlparse(base).netloc.lower().removeprefix("www.") if base else ""

        print(f"    {first} {last} @ {domain or '?'}")

        email = source = None

        if base:
            email = _scrape_pages(first, last, base)
            source = "Website scrape"

        if not email and domain:
            email = _site_brave(first, last, domain, api_key)
            source = "Brave search"

        if not email:
            email = _general_brave(first, last, org, api_key)
            source = "Brave search"

        if email:
            df.at[idx, "Email"] = email
            df.at[idx, "Email_Source"] = source
        else:
            print("      ✗ not found")

    return df


def main() -> None:
    api_key = get_api_key()
    df = load_csv()
    df = run(df, api_key)
    save(df)


if __name__ == "__main__":
    main()
