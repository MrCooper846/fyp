"""
Commercial / company target source.

Supports multiple input methods:
  - Static list of (name, url, country) tuples
  - CSV file  (columns: name, url, country)
  - API placeholders (Crunchbase, Tracxn, NAFSA exhibitors)

All methods yield Target instances with org_type="company".
"""

from __future__ import annotations
import csv
import logging
from pathlib import Path
from typing import AsyncIterator, List, Optional, Tuple

from gc_contacts.core.models import Target
from gc_contacts.sources.base import TargetSource

LOG = logging.getLogger("gc")


class CompanySource(TargetSource):
    """
    Yields company / commercial targets.

    Instantiate with one of:
        CompanySource(static_list=[...])
        CompanySource(csv_path="companies.csv")
        CompanySource(api="crunchbase")   # future
    """

    def __init__(
        self,
        static_list: Optional[List[Tuple[str, str, str]]] = None,
        csv_path: Optional[str | Path] = None,
        api: Optional[str] = None,
    ):
        """
        Args:
            static_list:  List of (name, url, country) tuples.
            csv_path:     Path to CSV with columns: name, url, country.
            api:          API provider name ("crunchbase", "tracxn", "nafsa").
        """
        self._static_list = static_list or []
        self._csv_path = Path(csv_path) if csv_path else None
        self._api = api

    async def fetch_targets(
        self,
        country: str,
        limit: Optional[int] = None,
    ) -> AsyncIterator[Target]:
        """
        Yield company Targets filtered by country.

        Args:
            country:  ISO country code to filter on (case-insensitive).
            limit:    Maximum number of targets to yield.
        """
        seen = 0

        # ── 1. Static list ──────────────────────────────────────────────────
        for name, url, row_country in self._static_list:
            if country and row_country.upper() != country.upper():
                continue
            yield Target(
                name=name,
                url=url,
                country=row_country,
                org_type="company",
                source="static",
            )
            seen += 1
            if limit is not None and seen >= limit:
                return

        # ── 2. CSV file ─────────────────────────────────────────────────────
        if self._csv_path and self._csv_path.exists():
            with open(self._csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_country = (row.get("country") or "").strip()
                    if country and row_country.upper() != country.upper():
                        continue
                    name = (row.get("name") or "").strip()
                    url = (row.get("url") or "").strip()
                    if not name or not url:
                        continue
                    yield Target(
                        name=name,
                        url=url,
                        country=row_country or country,
                        org_type="company",
                        source="csv",
                        metadata={k: v for k, v in row.items() if k not in ("name", "url", "country")},
                    )
                    seen += 1
                    if limit is not None and seen >= limit:
                        return

        # ── 3. API placeholders ─────────────────────────────────────────────
        if self._api:
            if self._api == "crunchbase":
                LOG.warning("Crunchbase API integration not yet implemented.")
            elif self._api == "tracxn":
                LOG.warning("Tracxn API integration not yet implemented.")
            elif self._api == "nafsa":
                LOG.warning("NAFSA exhibitor API integration not yet implemented.")
            else:
                LOG.warning("Unknown API source: %s", self._api)
