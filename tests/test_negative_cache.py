#!/usr/bin/env python3
"""
Focused regression tests for durable negative caching of dead URLs.
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("OPENAI_API_KEY", "test-key")
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub
if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")

    class _AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            pass

    openai_stub.AsyncOpenAI = _AsyncOpenAI
    sys.modules["openai"] = openai_stub

from gc_contacts.core import http_client  # noqa: E402


class NegativeCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_404_is_negative_cached_and_skipped_on_second_fetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            negative_dir = cache_dir / "negative"
            negative_dir.mkdir()
            calls: list[str] = []

            async def fake_allowed(url):
                return True

            async def fake_read_cache(url):
                return None

            async def fake_get_with_retry(url, tries=3):
                calls.append(url)
                if len(calls) == 1:
                    await http_client.write_negative_cache(url, 404)
                return None

            with patch.object(http_client.config, "CACHE_DIR", cache_dir), patch.object(
                http_client.config, "NEGATIVE_CACHE_DIR", negative_dir
            ), patch.object(http_client.config, "NEGATIVE_CACHE_TTL", 3600.0), patch(
                "gc_contacts.core.http_client.allowed", side_effect=fake_allowed
            ), patch(
                "gc_contacts.core.http_client.read_cache", side_effect=fake_read_cache
            ), patch(
                "gc_contacts.core.http_client.get_with_retry", side_effect=fake_get_with_retry
            ):
                first = await http_client.fetch_page("https://example.edu/missing")
                second = await http_client.fetch_page("https://example.edu/missing")

            self.assertIsNone(first)
            self.assertIsNone(second)
            self.assertEqual(calls, ["https://example.edu/missing"])


if __name__ == "__main__":
    unittest.main()
