from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[2]
INPUT_FILE = PROJECT_DIR / "data" / "staging" / "contacts_raw.csv"

STAGING_DIR = PROJECT_DIR / "data" / "staging"
EXCLUDED_FILE = STAGING_DIR / "contacts_cleaning_excluded.csv"
STAGED_FILE = STAGING_DIR / "contacts_staged.csv"
DUPLICATE_LIST_FILE = STAGING_DIR / "duplicate_list.csv"
ORGANISATIONS_PREVIEW_FILE = STAGING_DIR / "organisations_clean_preview.csv"
ORG_NAME_REVIEW_FILE = STAGING_DIR / "organisation_name_review.csv"

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s,;|]+|"
    r"\b(?:[A-Z0-9-]+\.)+[A-Z]{2,}(?::\d+)?(?:/[^\s,;|]*)?",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s()./-]{6,}\d)")
GERMAN_POSTCODE_RE = re.compile(r"\b\d{5}\b")
STREET_NUMBER_RE = re.compile(r"\b\d+[a-zA-Z]?\b")

INVALID_NAME_VALUES = {
    "", "?", "-", "--", "n/a", "na", "none", "null", "unknown",
    "not known", "tbd", "x", "domain", "email", "e-mail", "mail",
    "website", "web", "url", "contact", "contact person", "kontakt",
    "organisation", "organization", "name", "phone", "telephone",
    "address", "contact us", "click here", "see website", "send email",
}

CONTACT_NAME_REVIEW_WORDS = {
    "team", "office", "info", "general", "support", "service",
    "administration", "admin",
}

MAIN_OUTPUT_COLUMNS = [
    "ID", "Email_Domain", "Website", "Organisation_Name", "Email",
    "Contact_Name", "Email_Alternatives", "Phone", "Phone_Alternatives",
    "Website_Alternatives", "LinkedIn", "Role", "Organisation_Type",
    "Sector", "Location", "Address", "Description", "Responsible_Person",
    "Source_File", "Source_Row",
]


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_text(value: object) -> str:
    text = clean_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value: object) -> str:
    return normalize_text(value)


def normalize_obfuscated_email_text(value: object) -> str:
    text = clean_text(value)
    text = re.sub(r"\s*(?:\(|\[)?at(?:\)|\])?\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:\(|\[)?dot(?:\)|\])?\s*", ".", text, flags=re.IGNORECASE)
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


def split_pipe_values(value: object) -> list[str]:
    return unique([part.strip() for part in clean_text(value).split("|") if part.strip()])


def extract_emails(*values: object) -> list[str]:
    combined = " | ".join(
        normalize_obfuscated_email_text(value)
        for value in values
        if clean_text(value)
    )
    return unique([match.lower() for match in EMAIL_RE.findall(combined)])


def normalize_url_candidate(value: str) -> str:
    candidate = value.strip().rstrip(".,;:)")
    if not candidate:
        return ""
    if candidate.lower().startswith("www."):
        candidate = f"https://{candidate}"
    elif not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return candidate


def extract_urls(*values: object) -> list[str]:
    combined = " | ".join(clean_text(value) for value in values if clean_text(value))
    return unique(
        normalized
        for candidate in URL_RE.findall(combined)
        if (normalized := normalize_url_candidate(candidate))
    )


def website_domain(value: object) -> str:
    normalized = normalize_url_candidate(clean_text(value))
    if not normalized:
        return ""
    host = urlparse(normalized).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def extract_phones(*values: object) -> list[str]:
    combined = " | ".join(clean_text(value) for value in values if clean_text(value))
    return unique(PHONE_RE.findall(combined))


def clean_name(value: object) -> str:
    text = clean_text(value)
    if text.lower() in INVALID_NAME_VALUES:
        return ""
    if len(text) == 1 or not any(ch.isalpha() for ch in text):
        return ""
    return text


