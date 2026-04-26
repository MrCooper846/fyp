"""Simple JSON file cache with TTL support.

Used to cache discovery results (candidate URLs) and extraction results
(per-page contacts) to reduce repeated HTTP/LLM work.
"""

import json
import time
import hashlib
from pathlib import Path
from typing import Any, Optional

import gc_contacts.config as config

DEFAULT_TTL = 30 * 24 * 3600  # 30 days


def _key_path(key: str) -> Path:
    """Map a cache key to a file path under CACHE_DIR."""
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return config.CACHE_DIR / f"{h}.json"


def get_json(key: str, ttl: int = DEFAULT_TTL) -> Optional[Any]:
    """Return cached payload if present and not expired."""
    path = _key_path(key)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        ts = obj.get("ts", 0)
        if ttl > 0 and (time.time() - ts) > ttl:
            return None
        return obj.get("data")
    except Exception:
        return None


def set_json(key: str, data: Any) -> None:
    """Persist payload with timestamp."""
    path = _key_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f)
    except Exception:
        # Best-effort cache; ignore write errors
        return
