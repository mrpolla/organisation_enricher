#!/usr/bin/env python3
"""Step 01: Derive Website from Email_Domain when Website is still missing.

For each unique Email_Domain, probes URL variants (https/http, www/non-www)
before writing anything. Only domains that return an HTTP response are saved.

On success, populates all website columns in one pass:
  Website               — the bare domain (e.g. zollhof.de)
  Website_Normalized    — the working URL after redirects
  Website_Response_Code — HTTP status code
  Website_Response_Text — short description
  Website_Source        — "Email domain"

Rows with an unreachable domain are left with Website empty.
Safe to rerun — only fills rows where Website is still missing.

Usage:
    python scripts/01_website_from_domain.py

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
"""

from urllib.parse import urlparse

import pandas as pd
import requests
from requests.exceptions import ConnectionError, InvalidURL, SSLError, Timeout

from _utils import load, save

REQUEST_TIMEOUT = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; enrichment-bot/1.0)"}

HTTP_STATUS_TEXT: dict[int, str] = {
    200: "OK", 201: "Created", 301: "Moved Permanently", 302: "Found",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 410: "Gone", 429: "Too Many Requests",
    500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
}


def _status_text(code: int) -> str:
    return HTTP_STATUS_TEXT.get(code, f"HTTP {code}")


def get_variants(domain: str) -> list[str]:
    cleaned = domain.strip()
    if not cleaned.lower().startswith(("http://", "https://")):
        cleaned = "https://" + cleaned
    bare = urlparse(cleaned).netloc.lower().removeprefix("www.")
    if not bare:
        return []
    return [
        f"https://{bare}",
        f"https://www.{bare}",
        f"http://{bare}",
        f"http://www.{bare}",
    ]


def probe(domain: str) -> tuple[str, str, str] | None:
    """Try URL variants. Returns (normalized_url, code, text) on first 2xx
    response, or None if every variant fails or returns an error status."""
    for url in get_variants(domain):
        try:
            resp = requests.get(
                url, timeout=REQUEST_TIMEOUT, allow_redirects=True, headers=HEADERS
            )
            if 200 <= resp.status_code < 300:
                return resp.url, str(resp.status_code), _status_text(resp.status_code)
        except Timeout:
            pass
        except SSLError:
            pass
        except ConnectionError:
            pass
        except (InvalidURL, ValueError):
            pass
    return None  # all variants failed or returned an error


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["Website_Source", "Website_Normalized",
                "Website_Response_Code", "Website_Response_Text"]:
        if col not in df.columns:
            df[col] = pd.NA
    return df


def run(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_columns(df)

    mask = df["Website"].isna() & df["Email_Domain"].notna()
    domains = df.loc[mask, "Email_Domain"].unique().tolist()

    if not domains:
        print("  No rows to process.")
        return df

    print(f"  Probing {len(domains)} unique domain(s)...")

    filled = 0
    for domain in domains:
        print(f"    {domain} ... ", end="", flush=True)
        result = probe(str(domain))
        if result is None:
            print("unreachable — skipped")
            continue
        norm_url, code, text = result
        print(f"{code}  →  {norm_url}")
        row_mask = mask & (df["Email_Domain"] == domain)
        df.loc[row_mask, "Website"] = domain
        df.loc[row_mask, "Website_Normalized"] = norm_url
        df.loc[row_mask, "Website_Response_Code"] = code
        df.loc[row_mask, "Website_Response_Text"] = text
        df.loc[row_mask, "Website_Source"] = "Email domain"
        filled += int(row_mask.sum())

    print(f"  Rows updated: {filled}")
    return df


def main() -> None:
    df = load()
    df = run(df)
    save(df)


if __name__ == "__main__":
    main()

