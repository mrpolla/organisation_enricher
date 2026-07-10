#!/usr/bin/env python3
"""Step 01: Find Website by Organisation_Name using Brave Search API.

For each unique Organisation_Name where Website is missing:
  - Checks data/cache/brave/{slug}.json first (no API call if cached)
  - Queries Brave Search API (10 results) if not cached
  - Scores results by keyword overlap with the org name
  - Selects the highest-scoring result (ties broken by rank)
  - Writes the best URL to contacts_enriched.csv
  - Never overwrites an existing Website

Saves all candidates to data/output/brave_candidates.csv for inspection.
Sets Website_Source = "Brave search".

Usage:
    python scripts/01_brave_search.py

Requires:
    BRAVE_API_KEY in .env

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
        data/output/brave_candidates.csv
        data/cache/brave/{slug}.json      (one per unique org name)
"""

import json
import os
import pathlib
import re
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

from _utils import load as load_csv
from _utils import save

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
RESULTS_COUNT = 10
CACHE_DIR = pathlib.Path("data/cache/brave")
CANDIDATES_FILE = pathlib.Path("data/output/brave_candidates.csv")

# Words that carry no signal when scoring a URL against an org name
STOPWORDS = {
    "the", "and", "for", "with",
    "von", "van", "de", "der", "die", "das", "und", "fur",
    "gmbh", "ag", "ev", "mbh", "inc", "ltd", "llc",
}

# Third-party directories and profile sites — never the org's own website
DIRECTORY_DOMAINS = {
    "wikipedia.org",
    "linkedin.com", "xing.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "vimeo.com",
    "northdata.com", "dnb.com", "crunchbase.com",
    "bloomberg.com", "glassdoor.com", "kununu.com", "indeed.com",
    "usgbc.org", "worldgbc.org",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    load_dotenv()
    key = os.getenv("BRAVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BRAVE_API_KEY not set in .env")
    return key


def slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:80]


def extract_keywords(org_name: str) -> set[str]:
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]+", org_name.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def score(url: str, title: str, keywords: set[str]) -> int:
    """Score a result. Domain matches count double; known directory sites are penalised.
    Title is intentionally excluded: any page *about* a company carries the company
    name in its title, making title-based scoring unreliable."""
    from urllib.parse import urlparse
    parsed = urlparse(url if url.startswith("http") else "https://" + url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()

    for d in DIRECTORY_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return -1

    points = 0
    for kw in keywords:
        if kw in domain:
            points += 2   # keyword in domain = strong signal (own site)
        elif kw in path:
            points += 1   # keyword only in path = weaker signal
    return points


# ── Cache ─────────────────────────────────────────────────────────────────────

def load_cache(slug: str) -> dict[str, Any] | None:
    path = CACHE_DIR / f"{slug}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_cache(slug: str, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{slug}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── API ───────────────────────────────────────────────────────────────────────

def brave_search(query: str, api_key: str) -> dict[str, Any]:
    resp = requests.get(
        BRAVE_API_URL,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        params={"q": query, "count": RESULTS_COUNT},
        timeout=15,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        raise RuntimeError(
            f"Brave API returned {content_type!r} instead of JSON "
            f"(status {resp.status_code}). "
            "The API key may be invalid, expired, or missing Web Search access. "
            "Check your key at https://api.search.brave.com/app/keys"
        )
    return resp.json()


def get_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("web", {}).get("results", [])


def best_result(
    results: list[dict[str, Any]], keywords: set[str]
) -> dict[str, Any] | None:
    if not results:
        return None
    scored = [
        (score(r.get("url", ""), r.get("title", ""), keywords), i, r)
        for i, r in enumerate(results)
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2]


# ── Main logic ────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, api_key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "Website_Source" not in df.columns:
        df["Website_Source"] = pd.NA

    mask = df["Website"].isna() & df["Organisation_Name"].notna()
    orgs = df.loc[mask, "Organisation_Name"].unique().tolist()

    if not orgs:
        print("  Nothing to search — all rows already have a website or no org name.")
        return df, pd.DataFrame()

    print(f"  Searching {len(orgs)} unique organisation(s)...")
    all_candidates: list[dict] = []

    for org in orgs:
        slug = slugify(str(org))
        print(f"    {org!r} ... ", end="", flush=True)

        data = load_cache(slug)
        if data is not None:
            print("(cached) ", end="", flush=True)
        else:
            data = brave_search(str(org), api_key)
            write_cache(slug, data)

        results = get_results(data)
        keywords = extract_keywords(str(org))
        pick = best_result(results, keywords)

        best_url = pick.get("url", "") if pick else ""
        print(f"{len(results)} result(s)  best → {best_url or 'none'}")

        for rank, r in enumerate(results, start=1):
            url = r.get("url", "")
            title = r.get("title", "")
            all_candidates.append({
                "Organisation_Name": org,
                "Rank": rank,
                "Score": score(url, title, keywords),
                "Selected": "yes" if pick and url == best_url else "no",
                "URL": url,
                "Title": title,
                "Description": r.get("description", ""),
            })

        if best_url:
            org_mask = mask & (df["Organisation_Name"] == org)
            df.loc[org_mask, "Website"] = best_url
            df.loc[org_mask, "Website_Source"] = "Brave search"

    return df, pd.DataFrame(all_candidates)


def main() -> None:
    api_key = get_api_key()
    df = load_csv()
    df, candidates = run(df, api_key)
    save(df)

    if not candidates.empty:
        CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Append to existing candidates file if present, to preserve results
        # from previous runs for different orgs
        if CANDIDATES_FILE.exists():
            existing = pd.read_csv(CANDIDATES_FILE, dtype=str)
            orgs_in_run = candidates["Organisation_Name"].unique()
            existing = existing[~existing["Organisation_Name"].isin(orgs_in_run)]
            candidates = pd.concat([existing, candidates], ignore_index=True)
        candidates.to_csv(CANDIDATES_FILE, index=False)
        print(f"Candidates saved → {CANDIDATES_FILE}")


if __name__ == "__main__":
    main()
