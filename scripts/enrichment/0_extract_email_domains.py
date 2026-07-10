#!/usr/bin/env python3
"""Step 0: Extract Email_Domain from Email when Email_Domain is missing.

Never overwrites an existing Email_Domain.
Sets Email_Domain_Source = "Derived from email".

Usage:
    python scripts/0_extract_email_domains.py

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
"""

import pandas as pd

from _utils import load, save


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "Email_Domain_Source" not in df.columns:
        df["Email_Domain_Source"] = pd.NA
    return df


def run(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_columns(df)

    mask = df["Email"].notna() & df["Email_Domain"].isna()

    if mask.any():
        extracted = (
            df.loc[mask, "Email"]
            .str.split("@", n=1)
            .str[1]
            .str.strip()
            .str.lower()
        )
        filled_idx = extracted.dropna().index
        df.loc[filled_idx, "Email_Domain"] = extracted.loc[filled_idx]
        df.loc[filled_idx, "Email_Domain_Source"] = "Derived from email"
        count = len(filled_idx)
    else:
        count = 0

    print(f"  Rows updated: {count}")
    return df


def main() -> None:
    df = load()
    df = run(df)
    save(df)


if __name__ == "__main__":
    main()
