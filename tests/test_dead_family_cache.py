#!/usr/bin/env python3
"""
Focused regression tests for durable dead candidate family caching.
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

from gc_contacts.agent.controller import (  # noqa: E402
    _is_persistently_dead_candidate_family,
    _persist_dead_candidate_family,
)


class DeadFamilyCacheTests(unittest.TestCase):
    def test_dead_family_cache_persists_and_is_read_back(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dead_family_dir = Path(tmpdir)
            signature = "www.example.edu:international::team"
            with patch("gc_contacts.agent.controller.config.DEAD_FAMILY_CACHE_DIR", dead_family_dir), patch(
                "gc_contacts.agent.controller.config.DEAD_FAMILY_CACHE_TTL", 3600.0
            ):
                _persist_dead_candidate_family(signature, "zero_text_heuristic_family", {"text_length": 0})
                self.assertTrue(_is_persistently_dead_candidate_family(signature))


if __name__ == "__main__":
    unittest.main()
