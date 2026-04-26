#!/usr/bin/env python3
"""
Focused regression tests for the staged extraction pipeline.
"""

import os
import sys
import types
import unittest
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

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

from gc_contacts.core.extraction import detect_potential_anchor_patterns, run_contact_extraction_pipeline  # noqa: E402


PAGE_URL = "https://example.it/international"


def _text_from_html(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


class ExtractionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def _run_pipeline(
        self,
        *,
        html: str = "",
        text: str = "",
        llm_contacts: list[dict] | None = None,
        allow_generic_emails: bool = True,
        country: str | None = "IT",
    ) -> dict:
        if not text and html:
            text = _text_from_html(html)

        async def fake_llm_extractor(page_text: str, page_url: str, allow_generic: bool):
            return llm_contacts or []

        llm_extractor = fake_llm_extractor if llm_contacts is not None else None
        return await run_contact_extraction_pipeline(
            html,
            text,
            PAGE_URL,
            role_keywords=["international office", "international partnerships", "international relations"],
            country=country,
            allow_generic_emails=allow_generic_emails,
            llm_extractor=llm_extractor,
            llm_name_cleaner=None,
        )

    async def test_mailto_only_email_yields_candidate(self):
        html = """
            <div>
                <p>International Office</p>
                <a href="mailto:international@example.it">Email us</a>
            </div>
        """
        result = await self._run_pipeline(html=html)

        self.assertEqual(result["raw_evidence_count_by_strategy"]["mailto_explicit"], 1)
        self.assertEqual(result["office_contact_count"], 1)
        office = result["office_contacts"][0]
        self.assertEqual(office["email"], "international@example.it")
        self.assertEqual(office["name"], "")
        self.assertIn("mailto_only", office["cleanup_flags"])

    async def test_query_param_mailto_anchor_is_recovered(self):
        html = """
            <div>
                <p>Daniel OLLIVIER</p>
                <p>Director of International Relations</p>
                <a href="/servlet/com.jsbsoft.jtf.core.SG?PROC=ENVOIMAIL&ACTION=CREER_MAIL_DIRECT&MAILTO=ollivier.international@icp.fr">
                    Email
                </a>
            </div>
        """
        result = await self._run_pipeline(html=html, country="FR")

        self.assertEqual(result["raw_evidence_count_by_strategy"]["mailto_explicit"], 1)
        self.assertEqual(result["named_contact_count"], 1)
        candidate = result["named_contacts"][0]
        self.assertEqual(candidate["name"], "Daniel Ollivier")
        self.assertEqual(candidate["email"], "ollivier.international@icp.fr")

    async def test_onclick_mailto_anchor_is_recovered(self):
        html = """
            <div>
                <p>Christophe Farges</p>
                <p>International Relations Officer</p>
                <a onclick="window.location='mailto:c.farges@icp.fr'">Email</a>
            </div>
        """
        result = await self._run_pipeline(html=html, country="FR")

        self.assertEqual(result["raw_evidence_count_by_strategy"]["mailto_explicit"], 1)
        self.assertEqual(result["named_contact_count"], 1)
        candidate = result["named_contacts"][0]
        self.assertEqual(candidate["name"], "Christophe Farges")
        self.assertEqual(candidate["email"], "c.farges@icp.fr")

    async def test_explicit_obfuscation_recovers_named_contact(self):
        text = """
            Maria Rossi
            Director of International Partnerships
            maria.rossi [at] example [dot] it
        """
        result = await self._run_pipeline(text=text)

        self.assertEqual(result["raw_evidence_count_by_strategy"]["explicit_obfuscation"], 1)
        self.assertEqual(result["named_contact_count"], 1)
        candidate = result["named_contacts"][0]
        self.assertEqual(candidate["name"], "Maria Rossi")
        self.assertIn("obfuscation_recovered", candidate["cleanup_flags"])

    async def test_js_concatenated_email_is_traced(self):
        html = """
            <script>
                const staffEmail = "mobility" + "@" + "example.it";
            </script>
            <p>International Mobility</p>
        """
        result = await self._run_pipeline(html=html)

        self.assertEqual(result["raw_evidence_count_by_strategy"]["js_decode"], 1)
        self.assertEqual(result["typed_candidates"][0]["email"], "mobility@example.it")

    async def test_structured_attribute_email_is_recovered(self):
        html = """
            <div data-user="incoming" data-domain="example.it" data-role="International Office">
                Contact desk
            </div>
        """
        result = await self._run_pipeline(html=html)

        self.assertEqual(result["raw_evidence_count_by_strategy"]["html_attribute"], 1)
        self.assertEqual(result["office_contact_count"], 1)
        office = result["office_contacts"][0]
        self.assertEqual(office["email"], "incoming@example.it")
        self.assertEqual(office["role"], "international office")

    async def test_personal_email_localpart_can_seed_name(self):
        html = """
            <div>
                <p>International Mobility Coordinator</p>
                <a href="mailto:walid.larbi@lecnam.net">Email</a>
            </div>
        """
        result = await self._run_pipeline(html=html, country="FR")

        self.assertEqual(result["named_contact_count"], 1)
        candidate = result["named_contacts"][0]
        self.assertEqual(candidate["name"], "Walid Larbi")
        self.assertEqual(candidate["email"], "walid.larbi@lecnam.net")
        self.assertIn("name_inferred_from_email_localpart", candidate["cleanup_flags"])

    async def test_context_chrome_is_sanitized_before_typing(self):
        html = """
            <div>
                <p>Main menu</p>
                <p>Cookie preferences</p>
                <p>Christophe Farges</p>
                <p>International Relations Officer / Erasmus Programme Coordinator</p>
                <a href="mailto:c.farges@icp.fr">Email</a>
                <p>Search</p>
                <p>Social media</p>
            </div>
        """
        result = await self._run_pipeline(html=html, country="FR")

        candidate = result["named_contacts"][0]
        self.assertNotIn("Main menu", candidate["context"])
        self.assertNotIn("Cookie preferences", candidate["context"])
        self.assertIn("context_sanitized", candidate["cleanup_flags"])

    async def test_potential_anchor_patterns_are_traced(self):
        html = """
            <div class="h-card">
                <a href="javascript:openContactCard()">Contact</a>
                <form action="/contact?recipient=staff-card"></form>
                <button data-recipient-code="INTL-42">Open contact</button>
            </div>
        """
        result = await self._run_pipeline(html=html, country="FR")

        self.assertGreaterEqual(result["potential_anchor_pattern_count"], 3)
        pattern_types = {item["pattern_type"] for item in result["potential_anchor_patterns"]}
        self.assertIn("javascript_contact_anchor", pattern_types)
        self.assertIn("contact_form_param", pattern_types)
        self.assertIn("structured_contact_markup", pattern_types)
        self.assertIn("data_attribute_contact_hint", pattern_types)

    async def test_xml_like_documents_use_xml_parser_for_diagnostics(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url>
                <loc>https://example.fr/contact</loc>
            </url>
        </urlset>
        """
        with warnings.catch_warnings():
            warnings.simplefilter("error", XMLParsedAsHTMLWarning)
            findings = detect_potential_anchor_patterns(xml, "https://example.fr/sitemap.xml")

        self.assertEqual(findings, [])

    async def test_address_contamination_becomes_junk(self):
        result = await self._run_pipeline(
            text="International Office staff page",
            llm_contacts=[
                {
                    "name": "Via Febbraio Padova",
                    "role": "International Office",
                    "email": "",
                    "page_url": PAGE_URL,
                    "evidence_type": "person_without_email",
                }
            ],
        )

        self.assertEqual(result["person_without_email_count"], 0)
        self.assertEqual(result["junk_candidate_count"], 1)
        self.assertIn("address_like_name", result["typed_candidates"][0]["cleanup_flags"])

    async def test_office_label_contamination_becomes_junk(self):
        result = await self._run_pipeline(
            text="International office directory",
            llm_contacts=[
                {
                    "name": "Palazzo Anselmi",
                    "role": "International Office",
                    "email": "",
                    "page_url": PAGE_URL,
                    "evidence_type": "person_without_email",
                }
            ],
        )

        self.assertEqual(result["person_without_email_count"], 0)
        self.assertEqual(result["junk_candidate_count"], 1)
        self.assertIn("office_label_name", result["typed_candidates"][0]["cleanup_flags"])

    async def test_named_person_with_email_becomes_named_contact(self):
        text = """
            Maria Rossi
            Director of International Partnerships
            maria.rossi@example.it
        """
        result = await self._run_pipeline(text=text)

        self.assertEqual(result["named_contact_count"], 1)
        candidate = result["named_contacts"][0]
        self.assertEqual(candidate["name"], "Maria Rossi")
        self.assertEqual(candidate["email"], "maria.rossi@example.it")

    async def test_named_person_without_email_is_preserved_for_enrichment(self):
        result = await self._run_pipeline(
            text="International Relations page",
            llm_contacts=[
                {
                    "name": "Giulia Bianchi",
                    "role": "Head of International Relations",
                    "email": "",
                    "page_url": PAGE_URL,
                    "evidence_type": "person_without_email",
                }
            ],
        )

        self.assertEqual(result["person_without_email_count"], 1)
        candidate = result["missing_email_candidates"][0]
        self.assertEqual(candidate["candidate_type"], "person_without_email")
        self.assertEqual(candidate["name"], "Giulia Bianchi")
        self.assertIn("llm_named_without_email", candidate["cleanup_flags"])

    async def test_office_inbox_stays_nameless(self):
        html = """
            <div>
                <p>International Relations Office</p>
                <a href="mailto:office@example.it">Write to the office</a>
            </div>
        """
        result = await self._run_pipeline(html=html)

        self.assertEqual(result["office_contact_count"], 1)
        candidate = result["office_contacts"][0]
        self.assertEqual(candidate["name"], "")
        self.assertEqual(candidate["candidate_type"], "office_contact")

    async def test_italy_localisation_normalises_local_role_terms(self):
        text = """
            Mario Rossi
            Ufficio Relazioni Internazionali
            mario.rossi@example.it
        """
        result = await self._run_pipeline(text=text, country="IT")

        self.assertEqual(result["named_contact_count"], 1)
        candidate = result["named_contacts"][0]
        self.assertEqual(candidate["role"], "international relations")

    async def test_bogus_prose_fragment_becomes_junk_candidate(self):
        text = "Please review applic@ions.the instructions before submission."
        result = await self._run_pipeline(text=text, allow_generic_emails=False)

        self.assertEqual(result["named_contact_count"], 0)
        self.assertEqual(result["office_contact_count"], 0)
        self.assertEqual(len(result["candidates_for_filtering"]), 0)
        self.assertEqual(result["junk_candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
