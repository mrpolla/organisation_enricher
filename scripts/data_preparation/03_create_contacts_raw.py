from pathlib import Path
import re
import unicodedata

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[2]

STAGING_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "staging_all_rows.csv"
)

SELECTION_FILE = (
    PROJECT_DIR
    / "data"
    / "input_raw"
    / "selection.csv"
)

CONTACTS_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "contacts_raw.csv"
)

EXCLUDED_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "contacts_raw_excluded.csv"
)

STAGING_COLUMNS = [
    "Organisation_Name",
    "Contact_Name",
    "Email",
    "Email_Alternatives",
    "Phone",
    "Phone_Alternatives",
    "Website",
    "Website_Alternatives",
    "LinkedIn",
    "Role",
    "Organisation_Type",
    "Sector",
    "Location",
    "Address",
    "Description",
    "Responsible_Person",
    "Source_File",
    "Source_Row",
]

OUTPUT_COLUMNS = [
    "ID",
    "Email_Domain",
    "Website",
    "Organisation_Name",
    "Email",
    "Contact_Name",
    "Email_Alternatives",
    "Phone",
    "Phone_Alternatives",
    "Website_Alternatives",
    "LinkedIn",
    "Role",
    "Organisation_Type",
    "Sector",
    "Location",
    "Address",
    "Description",
    "Responsible_Person",
    "Source_File",
    "Source_Row",
]


def clean(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_email_for_matching(value: object) -> str:
    return clean(value).lower()


def normalize_organisation_for_matching(value: object) -> str:
    text = clean(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_email_domain_raw(value: object) -> str:
    email = clean(value).lower()

    if email.count("@") != 1:
        return ""

    return email.rsplit("@", 1)[1].strip()


def ensure_columns(
    dataframe: pd.DataFrame,
    columns: list[str],
) -> None:
    for column in columns:
        if column not in dataframe.columns:
            dataframe[column] = ""

        dataframe[column] = (
            dataframe[column]
            .fillna("")
            .astype(str)
            .str.strip()
        )


def main() -> None:
    CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    staging = pd.read_csv(
        STAGING_FILE,
        dtype=str,
        keep_default_na=False,
    )

    selection = pd.read_csv(
        SELECTION_FILE,
        dtype=str,
        keep_default_na=False,
    )

    ensure_columns(staging, STAGING_COLUMNS)
    ensure_columns(
        selection,
        ["Status", "Email", "Organisation name"],
    )

    rejected_selection = selection[
        selection["Status"]
        .str.strip()
        .str.lower()
        .eq("x")
    ].copy()

    rejected_emails = {
        normalize_email_for_matching(value)
        for value in rejected_selection["Email"]
        if normalize_email_for_matching(value)
    }

    rejected_organisations = {
        normalize_organisation_for_matching(value)
        for value in rejected_selection["Organisation name"]
        if normalize_organisation_for_matching(value)
    }

    useful_row = (
        staging["Contact_Name"].ne("")
        | staging["Email"].ne("")
        | staging["Organisation_Name"].ne("")
        | staging["Website"].ne("")
    )

    contacts = staging.loc[useful_row].copy()

    contacts["_Email_Match"] = contacts["Email"].apply(
        normalize_email_for_matching
    )

    contacts["_Organisation_Match"] = (
        contacts["Organisation_Name"]
        .apply(normalize_organisation_for_matching)
    )

    contacts["_Excluded_By_Email"] = (
        contacts["_Email_Match"].ne("")
        & contacts["_Email_Match"].isin(rejected_emails)
    )

    contacts["_Excluded_By_Organisation"] = (
        contacts["_Organisation_Match"].ne("")
        & contacts["_Organisation_Match"].isin(
            rejected_organisations
        )
    )

    contacts["_Is_Excluded"] = (
        contacts["_Excluded_By_Email"]
        | contacts["_Excluded_By_Organisation"]
    )

    def exclusion_reason(row: pd.Series) -> str:
        reasons: list[str] = []

        if row["_Excluded_By_Email"]:
            reasons.append(
                "Email matched selection.csv with Status x"
            )

        if row["_Excluded_By_Organisation"]:
            reasons.append(
                "Organisation matched selection.csv with Status x"
            )

        return " | ".join(reasons)

    contacts["Exclusion_Reason"] = contacts.apply(
        exclusion_reason,
        axis=1,
    )

    excluded = contacts[
        contacts["_Is_Excluded"]
    ].copy()

    included = contacts[
        ~contacts["_Is_Excluded"]
    ].copy()

    included["Email_Domain"] = included["Email"].apply(
        extract_email_domain_raw
    )

    included.insert(
        0,
        "ID",
        range(1, len(included) + 1),
    )

    included[OUTPUT_COLUMNS].to_csv(
        CONTACTS_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    excluded_columns = [
        "Organisation_Name",
        "Contact_Name",
        "Email",
        "Email_Alternatives",
        "Phone",
        "Phone_Alternatives",
        "Website",
        "Website_Alternatives",
        "LinkedIn",
        "Role",
        "Organisation_Type",
        "Sector",
        "Location",
        "Address",
        "Description",
        "Responsible_Person",
        "Exclusion_Reason",
        "Source_File",
        "Source_Row",
    ]

    excluded[excluded_columns].to_csv(
        EXCLUDED_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Useful staging rows: {len(contacts)}")
    print(f"Included rows:       {len(included)}")
    print(f"Excluded rows:       {len(excluded)}")
    print()
    print(f"Raw contacts: {CONTACTS_FILE}")
    print(f"Excluded:     {EXCLUDED_FILE}")


if __name__ == "__main__":
    main()
