#!/usr/bin/env python3
"""Step 05: Scrape email addresses from cached website pages.

Reads HTML pages cached by 03_fetch_pages.py and extracts email addresses from:
  - mailto: links
  - Visible page text (standard regex)
  - Common obfuscation patterns: (at), [at], (dot), [dot]
  - JSON-LD email fields

Primary Email selection:
    - A contact name is required, and the email local part must contain a first-
        or last-name fragment.
    - The email domain must match the organisation website domain.
    - Existing source/manual emails are never overwritten; prior website-scraped
        emails and generated alternatives are rebuilt on rerun.

Only other name-matched, same-domain emails are added to Email_Alternatives.

Usage:
    python scripts/enrichment/05_scrape_emails.py [--limit N] [--save-interval N]

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
import unicodedata
from urllib.parse import urlparse

from _audit_writer import AuditRow, VerificationRow, update_audit_file, update_verify_file
from _email_extract import extract_emails
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

CT_EMAIL        = "Email"
CANDIDATE_TYPES = {CT_EMAIL}
STATUS_FIELD    = "Email_Scrape_Status"

_PAGE_PRIORITY = {"Impressum": 6, "Contact": 5, "Footer": 4, "About": 3, "Team": 2, "Homepage": 1}


def _page_rank(page_type: str) -> int:
    return _PAGE_PRIORITY.get(page_type, 1)


def _name_score(email: str, first: str | None, last: str | None) -> int:
    """Return > 0 if the email local part contains a fragment of first or last name."""
    def ascii_fold(value: str) -> str:
        return "".join(
            char for char in unicodedata.normalize("NFKD", value.lower())
            if not unicodedata.combining(char)
        )

    local = ascii_fold(email.split("@")[0])
    terms = [ascii_fold(t) for t in (first, last) if t and len(t) > 1]
    return sum(1 for t in terms if t in local)


def _email_matches_site(email: str, website_norm: str) -> bool:
    email_domain = email.rsplit("@", 1)[-1].lower().removeprefix("www.")
    site_domain = urlparse(website_norm).netloc.lower().split(":")[0].removeprefix("www.")
    return (
        email_domain == site_domain
        or email_domain.endswith("." + site_domain)
        or site_domain.endswith("." + email_domain)
    )


def _select_primary_email(
    all_emails: list[tuple[str, str, str]],  # (email, evidence, page_type)
    first: str | None,
    last: str | None,
) -> str | None:
    """Pick the best primary email.

    Scoring: name-fragment match first, then page priority.
    Returns None if no clear candidate exists (empty list).
    """
    if not all_emails or not (first or last):
        return None

    def _key(item):
        email, _, page_type = item
        name_sc  = _name_score(email, first, last)
        page_sc  = _page_rank(page_type)
        return (name_sc, page_sc)

    best = max(all_emails, key=_key)
    return best[0] if _name_score(best[0], first, last) > 0 else None


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(df, cache, limit, save_interval):
    df = ensure_new_columns(df)
    website_map = build_website_map(df)
    clear_ineligible_website_values(
        df,
        website_map,
        [("Email", "Email_Source")],
        ["Email_Alternatives"],
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

        # Collect all emails with page context
        site_emails: list[tuple[str, str, str]] = []  # (email, evidence, page_type)
        for html, page_type, page_url in pages:
            for email, evidence in extract_emails(html, page_url):
                if _email_matches_site(email, website_norm):
                    site_emails.append((email, evidence, page_type))

        # Deduplicate (case-insensitive), preserving first occurrence
        seen: set[str] = set()
        site_emails_deduped: list[tuple[str, str, str]] = []
        for email, evidence, page_type in site_emails:
            if email.lower() not in seen:
                seen.add(email.lower())
                site_emails_deduped.append((email, evidence, page_type))

        row_ids_str = pipe_join([str(df.at[i, "ID"]) for i in indices if val(df.at[i, "ID"])])

        # Audit rows for all discovered emails
        for email, evidence, page_type in site_emails_deduped:
            all_audit.append(AuditRow(
                Website_Normalized=website_norm,
                Page_URL=next((p for _, pt, p in pages if pt == page_type), ""),
                Page_Type=page_type,
                Candidate_Type=CT_EMAIL,
                Candidate_Value=email,
                Evidence_Text=evidence[:200],
                Related_Row_IDs=row_ids_str,
            ))

        changed = 0
        for idx in indices:
            existing_email = val(df.at[idx, "Email"]) if "Email" in df.columns else None
            # This column is generated by this step, so rebuild it from the
            # current validated candidates instead of retaining stale values.
            existing_alts: list[str] = []
            df.at[idx, "Email_Alternatives"] = None
            existing_source = val(df.at[idx, "Email_Source"]) if "Email_Source" in df.columns else None
            if existing_source and existing_source.lower().startswith("website"):
                df.at[idx, "Email"] = None
                df.at[idx, "Email_Source"] = None
                df.at[idx, "Email_Alternatives"] = None
                existing_email = None
                existing_alts = []

            # Contact name (for scoring)
            first = val(df.at[idx, "Contact_Name_First_Name"]) if "Contact_Name_First_Name" in df.columns else None
            last  = val(df.at[idx, "Contact_Name_Last_Name"])  if "Contact_Name_Last_Name"  in df.columns else None

            if not existing_email and site_emails_deduped:
                primary = _select_primary_email(site_emails_deduped, first, last)
                if primary:
                    df.at[idx, "Email"]        = primary
                    df.at[idx, "Email_Source"] = "Website scrape"
                    existing_email = primary
                    changed += 1

            # For named contacts, only other name-matched same-site addresses
            # are safe alternatives. Generic/unrelated candidates stay audit-only.
            primary_lower = (existing_email or "").lower()
            new_alts = [
                e for e, _, _ in site_emails_deduped
                if e.lower() != primary_lower
                and _name_score(e, first, last) > 0
                and e not in existing_alts
                and e.lower() not in {a.lower() for a in existing_alts}
            ]
            all_alts_deduped: list[str] = []
            seen_alts: set[str] = set()
            for a in existing_alts + new_alts:
                if a.lower() not in seen_alts and a.lower() != primary_lower:
                    seen_alts.add(a.lower())
                    all_alts_deduped.append(a)
            if all_alts_deduped:
                df.at[idx, "Email_Alternatives"] = pipe_join(all_alts_deduped)

        changed_total += changed
        print(f"{len(site_emails_deduped)} emails found  (+{changed} rows)")

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