def clean_organisation_name(value: object) -> tuple[str, str]:
    text = clean_text(value)
    if not text:
        return "", ""

    lines = [
        re.sub(r"^[>\-*•\s]+", "", line).strip()
        for line in re.split(r"[\r\n]+", text)
        if line.strip()
    ]
    valid = [
        line for line in lines
        if line.lower() not in INVALID_NAME_VALUES
        and any(ch.isalpha() for ch in line)
    ]
    if not valid:
        return "", ""

    main = valid[0]
    leftovers = " | ".join(valid[1:])
    main = re.sub(r"\s+\d+\s*x\s+.*$", "", main, flags=re.IGNORECASE).strip(" ,;-")
    return main, leftovers


def ensure_columns(dataframe: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column not in dataframe.columns:
            dataframe[column] = ""
        dataframe[column] = dataframe[column].fillna("").astype(str).str.strip()


def is_strong_contact_name(value: object) -> bool:
    name = clean_name(value)
    return bool(
        name
        and "\n" not in name
        and not any(ch.isdigit() for ch in name)
        and len(normalize_name(name).split()) >= 2
    )


def is_weak_contact_name(value: object) -> bool:
    name = clean_name(value)
    return bool(name and len(normalize_name(name).split()) == 1)


def address_quality(value: object) -> int:
    text = clean_text(value)
    if not text:
        return 0
    score = min(len(text), 100)
    if GERMAN_POSTCODE_RE.search(text):
        score += 100
    if STREET_NUMBER_RE.search(text):
        score += 40
    if re.search(r"\b(berlin|hamburg|munich|münchen|cologne|köln|frankfurt|leipzig|dresden|potsdam)\b", text, re.IGNORECASE):
        score += 30
    if "," in text:
        score += 10
    return score


def row_priority_score(row: pd.Series) -> int:
    score = 0
    if is_strong_contact_name(row.get("Contact_Name", "")):
        score += 100
    elif is_weak_contact_name(row.get("Contact_Name", "")):
        score += 10
    if clean_text(row.get("Website", "")):
        score += 70
    score += address_quality(row.get("Address", ""))
    if clean_text(row.get("Email", "")):
        score += 40
    if clean_text(row.get("Phone", "")):
        score += 10
    if clean_text(row.get("LinkedIn", "")):
        score += 10
    for column in ("Organisation_Type", "Sector", "Location", "Description", "Role"):
        if clean_text(row.get(column, "")):
            score += 5
    return score


def values_are_nested(values: list[str]) -> bool:
    normalized = unique([normalize_text(value) for value in values if clean_text(value)])
    if len(normalized) <= 1:
        return True
    longest = max(normalized, key=len)
    return all(value in longest for value in normalized)


def contact_names_compatible(values: list[str]) -> bool:
    normalized = unique([normalize_name(value) for value in values if clean_name(value)])
    if len(normalized) <= 1:
        return True
    strong = [value for value in normalized if len(value.split()) >= 2]
    weak = [value for value in normalized if len(value.split()) == 1]
    if len(unique(strong)) > 1:
        return False
    if len(strong) == 1:
        tokens = set(strong[0].split())
        return all(value in tokens for value in weak)
    return len(unique(weak)) <= 1


def find_conflicts(group: pd.DataFrame) -> dict[str, list[str]]:
    conflicts: dict[str, list[str]] = {}

    emails = unique([clean_text(value).lower() for value in group["Email"] if clean_text(value)])
    if len(emails) > 1:
        conflicts["Email"] = emails

    domains = unique([website_domain(value) for value in group["Website"] if website_domain(value)])
    if len(domains) > 1:
        conflicts["Website"] = domains

    names = unique([clean_text(value) for value in group["Contact_Name"] if clean_text(value)])
    if not contact_names_compatible(names):
        conflicts["Contact_Name"] = names

    addresses = unique([clean_text(value) for value in group["Address"] if clean_text(value)])
    if len(addresses) > 1 and not values_are_nested(addresses):
        conflicts["Address"] = addresses

    for column in ("Role", "Location"):
        values = unique([clean_text(value) for value in group[column] if clean_text(value)])
        if len(unique([normalize_text(value) for value in values])) > 1:
            conflicts[column] = values

    return conflicts


def choose_best_address(values: pd.Series) -> str:
    candidates = unique([clean_text(value) for value in values if clean_text(value)])
    return max(candidates, key=address_quality) if candidates else ""


def choose_best_name(values: pd.Series) -> str:
    candidates = unique([clean_text(value) for value in values if clean_text(value)])
    return max(candidates, key=lambda value: (len(normalize_name(value).split()), len(value))) if candidates else ""


def merge_compatible_group(group: pd.DataFrame) -> pd.Series:
    ranked = group.copy()
    ranked["_Priority_Score"] = ranked.apply(row_priority_score, axis=1)
    base = ranked.sort_values("_Priority_Score", ascending=False, kind="stable").iloc[0].copy()

    base["Organisation_Name"] = choose_best_name(ranked["Organisation_Name"])
    base["Contact_Name"] = choose_best_name(ranked["Contact_Name"])
    base["Address"] = choose_best_address(ranked["Address"])

    websites = unique(
        [clean_text(value) for value in ranked["Website"] if clean_text(value)]
        + [item for value in ranked["Website_Alternatives"] for item in split_pipe_values(value)]
    )
    if websites:
        base_website = clean_text(base["Website"])
        base["Website"] = base_website or websites[0]
        base["Website_Alternatives"] = " | ".join(
            value for value in websites if value.lower() != base["Website"].lower()
        )

    for column in (
        "Email", "Email_Domain", "Phone", "LinkedIn", "Role",
        "Organisation_Type", "Sector", "Location", "Description",
        "Responsible_Person",
    ):
        if not clean_text(base.get(column, "")):
            for value in ranked[column]:
                if clean_text(value):
                    base[column] = clean_text(value)
                    break

    base["Email_Alternatives"] = " | ".join(unique(
        item for value in ranked["Email_Alternatives"] for item in split_pipe_values(value)
    ))
    base["Phone_Alternatives"] = " | ".join(unique(
        item for value in ranked["Phone_Alternatives"] for item in split_pipe_values(value)
    ))
    base["Source_File"] = " | ".join(unique([clean_text(value) for value in ranked["Source_File"] if clean_text(value)]))
    base["Source_Row"] = " | ".join(unique([clean_text(value) for value in ranked["Source_Row"] if clean_text(value)]))
    return base


def build_contact_clusters(group: pd.DataFrame) -> list[list[int]]:
    strong_clusters: dict[str, list[int]] = defaultdict(list)
    weak_rows: list[int] = []
    nameless_rows: list[int] = []

    for index, row in group.iterrows():
        name = clean_text(row["Contact_Name"])
        if is_strong_contact_name(name):
            strong_clusters[normalize_name(name)].append(index)
        elif is_weak_contact_name(name):
            weak_rows.append(index)
        else:
            nameless_rows.append(index)

    clusters: list[list[int]] = list(strong_clusters.values())

    for index in weak_rows:
        weak_name = normalize_name(group.loc[index, "Contact_Name"])
        matches: list[int] = []
        for cluster_index, cluster in enumerate(clusters):
            strong_names = unique([
                normalize_name(group.loc[row_index, "Contact_Name"])
                for row_index in cluster
                if is_strong_contact_name(group.loc[row_index, "Contact_Name"])
            ])
            if len(strong_names) == 1 and weak_name in set(strong_names[0].split()):
                matches.append(cluster_index)
        if len(matches) == 1:
            clusters[matches[0]].append(index)
        else:
            clusters.append([index])

    named_clusters = [
        cluster_index for cluster_index, cluster in enumerate(clusters)
        if any(clean_text(group.loc[row_index, "Contact_Name"]) for row_index in cluster)
    ]
    organisation_only: list[int] = []

    for index in nameless_rows:
        email = clean_text(group.loc[index, "Email"]).lower()
        email_matches: list[int] = []
        if email:
            for cluster_index, cluster in enumerate(clusters):
                cluster_emails = {
                    clean_text(group.loc[row_index, "Email"]).lower()
                    for row_index in cluster
                    if clean_text(group.loc[row_index, "Email"])
                }
                if email in cluster_emails:
                    email_matches.append(cluster_index)
        if len(email_matches) == 1:
            clusters[email_matches[0]].append(index)
        elif len(named_clusters) == 1:
            clusters[named_clusters[0]].append(index)
        else:
            organisation_only.append(index)

    if organisation_only:
        clusters.append(organisation_only)
    return clusters


def clean_source_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    cleaned_rows: list[dict[str, str]] = []

    for _, row in dataframe.iterrows():
        emails = extract_emails(row["Email"], row["Email_Alternatives"])
        websites = extract_urls(row["Website"], row["Website_Alternatives"])
        phones = extract_phones(row["Phone"], row["Phone_Alternatives"])

        contact_name = clean_name(row["Contact_Name"])
        organisation_name, organisation_leftovers = clean_organisation_name(row["Organisation_Name"])

        if contact_name and organisation_name and normalize_name(contact_name) == normalize_name(organisation_name):
            contact_name = ""

        email = emails[0] if emails else ""
        website = websites[0] if websites else ""
        phone = phones[0] if phones else ""

        issues: list[str] = []
        if clean_text(row["Contact_Name"]) and not contact_name:
            issues.append("Contact name cleared as invalid")
        if clean_text(row["Organisation_Name"]) and not organisation_name:
            issues.append("Organisation name cleared as invalid")
        if organisation_leftovers:
            issues.append("Organisation field contained additional lines: " + organisation_leftovers)
        if len(emails) > 1:
            issues.append("Multiple emails extracted")
        if len(websites) > 1:
            issues.append("Multiple websites extracted")
        if contact_name:
            if "\n" in contact_name:
                issues.append("Contact name contains multiple lines")
            if any(ch.isdigit() for ch in contact_name):
                issues.append("Contact name contains numbers")
            if len(normalize_name(contact_name).split()) == 1:
                issues.append("Contact name has one word")
            if contact_name.lower() in CONTACT_NAME_REVIEW_WORDS:
                issues.append("Contact name looks generic")
            if any(separator in contact_name.lower() for separator in (";", "/", " & ", " and ", " und ")):
                issues.append("Contact name may contain multiple people")

        cleaned_rows.append({
            "ID": row["ID"],
            "Email_Domain": email.rsplit("@", 1)[1] if "@" in email else "",
            "Website": website,
            "Organisation_Name": organisation_name,
            "Email": email,
            "Contact_Name": contact_name,
            "Email_Alternatives": " | ".join(emails[1:]),
            "Phone": phone,
            "Phone_Alternatives": " | ".join(phones[1:]),
            "Website_Alternatives": " | ".join(websites[1:]),
            "LinkedIn": clean_text(row["LinkedIn"]),
            "Role": clean_text(row["Role"]),
            "Organisation_Type": clean_text(row["Organisation_Type"]),
            "Sector": clean_text(row["Sector"]),
            "Location": clean_text(row["Location"]),
            "Address": clean_text(row["Address"]),
            "Description": clean_text(row["Description"]),
            "Responsible_Person": clean_text(row["Responsible_Person"]),
            "Source_File": clean_text(row["Source_File"]),
            "Source_Row": clean_text(row["Source_Row"]),
            "Cleaning_Issues": " | ".join(issues),
        })

    return pd.DataFrame(cleaned_rows)


def suspicious_org_name_reason(org_name: str) -> str:
    if re.fullmatch(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", org_name, re.IGNORECASE):
        return "Organisation name is an email address"
    if re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}", org_name):
        return "Organisation name looks like a date or form submission"
    return ""


def make_exclusion_reason(row: pd.Series) -> str:
    reasons: list[str] = []

    if clean_text(row["Email"]).lower().endswith("@gmail.com"):
        reasons.append("Contact has a Gmail email address")

    key_values = [row["Organisation_Name"], row["Website"], row["Email"], row["Contact_Name"]]
    if not any(clean_text(value) for value in key_values):
        reasons.append("No organisation, website, email, or contact name")
    if (
        not clean_text(row["Organisation_Name"])
        and not clean_text(row["Website"])
        and not clean_text(row["Email"])
        and clean_text(row["Contact_Name"])
    ):
        reasons.append("Only contact name available")
    return " | ".join(reasons)


def process_duplicates(working: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    output_rows: list[pd.Series] = []
    duplicate_list_parts: list[pd.DataFrame] = []
    group_number = 0

    working = working.copy()
    working["_Organisation_Key"] = working["Organisation_Name"].apply(normalize_name)

    for _, row in working[working["_Organisation_Key"].eq("")].iterrows():
        output_rows.append(row)

    grouped = working[working["_Organisation_Key"].ne("")].groupby("_Organisation_Key", sort=False)

    for organisation_key, organisation_group in grouped:
        for cluster_number, indices in enumerate(build_contact_clusters(organisation_group), start=1):
            cluster = organisation_group.loc[indices].copy()
            cluster["_Priority_Score"] = cluster.apply(row_priority_score, axis=1)
            recommended = cluster.sort_values("_Priority_Score", ascending=False, kind="stable").iloc[0]

            if len(cluster) == 1:
                output_rows.append(cluster.iloc[0])
                continue

            conflicts = find_conflicts(cluster)
            recommended_id = clean_text(recommended["ID"])

            if conflicts:
                group_number += 1
                dup_cluster = cluster[MAIN_OUTPUT_COLUMNS].copy()
                verify_reason_val = " | ".join(
                    f"{field}: {vals}" for field, vals in conflicts.items()
                )
                selected_vals = dup_cluster["ID"].apply(
                    lambda id_val: "X" if clean_text(id_val) == recommended_id else ""
                )
                dup_cluster.insert(1, "duplicate_group", str(group_number))
                dup_cluster.insert(2, "selected", selected_vals)
                dup_cluster.insert(3, "to_verify", "yes")
                dup_cluster.insert(4, "verify_reason", verify_reason_val)
                duplicate_list_parts.append(dup_cluster)
                output_rows.extend(row for _, row in cluster.iterrows())
                continue

            merged = merge_compatible_group(cluster)
            output_rows.append(merged)

    output = pd.DataFrame(output_rows)
    duplicate_list = pd.concat(duplicate_list_parts, ignore_index=True) if duplicate_list_parts else pd.DataFrame(columns=["ID", "duplicate_group", "selected", "to_verify", "verify_reason"] + [c for c in MAIN_OUTPUT_COLUMNS if c != "ID"])
    return output, duplicate_list


def build_organisations_preview(clean_output: pd.DataFrame) -> pd.DataFrame:
    source = clean_output[clean_output["Organisation_Name"].ne("")].copy()
    source["_Organisation_Key"] = source["Organisation_Name"].apply(normalize_name)
    preview_rows: list[dict[str, str]] = []

    for organisation_key, group in source.groupby("_Organisation_Key", sort=False):
        ranked = group.copy()
        ranked["_Priority_Score"] = ranked.apply(row_priority_score, axis=1)
        recommended = ranked.sort_values("_Priority_Score", ascending=False, kind="stable").iloc[0]
        conflicts = find_conflicts(ranked)

        websites = unique([clean_text(value) for value in ranked["Website"] if clean_text(value)])
        primary_website = clean_text(recommended["Website"])
        contact_keys = {
            (normalize_name(row["Contact_Name"]), clean_text(row["Email"]).lower())
            for _, row in ranked.iterrows()
            if clean_text(row["Contact_Name"]) or clean_text(row["Email"])
        }

        preview_rows.append({
            "Organisation_Name": choose_best_name(ranked["Organisation_Name"]),
            "Website": primary_website,
            "Website_Alternatives": " | ".join(
                value for value in websites if value.lower() != primary_website.lower()
            ),
            "Organisation_Type": " | ".join(unique([
                clean_text(value) for value in ranked["Organisation_Type"] if clean_text(value)
            ])),
            "Sector": " | ".join(unique([
                clean_text(value) for value in ranked["Sector"] if clean_text(value)
            ])),
            "Location": max(
                [clean_text(value) for value in ranked["Location"] if clean_text(value)],
                key=len,
                default="",
            ),
            "Address": choose_best_address(ranked["Address"]),
            "Description": max(
                [clean_text(value) for value in ranked["Description"] if clean_text(value)],
                key=len,
                default="",
            ),
            "LinkedIn": clean_text(recommended["LinkedIn"]),
            "Phone": clean_text(recommended["Phone"]),
            "Recommended_Base_ID": clean_text(recommended["ID"]),
            "Source_IDs": " | ".join(unique([
                clean_text(value) for value in ranked["ID"] if clean_text(value)
            ])),
            "Contact_Count": str(len(contact_keys)),
            "Manual_Verification_Necessary": str(bool(conflicts)),
            "Conflicting_Fields": " | ".join(
                f"{field}: {values}" for field, values in conflicts.items()
            ),
        })

    return pd.DataFrame(preview_rows)


def main() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    dataframe = pd.read_csv(INPUT_FILE, dtype=str, keep_default_na=False)
    ensure_columns(dataframe, MAIN_OUTPUT_COLUMNS)

    cleaned = clean_source_rows(dataframe)
    cleaned["Exclusion_Reason"] = cleaned.apply(make_exclusion_reason, axis=1)

    excluded = cleaned[cleaned["Exclusion_Reason"].ne("")].copy()
    working = cleaned[cleaned["Exclusion_Reason"].eq("")].copy()

    _review_cols = ["ID", "selected", "to_verify", "verify_reason"] + [c for c in MAIN_OUTPUT_COLUMNS if c != "ID"]
    _org_review_rows = [
        row for _, row in working.iterrows()
        if suspicious_org_name_reason(clean_text(row["Organisation_Name"]))
    ]
    if _org_review_rows:
        org_name_review = pd.DataFrame(_org_review_rows)[MAIN_OUTPUT_COLUMNS].copy()
        org_name_review.insert(1, "selected", "yes")
        org_name_review.insert(2, "to_verify", "yes")
        org_name_review.insert(3, "verify_reason", org_name_review["Organisation_Name"].apply(
            lambda name: suspicious_org_name_reason(clean_text(name))
        ))
    else:
        org_name_review = pd.DataFrame(columns=_review_cols)

    clean_output, duplicate_list = process_duplicates(working)
    clean_output = clean_output.reset_index(drop=True)

    staged = clean_output[MAIN_OUTPUT_COLUMNS].copy()
    organisations_preview = build_organisations_preview(clean_output)

    staged.to_csv(STAGED_FILE, index=False, encoding="utf-8-sig")
    excluded.to_csv(EXCLUDED_FILE, index=False, encoding="utf-8-sig")
    duplicate_list.to_csv(DUPLICATE_LIST_FILE, index=False, encoding="utf-8-sig")
    org_name_review.to_csv(ORG_NAME_REVIEW_FILE, index=False, encoding="utf-8-sig")
    organisations_preview.to_csv(ORGANISATIONS_PREVIEW_FILE, index=False, encoding="utf-8-sig")

    print(f"Raw rows:                   {len(dataframe)}")
    print(f"Excluded rows:              {len(excluded)}")
    print(f"Staged rows:                {len(staged)}")
    print(f"Org name review rows:       {len(org_name_review)}")
    print(f"Duplicate groups:           {duplicate_list['duplicate_group'].nunique() if not duplicate_list.empty else 0} groups, {len(duplicate_list)} rows")
    print(f"Organisation preview rows:  {len(organisations_preview)}")
    print()
    print(f"Staged:               {STAGED_FILE}")
    print(f"Excluded:             {EXCLUDED_FILE}")
    print(f"Duplicate list:       {DUPLICATE_LIST_FILE}")
    print(f"Org name review:      {ORG_NAME_REVIEW_FILE}")
    print(f"Organisations:        {ORGANISATIONS_PREVIEW_FILE}")


if __name__ == "__main__":
    main()
