"""
Base interface for all target sources.
All sources must implement TargetSource and yield Target instances.
"""

from __future__ import annotations
from typing import AsyncIterator, Optional

from gc_contacts.core.models import Target


class TargetSource:
    """
    Abstract base class for target providers.

    Subclasses must implement fetch_targets().
    Each yielded value must be a Target instance.
    """

    async def fetch_targets(
        self,
        country: str,
        limit: Optional[int] = None,
    ) -> AsyncIterator[Target]:
        """
        Yield Target objects for the given country up to `limit`.

        Args:
            country:  ISO country code, e.g. "GB", "US", "AE".
            limit:    Maximum number of targets to yield; None = unlimited.
        """
        raise NotImplementedError
        # make the type-checker happy: this is an async generator stub
        yield  # type: ignore[misc]
