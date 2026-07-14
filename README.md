# Organisation Enricher

This project combines contact and organisation data from multiple CSV exports, cleans and deduplicates it, and enriches missing information using email domains, Brave Search, and organisation websites.

The workflow has two parts:

1. **Data preparation** turns the source exports into one clean contact file.
2. **Enrichment** finds and validates websites, scrapes organisation details, emails, and addresses, and creates separate organisation and contact outputs.

Run all commands below from the repository root.

## Setup

Clone the repository and enter it:

```bash
git clone <repository-url>
cd organisation_enricher
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows, activate it with:

```powershell
.venv\Scripts\Activate.ps1
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

### Brave Search API key

Some enrichment steps use the Brave Search API. Create an API key at [Brave Search API](https://api.search.brave.com/app/keys), then create a `.env` file in the repository root:

```dotenv
BRAVE_API_KEY=your_key_here
```

The `.env` file is ignored by Git and should not be committed.

## Data preparation pipeline

Place the source CSV files in `data/input_raw/`. Column mappings and source-specific configuration are maintained in `scripts/data_preparation/mappings.py`.

Run the preparation scripts in order:

```bash
python scripts/data_preparation/01_consolidate_inputs.py
python scripts/data_preparation/02_analyze_staging.py
python scripts/data_preparation/03_create_contacts_raw.py
python scripts/data_preparation/04_review_contacts.py
```

These steps:

1. Combine the configured source files into a common staging format.
2. Report suspicious or malformed values without changing the data.
3. Remove rows excluded through `data/input_raw/selection.csv` and create the raw contact dataset.
4. Clean emails, websites, phone numbers, names, and organisation names; identify duplicates and generate review files.

Before the final preparation step, review the generated files in `data/staging/`, especially:

- `duplicate_list.csv` — choose which records to keep in conflicting duplicate groups.
- `organisation_name_review.csv` — correct or exclude suspicious organisation names.

Edits made to those review files are applied by the final step:

```bash
python scripts/data_preparation/05_create_contacts_clean.py
```

The main result is:

```text
data/input/contacts_clean.csv
```

This is the input for the enrichment pipeline.

## Enrichment pipeline

The enrichment scripts work incrementally. On the first run they read `data/input/contacts_clean.csv`; afterwards they continue updating `data/output/contacts_enriched.csv`.

Run them in this order:

```bash
python scripts/enrichment/0_extract_email_domains.py
python scripts/enrichment/0_parse_names.py
python scripts/enrichment/01_website_from_domain.py
python scripts/enrichment/02_website_from_brave_search.py
python scripts/enrichment/check_websites.py
python scripts/enrichment/03_fetch_pages.py
python scripts/enrichment/04_scrape_org_metadata.py
python scripts/enrichment/05_scrape_emails.py
python scripts/enrichment/06_scrape_addresses.py
python scripts/enrichment/07_split_outputs.py
```

In short, the pipeline:

1. Extracts domains from known emails and splits contact names into first and last names.
2. Derives websites from email domains where possible.
3. Uses Brave Search to find websites still missing and saves search candidates for review.
4. Checks websites, follows redirects, and stores normalized URLs and response information.
5. Fetches relevant website pages such as the homepage, contact page, about page, and Impressum.
6. Extracts organisation names and legal metadata from validated organisation evidence.
7. Extracts contact emails only when the address matches both the contact name and organisation website domain.
8. Extracts complete postal addresses and sends competing candidates to manual review.
9. Splits the combined enriched data into organisation and contact tables.

Only rows with an exact `Website_Response_Code` of `200` are eligible for page
fetching and scraping. Run `check_websites.py` before `03_fetch_pages.py`;
unchecked rows and non-200 responses are skipped.

Organisation metadata and contact emails from source/manual data are preserved.
An existing address may be replaced only when a scraped address is more
complete. Re-running steps 04–06 recomputes website-derived values, while
ambiguous or conflicting candidates are written to
`data/output/enrichment_manual_verification.csv` for review.

Website fetching can be tested on a smaller batch:

```bash
python scripts/enrichment/03_fetch_pages.py --limit 10
```

Fetched HTML is cached by the complete normalized website URL. A second run
reuses valid manifests and does not download those pages again. To deliberately
discard the valid-site marker and download pages again, use:

```bash
python scripts/enrichment/03_fetch_pages.py --force-rescrape
```

The page cache accepts only HTTP 200 HTML and rejects common soft-404/error
pages. Older unversioned domain-level manifests are ignored automatically.
`check_websites.py` saves progress after every website and, by default, skips
websites that already have a `Website_Response_Code` (resume after an
interrupted run). Use `--recheck-all` to force a full refresh of every
website's response check.

The scraping steps also accept `--limit N` and `--save-interval N`. Run a script with `--help` to see its options.

## Output datasets

### `contacts_enriched.csv`

`data/output/contacts_enriched.csv` is the combined working dataset. It keeps
one row per input contact or source record and contains both contact-level and
organisation-level data. Each enrichment step reads and updates this file. The
split step reads it but does not overwrite it.

| Column                           | Description                                                                                                                                               |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ID`                             | Contact/source-record identifier created during data preparation.                                                                                         |
| `Email_Domain`                   | Domain extracted from the primary email address.                                                                                                          |
| `Email_Domain_Source`            | How the email domain was obtained, for example derived from an email.                                                                                     |
| `Website`                        | Original or selected organisation website value.                                                                                                          |
| `Website_Normalized`             | Reachable final URL after URL checks and redirects.                                                                                                       |
| `Website_Response_Code`          | HTTP response code or a named connection error such as `TIMEOUT` or `DNS_ERROR`.                                                                          |
| `Website_Response_Text`          | Human-readable explanation of the website response.                                                                                                       |
| `Website_Source`                 | Origin of the website, such as the input data, email domain, or Brave Search.                                                                             |
| `Organisation_Name`              | Primary organisation name.                                                                                                                                |
| `Organisation_Name_Source`       | Origin of the organisation name. Values beginning with `Website` were produced by scraping.                                                               |
| `Organisation_Type`              | Broad type, such as company, association, foundation, or public institution.                                                                              |
| `Organisation_Type_Source`       | Origin of the organisation type.                                                                                                                          |
| `Organisation_Legal_Form`        | Detected legal form, such as `GmbH`, `gGmbH`, `e.V.`, or `Stiftung`.                                                                                      |
| `Organisation_Legal_Form_Source` | Origin of the legal form.                                                                                                                                 |
| `Organisation_Nonprofit_Status`  | `Yes`, `No`, or `Unclear`, based on strong legal-form or website evidence.                                                                                |
| `Organisation_Nonprofit_Source`  | Origin of the nonprofit status.                                                                                                                           |
| `Email`                          | Primary contact email. Website scraping only fills this for a named contact when the name and site domain match.                                          |
| `Email_Source`                   | Origin of the primary email.                                                                                                                              |
| `Email_Alternatives`             | Additional validated, pipe-separated contact emails.                                                                                                      |
| `Address`                        | Primary postal address.                                                                                                                                   |
| `Address_Source`                 | Origin of the address, for example `Website Impressum`.                                                                                                   |
| `Address_Alternatives`           | Reserved for pipe-separated alternatives. The current scraper sends competing addresses to the review output instead of filling this field automatically. |
| `Contact_Name`                   | Original full contact name.                                                                                                                               |
| `Contact_Name_First_Name`        | Parsed first name when parsing is unambiguous.                                                                                                            |
| `Contact_Name_Last_Name`         | Parsed last name when parsing is unambiguous.                                                                                                             |
| `Contact_Name_Source`            | Origin of the parsed or supplied contact name.                                                                                                            |
| `Phone`                          | Primary phone number from the prepared source data.                                                                                                       |
| `Phone_Alternatives`             | Pipe-separated additional phone numbers.                                                                                                                  |
| `Website_Alternatives`           | Pipe-separated additional website values from source data.                                                                                                |
| `LinkedIn`                       | LinkedIn URL or value from source data.                                                                                                                   |
| `Role`                           | Contact role or job title.                                                                                                                                |
| `Sector`                         | Organisation sector from source data.                                                                                                                     |
| `Location`                       | General location from source data.                                                                                                                        |
| `Description`                    | Organisation or contact description from source data.                                                                                                     |
| `Responsible_Person`             | Internal person responsible for the record.                                                                                                               |
| `Source_File`                    | Original import filename.                                                                                                                                 |
| `Source_Row`                     | Row number in the original source file.                                                                                                                   |

### `organisations_enriched.csv`

`data/output/organisations_enriched.csv` contains one row per organisation
group. Rows are grouped using the first available key in this order:
`Website_Normalized`, `Website`, then `Email_Domain`. Records without any of
these values cannot be assigned to an organisation and are omitted from this
file.

When grouped contact rows contain different non-empty organisation values, the
split step selects a representative value, preferring longer and more frequent
values. `Organisation_ID` values are sequential integers assigned in first-seen
order and are regenerated each time the split step runs; they are not permanent
IDs across differently ordered datasets.

| Column                           | Description                                                                     |
| -------------------------------- | ------------------------------------------------------------------------------- |
| `Organisation_ID`                | Sequential organisation ID used to link the two split outputs.                  |
| `Organisation_Key`               | Normalized grouping value selected from website or email domain.                |
| `Organisation_Name`              | Representative organisation name for the group.                                 |
| `Organisation_Name_Source`       | Origin of the representative organisation name.                                 |
| `Organisation_Type`              | Representative broad organisation type.                                         |
| `Organisation_Type_Source`       | Origin of the organisation type.                                                |
| `Organisation_Legal_Form`        | Representative legal form.                                                      |
| `Organisation_Legal_Form_Source` | Origin of the legal form.                                                       |
| `Organisation_Nonprofit_Status`  | Representative nonprofit status.                                                |
| `Organisation_Nonprofit_Source`  | Origin of the nonprofit status.                                                 |
| `Website`                        | Representative original or selected website.                                    |
| `Website_Normalized`             | Representative checked and normalized website URL.                              |
| `Website_Source`                 | Origin of the website.                                                          |
| `Website_Response_Code`          | Result of checking the website.                                                 |
| `Website_Response_Text`          | Human-readable website check result.                                            |
| `Email_Domain`                   | Representative organisation email domain.                                       |
| `Address`                        | Representative postal address.                                                  |
| `Address_Source`                 | Origin of the address.                                                          |
| `Address_Alternatives`           | Representative alternative-address value, when present in the combined dataset. |
| `Related_Contact_IDs`            | Pipe-separated `ID` values of contacts in the organisation group.               |
| `Contact_Count`                  | Number of contact rows assigned to the organisation.                            |

### `contacts_enriched_split.csv`

`data/output/contacts_enriched_split.csv` is the contact-focused export. It
keeps every row from `contacts_enriched.csv`, removes the organisation-level
columns, and adds `Organisation_ID` as the link to
`organisations_enriched.csv`. The ID is blank when a contact has no website or
email-domain grouping key.

| Column                    | Description                                                                            |
| ------------------------- | -------------------------------------------------------------------------------------- |
| `ID`                      | Original contact/source-record identifier.                                             |
| `Organisation_ID`         | Link to the matching row in `organisations_enriched.csv`; blank if no group was found. |
| `Contact_Name`            | Full contact name.                                                                     |
| `Contact_Name_First_Name` | Parsed first name.                                                                     |
| `Contact_Name_Last_Name`  | Parsed last name.                                                                      |
| `Contact_Name_Source`     | Origin of the contact name components.                                                 |
| `Email`                   | Primary contact email.                                                                 |
| `Email_Source`            | Origin of the primary email.                                                           |
| `Email_Alternatives`      | Pipe-separated additional validated contact emails.                                    |
| `Phone`                   | Primary phone number.                                                                  |
| `Phone_Alternatives`      | Pipe-separated additional phone numbers.                                               |
| `LinkedIn`                | LinkedIn URL or value from source data.                                                |
| `Role`                    | Contact role or job title.                                                             |
| `Source_File`             | Original import filename.                                                              |
| `Source_Row`              | Row number in the original source file.                                                |
| `Email_Domain_Source`     | Origin of the email domain; retained as additional provenance.                         |
| `Website_Alternatives`    | Alternative source websites; retained as additional source data.                       |
| `Sector`                  | Sector from source data.                                                               |
| `Location`                | General location from source data.                                                     |
| `Description`             | Description from source data.                                                          |
| `Responsible_Person`      | Internal person responsible for the record.                                            |

The final six columns above are source/provenance fields that are not classified
as organisation-level fields by the current split script. Unknown future columns
are also preserved in this contact export unless they are explicitly designated
as organisation-level columns.

## Review, audit, and cache outputs

- `data/output/brave_candidates.csv` — Brave results used when selecting websites.
- `data/output/website_scrape_results.csv` — audit trail for website extraction.
- `data/output/enrichment_manual_verification.csv` — conflicts and uncertain values to review.

Brave responses and fetched website pages are stored under `data/cache/`.
Reruns reuse valid caches, making the pipeline restartable and reducing
unnecessary API calls and downloads.

## Notes

- The `data/` directory is ignored by Git because it contains source data, generated outputs, and cached website content.
- Do not commit personal data or API credentials.
- Check review and audit files before importing the final outputs into another system.
