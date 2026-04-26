"""
OpenAlex institution source.

Fetches universities for a given country using the OpenAlex public API
and yields them as Target instances.
"""

from __future__ import annotations
import logging
from typing import AsyncIterator, Optional

import gc_contacts.config as config
from gc_contacts.core.models import Target
from gc_contacts.sources.base import TargetSource

LOG = logging.getLogger("gc")


class OpenAlexSource(TargetSource):
    """Yields universities from the OpenAlex /institutions endpoint."""

    async def fetch_targets(
        self,
        country: str,
        limit: Optional[int] = None,
    ) -> AsyncIterator[Target]:
        """
        Stream universities for `country` from OpenAlex.

        Args:
            country:  ISO country code (e.g. "GB", "US").
            limit:    Maximum number of institutions to yield.

        Yields:
            Target(name, url, country, org_type="university", source="openalex")
        """
        per_page = 200
        cursor = "*"
        seen = 0
        root = (
            f"{config.OPENALEX_API}"
            f"?filter=country_code:{country},type:education"
            f"&per_page={per_page}"
            )

        while True:
            url = f"{root}&cursor={cursor}"
            try:
                r = await config.HTTP.get(url)
                r.raise_for_status()
                rsp = r.json()
            except Exception as e:
                LOG.warning("OpenAlex request failed: %s", e)
                import asyncio
                await asyncio.sleep(2)
                continue

            for item in rsp.get("results", []):
                home = item.get("homepage_url")
                if home:
                    yield Target(
                        name=item["display_name"],
                        url=home,
                        country=country,
                        org_type="university",
                        source="openalex",
                    )
                    seen += 1
                    if limit is not None and seen >= limit:
                        return

            cursor = rsp.get("meta", {}).get("next_cursor")
            if not cursor:
                break


# ── module-level convenience function (mirrors old openalex.py API) ──────────
async def fetch_openalex_unis(country: str, limit: Optional[int] = None):
    """
    Convenience async-generator wrapper around OpenAlexSource.

    Yields plain dicts {"name": ..., "url": ...} for backward compatibility
    with code that still calls fetch_openalex_unis directly.
    """
    source = OpenAlexSource()
    async for target in source.fetch_targets(country, limit):
        yield {"name": target.name, "url": target.url}
