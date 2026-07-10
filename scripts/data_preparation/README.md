# Data preparation workflow

Place these scripts in:

```text
scripts/data_preparation/
```

The existing `mappings.py` stays in the same folder.

## Folder structure

```text
organisation_enricher/
├── data/
│   ├── input_raw/
│   │   ├── selection.csv
│   │   └── source CSV files
│   ├── staging/
│   ├── input/
│   └── output/
└── scripts/
    ├── data_preparation/
    │   ├── mappings.py
    │   ├── 01_consolidate_inputs.py
    │   ├── 02_analyze_staging.py
    │   ├── 03_create_contacts_raw.py
    │   └── 04_clean_contacts.py
    └── enrichment/
```

## Run order

```bash
python scripts/data_preparation/01_consolidate_inputs.py
python scripts/data_preparation/02_analyze_staging.py
python scripts/data_preparation/03_create_contacts_raw.py
python scripts/data_preparation/04_clean_contacts.py
```

## Outputs

### Step 1

```text
data/staging/staging_all_rows.csv
data/staging/import_report.txt
```

Step 1 only maps and preserves raw values. It does not extract emails, phones, or websites.

### Step 2

```text
data/staging/staging_analysis.txt
data/staging/staging_suspicious_rows.csv
```

Step 2 only reports suspicious values. It does not modify data.

### Step 3

```text
data/staging/contacts_raw.csv
data/staging/contacts_raw_excluded.csv
```

Step 3 applies `selection.csv` exclusions. It does not clean contact data.

### Step 4

```text
data/input/contacts_clean.csv
data/staging/contacts_duplicate_emails.csv
data/staging/contacts_cleaning_review.csv
```

Step 4:

- extracts valid emails from messy text
- supports `(at)`, `[at]`, `(dot)`, and `[dot]`
- separates multiple emails
- extracts and normalizes website candidates
- extracts phone numbers
- clears obvious placeholder names such as `?`
- deduplicates only exact normalized email matches
- saves duplicate-email rows and uncertain cleaning results for review

Use `data/input/contacts_clean.csv` as the input for the enrichment pipeline.
