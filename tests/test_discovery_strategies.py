#!/usr/bin/env python3
"""
Focused regression tests for the strategy-aware discovery layer.
"""

import sys
import unittest
import types
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

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
if "gc_contacts.core.http_client" not in sys.modules:
    http_client_stub = types.ModuleType("gc_contacts.core.http_client")

    async def _fetch_page(url, expect_html=True):
        return None

    async def _get_with_retry(url, tries=3):
        return None

    def _normalize_url(url):
        parsed = urlparse(url)
        parsed = parsed._replace(fragment="")
        return urlunparse(parsed)

    http_client_stub.fetch_page = _fetch_page
    http_client_stub.get_with_retry = _get_with_retry
    http_client_stub.normalize_url = _normalize_url
    sys.modules["gc_contacts.core.http_client"] = http_client_stub

from gc_contacts.core.discovery import clear_discovery_runtime_state, gather_candidates_bundle, pick_preferred_hreflang  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, json_data=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data


class DiscoveryStrategyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_discovery_runtime_state()

    async def test_real_link_only_surfaces_second_hop_contacts_page(self):
        pages = {
            "https://example.it": """
                <html><body>
                    <nav><a href="/services">Services</a></nav>
                </body></html>
            """,
            "https://example.it/services": """
                <html><body>
                    <a href="/contatti">Contatti</a>
                </body></html>
            """,
        }

        async def fake_fetch_page(url, expect_html=True):
            return pages.get(url.rstrip("/")) or pages.get(url)

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle("https://example.it", country="IT", mode="real_link_only")

        urls = [candidate["url"] for candidate in bundle["candidates"]]
        self.assertIn("https://example.it/contatti", urls)
        self.assertTrue(any(candidate["source_strategy"] == "real_link_multihop" for candidate in bundle["candidates"]))

    async def test_generated_slug_only_keeps_profile_and_localised_slug_candidates(self):
        async def fake_fetch_page(url, expect_html=True):
            if url.rstrip("/") == "https://example.it":
                return "<html><body><a href='/foo'>Foo</a></body></html>"
            return None

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle(
                "https://example.it",
                country="IT",
                mode="generated_slug_only",
                extra_slugs=["/international-office"],
            )

        urls = {candidate["url"] for candidate in bundle["candidates"]}
        self.assertIn("https://example.it/rubrica", urls)
        self.assertIn("https://example.it/docenti", urls)
        self.assertTrue(any(candidate["source_strategy"] == "profile_slugs" for candidate in bundle["candidates"]))

    async def test_generated_slug_only_uses_france_localised_slug_candidates(self):
        async def fake_fetch_page(url, expect_html=True):
            if url.rstrip("/") == "https://example.fr":
                return "<html><body><a href='/foo'>Foo</a></body></html>"
            return None

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle(
                "https://example.fr",
                country="FR",
                mode="generated_slug_only",
            )

        urls = {candidate["url"] for candidate in bundle["candidates"]}
        self.assertIn("https://example.fr/annuaire", urls)
        self.assertIn("https://example.fr/fr/annuaire", urls)
        self.assertTrue(any(candidate["source_strategy"] == "profile_slugs" for candidate in bundle["candidates"]))

    async def test_generated_slug_only_builds_branch_family_templates_from_homepage_links(self):
        async def fake_fetch_page(url, expect_html=True):
            if url.rstrip("/") == "https://example.fr":
                return "<html><body><nav><a href='/vie-internationale'>Vie internationale</a></nav></body></html>"
            return None

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle(
                "https://example.fr",
                country="FR",
                mode="generated_slug_only",
            )

        urls = {candidate["url"] for candidate in bundle["collector_breakdown"]["family_templates"]}
        self.assertTrue(any(url.endswith("/vie-internationale/personnels") for url in urls))
        self.assertTrue(any(candidate["source_strategy"] == "family_templates" for candidate in bundle["candidates"]))

    async def test_real_link_only_uses_robots_and_nested_sitemaps(self):
        pages = {
            "https://example.fr": "<html><body><a href='/foo'>Foo</a></body></html>",
            "https://example.fr/robots.txt": "User-agent: *\nSitemap: https://example.fr/custom-sitemap.xml\n",
            "https://example.fr/custom-sitemap.xml": (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<sitemapindex>"
                "<sitemap><loc>https://example.fr/pages-sitemap.xml</loc></sitemap>"
                "</sitemapindex>"
            ),
            "https://example.fr/pages-sitemap.xml": (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<urlset>"
                "<url><loc>https://example.fr/relations-internationales</loc></url>"
                "</urlset>"
            ),
        }

        async def fake_fetch_page(url, expect_html=True):
            return pages.get(url.rstrip("/")) or pages.get(url)

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle("https://example.fr", country="FR", mode="real_link_only")

        urls = {candidate["url"] for candidate in bundle["collector_breakdown"]["sitemap"]}
        self.assertIn("https://example.fr/relations-internationales", urls)

    async def test_real_link_only_collects_directory_search_forms(self):
        pages = {
            "https://example.fr": """
                <html><body>
                    <form action="/annuaire" method="get" aria-label="Annuaire du personnel">
                        <input type="text" name="q" placeholder="Rechercher un personnel" />
                    </form>
                </body></html>
            """,
        }

        async def fake_fetch_page(url, expect_html=True):
            return pages.get(url.rstrip("/")) or pages.get(url)

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle("https://example.fr", country="FR", mode="real_link_only")

        structured_candidates = bundle["collector_breakdown"]["structured_endpoints"]
        urls = {candidate["url"] for candidate in structured_candidates}
        self.assertIn("https://example.fr/annuaire", urls)
        self.assertTrue(all(candidate.get("candidate_bucket") == "search_interface" for candidate in structured_candidates))
        self.assertTrue(
            any(candidate["source_strategy"] == "structured_endpoints" for candidate in bundle["candidates"])
        )

    async def test_discovery_bundle_is_cached_within_process(self):
        calls = {"fetch": 0}

        async def fake_fetch_page(url, expect_html=True):
            calls["fetch"] += 1
            if url.rstrip("/") == "https://example.fr":
                return "<html><body><a href='/foo'>Foo</a></body></html>"
            return None

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            first = await gather_candidates_bundle("https://example.fr", country="FR", mode="generated_slug_only")
            first_fetch_count = calls["fetch"]
            second = await gather_candidates_bundle("https://example.fr", country="FR", mode="generated_slug_only")

        self.assertEqual(first["candidates"], second["candidates"])
        self.assertEqual(calls["fetch"], first_fetch_count)

    async def test_discovery_rescues_stale_language_subdomain_homepage(self):
        async def fake_fetch_page(url, expect_html=True):
            normalized = url.rstrip("/")
            if normalized == "http://en.example.fr":
                return None
            if normalized == "https://example.fr":
                return "<html><body><nav><a href='/relations-internationales'>Relations internationales</a></nav></body></html>"
            return None

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle("http://en.example.fr", country="FR", mode="hybrid")

        self.assertEqual(bundle.get("resolved_home_url"), "https://example.fr")
        self.assertTrue(any(item.get("reason") == "drop_lang_subdomain_https_root" and item.get("fetched") == "true" for item in bundle.get("homepage_rescue_trace", [])))
        self.assertTrue(any(candidate["url"] == "https://example.fr/relations-internationales" for candidate in bundle["candidates"]))

    async def test_discovery_can_rescue_homepage_via_web_search(self):
        async def fake_fetch_page(url, expect_html=True):
            normalized = url.rstrip("/")
            if normalized in {
                "http://old.example.fr",
                "https://old.example.fr",
                "http://www.old.example.fr",
                "https://www.old.example.fr",
                "https://official-example.fr",
            }:
                if normalized == "https://official-example.fr":
                    return "<html><head><title>Université Example</title></head><body><h1>Université Example</h1></body></html>"
                return None
            return None

        async def fake_get_with_retry(url, tries=3):
            if url.startswith("https://html.duckduckgo.com/html/?q="):
                return types.SimpleNamespace(
                    text="""
                        <html><body>
                            <a href="https://official-example.fr">Université Example</a>
                        </body></html>
                    """
                )
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle(
                "http://old.example.fr",
                country="FR",
                mode="hybrid",
                target_name="Université Example",
            )

        self.assertEqual(bundle.get("resolved_home_url"), "https://official-example.fr")
        self.assertTrue(any(item.get("reason") == "web_search_query" and item.get("fetched") == "true" for item in bundle.get("homepage_rescue_trace", [])))
        self.assertTrue(any(item.get("reason") == "web_search_candidate" and item.get("fetched") == "true" for item in bundle.get("homepage_rescue_trace", [])))

    async def test_pick_preferred_hreflang_uses_country_specific_preferences(self):
        home_html = """
            <html>
                <head>
                    <link rel="alternate" hreflang="en" href="https://example.fr/en" />
                    <link rel="alternate" hreflang="fr" href="https://example.fr/fr" />
                </head>
                <body></body>
            </html>
        """

        preferred_url, hopped = pick_preferred_hreflang(home_html, "https://example.fr", country="FR")

        self.assertTrue(hopped)
        self.assertEqual(preferred_url, "https://example.fr/fr")

    async def test_hybrid_breakdown_includes_all_named_modes(self):
        async def fake_fetch_page(url, expect_html=True):
            if url.rstrip("/") == "https://example.it":
                return "<html><body><nav><a href='/services'>Services</a></nav></body></html>"
            if url.rstrip("/") == "https://example.it/services":
                return "<html><body><a href='/rubrica'>Rubrica</a></body></html>"
            return None

        async def fake_get_with_retry(url, tries=3):
            return None

        with patch("gc_contacts.core.discovery.fetch_page", side_effect=fake_fetch_page), patch(
            "gc_contacts.core.discovery.get_with_retry", side_effect=fake_get_with_retry
        ):
            bundle = await gather_candidates_bundle(
                "https://example.it",
                country="IT",
                mode="hybrid",
                extra_slugs=["/international-office"],
                include_strategy_breakdown=True,
            )

        self.assertEqual(
            set(bundle["by_strategy"].keys()),
            {"heuristic_only", "generated_slug_only", "real_link_only", "hybrid"},
        )
        self.assertTrue(any(candidate["source_strategy"] == "real_link_multihop" for candidate in bundle["by_strategy"]["real_link_only"]))
        self.assertTrue(any(candidate["source_strategy"] == "profile_slugs" for candidate in bundle["by_strategy"]["generated_slug_only"]))


if __name__ == "__main__":
    unittest.main()
