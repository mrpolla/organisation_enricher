"""Shared helpers for all enrichment scripts.

Defines the canonical column order, and provides load() / save() so that
every script reads and writes contacts_enriched.csv consistently.
"""

import pathlib

import pandas as pd

RAW_INPUT = pathlib.Path("data/input/contacts_raw.csv")
ENRICHED = pathlib.Path("data/output/contacts_enriched.csv")

# Final output column order — all scripts write in this order.
# Columns not yet produced by earlier steps are simply absent until added.
FINAL_COLUMNS = [
    "ID",
    "Email_Domain",
    "Email_Domain_Source",
    "Website",
    "Website_Normalized",
    "Website_Response_Code",
    "Website_Response_Text",
    "Website_Source",
    "Organisation_Name",
    "Organisation_Name_Source",
    "Email",
    "Email_Source",
    "Contact_Name",
    "Contact_Name_First_Name",
    "Contact_Name_Last_Name",
    "Contact_Name_Source",
]


def _ordered(df: pd.DataFrame) -> list[str]:
    """Return columns in canonical order; unknown columns go at the end."""
    known = [c for c in FINAL_COLUMNS if c in df.columns]
    extra = [c for c in df.columns if c not in FINAL_COLUMNS]
    return known + extra


def load() -> pd.DataFrame:
    path = ENRICHED if ENRICHED.exists() else RAW_INPUT
    print(f"Reading {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df.apply(lambda col: col.str.strip())
    df = df.replace("", pd.NA)
    return df


def save(df: pd.DataFrame) -> None:
    ENRICHED.parent.mkdir(parents=True, exist_ok=True)
    df[_ordered(df)].to_csv(ENRICHED, index=False)
    print(f"Saved → {ENRICHED}")
