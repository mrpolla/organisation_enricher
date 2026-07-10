from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlparse

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "contacts_raw.csv"
)

OUTPUT_FILE = (
    PROJECT_DIR
    / "data"
    / "input"
    / "contacts_clean.csv"
)

DUPLICATES_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "contacts_exact_duplicates.csv"
)

SHARED_EMAIL_REVIEW_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "contacts_shared_email_review.csv"
)

REVIEW_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "contacts_cleaning_review.csv"
)

EMAIL_RE = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    re.IGNORECASE,
)

URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s,;|]+|"
    r"\b(?:[A-Z0-9-]+\.)+[A-Z]{2,}"
    r"(?::\d+)?(?:/[^\s,;|]*)?",
    re.IGNORECASE,
)

PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d[\d\s()./-]{6,}\d)"
)

NAME_PLACEHOLDERS = {
    "",
    "?",
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "not known",
    "tbd",
    "x",
    "contact",
    "contact person",
    "kontakt",
    "team",
    "office",
    "info",
    "general",
    "not available",
}


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_obfuscated_email_text(value: object) -> str:
    text = clean_text(value)

    text = re.sub(
        r"\s*(?:\(|\[)?at(?:\)|\])?\s*",
        "@",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*(?:\(|\[)?dot(?:\)|\])?\s*",
        ".",
        text,
        flags=re.IGNORECASE,
    )

    return text


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        cleaned = value.strip().rstrip(".,;:")
        key = cleaned.lower()

        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)

    return result


def extract_emails(*values: object) -> list[str]:
    combined = " | ".join(
        normalize_obfuscated_email_text(value)
        for value in values
        if clean_text(value)
    )

    return unique(
        [match.lower() for match in EMAIL_RE.findall(combined)]
    )


def normalize_url_candidate(value: str) -> str:
    candidate = value.strip().rstrip(".,;:)")

    if not candidate:
        return ""

    if candidate.lower().startswith("www."):
        candidate = f"https://{candidate}"
    elif not re.match(
        r"^https?://",
        candidate,
        flags=re.IGNORECASE,
    ):
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)

    if not parsed.netloc or "." not in parsed.netloc:
        return ""

    return candidate


def extract_urls(*values: object) -> list[str]:
    combined = " | ".join(
        clean_text(value)
        for value in values
        if clean_text(value)
    )

    candidates = URL_RE.findall(combined)

    return unique(
        [
            normalized
            for candidate in candidates
            if (normalized := normalize_url_candidate(candidate))
        ]
    )


def extract_phones(*values: object) -> list[str]:
    combined = " | ".join(
        clean_text(value)
        for value in values
        if clean_text(value)
    )

    return unique(PHONE_RE.findall(combined))


def clean_name(value: object) -> str:
    text = clean_text(value)

    if text.lower() in NAME_PLACEHOLDERS:
        return ""

    if len(text) == 1:
        return ""

    if not any(character.isalpha() for character in text):
        return ""

    return text


