#!/usr/bin/env python3
"""Step 04: Scrape organisation metadata from cached website pages.

Reads HTML pages cached by 03_fetch_pages.py and extracts:
  Organisation_Name / Organisation_Name_Source
  Organisation_Type / Organisation_Type_Source
  Organisation_Legal_Form / Organisation_Legal_Form_Source
  Organisation_Nonprofit_Status / Organisation_Nonprofit_Source

Source labels reflect the page type:
  "Website Impressum", "Website contact page", "Website about page",
  "Website structured data", "Website homepage"

Existing source/manual values are never overwritten. Values previously created
by website scraping are replaced (or cleared) on every rerun.

Usage:
    python scripts/enrichment/04_scrape_org_metadata.py [--limit N] [--save-interval N]

Flags:
  --limit N          Process at most N new sites per run.
  --save-interval N  Save contacts_enriched.csv every N sites (default 20).

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_clean.csv)
Output: data/output/contacts_enriched.csv  (updated in place)
        data/output/website_scrape_results.csv
        data/output/enrichment_manual_verification.csv
        data/cache/website_scrape_cache.csv
"""

from __future__ import annotations

import argparse

from _audit_writer import (
    AuditRow,
    VerificationRow,
    update_audit_file,
    update_verify_file,
)
from _candidates import Candidate, detect_conflicts, group_by_type, select_best
from _html_extract import extract_json_ld, page_text
from _org_extract import (
    extract_legal_form,
    extract_org_name,
    infer_nonprofit_status,
    infer_org_type,
)
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

CT_NAME      = "Organisation_Name"
CT_LEGAL     = "Organisation_Legal_Form"
CT_TYPE      = "Organisation_Type"
CT_NONPROFIT = "Organisation_Nonprofit_Status"
CANDIDATE_TYPES = {CT_NAME, CT_LEGAL, CT_TYPE, CT_NONPROFIT}
STATUS_FIELD    = "Org_Meta_Status"

_PAGE_TYPE_TO_SOURCE = {
    "Impressum": "Website Impressum",
    "Contact":   "Website contact page",
    "About":     "Website about page",
    "Team":      "Website about page",
    "Homepage":  "Website homepage",
}


def _source_label(page_type: str) -> str:
    return _PAGE_TYPE_TO_SOURCE.get(page_type, f"Website {page_type.lower()}")


# ── Candidate extraction ──────────────────────────────────────────────────────

