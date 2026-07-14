#!/usr/bin/env python3
"""Step 07: Split enriched combined dataset into organisation and contact tables.

Reads the enriched contacts_enriched.csv and produces:

  data/output/organisations_enriched.csv   — one row per unique organisation
  data/output/contacts_enriched_split.csv  — all contacts with Organisation_ID

The combined working file (contacts_enriched.csv) is NOT overwritten.

Organisation_ID is an increasing integer (1, 2, 3, ...) assigned in the order
each unique organisation first appears in the input. Numbering restarts on
each run.

Grouping key (in priority order per row):
  1. Website_Normalized
  2. Website
  3. Email_Domain

Multiple contacts under the same organisation key remain separate rows in
the contact output.

Usage:
    python scripts/enrichment/07_split_outputs.py

Input:  data/output/contacts_enriched.csv
Output: data/output/organisations_enriched.csv
        data/output/contacts_enriched_split.csv
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from _utils import ENRICHED, load

ORGS_OUTPUT = ENRICHED.parent / "organisations_enriched.csv"
CONTACTS_SPLIT_OUTPUT = ENRICHED.parent / "contacts_enriched_split.csv"

# Organisation-level columns (values are collapsed per group)
ORG_COLS = [
    "Organisation_Name",
    "Organisation_Name_Source",
    "Organisation_Type",
    "Organisation_Type_Source",
    "Organisation_Legal_Form",
    "Organisation_Legal_Form_Source",
    "Organisation_Nonprofit_Status",
    "Organisation_Nonprofit_Source",
    "Website",
    "Website_Normalized",
    "Website_Source",
    "Website_Response_Code",
    "Website_Response_Text",
    "Email_Domain",
    "Address",
    "Address_Source",
    "Address_Alternatives",
]

# Contact-level columns preserved in the split output
CONTACT_COLS = [
    "ID",
    "Organisation_ID",   # inserted
    "Contact_Name",
    "Contact_Name_First_Name",
    "Contact_Name_Last_Name",
    "Contact_Name_Source",
    "Email",
    "Email_Source",
    "Email_Alternatives",
    "Phone",
    "Phone_Alternatives",
    "LinkedIn",
    "Role",
    "Source_File",
    "Source_Row",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _val(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "none", "<na>") else None


def _org_key(row: pd.Series) -> str | None:
    """Return the grouping key for *row*, or None if none is available."""
    for col in ("Website_Normalized", "Website", "Email_Domain"):
        v = _val(row.get(col))
        if v:
            return v.lower().rstrip("/")
    return None


def _best_value(series: pd.Series) -> str | None:
    """Return the most common non-null value in *series*, preferring longer values."""
    values = series.dropna().apply(lambda v: str(v).strip()).replace("", pd.NA).dropna()
    if values.empty:
        return None
    # Prefer longer (more complete) values; break ties by frequency
    ranked = values.value_counts().reset_index()
    ranked.columns = ["value", "count"]
    ranked["length"] = ranked["value"].str.len()
    ranked = ranked.sort_values(["length", "count"], ascending=False)
    return str(ranked.iloc[0]["value"])


# ── Organisation table ────────────────────────────────────────────────────────

def build_organisations(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse df rows by org key into one row per organisation."""
    # Add org key column
    df = df.copy()
    df["_org_key"] = df.apply(_org_key, axis=1)

    # Only rows that have an org key can be grouped
    keyed = df[df["_org_key"].notna()].copy()

    org_rows: list[dict] = []

    for org_id, (key, group) in enumerate(
        keyed.groupby("_org_key", sort=False), start=1
    ):
        row: dict[str, Any] = {
            "Organisation_ID": org_id,
            "Organisation_Key": key,
        }
        for col in ORG_COLS:
            if col in group.columns:
                row[col] = _best_value(group[col])

        # Related contact IDs
        if "ID" in group.columns:
            row["Related_Contact_IDs"] = " | ".join(
                str(v) for v in group["ID"].dropna().unique()
            )
        row["Contact_Count"] = len(group)
        org_rows.append(row)

    cols = (
        ["Organisation_ID", "Organisation_Key"]
        + [c for c in ORG_COLS if c not in ("Organisation_ID", "Organisation_Key")]
        + ["Related_Contact_IDs", "Contact_Count"]
    )
    orgs_df = pd.DataFrame(org_rows)
    # Keep only columns that actually exist
    final_cols = [c for c in cols if c in orgs_df.columns]
    return orgs_df[final_cols]


# ── Contact split table ───────────────────────────────────────────────────────

def build_contacts_split(df: pd.DataFrame, orgs_df: pd.DataFrame) -> pd.DataFrame:
    """Add Organisation_ID to every contact row."""
    df = df.copy()
    df["_org_key"] = df.apply(_org_key, axis=1)

    # Build mapping: org_key → Organisation_ID
    if "Organisation_Key" in orgs_df.columns and "Organisation_ID" in orgs_df.columns:
        key_to_id = dict(zip(orgs_df["Organisation_Key"], orgs_df["Organisation_ID"]))
    else:
        key_to_id = {}

    # Nullable integer keeps missing IDs blank without serialising valid IDs as 1.0.
    df["Organisation_ID"] = df["_org_key"].map(key_to_id).astype("Int64")

    # Select only the contact-level columns that exist
    available = [c for c in CONTACT_COLS if c in df.columns]
    # Preserve extra columns not in either list
    org_col_set = set(ORG_COLS) | {"_org_key"}
    extra = [c for c in df.columns if c not in set(CONTACT_COLS) and c not in org_col_set]
    final_cols = available + extra

    return df[final_cols]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    df = load()

    print(f"  Total rows: {len(df)}")

    orgs_df = build_organisations(df)
    print(f"  Unique organisations: {len(orgs_df)}")

    contacts_df = build_contacts_split(df, orgs_df)
    print(f"  Contact rows: {len(contacts_df)}")

    ORGS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    orgs_df.to_csv(ORGS_OUTPUT, index=False)
    print(f"  Saved → {ORGS_OUTPUT}")

    contacts_df.to_csv(CONTACTS_SPLIT_OUTPUT, index=False)
    print(f"  Saved → {CONTACTS_SPLIT_OUTPUT}")


if __name__ == "__main__":
    main()
