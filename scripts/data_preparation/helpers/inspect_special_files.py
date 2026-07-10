from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input_raw"

FILES = [
    "Construction account management list - Construction account management.csv",
    "List of funding opportunities.xlsx - Organizations.csv",
    "February 2026 export-circular-berlin-ecosystem-21.xlsx - Elements.csv",
    "Pilot Partner & Stakeholder - SOLSTICE 2025-26 - 6. Botschafter_innen.csv",
]


def read_raw(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return pd.read_csv(
                path,
                header=None,
                dtype=str,
                keep_default_na=False,
                encoding=encoding,
            )
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Could not read {path.name}")


for filename in FILES:
    path = INPUT_DIR / filename
    print("\n" + "=" * 100)
    print(filename)

    if not path.exists():
        print("FILE NOT FOUND")
        continue

    try:
        df = read_raw(path)
        print(df.head(20).to_string(index=True, header=False))
    except Exception as error:
        print(f"ERROR: {error}")
