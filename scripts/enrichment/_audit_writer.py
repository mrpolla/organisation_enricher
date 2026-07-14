"""Audit and manual-verification row writers.

Each extraction script (04/05/06) writes only the rows belonging to its own
candidate types, replacing any previous rows of those types.  Other scripts'
rows are preserved.  This allows re-running a single step without clobbering
the rest of the audit/verification history.

Public API
----------
AuditRow            dataclass
VerificationRow     dataclass
update_audit_file(new_rows, candidate_types, path)
update_verify_file(new_rows, candidate_types, path)
"""

from __future__ import annotations

import pathlib
from dataclasses import asdict, dataclass

import pandas as pd

AUDIT_FILE  = pathlib.Path("data/output/website_scrape_results.csv")
VERIFY_FILE = pathlib.Path("data/output/enrichment_manual_verification.csv")

AUDIT_COLUMNS = [
    "Website_Normalized", "Page_URL", "Page_Type",
    "Candidate_Type", "Candidate_Value", "Evidence_Text",
    "Selected", "Selection_Reason", "Related_Row_IDs",
]

VERIFICATION_COLUMNS = [
    "Related_Row_IDs", "Website_Normalized", "Issue_Type",
    "Existing_Value", "Candidate_Values", "Recommended_Value",
    "Evidence_URLs", "Reason",
]


@dataclass
class AuditRow:
    Website_Normalized: str
    Page_URL: str
    Page_Type: str
    Candidate_Type: str
    Candidate_Value: str
    Evidence_Text: str
    Selected: bool = False
    Selection_Reason: str = ""
    Related_Row_IDs: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["Selected"] = "True" if d["Selected"] else "False"
        return d


@dataclass
class VerificationRow:
    Related_Row_IDs: str
    Website_Normalized: str
    Issue_Type: str
    Existing_Value: str = ""
    Candidate_Values: str = ""
    Recommended_Value: str = ""
    Evidence_URLs: str = ""
    Reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _update_file(
    new_rows: list,
    candidate_types: set[str],
    path: pathlib.Path,
    columns: list[str],
    filter_col: str,
) -> None:
    """Replace rows whose *filter_col* contains a value from *candidate_types*;
    append *new_rows* (may be empty to just clear old entries)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    kept_df = pd.DataFrame(columns=columns)
    if path.exists():
        try:
            existing = pd.read_csv(path, dtype=str, keep_default_na=False)
            # Keep rows not related to our candidate types
            mask = existing[filter_col].apply(
                lambda v: not any(ct in str(v) for ct in candidate_types)
            )
            kept_df = existing[mask]
        except Exception:
            pass

    if new_rows:
        new_df = pd.DataFrame([r.as_dict() for r in new_rows], columns=columns)
        combined = pd.concat([kept_df, new_df], ignore_index=True)
    else:
        combined = kept_df

    combined.to_csv(path, index=False)


def update_audit_file(
    new_rows: list[AuditRow],
    candidate_types: set[str],
    path: pathlib.Path = AUDIT_FILE,
) -> None:
    _update_file(new_rows, candidate_types, path, AUDIT_COLUMNS, "Candidate_Type")


def update_verify_file(
    new_rows: list[VerificationRow],
    candidate_types: set[str],
    path: pathlib.Path = VERIFY_FILE,
) -> None:
    _update_file(new_rows, candidate_types, path, VERIFICATION_COLUMNS, "Issue_Type")
