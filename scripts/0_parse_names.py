#!/usr/bin/env python3
"""Step 0: Parse Contact_Name into first and last name components.

Fills Contact_Name_First_Name and Contact_Name_Last_Name only when both
parts are clearly identifiable (e.g. single-word usernames are skipped).
Sets Contact_Name_Source = "Parsed from contact name".
Never modifies the original Contact_Name.

Usage:
    python scripts/0_parse_names.py

Input:  data/output/contacts_enriched.csv  (falls back to data/input/contacts_raw.csv)
Output: data/output/contacts_enriched.csv
"""

import pandas as pd
from nameparser import HumanName

from _utils import load, save


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["Contact_Name_First_Name", "Contact_Name_Last_Name", "Contact_Name_Source"]:
        if col not in df.columns:
            df[col] = pd.NA
    return df


def parse_name(raw: str) -> tuple[str | None, str | None]:
    """Return (first, last) when both are clearly identifiable, else (None, None)."""
    p = HumanName(raw)
    first = p.first.strip() or None
    last = p.last.strip() or None
    if first and last:
        return first, last
    return None, None


def run(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_columns(df)

    # Only rows that have a contact name and haven't been parsed yet
    mask = df["Contact_Name"].notna() & df["Contact_Name_First_Name"].isna()
    count = 0
    skipped = 0

    for idx in df[mask].index:
        first, last = parse_name(str(df.at[idx, "Contact_Name"]))
        if first and last:
            df.at[idx, "Contact_Name_First_Name"] = first
            df.at[idx, "Contact_Name_Last_Name"] = last
            df.at[idx, "Contact_Name_Source"] = "Parsed from contact name"
            count += 1
        else:
            skipped += 1

    print(f"  Parsed: {count} row(s)  |  Skipped (unclear): {skipped} row(s)")
    return df


def main() -> None:
    df = load()
    df = run(df)
    save(df)


if __name__ == "__main__":
    main()
