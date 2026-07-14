"""Shared helpers for all enrichment scripts.

Defines the canonical column order, and provides load() / save() so that
every script reads and writes contacts_enriched.csv consistently.
"""

import pathlib
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import pandas as pd

RAW_INPUT = pathlib.Path("data/input/contacts_clean.csv")
ENRICHED = pathlib.Path("data/output/contacts_enriched.csv")

# Third-party directories and profile sites — never an org's own website.
DIRECTORY_DOMAINS = {
    "wikipedia.org",
    "linkedin.com", "xing.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "vimeo.com",
    "northdata.com", "dnb.com", "crunchbase.com",
    "bloomberg.com", "glassdoor.com", "kununu.com", "indeed.com",
    "usgbc.org", "worldgbc.org",
}

# Consumer mailbox and hosted-service domains are not organisation websites.
# Redirecting these domains commonly lands on login or generic error pages.
NON_ORGANISATION_DOMAINS = DIRECTORY_DOMAINS | {
    "gmail.com", "googlemail.com", "google.com", "accounts.google.com",
    "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com",
    "gmx.de", "gmx.net", "web.de", "mail.com",
}

# Final output column order.
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
    "Organisation_Type",
    "Organisation_Type_Source",
    "Organisation_Legal_Form",
    "Organisation_Legal_Form_Source",
    "Organisation_Nonprofit_Status",
    "Organisation_Nonprofit_Source",
    "Email",
    "Email_Source",
    "Email_Alternatives",
    "Address",
    "Address_Source",
    "Address_Alternatives",
    "Contact_Name",
    "Contact_Name_First_Name",
    "Contact_Name_Last_Name",
    "Contact_Name_Source",
]

# Columns added / populated by website-scrape steps 04–06.
NEW_ENRICHMENT_COLUMNS = [
    "Organisation_Type",        "Organisation_Type_Source",
    "Organisation_Legal_Form",  "Organisation_Legal_Form_Source",
    "Organisation_Nonprofit_Status", "Organisation_Nonprofit_Source",
    "Email_Alternatives",
    "Address",                  "Address_Source",
    "Address_Alternatives",
]


def _ordered(df: pd.DataFrame) -> list[str]:
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


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:80]


# ── Helpers shared by pipeline steps 03–06 ───────────────────────────────────

def val(v: Any) -> str | None:
    """Return a clean non-empty string, or None."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "none", "<na>", "na") else None


def effective_website(row: "pd.Series") -> str | None:
    """Return Website_Normalized if present, else Website, else None."""
    for col in ("Website_Normalized", "Website"):
        v = val(row.get(col))
        if v:
            return v
    return None


def normalise_url_key(url: str) -> str:
    """Lowercase scheme+host, strip trailing slash — stable grouping key."""
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    query = ("?" + parsed.query) if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def _is_non_organisation_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    return any(domain == d or domain.endswith("." + d) for d in NON_ORGANISATION_DOMAINS)


def build_website_map(
    df: "pd.DataFrame",
    *,
    require_http_200: bool = True,
) -> dict[str, list[int]]:
    """Return eligible websites grouped by normalized URL.

    Website scraping is intentionally conservative: by default a row is
    eligible only after ``check_websites.py`` recorded an exact HTTP 200.
    Consumer mailbox, login, directory, and social-profile domains are excluded.
    """
    mapping: dict[str, list[int]] = defaultdict(list)
    for idx, row in df.iterrows():
        if require_http_200 and val(row.get("Website_Response_Code")) != "200":
            continue
        site = effective_website(row)
        if site and not _is_non_organisation_url(site):
            mapping[normalise_url_key(site)].append(idx)
    return dict(mapping)


def is_website_source(value: Any) -> bool:
    """Return whether a value was generated by a website scrape step."""
    source = val(value)
    return bool(source and source.lower().startswith("website"))


def clear_ineligible_website_values(
    df: "pd.DataFrame",
    website_map: dict[str, list[int]],
    fields: list[tuple[str, str]],
    generated_columns: list[str] | None = None,
) -> None:
    """Clear stale website results only on rows no longer scrape-eligible.

    Eligible rows are left for the extraction loop, which makes this safe when
    ``--limit`` defers some websites until a later invocation.
    """
    eligible = {idx for indices in website_map.values() for idx in indices}
    for idx in df.index:
        if idx in eligible:
            continue
        cleared = False
        for value_col, source_col in fields:
            if source_col in df.columns and is_website_source(df.at[idx, source_col]):
                if value_col in df.columns:
                    df.at[idx, value_col] = None
                df.at[idx, source_col] = None
                cleared = True
        if cleared:
            for col in generated_columns or []:
                if col in df.columns:
                    df.at[idx, col] = None


def pipe_join(items: list[str]) -> str:
    return " | ".join(i for i in items if i)


def pipe_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in str(value).split("|") if v.strip()]


def ensure_new_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """Add any missing enrichment columns with NA values (non-destructive)."""
    for col in NEW_ENRICHMENT_COLUMNS + ["Organisation_Name_Source", "Email_Source"]:
        if col not in df.columns:
            df[col] = pd.NA
    return df
