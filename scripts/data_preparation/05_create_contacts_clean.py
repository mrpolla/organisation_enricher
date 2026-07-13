from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[2]
STAGED_FILE = PROJECT_DIR / "data" / "staging" / "contacts_staged.csv"
DUPLICATE_LIST_FILE = PROJECT_DIR / "data" / "staging" / "duplicate_list.csv"
ORG_NAME_REVIEW_FILE = PROJECT_DIR / "data" / "staging" / "organisation_name_review.csv"
OUTPUT_FILE = PROJECT_DIR / "data" / "input" / "contacts_clean.csv"


def main() -> None:
    if not STAGED_FILE.exists():
        sys.exit(f"Staged file not found: {STAGED_FILE}\nRun 04_review_contacts.py first.")
    if not DUPLICATE_LIST_FILE.exists():
        sys.exit(f"Duplicate list not found: {DUPLICATE_LIST_FILE}\nRun 04_review_contacts.py first.")

    staged = pd.read_csv(STAGED_FILE, dtype=str, keep_default_na=False)
    duplicate_list = pd.read_csv(DUPLICATE_LIST_FILE, dtype=str, keep_default_na=False)

    # Conflict-group filtering with data overrides from (possibly edited) duplicate_list
    conflict_rows = duplicate_list[duplicate_list["to_verify"].eq("yes")]
    conflict_all_ids: set[str] = set(conflict_rows["ID"].str.strip())
    conflict_selected_ids: set[str] = set(
        conflict_rows.loc[conflict_rows["selected"].eq("X"), "ID"].str.strip()
    )
    _dup_data_cols = [c for c in duplicate_list.columns if c not in ("duplicate_group", "to_verify", "selected", "verify_reason")]
    conflict_overrides: dict[str, dict[str, str]] = {
        row["ID"].strip(): {c: row[c] for c in _dup_data_cols}
        for _, row in conflict_rows[conflict_rows["selected"].eq("X")].iterrows()
    }

    # Org name review: exclusions + data overrides from (possibly edited) review CSV
    org_excluded_ids: set[str] = set()
    org_overrides: dict[str, dict[str, str]] = {}
    if ORG_NAME_REVIEW_FILE.exists():
        org_review = pd.read_csv(ORG_NAME_REVIEW_FILE, dtype=str, keep_default_na=False)
        data_cols = [c for c in org_review.columns if c not in ("to_verify", "selected", "verify_reason")]
        for _, row in org_review.iterrows():
            row_id = row["ID"].strip()
            if row.get("selected", "yes").strip() == "no":
                org_excluded_ids.add(row_id)
            else:
                org_overrides[row_id] = {c: row[c] for c in data_cols if c in org_review.columns}

    def include_row(row_id: str) -> bool:
        if row_id in org_excluded_ids:
            return False
        if row_id not in conflict_all_ids:
            return True
        return row_id in conflict_selected_ids

    mask = staged["ID"].str.strip().apply(include_row)
    output = staged[mask].copy()

    # Apply conflict-group data overrides (preserves manual edits in duplicate_list.csv)
    if conflict_overrides:
        for idx, row in output.iterrows():
            row_id = str(row["ID"]).strip()
            if row_id in conflict_overrides:
                for col, val in conflict_overrides[row_id].items():
                    if col in output.columns:
                        output.at[idx, col] = val

    # Apply org name review overrides (preserves manual edits made in the review CSV)
    if org_overrides:
        for idx, row in output.iterrows():
            row_id = str(row["ID"]).strip()
            if row_id in org_overrides:
                for col, val in org_overrides[row_id].items():
                    if col in output.columns:
                        output.at[idx, col] = val

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    total_conflict = len(conflict_all_ids)
    kept_conflict = len(conflict_selected_ids)
    print(f"Staged rows:            {len(staged)}")
    print(f"Conflict-group rows:    {total_conflict}  (kept {kept_conflict}, dropped {total_conflict - kept_conflict})")
    print(f"Org review rows:        {len(org_overrides)} kept, {len(org_excluded_ids)} excluded")
    print(f"Output rows:            {len(output)}")
    print()
    print(f"Clean contacts: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
