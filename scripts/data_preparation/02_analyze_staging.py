from pathlib import Path
import re

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[2]

INPUT_FILE = PROJECT_DIR / "data" / "staging" / "staging_all_rows.csv"
REPORT_FILE = PROJECT_DIR / "data" / "staging" / "staging_analysis.txt"
SUSPICIOUS_FILE = (
    PROJECT_DIR
    / "data"
    / "staging"
    / "staging_suspicious_rows.csv"
)

EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$",
    re.IGNORECASE,
)

URL_PATTERN = re.compile(
    r"^(?:https?://)?"
    r"(?:www\.)?"
    r"[A-Z0-9-]+"
    r"(?:\.[A-Z0-9-]+)+"
    r"(?::\d+)?"
    r"(?:/[^\s]*)?$",
    re.IGNORECASE,
)

PLACEHOLDERS = {
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
}

NAME_PLACEHOLDERS = PLACEHOLDERS | {
    "contact",
    "contact person",
    "kontakt",
    "team",
    "office",
    "info",
    "general",
    "not available",
}


def clean(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def looks_like_single_email(value: object) -> bool:
    return bool(EMAIL_PATTERN.fullmatch(clean(value)))


def looks_like_single_url(value: object) -> bool:
    text = clean(value).rstrip(".,;")
    return bool(URL_PATTERN.fullmatch(text))


def suspicious_name(value: object) -> bool:
    text = clean(value)
    normalized = text.lower()

    if normalized in NAME_PLACEHOLDERS:
        return True

    if len(text) == 1:
        return True

    if not any(character.isalpha() for character in text):
        return True

    return False


def contains_multiple_email_candidates(value: object) -> bool:
    text = clean(value)
    return text.count("@") > 1


def contains_email_text_noise(value: object) -> bool:
    text = clean(value).lower()

    if not text:
        return False

    noise_markers = (
        "contact us",
        "kontakt",
        "email:",
        "e-mail:",
        "mail:",
        " or ",
        " oder ",
        ";",
        "|",
        "\n",
    )

    return any(marker in text for marker in noise_markers)


def contains_multiple_url_candidates(value: object) -> bool:
    text = clean(value).lower()

    if not text:
        return False

    markers = ("http://", "https://", "www.")
    count = sum(text.count(marker) for marker in markers)

    return count > 1 or "|" in text or ";" in text or "\n" in text


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
    dataframe = pd.read_csv(
        INPUT_FILE,
        dtype=str,
        keep_default_na=False,
    )

    required_columns = [
        "Source_File",
        "Source_Row",
        "Organisation_Name",
        "Contact_Name",
        "Email",
        "Email_Alternatives",
        "Website",
        "Website_Alternatives",
        "Phone",
        "Phone_Alternatives",
        "Original_Contact_Info",
    ]

    ensure_columns(dataframe, required_columns)

    dataframe["Suspicious_Contact_Name"] = (
        dataframe["Contact_Name"].ne("")
        & dataframe["Contact_Name"].apply(suspicious_name)
    )

    dataframe["Suspicious_Organisation_Name"] = (
        dataframe["Organisation_Name"].ne("")
        & dataframe["Organisation_Name"].apply(suspicious_name)
    )

    dataframe["Invalid_Email_Format"] = (
        dataframe["Email"].ne("")
        & ~dataframe["Email"].apply(looks_like_single_email)
    )

    dataframe["Multiple_Emails_In_Main_Field"] = (
        dataframe["Email"].apply(contains_multiple_email_candidates)
    )

    dataframe["Email_Field_Contains_Text"] = (
        dataframe["Email"].apply(contains_email_text_noise)
    )

    dataframe["Invalid_Website_Format"] = (
        dataframe["Website"].ne("")
        & ~dataframe["Website"].apply(looks_like_single_url)
    )

    dataframe["Multiple_Websites_In_Main_Field"] = (
        dataframe["Website"].apply(contains_multiple_url_candidates)
    )

    normalized_email = dataframe["Email"].str.strip().str.lower()

    dataframe["Duplicate_Email"] = (
        normalized_email.ne("")
        & normalized_email.duplicated(keep=False)
    )

    dataframe["Almost_Empty_Row"] = (
        dataframe["Organisation_Name"].eq("")
        & dataframe["Contact_Name"].eq("")
        & dataframe["Email"].eq("")
        & dataframe["Website"].eq("")
    )

    issue_columns = [
        "Suspicious_Contact_Name",
        "Suspicious_Organisation_Name",
        "Invalid_Email_Format",
        "Multiple_Emails_In_Main_Field",
        "Email_Field_Contains_Text",
        "Invalid_Website_Format",
        "Multiple_Websites_In_Main_Field",
        "Duplicate_Email",
        "Almost_Empty_Row",
    ]

    suspicious = dataframe[
        dataframe[issue_columns].any(axis=1)
    ].copy()

    suspicious["Issues"] = suspicious.apply(
        lambda row: " | ".join(
            issue
            for issue in issue_columns
            if bool(row[issue])
        ),
        axis=1,
    )

    output_columns = [
        "Source_File",
        "Source_Row",
        "Organisation_Name",
        "Contact_Name",
        "Email",
        "Email_Alternatives",
        "Phone",
        "Phone_Alternatives",
        "Website",
        "Website_Alternatives",
        "Original_Contact_Info",
        "Issues",
    ]

    suspicious[output_columns].to_csv(
        SUSPICIOUS_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    report_lines = [
        "STAGING DATA ANALYSIS",
        "=====================",
        "",
        f"Total rows: {len(dataframe)}",
        "",
        f"Missing organisation name: {dataframe['Organisation_Name'].eq('').sum()}",
        f"Missing contact name: {dataframe['Contact_Name'].eq('').sum()}",
        f"Missing email: {dataframe['Email'].eq('').sum()}",
        f"Missing website: {dataframe['Website'].eq('').sum()}",
        "",
        f"Suspicious contact names: {dataframe['Suspicious_Contact_Name'].sum()}",
        f"Suspicious organisation names: {dataframe['Suspicious_Organisation_Name'].sum()}",
        f"Invalid email format: {dataframe['Invalid_Email_Format'].sum()}",
        f"Main email field contains multiple emails: {dataframe['Multiple_Emails_In_Main_Field'].sum()}",
        f"Main email field contains extra text: {dataframe['Email_Field_Contains_Text'].sum()}",
        f"Invalid website format: {dataframe['Invalid_Website_Format'].sum()}",
        f"Main website field contains multiple websites: {dataframe['Multiple_Websites_In_Main_Field'].sum()}",
        f"Rows sharing a duplicate email: {dataframe['Duplicate_Email'].sum()}",
        f"Almost empty rows: {dataframe['Almost_Empty_Row'].sum()}",
        "",
        f"Suspicious rows saved to:",
        str(SUSPICIOUS_FILE),
    ]

    report = "\n".join(report_lines)

    REPORT_FILE.write_text(
        report,
        encoding="utf-8",
    )

    print(report)


if __name__ == "__main__":
    main()
