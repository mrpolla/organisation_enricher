#!/usr/bin/env python3
"""Step 03: Extract Organisation_Name from website when Organisation_Name is missing.

For each unique Website (prefers Website_Normalized), fetches the page and
attempts to extract the organisation name from, in order:
  1. <meta property="og:site_name">   — most reliable, set explicitly by the site
  2. <meta name="application-name">   — also explicit
  3. <title>                          — first segment before | - – —

Sets Organisation_Name_Source = "Name from website".
Never overwrites an existing Organisation_Name.

Usage:
    python scripts/03_org_name_from_website.py

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
"""

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException

from _utils import load, save

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; enrichment-bot/1.0)"}
REQUEST_TIMEOUT = 10


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "Organisation_Name_Source" not in df.columns:
        df["Organisation_Name_Source"] = pd.NA
    return df


def effective_url(row: pd.Series) -> str | None:
    """Prefer Website_Normalized over Website."""
    for col in ("Website_Normalized", "Website"):
        val = row.get(col)
        if pd.notna(val) and str(val).strip():
            return str(val).strip()
    return None


GENERIC_TITLES = {
    "home", "welcome", "homepage", "start", "startseite",
    "index", "untitled", "willkommen",
}


def extract_org_name(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")

    og = soup.find("meta", property="og:site_name")
    if og and og.get("content", "").strip():
        return og["content"].strip()

    app = soup.find("meta", attrs={"name": "application-name"})
    if app and app.get("content", "").strip():
        return app["content"].strip()

    title_tag = soup.find("title")
    if title_tag and title_tag.text.strip():
        text = title_tag.text.strip()
        for sep in [" | ", " - ", " – ", " — ", " : "]:
            if sep in text:
                text = text.split(sep)[0].strip()
                break
        if len(text) > 2 and text.lower() not in GENERIC_TITLES:
            return text

    return None


def fetch_and_extract(url: str) -> str | None:
    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT, headers=HEADERS, allow_redirects=True
        )
        if resp.ok and "text/html" in resp.headers.get("Content-Type", ""):
            return extract_org_name(resp.text)
    except RequestException:
        pass
    return None


def run(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_columns(df)

    has_website = df["Website"].notna()
    if "Website_Normalized" in df.columns:
        has_website = has_website | df["Website_Normalized"].notna()
    mask = df["Organisation_Name"].isna() & has_website

    # Deduplicate: check each unique URL once
    url_map: dict[str, list[int]] = {}
    for idx in df[mask].index:
        url = effective_url(df.loc[idx])
        if url:
            url_map.setdefault(url, []).append(idx)

    if not url_map:
        print("  No rows to process.")
        return df

    print(f"  Fetching {len(url_map)} unique website(s)...")
    count = 0

    for url, indices in url_map.items():
        print(f"    {url} ... ", end="", flush=True)
        name = fetch_and_extract(url)
        if name:
            print(name)
            for idx in indices:
                df.at[idx, "Organisation_Name"] = name
                df.at[idx, "Organisation_Name_Source"] = "Name from website"
            count += len(indices)
        else:
            print("not found")

    print(f"  Rows updated: {count}")
    return df


def main() -> None:
    df = load()
    df = run(df)
    save(df)


if __name__ == "__main__":
    main()
