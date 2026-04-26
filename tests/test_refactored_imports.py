"""
Integration test to verify the canonical modular structure works correctly.
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_imports():
    """Test that canonical modules can be imported and legacy shims are gone."""
    try:
        import gc_contacts
        print(f"gc_contacts version {gc_contacts.__version__}")

        from gc_contacts import config
        print(f"config (has {len(config.TOKENS)} keyword tokens)")

        from gc_contacts.core import acquisition, debug, discovery, extraction, filtering, http_client, llm, utils
        print("core modules imported")

        from gc_contacts.sources.openalex_source import OpenAlexSource, fetch_openalex_unis
        print("sources imported")

        from gc_contacts.main import run_all
        print("main imported")

        removed_modules = [
            "gc_contacts.http_client",
            "gc_contacts.discovery",
            "gc_contacts.extraction",
            "gc_contacts.filtering",
            "gc_contacts.llm",
            "gc_contacts.openalex",
            "gc_contacts.debug",
            "gc_contacts.utils",
            "gc_contacts.cache",
            "gc_contacts.models",
        ]
        for module_name in removed_modules:
            assert importlib.util.find_spec(module_name) is None, f"Legacy shim still present: {module_name}"

        print("all modules imported successfully")
        print(f"Config: {len(config.TOKENS)} keyword tokens, {len(config.SLUGS)} slug templates")
        print("Discovery sources: real_link, sitemap, heuristic, subdomain, WordPress, Drupal")
        print("Extraction: staged pipeline, regex, JS deobfuscation, email deobfuscation")
        print("Filtering: role scoring, domain validation, name validation")
        print("LLM: GPT-powered slugs, extraction, name cleaning")
        return True
    except Exception as exc:
        print(f"\nImport failed: {exc}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_imports()
    sys.exit(0 if success else 1)
