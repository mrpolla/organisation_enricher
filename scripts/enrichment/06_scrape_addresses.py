#!/usr/bin/env python3
"""Step 06: Scrape postal addresses from cached website pages.

Reads HTML pages cached by 03_fetch_pages.py and extracts postal addresses from:
  - JSON-LD PostalAddress objects
  - Impressum and footer text (German address pattern)
  - Contact page text

Address selection rules:
  - If Address is empty: fill from the best scraped candidate.
  - If scraped address is strictly more complete than existing: replace it
    (move old value to Address_Alternatives).
  - If scraped and existing are equally complete but differ: flag for manual
    verification, keep existing.
    - Additional scraped addresses remain in the audit output for review rather
        than being copied automatically to every row.

Address_Source labels: "Website Impressum", "Website contact page", etc.

Usage:
    python scripts/enrichment/06_scrape_addresses.py [--limit N] [--save-interval N]

Flags:
  --limit N          Process at most N new sites per run.
  --save-interval N  Save contacts_enriched.csv every N sites (default 20).

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_clean.csv)
Output: data/output/contacts_enriched.csv
        data/output/website_scrape_results.csv
        data/output/enrichment_manual_verification.csv
        data/cache/website_scrape_cache.csv
"""

from __future__ import annotations

import argparse

from _address_extract import completeness_score, extract_addresses, is_more_complete
from _audit_writer import AuditRow, VerificationRow, update_audit_file, update_verify_file
from _candidates import Candidate, select_best
from _html_extract import extract_json_ld
from _page_fetcher import load_cached_pages
from _utils import (
    build_website_map,
    clear_ineligible_website_values,
    ensure_new_columns,
    load,
    pipe_join,
    save,
    val,
)
from _website_cache import WebsiteCache

CT_ADDRESS      = "Address"
CANDIDATE_TYPES = {CT_ADDRESS}
STATUS_FIELD    = "Address_Scrape_Status"

_PAGE_PRIORITY = {"Impressum": 6, "Contact": 5, "Footer": 4, "About": 3, "Team": 2, "Homepage": 1}

_PAGE_TYPE_TO_SOURCE = {
    "Impressum": "Website Impressum",
    "Contact":   "Website contact page",
    "About":     "Website about page",
    "Homepage":  "Website homepage",
}


def _source_label(page_type: str) -> str:
    return _PAGE_TYPE_TO_SOURCE.get(page_type, f"Website {page_type.lower()}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(df, cache, limit, save_interval):
    df = ensure_new_columns(df)
    website_map = build_website_map(df)
    clear_ineligible_website_values(
        df,
        website_map,
        [("Address", "Address_Source")],
        ["Address_Alternatives"],
    )

    all_audit:  list[AuditRow]        = []
    all_verify: list[VerificationRow] = []
    processed = changed_total = 0

    print(f"  Websites to check: {len(website_map)}")

    for website_norm, indices in website_map.items():
        if limit is not None and processed >= limit:
            print(f"  Limit of {limit} reached.")
            break
        pages = load_cached_pages(website_norm)
        if not pages:
            continue

        print(f"  {website_norm} ... ", end="", flush=True)

        # Collect address candidates across all pages
        addr_candidates: list[Candidate] = []
        for html, page_type, page_url in pages:
            json_ld = extract_json_ld(html)
            for addr, evidence in extract_addresses(html, json_ld, page_url):
                addr_candidates.append(Candidate(
                    website_normalized=website_norm,
                    page_url=page_url,
                    page_type=page_type,
                    candidate_type=CT_ADDRESS,
                    value=addr,
                    evidence_text=evidence[:200],
                    extra_score=completeness_score(addr),
                ))

        row_ids_str = pipe_join([str(df.at[i, "ID"]) for i in indices if val(df.at[i, "ID"])])

        # Audit rows
        best_result = select_best(addr_candidates)
        best_addr = best_result[0] if best_result else None
        for c in addr_candidates:
            is_sel = bool(best_addr and c.value == best_addr.value and c.page_url == best_addr.page_url)
            all_audit.append(AuditRow(
                Website_Normalized=c.website_normalized,
                Page_URL=c.page_url,
                Page_Type=c.page_type,
                Candidate_Type=CT_ADDRESS,
                Candidate_Value=c.value,
                Evidence_Text=c.evidence_text,
                Selected=is_sel,
                Selection_Reason=f"Score {c.total_score}" if is_sel else "",
                Related_Row_IDs=row_ids_str,
            ))

        all_addr_values = list(dict.fromkeys(c.value for c in addr_candidates))
        changed = 0

        for idx in indices:
            existing = val(df.at[idx, "Address"]) if "Address" in df.columns else None
            df.at[idx, "Address_Alternatives"] = None
            existing_source = val(df.at[idx, "Address_Source"]) if "Address_Source" in df.columns else None
            if existing_source and existing_source.lower().startswith("website"):
                df.at[idx, "Address"] = None
                df.at[idx, "Address_Source"] = None
                df.at[idx, "Address_Alternatives"] = None
                existing = None

            if not best_addr:
                continue
            if not existing:
                df.at[idx, "Address"]        = best_addr.value
                df.at[idx, "Address_Source"] = _source_label(best_addr.page_type)
                changed += 1
            elif is_more_complete(best_addr.value, existing):
                df.at[idx, "Address"]        = best_addr.value
                df.at[idx, "Address_Source"] = _source_label(best_addr.page_type)
                changed += 1
            elif (completeness_score(best_addr.value) == completeness_score(existing)
                  and best_addr.value.strip().lower() != existing.strip().lower()):
                all_verify.append(VerificationRow(
                    Related_Row_IDs=str(df.at[idx, "ID"]),
                    Website_Normalized=website_norm,
                    Issue_Type="Conflicting Address",
                    Existing_Value=existing,
                    Candidate_Values=best_addr.value,
                    Evidence_URLs=best_addr.page_url,
                    Reason="Existing and scraped address are equally complete but differ",
                ))

            if len(all_addr_values) > 1:
                all_verify.append(VerificationRow(
                    Related_Row_IDs=str(df.at[idx, "ID"]),
                    Website_Normalized=website_norm,
                    Issue_Type="Multiple scraped addresses",
                    Existing_Value=existing or "",
                    Candidate_Values=pipe_join(all_addr_values),
                    Evidence_URLs=pipe_join(list(dict.fromkeys(c.page_url for c in addr_candidates))),
                    Reason="Multiple complete postal addresses found; only the highest-priority one was selected",
                ))

        changed_total += changed
        print(f"{len(addr_candidates)} candidates  (+{changed} rows)")

        cache.set_done(website_norm, STATUS_FIELD)
        cache.save()
        processed += 1

        if processed % save_interval == 0:
            print(f"  [checkpoint] saving after {processed} sites...")
            save(df)

    return df, all_audit, all_verify, processed, changed_total


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit",         type=int, default=None)
    p.add_argument("--save-interval", type=int, default=20)
    args = p.parse_args()

    df    = load()
    cache = WebsiteCache.load()
    df, audit_rows, verify_rows, processed, changed = run(
        df, cache, args.limit, args.save_interval
    )
    save(df)
    update_audit_file(audit_rows,  CANDIDATE_TYPES)
    update_verify_file(verify_rows, CANDIDATE_TYPES)
    print(f"\n  Sites processed: {processed}  |  Rows updated: {changed}")


if __name__ == "__main__":
    main()
