#!/usr/bin/env python3
"""
Regression tests for targeted NAFSA runs by organisation name.
"""

import os
import sys
import types
import unittest
from pathlib import Path

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
if "aiofiles" not in sys.modules:
    aiofiles_stub = types.ModuleType("aiofiles")

    class _AsyncFile:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read(self):
            return ""

        async def write(self, data):
            return len(str(data or ""))

    def _open(*args, **kwargs):
        return _AsyncFile()

    aiofiles_stub.open = _open
    sys.modules["aiofiles"] = aiofiles_stub

from gc_contacts.core.models import Target  # noqa: E402
from gc_contacts.pipelines.nafsa_pipeline import _filter_targets_by_name  # noqa: E402


class NafsaTargetFilterTests(unittest.TestCase):
    def test_filter_targets_by_name_keeps_requested_subset(self):
        targets = [
            Target(name="Aix-Marseille Universite", url="https://amu.example", country="FR"),
            Target(name="Sorbonne Universite", url="https://sorbonne.example", country="FR"),
            Target(name="Universite de Tours", url="https://tours.example", country="FR"),
        ]

        filtered = _filter_targets_by_name(
            targets,
            ["Sorbonne Université", "Université de Tours"],
        )

        self.assertEqual([target.name for target in filtered], ["Sorbonne Universite", "Universite de Tours"])

    def test_filter_targets_by_name_returns_all_when_no_filter(self):
        targets = [
            Target(name="Aix-Marseille Universite", url="https://amu.example", country="FR"),
            Target(name="Sorbonne Universite", url="https://sorbonne.example", country="FR"),
        ]

        filtered = _filter_targets_by_name(targets, None)

        self.assertEqual([target.name for target in filtered], [target.name for target in targets])


if __name__ == "__main__":
    unittest.main()
