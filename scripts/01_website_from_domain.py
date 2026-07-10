#!/usr/bin/env python3
"""Step 02: Set Website from Email_Domain when Website is still missing.

Fallback after Brave search. Rows that already have a Website are untouched.
Sets Website_Source = "Email domain".
Run 0_check_websites.py afterwards to validate and normalise the URL.

Usage:
    python scripts/02_website_from_domain.py

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
"""

import pandas as pd

from _utils import load, save


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "Website_Source" not in df.columns:
        df["Website_Source"] = pd.NA
    return df


def run(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_columns(df)

    mask = df["Website"].isna() & df["Email_Domain"].notna()

    if mask.any():
        df.loc[mask, "Website"] = df.loc[mask, "Email_Domain"]
        df.loc[mask, "Website_Source"] = "Email domain"

    print(f"  Rows updated: {int(mask.sum())}")
    return df


def main() -> None:
    df = load()
    df = run(df)
    save(df)


if __name__ == "__main__":
    main()