def normalize_name_for_matching(value: object) -> str:
    text = clean_text(value).lower()
    return re.sub(r"\s+", " ", text).strip()


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
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    DUPLICATES_FILE.parent.mkdir(parents=True, exist_ok=True)

    dataframe = pd.read_csv(
        INPUT_FILE,
        dtype=str,
        keep_default_na=False,
    )

    required_columns = [
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

    ensure_columns(dataframe, required_columns)

    cleaned_rows: list[dict[str, str]] = []

    for _, row in dataframe.iterrows():
        email_candidates = extract_emails(
            row["Email"],
            row["Email_Alternatives"],
        )

        website_candidates = extract_urls(
            row["Website"],
            row["Website_Alternatives"],
        )

        phone_candidates = extract_phones(
            row["Phone"],
            row["Phone_Alternatives"],
        )

        contact_name = clean_name(row["Contact_Name"])
        organisation_name = clean_name(
            row["Organisation_Name"]
        )

        email = email_candidates[0] if email_candidates else ""
        email_alternatives = " | ".join(email_candidates[1:])

        website = (
            website_candidates[0]
            if website_candidates
            else ""
        )

        website_alternatives = " | ".join(
            website_candidates[1:]
        )

        phone = phone_candidates[0] if phone_candidates else ""
        phone_alternatives = " | ".join(phone_candidates[1:])

        email_domain = (
            email.rsplit("@", 1)[1]
            if "@" in email
            else ""
        )

        issues: list[str] = []

        if clean_text(row["Email"]) and not email:
            issues.append("No valid email extracted")

        if clean_text(row["Website"]) and not website:
            issues.append("No valid website extracted")

        if clean_text(row["Contact_Name"]) and not contact_name:
            issues.append("Contact name cleared as invalid")

        if (
            clean_text(row["Organisation_Name"])
            and not organisation_name
        ):
            issues.append("Organisation name cleared as invalid")

        if len(email_candidates) > 1:
            issues.append("Multiple emails extracted")

        if len(website_candidates) > 1:
            issues.append("Multiple websites extracted")

        cleaned_rows.append(
            {
                "ID": row["ID"],
                "Email_Domain": email_domain,
                "Website": website,
                "Organisation_Name": organisation_name,
                "Email": email,
                "Contact_Name": contact_name,
                "Email_Alternatives": email_alternatives,
                "Phone": phone,
                "Phone_Alternatives": phone_alternatives,
                "Website_Alternatives": website_alternatives,
                "LinkedIn": clean_text(row["LinkedIn"]),
                "Role": clean_text(row["Role"]),
                "Organisation_Type": clean_text(
                    row["Organisation_Type"]
                ),
                "Sector": clean_text(row["Sector"]),
                "Location": clean_text(row["Location"]),
                "Address": clean_text(row["Address"]),
                "Description": clean_text(row["Description"]),
                "Responsible_Person": clean_text(
                    row["Responsible_Person"]
                ),
                "Source_File": clean_text(row["Source_File"]),
                "Source_Row": clean_text(row["Source_Row"]),
                "Cleaning_Issues": " | ".join(issues),
            }
        )

    cleaned_df = pd.DataFrame(cleaned_rows)

    normalized_email = (
        cleaned_df["Email"]
        .str.strip()
        .str.lower()
    )

    normalized_name = (
        cleaned_df["Contact_Name"]
        .apply(normalize_name_for_matching)
    )

    has_exact_identity = (
        normalized_email.ne("")
        & normalized_name.ne("")
    )

    duplicate_key = (
        normalized_email
        + "||"
        + normalized_name
    )

    exact_duplicate_group_mask = (
        has_exact_identity
        & duplicate_key.duplicated(keep=False)
    )

    exact_duplicates = cleaned_df.loc[
        exact_duplicate_group_mask
    ].copy()

    removed_exact_duplicate_mask = (
        has_exact_identity
        & duplicate_key.duplicated(keep="first")
    )

    deduplicated = cleaned_df.loc[
        ~removed_exact_duplicate_mask
    ].copy()

    shared_email_mask = (
        normalized_email.ne("")
        & normalized_email.duplicated(keep=False)
        & ~exact_duplicate_group_mask
    )

    shared_email_review = cleaned_df.loc[
        shared_email_mask
    ].copy()

    review = deduplicated.loc[
        deduplicated["Cleaning_Issues"].ne("")
    ].copy()

    output_columns = [
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

    deduplicated[output_columns].to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    exact_duplicates.to_csv(
        DUPLICATES_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    shared_email_review.to_csv(
        SHARED_EMAIL_REVIEW_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    review.to_csv(
        REVIEW_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Raw rows:                    {len(dataframe)}")
    print(f"Rows after exact dedupe:     {len(deduplicated)}")
    print(f"Exact duplicate group rows:  {len(exact_duplicates)}")
    print(f"Removed exact duplicates:    {removed_exact_duplicate_mask.sum()}")
    print(f"Shared-email review rows:     {len(shared_email_review)}")
    print(f"Rows needing review:         {len(review)}")
    print()
    print(f"Clean input:       {OUTPUT_FILE}")
    print(f"Exact duplicates:  {DUPLICATES_FILE}")
    print(f"Shared emails:     {SHARED_EMAIL_REVIEW_FILE}")
    print(f"Review:            {REVIEW_FILE}")


if __name__ == "__main__":
    main()
