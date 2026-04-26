"""
Debug and training CSV output helpers.
"""

import json
import logging
from pathlib import Path
from typing import Dict

import aiofiles

import gc_contacts.config as config
from gc_contacts.core.utils import safe_slug, url_features

LOG = logging.getLogger("gc")


async def append_training_row(row: Dict[str, object]):
    """Append a row to the debug training CSV."""
    if not config.DEBUG_ENABLED or not config.TRAIN_CSV_PATH:
        return
    header = not config.TRAIN_CSV_PATH.exists()
    async with aiofiles.open(config.TRAIN_CSV_PATH, "a", encoding="utf-8", newline="") as f:
        if header:
            await f.write(",".join(config.TRAIN_HEADERS) + "\n")
        vals = []
        for h in config.TRAIN_HEADERS:
            v = row.get(h, "")
            if v is None:
                v = ""
            s = str(v).replace("\n", " ").replace("\r", " ").replace(",", " ")
            vals.append(s)
        await f.write(",".join(vals) + "\n")


async def write_debug_json(uni_name: str, data: dict):
    """Write per-university debug JSON."""
    if not config.DEBUG_ENABLED:
        return
    config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{safe_slug(uni_name)}.json"
    path = config.DEBUG_DIR / fname
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))
