#!/usr/bin/env python3
"""Step 03: Fetch and cache website pages.

For each unique website whose Website_Response_Code is exactly 200, fetches:
  - Homepage
  - Up to MAX_PAGES_PER_SITE additional pages (Impressum, Contact, About, Team)
    discovered from internal links and common URL patterns

All HTML is saved to data/cache/pages/{domain}/.
A URL-isolated, versioned manifest records which pages were successfully
fetched. Restarts skip sites that already have a valid manifest.

Does NOT modify contacts_enriched.csv.

After this step run:
  04_scrape_org_metadata.py
  05_scrape_emails.py
  06_scrape_addresses.py

Usage:
    python scripts/enrichment/03_fetch_pages.py [--limit N] [--force-rescrape]

Flags:
  --limit N         Process at most N new sites per run (cached sites excluded).
  --force-rescrape  Ignore existing manifests and re-fetch everything.

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_clean.csv)
Output: data/cache/pages/{domain}/_manifest.json  (one per website)
        data/cache/website_scrape_cache.csv
"""

from __future__ import annotations

import argparse
from _page_fetcher import (
    MAX_PAGES_PER_SITE,
    discover_pages,
    fetch_page,
    read_manifest,
    remove_manifest,
    write_manifest,
)
from _utils import build_website_map, load
from _website_cache import WebsiteCache


def run(limit: int | None = None, force_rescrape: bool = False) -> None:
    df = load()
    cache = WebsiteCache.load()
    website_map = build_website_map(df)
    all_websites = build_website_map(df, require_http_200=False)

    total    = len(website_map)
    skipped  = 0
    fetched  = 0
    failed   = 0
    processed = 0  # newly attempted (not cached)

    print(f"  Websites in dataset: {total}")
    print(f"  Skipped (not HTTP 200): {len(all_websites) - total}")

    for website_norm, _indices in website_map.items():
        if limit is not None and processed >= limit:
            print(f"  Limit of {limit} reached.")
            break

        # Skip if manifest already exists (restartable)
        if not force_rescrape and read_manifest(website_norm) is not None:
            skipped += 1
            continue

        print(f"  {website_norm} ... ", end="", flush=True)
        processed += 1
        if force_rescrape:
            remove_manifest(website_norm)

        homepage_html = fetch_page(website_norm, force=force_rescrape)
        if not homepage_html:
            print("failed (homepage)")
            failed += 1
            cache.set(website_norm, {
                "Scrape_Status": "failed",
                "Pages_Checked": "",
                "HTTP_Status":   "FETCH_ERROR",
                "Error_Message": "Homepage could not be fetched",
            })
            cache.save()
            continue

        pages_fetched = [{"page_type": "Homepage", "url": website_norm}]

        for page_url, page_type in discover_pages(website_norm, homepage_html,
                                                   max_pages=MAX_PAGES_PER_SITE):
            html = fetch_page(page_url, force=force_rescrape)
            if html:
                pages_fetched.append({"page_type": page_type, "url": page_url})

        # Write manifest atomically AFTER all pages are done
        write_manifest(website_norm, pages_fetched)

        types_str = " | ".join(p["page_type"] for p in pages_fetched)
        print(f"ok  ({types_str})")
        fetched += 1

        cache.set(website_norm, {
            "Scrape_Status": "ok",
            "Pages_Checked": types_str,
            "HTTP_Status":   "200",
            "Error_Message": "",
        })
        cache.save()

    print()
    print(f"  Total in dataset : {total}")
    print(f"  Skipped (cached) : {skipped}")
    print(f"  Fetched          : {fetched}")
    print(f"  Failed           : {failed}")


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch and cache website pages.")
    p.add_argument("--limit",         type=int, default=None,
                   help="Max new sites to fetch per run.")
    p.add_argument("--force-rescrape", action="store_true",
                   help="Ignore existing manifests and re-fetch.")
    args = p.parse_args()
    run(limit=args.limit, force_rescrape=args.force_rescrape)


if __name__ == "__main__":
    main()
