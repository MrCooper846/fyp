#!/usr/bin/env python3
"""
Focused regression tests for durable redirect caching of legacy URLs.
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


class _FakeResponse:
    def __init__(self, url: str, text: str = "<html></html>", headers: dict | None = None):
        self.url = url
        self.text = text
        self.headers = headers or {"Content-Type": "text/html"}


class RedirectCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirected_url_is_cached_and_reused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            negative_dir = cache_dir / "negative"
            redirect_dir = cache_dir / "redirects"
            negative_dir.mkdir()
            redirect_dir.mkdir()
            calls: list[str] = []

            async def fake_allowed(url):
                return True

            async def fake_get_with_retry(url, tries=3):
                calls.append(url)
                return _FakeResponse("https://www.example.edu/home", "<html><body>ok</body></html>")

            with patch.object(http_client.config, "CACHE_DIR", cache_dir), patch.object(
                http_client.config, "NEGATIVE_CACHE_DIR", negative_dir
            ), patch.object(
                http_client.config, "REDIRECT_CACHE_DIR", redirect_dir
            ), patch.object(
                http_client.config, "REDIRECT_CACHE_TTL", 3600.0
            ), patch(
                "gc_contacts.core.http_client.allowed", side_effect=fake_allowed
            ), patch(
                "gc_contacts.core.http_client.get_with_retry", side_effect=fake_get_with_retry
            ):
                first = await http_client.fetch_page("http://www.example.edu/legacy")
                second = await http_client.fetch_page("http://www.example.edu/legacy")

            self.assertEqual(first, "<html><body>ok</body></html>")
            self.assertEqual(second, "<html><body>ok</body></html>")
            self.assertEqual(calls, ["http://www.example.edu/legacy"])


if __name__ == "__main__":
    unittest.main()
