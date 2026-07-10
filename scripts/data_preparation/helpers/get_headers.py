from pathlib import Path
import pandas as pd

folder = Path("data/input_raw")

for file in sorted(folder.glob("*.csv")):
    try:
        columns = pd.read_csv(file, nrows=0).columns.tolist()
        print(f"\n{file.name}")
        print(columns)
    except Exception as error:
        print(f"\n{file.name}")
        print(f"ERROR: {error}")