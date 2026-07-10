#!/usr/bin/env python3
"""Step 1: Analyse input CSV — report missing data and enrichment possibilities.

Usage:
    python scripts/analyse.py

Output:
    Console + data/output/analysis.txt
"""

import pathlib

import pandas as pd

INPUT = pathlib.Path("data/input/contacts_raw.csv")
OUTPUT_DIR = pathlib.Path("data/output")
OUTPUT_FILE = OUTPUT_DIR / "analysis.txt"

ENRICHMENT_COLS = ["Website", "Organisation_Name", "Email", "Contact_Name"]

SEP = "=" * 62
SUB = "-" * 62


def build_report(df: pd.DataFrame) -> str:
    lines: list[str] = []

    def row(label: str, n: int, total: int) -> None:
        pct = f"{n}/{total}"
        lines.append(f"  {label:<52} {n:>3}  ({pct})")

    lines += [SEP, "INPUT ANALYSIS", SEP, ""]

    # ── Missing value counts ─────────────────────────────────────────────────
    lines += ["MISSING VALUES", SUB]

    total = len(df)
    lines.append(f"  {'Total rows':<52} {total}")

    for col in ENRICHMENT_COLS:
        missing = int(df[col].isna().sum())
        row(f"Missing {col}", missing, total)

    no_contact_reachable = int((df["Contact_Name"].isna() & df["Email"].isna()).sum())
    row("Missing Contact_Name AND Email (no contact reachable)", no_contact_reachable, total)
    
    complete = int(df[ENRICHMENT_COLS].notna().all(axis=1).sum())
    row("Fully complete rows (all 4 columns present)", complete, total)

    # ── Enrichment possibilities ─────────────────────────────────────────────
    lines += ["", "ENRICHMENT POSSIBILITIES", SUB]

    has_domain  = df["Email_Domain"].notna()
    has_website = df["Website"].notna()
    has_org     = df["Organisation_Name"].notna()
    has_email   = df["Email"].notna()
    has_contact = df["Contact_Name"].notna()

    miss_website = ~has_website
    miss_email   = ~has_email
    miss_org     = ~has_org

    buckets = [
        (
            "Missing Website + has Organisation_Name",
            "→ Brave search (Step 3)",
            miss_website & has_org,
        ),
        (
            "Missing Website + has Email_Domain or Email",
            "→ Use email domain (Step 4)",
            miss_website & (has_domain | has_email),
        ),
        (
            "Missing Email + has Website + has Contact_Name",
            "→ Website scrape (Step 7)",
            miss_email & has_website & has_contact,
        ),
        (
            "Missing Organisation_Name + has Website or Email_Domain",
            "→ Inferable from domain / web",
            miss_org & (has_website | has_domain),
        ),
        (
            "Has only Contact_Name, nothing else useful",
            "→ Very limited enrichment possible",
            has_contact & ~has_domain & ~has_website & ~has_org & ~has_email,
        ),
        (
            "Almost no useful information",
            "→ Cannot be enriched",
            ~has_domain & ~has_website & ~has_org & ~has_email & ~has_contact,
        ),
    ]

    for label, note, mask in buckets:
        count = int(mask.sum())
        lines.append(f"  {count:>3}  {label}")
        lines.append(f"       {note}")
        if buckets.index((label, note, mask)) < len(buckets) - 1:
            lines.append("")

    lines += ["", SEP]
    return "\n".join(lines)


def main() -> None:
    df = pd.read_csv(INPUT, dtype=str, keep_default_na=False)

    # Strip whitespace and treat empty strings as missing
    df = df.apply(lambda col: col.str.strip())
    df = df.replace("", pd.NA)

    report = build_report(df)

    print(report)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(report + "\n", encoding="utf-8")
    print(f"\nSaved → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
