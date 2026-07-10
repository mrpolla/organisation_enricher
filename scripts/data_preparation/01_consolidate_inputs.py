from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from mappings import FILE_MAPPINGS, SPECIAL_FILE_CONFIG, STAGING_COLUMNS


PROJECT_DIR = Path(__file__).resolve().parents[2]
INPUT_DIR = PROJECT_DIR / "data" / "input_raw"
STAGING_DIR = PROJECT_DIR / "data" / "staging"

OUTPUT_FILE = STAGING_DIR / "staging_all_rows.csv"
REPORT_FILE = STAGING_DIR / "import_report.txt"


def clean(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def read_csv(path: Path, header: int = 0) -> pd.DataFrame:
    last_error: Exception | None = None

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return pd.read_csv(
                path,
                dtype=str,
                keep_default_na=False,
                encoding=encoding,
                header=header,
            )
        except Exception as error:
            last_error = error

    raise RuntimeError(f"Could not read {path.name}: {last_error}")


def source_value(row: pd.Series, source: str) -> str:
    if source.startswith("__CONSTANT__:"):
        return source.split(":", 1)[1]

    if source not in row.index:
        return ""

    return clean(row[source])


def combine_raw_values(*values: Any) -> str:
    parts: list[str] = []

    for value in values:
        text = clean(value)
        if text and text not in parts:
            parts.append(text)

    return " | ".join(parts)


def map_frame(
    path: Path,
    source: pd.DataFrame,
    mapping: dict[str, str],
    header_row: int,
) -> pd.DataFrame:
    rows: list[dict[str, str]] = []

    for index, row in source.iterrows():
        target = {column: "" for column in STAGING_COLUMNS}
        target["Source_File"] = path.name
        target["Source_Row"] = str(index + header_row + 2)

        for target_column, source_column in mapping.items():
            target[target_column] = source_value(row, source_column)

        # Alternative fields in the form export.
        if path.name == "Add an organization to Berlin's circular ecosystem (Responses) - Form Responses 1.csv":
            if not target["Organisation_Name"]:
                target["Organisation_Name"] = clean(
                    row.get(
                        "What is the name of the organization/project you want to modify?",
                        "",
                    )
                )

            if not target["Email"]:
                target["Email"] = clean(
                    row.get(
                        "Add your email in case we have clarification questions..1",
                        "",
                    )
                )

        # Preserve mixed fields exactly. Parsing happens in 04_clean_contacts.py.
        if path.name == "Circular City Guide 2023_Ideas_organisations_initiatives - List.csv":
            links = row.get(
                "Hier kannst du deine Links teilen: Website; Social Media; Google Tools (Maps); usw.",
                "",
            )
            details = row.get(
                "Deine Kontaktdaten (gerne auch LinkedIn):",
                "",
            )
            target["Original_Contact_Info"] = combine_raw_values(details, links)

        if path.name == "Contact list for the cooperation partners in food MASTER FILE - Food related stakeholders.csv":
            target["Contact_Name"] = clean(
                row.get("Contact name and position", "")
            )
            target["Original_Contact_Info"] = clean(
                row.get("Email/LinkedIn", "")
            )

        # Preserve active ecosystem categories as tags.
        if path.name == "February 2026 export-circular-berlin-ecosystem-21.xlsx - Elements.csv":
            categories = [
                "Construction & Architecture",
                "Urban Transformation & Infrastructure",
                "Energy & Water Systems",
                "Waste Management & Recovery",
                "Circular Materials and Products for Industry",
                "Logistics & Supply Chain",
                "Fashion, Clothes & Accessories",
                "Electronic Equipment & IT",
                "Kids, Toys & Baby",
                "Sports & Outdoors",
                "Circular Products for Home & Office",
                "Furniture & Interior Design",
                "Grocery & Zero Waste Shopping",
                "Gastronomy & Hospitality",
                "Packaging & Return Systems",
                "Urban Farming & Food Production",
                "Event & Entertainment",
                "Space Rental & Coworking",
                "Consulting & Education",
                "Digital Products & Platforms",
                "Research & Science",
            ]

            active_categories = [
                category
                for category in categories
                if clean(row.get(category, ""))
            ]

            target["Tags"] = " | ".join(active_categories)

            if not target["Description"]:
                target["Description"] = clean(row.get("Description", ""))

        rows.append(target)

    return pd.DataFrame(rows, columns=STAGING_COLUMNS)


def main() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    report_lines: list[str] = []

    for path in sorted(INPUT_DIR.glob("*.csv")):
        if path.name == "selection.csv":
            continue

        try:
            if path.name in SPECIAL_FILE_CONFIG:
                config = SPECIAL_FILE_CONFIG[path.name]
                header_row = int(config["header_row"])
                source = read_csv(path, header=header_row)
                frame = map_frame(
                    path,
                    source,
                    config["mapping"],
                    header_row,
                )

            elif path.name in FILE_MAPPINGS:
                source = read_csv(path)
                frame = map_frame(
                    path,
                    source,
                    FILE_MAPPINGS[path.name],
                    0,
                )

            else:
                message = f"NO MAPPING: {path.name}"
                report_lines.append(message)
                print(f"SKIP {path.name}")
                continue

            frames.append(frame)
            report_lines.append(
                f"OK: {path.name}: {len(frame)} source rows"
            )
            print(f"OK   {path.name}: {len(frame)} rows")

        except Exception as error:
            report_lines.append(f"ERROR: {path.name}: {error}")
            print(f"ERR  {path.name}: {error}")

    if not frames:
        raise SystemExit("No source files were imported.")

    result = pd.concat(frames, ignore_index=True)

    data_columns = [
        column
        for column in STAGING_COLUMNS
        if column not in {"Source_File", "Source_Row"}
    ]

    non_empty_mask = result[data_columns].apply(
        lambda row: any(clean(value) for value in row),
        axis=1,
    )

    result = result.loc[non_empty_mask].copy()

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    REPORT_FILE.write_text(
        "\n".join(report_lines),
        encoding="utf-8",
    )

    print(f"\nSaved {len(result)} rows to {OUTPUT_FILE}")
    print(f"Report: {REPORT_FILE}")


if __name__ == "__main__":
    main()
