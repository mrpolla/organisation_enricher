"""Website-level scrape-status cache.

Stores one record per normalised URL for progress and diagnostics. Fetching uses
versioned page manifests for restartability; extraction status fields are
informational because extraction steps intentionally recompute on rerun.

Public API
----------
WebsiteCache.load()                       -> WebsiteCache  (classmethod)
cache.get(url)                            -> dict | None
cache.set(url, entry)
cache.is_fresh(url, max_age_days)         -> bool
cache.is_done(url, field)                 -> bool
cache.set_done(url, field, status="ok")
cache.save()
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import pandas as pd

CACHE_FILE = pathlib.Path("data/cache/website_scrape_cache.csv")

_COLUMNS = [
    "Website_Normalized",
    "Scrape_Status",        # ok | failed | partial  (set by 03_fetch_pages)
    "Scraped_At",
    "Pages_Checked",
    "HTTP_Status",
    "Error_Message",
    "Org_Meta_Status",      # ok | failed  (set by 04_scrape_org_metadata)
    "Email_Scrape_Status",  # ok | failed  (set by 05_scrape_emails)
    "Address_Scrape_Status",# ok | failed  (set by 06_scrape_addresses)
]


class WebsiteCache:

    def __init__(self, records: dict[str, dict]) -> None:
        self._data = records

    @classmethod
    def load(cls, path: pathlib.Path = CACHE_FILE) -> "WebsiteCache":
        if not path.exists():
            return cls({})
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False)
            df = df.apply(lambda col: col.str.strip()).replace("", None)
            return cls({
                row["Website_Normalized"]: row.to_dict()
                for _, row in df.iterrows()
                if row.get("Website_Normalized")
            })
        except Exception:
            return cls({})

    def get(self, url: str) -> dict | None:
        return self._data.get(url)

    def set(self, url: str, entry: dict) -> None:
        record = {col: entry.get(col) for col in _COLUMNS}
        record["Website_Normalized"] = url
        if not record.get("Scraped_At"):
            record["Scraped_At"] = datetime.now(tz=timezone.utc).isoformat()
        # Preserve existing step-status fields if not explicitly overwritten
        existing = self._data.get(url, {})
        for field in ("Org_Meta_Status", "Email_Scrape_Status", "Address_Scrape_Status"):
            if record.get(field) is None and existing.get(field):
                record[field] = existing[field]
        self._data[url] = record

    def is_fresh(self, url: str, max_age_days: int = 30) -> bool:
        entry = self._data.get(url)
        if not entry:
            return False
        raw = entry.get("Scraped_At")
        if not raw:
            return False
        try:
            ts = datetime.fromisoformat(str(raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(tz=timezone.utc) - ts).days < max_age_days
        except (ValueError, TypeError):
            return False

    def is_done(self, url: str, field: str) -> bool:
        """Return True if *field* is 'ok' for *url*."""
        return self._data.get(url, {}).get(field) == "ok"

    def set_done(self, url: str, field: str, status: str = "ok") -> None:
        """Mark *field* as *status* for *url*, creating the entry if needed."""
        if url not in self._data:
            self._data[url] = {"Website_Normalized": url}
        self._data[url][field] = status

    def save(self, path: pathlib.Path = CACHE_FILE) -> None:
        if not self._data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(list(self._data.values()), columns=_COLUMNS).to_csv(path, index=False)

    def __len__(self) -> int:
        return len(self._data)
