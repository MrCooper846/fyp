"""
Shared pipeline runtime helpers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

import gc_contacts.config as config
from gc_contacts.core.models import Target
from gc_contacts.sources.base import TargetSource


async def collect_targets(
    source: TargetSource,
    country: str,
    limit: Optional[int] = None,
) -> list[Target]:
    """Materialize a source stream into a concrete list of crawl targets."""
    targets: list[Target] = []
    async for target in source.fetch_targets(country, limit):
        targets.append(target)
    return targets


@asynccontextmanager
async def pipeline_runtime(
    *,
    ignore_robots: bool = False,
    debug: bool = False,
    debug_dir: Optional[str | Path] = None,
    browser_ua: bool = False,
    training_csv: bool = False,
):
    """
    Configure shared global crawler runtime state for one pipeline execution.
    """
    previous_ignore_robots = config.IGNORE_ROBOTS
    previous_debug_enabled = config.DEBUG_ENABLED
    previous_debug_dir = config.DEBUG_DIR
    previous_train_csv = config.TRAIN_CSV_PATH
    previous_headers = dict(config.HEADERS)
    previous_http = config.HTTP

    config.IGNORE_ROBOTS = ignore_robots
    config.DEBUG_ENABLED = debug

    if browser_ua:
        config.HEADERS["User-Agent"] = config.BROWSER_UA

    if debug_dir:
        config.DEBUG_DIR = Path(debug_dir)
    if config.DEBUG_ENABLED:
        config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        config.TRAIN_CSV_PATH = config.DEBUG_DIR / "debug_training_data.csv" if training_csv else None
    else:
        config.TRAIN_CSV_PATH = None

    client = httpx.AsyncClient(
        headers=config.HEADERS,
        timeout=config.TIMEOUT,
        follow_redirects=True,
        http2=True,
    )
    config.HTTP = client

    try:
        yield
    finally:
        await config.OAI.close()
        await client.aclose()
        config.HTTP = previous_http
        config.IGNORE_ROBOTS = previous_ignore_robots
        config.DEBUG_ENABLED = previous_debug_enabled
        config.DEBUG_DIR = previous_debug_dir
        config.TRAIN_CSV_PATH = previous_train_csv
        config.HEADERS.clear()
        config.HEADERS.update(previous_headers)