def _candidates_from_page(
    html: str,
    page_type: str,
    page_url: str,
    website_norm: str,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    text = page_text(html)

    name_result = extract_org_name(html, page_type)
    if name_result:
        name, evidence = name_result
        candidates.append(Candidate(
            website_normalized=website_norm,
            page_url=page_url,
            page_type=page_type,
            candidate_type=CT_NAME,
            value=name,
            evidence_text=evidence[:200],
        ))

    # Legal metadata must be tied to the validated entity name. Searching the
    # complete page picks up unrelated companies in navigation/privacy text.
    lf_result = extract_legal_form(name_result[0]) if name_result else None
    if lf_result:
        legal_form, evidence = lf_result
        candidates.append(Candidate(
            website_normalized=website_norm,
            page_url=page_url,
            page_type=page_type,
            candidate_type=CT_LEGAL,
            value=legal_form,
            evidence_text=evidence[:200],
        ))
        org_type = infer_org_type(legal_form, text)
        if org_type:
            candidates.append(Candidate(
                website_normalized=website_norm,
                page_url=page_url,
                page_type=page_type,
                candidate_type=CT_TYPE,
                value=org_type,
                evidence_text=f"Derived from legal form: {legal_form}",
            ))
        np_status = infer_nonprofit_status(legal_form, text)
        candidates.append(Candidate(
            website_normalized=website_norm,
            page_url=page_url,
            page_type=page_type,
            candidate_type=CT_NONPROFIT,
            value=np_status,
            evidence_text=f"Legal form: {legal_form}",
        ))
    return candidates


# ── Apply results to df rows ──────────────────────────────────────────────────

def _apply(df, indices, selected, verify_rows, row_ids_str) -> int:
    changed = 0
    fields = [
        (CT_NAME,      "Organisation_Name",           "Organisation_Name_Source"),
        (CT_LEGAL,     "Organisation_Legal_Form",      "Organisation_Legal_Form_Source"),
        (CT_TYPE,      "Organisation_Type",            "Organisation_Type_Source"),
        (CT_NONPROFIT, "Organisation_Nonprofit_Status", "Organisation_Nonprofit_Source"),
    ]
    for ctype, col, src_col in fields:
        best = selected.get(ctype)
        for idx in indices:
            existing = val(df.at[idx, col]) if col in df.columns else None
            existing_source = val(df.at[idx, src_col]) if src_col in df.columns else None
            if existing_source and existing_source.lower().startswith("website"):
                df.at[idx, col] = None
                df.at[idx, src_col] = None
                existing = None
            if not best:
                continue
            source_label = _source_label(best.page_type)
            if existing:
                if existing.strip().lower() != best.value.strip().lower():
                    verify_rows.append(VerificationRow(
                        Related_Row_IDs=str(df.at[idx, "ID"]),
                        Website_Normalized=best.website_normalized,
                        Issue_Type=f"Existing {col} conflicts with scraped value",
                        Existing_Value=existing,
                        Candidate_Values=best.value,
                        Evidence_URLs=best.page_url,
                        Reason=f"Row has existing {col}; scraped value differs",
                    ))
                continue
            df.at[idx, col]     = best.value
            df.at[idx, src_col] = source_label
            changed += 1
    return changed


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(df, cache, limit, save_interval):
    df = ensure_new_columns(df)
    website_map = build_website_map(df)
    clear_ineligible_website_values(df, website_map, [
        ("Organisation_Name", "Organisation_Name_Source"),
        ("Organisation_Legal_Form", "Organisation_Legal_Form_Source"),
        ("Organisation_Type", "Organisation_Type_Source"),
        ("Organisation_Nonprofit_Status", "Organisation_Nonprofit_Source"),
    ])

    all_audit:  list[AuditRow]         = []
    all_verify: list[VerificationRow]  = []
    processed = changed_total = 0

    print(f"  Websites to check: {len(website_map)}")

    for website_norm, indices in website_map.items():
        if limit is not None and processed >= limit:
            print(f"  Limit of {limit} reached.")
            break
        pages = load_cached_pages(website_norm)
        if not pages:
            continue  # not yet fetched — run 03_fetch_pages.py first

        print(f"  {website_norm} ... ", end="", flush=True)
        all_candidates: list[Candidate] = []
        for html, page_type, page_url in pages:
            all_candidates.extend(_candidates_from_page(html, page_type, page_url, website_norm))

        by_type = group_by_type(all_candidates)
        selected = {}
        for ctype, cs in by_type.items():
            result = select_best(cs)
            if result:
                selected[ctype] = result[0]

        row_ids_str = pipe_join([str(df.at[i, "ID"]) for i in indices if val(df.at[i, "ID"])])

        # Conflict detection
        for ctype, cs in by_type.items():
            for group in detect_conflicts(cs):
                values = " | ".join(dict.fromkeys(c.value for c in group))
                urls   = " | ".join(dict.fromkeys(c.page_url for c in group))
                all_verify.append(VerificationRow(
                    Related_Row_IDs=row_ids_str,
                    Website_Normalized=website_norm,
                    Issue_Type=f"Conflicting {ctype}",
                    Candidate_Values=values,
                    Evidence_URLs=urls,
                    Reason="Multiple pages report different values at comparable authority",
                ))

        # Audit rows
        for c in all_candidates:
            best = selected.get(c.candidate_type)
            is_sel = bool(best and best.value == c.value and best.page_url == c.page_url)
            all_audit.append(AuditRow(
                Website_Normalized=c.website_normalized,
                Page_URL=c.page_url,
                Page_Type=c.page_type,
                Candidate_Type=c.candidate_type,
                Candidate_Value=c.value,
                Evidence_Text=c.evidence_text,
                Selected=is_sel,
                Selection_Reason=(
                    f"{c.page_type} priority {c.priority_score}" if is_sel else ""
                ),
                Related_Row_IDs=row_ids_str,
            ))

        changed = _apply(df, indices, selected, all_verify, row_ids_str)
        changed_total += changed
        print(f"{'ok' if selected else 'no data'}  (+{changed} rows)")

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
