#!/usr/bin/env python3
"""Step 0: Check and normalize Website values.

For every non-empty Website, tries URL variants in order:
  1. https://domain
  2. https://www.domain
  3. http://domain
  4. http://www.domain

Stops on the first variant that returns any HTTP response.
Follows redirects; Website_Normalized holds the final URL.

Each unique Website is checked once per run (deduplication).
Safe to rerun — results are always refreshed.

Populates:
  Website_Normalized    — final URL after redirects
  Website_Response_Code — HTTP status code, or: TIMEOUT / SSL_ERROR /
                          CONNECTION_ERROR / DNS_ERROR / INVALID_URL
  Website_Response_Text — short readable description

Usage:
    python scripts/0_check_websites.py

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
"""

from urllib.parse import urlparse

import pandas as pd
import requests
from requests.exceptions import ConnectionError, InvalidURL, SSLError, Timeout

from _utils import load, save

REQUEST_TIMEOUT = 10  # seconds per attempt

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; enrichment-bot/1.0)"}

HTTP_STATUS_TEXT: dict[int, str] = {
    200: "OK",
    201: "Created",
    301: "Moved Permanently",
    302: "Found",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    410: "Gone",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _status_text(code: int) -> str:
    return HTTP_STATUS_TEXT.get(code, f"HTTP {code}")


def get_variants(website: str) -> list[str]:
    """Return the 4 canonical URL variants for a website string, HTTPS first."""
    cleaned = website.strip()
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


def check_website(website: str) -> tuple[str, str, str]:
    """
    Try each variant until one responds. Returns:
        (normalized_url, response_code_str, response_text)
    """
    variants = get_variants(website)
    if not variants:
        return website, "INVALID_URL", "Invalid URL"

    last: tuple[str, str, str] = (website, "INVALID_URL", "Invalid URL")

    for url in variants:
        try:
            resp = requests.get(
                url, timeout=REQUEST_TIMEOUT, allow_redirects=True, headers=HEADERS
            )
            return resp.url, str(resp.status_code), _status_text(resp.status_code)
        except Timeout:
            last = (url, "TIMEOUT", "Timeout")
        except SSLError:
            last = (url, "SSL_ERROR", "SSL error")
        except ConnectionError as exc:
            msg = str(exc)
            if "NameResolutionError" in msg or "getaddrinfo" in msg:
                last = (url, "DNS_ERROR", "DNS error")
            else:
                last = (url, "CONNECTION_ERROR", "Connection failed")
        except (InvalidURL, ValueError):
            last = (url, "INVALID_URL", "Invalid URL")

    return last


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["Website_Normalized", "Website_Response_Code", "Website_Response_Text"]:
        if col not in df.columns:
            df[col] = pd.NA
    return df


def run(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_columns(df)

    websites = df.loc[df["Website"].notna(), "Website"].unique().tolist()
    if not websites:
        print("  No websites to check.")
        return df

    print(f"  Checking {len(websites)} unique website(s)...")
    cache: dict[str, tuple[str, str, str]] = {}

    for website in websites:
        print(f"    {website} ... ", end="", flush=True)
        result = check_website(str(website))
        cache[website] = result
        print(result[1])

    for website, (norm_url, code, text) in cache.items():
        mask = df["Website"] == website
        df.loc[mask, "Website_Normalized"] = norm_url
        df.loc[mask, "Website_Response_Code"] = code
        df.loc[mask, "Website_Response_Text"] = text

    return df


def main() -> None:
    df = load()
    df = run(df)
    save(df)


if __name__ == "__main__":
    main()
