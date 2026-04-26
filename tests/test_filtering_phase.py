#!/usr/bin/env python3
"""
Focused regression tests for filtering and enrichment hardening.
"""

import os
import sys
import types
import unittest
from pathlib import Path
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

    def _bs_text(html):
        return str(html or "")

    http_client_stub.fetch_page = _fetch_page
    http_client_stub.get_with_retry = _get_with_retry
    http_client_stub.normalize_url = _normalize_url
    http_client_stub.bs_text = _bs_text
    sys.modules["gc_contacts.core.http_client"] = http_client_stub

from gc_contacts.agent.enrichment import (  # noqa: E402
    _score_email_match,
    apply_evidence_to_candidate,
    build_person_candidate,
    build_role_holder_candidate,
    infer_email_pattern,
    plan_next_enrichment_action,
)
from gc_contacts.agent.rules import (  # noqa: E402
    contact_priority,
    is_university_qualified_contact,
    is_valid_contact,
)
from gc_contacts.core.filtering import explain_contact_decision, looks_like_person_name  # noqa: E402


class FilteringPhaseTests(unittest.TestCase):
    def test_named_contact_can_be_salvaged_from_supporting_context(self):
        contact = {
            "name": "Marco Rossi",
            "role": "General enquiries",
            "raw_role": "General enquiries",
            "context": "International mobility office admissions desk",
            "page_context": "International mobility office admissions desk",
            "page_url": "https://example.it/international/admissions",
            "email": "marco.rossi@example.it",
            "candidate_type": "named_contact",
            "country": "IT",
        }

        decision = explain_contact_decision(contact, "example.it", min_score=7, country="IT")

        self.assertTrue(decision["keep"])
        self.assertEqual(decision["reason"], "salvaged named contact")
        self.assertTrue(decision["name_email_alignment"])
        self.assertGreaterEqual(decision["supporting_bonus"], 2)

    def test_irrelevant_office_mailbox_is_rejected(self):
        contact = {
            "name": "",
            "role": "international mobility",
            "email": "biblioteca@consno.it",
            "candidate_type": "office_contact",
            "page_context": "international mobility office",
            "country": "IT",
        }

        decision = explain_contact_decision(contact, "consno.it", allow_generic=True, country="IT")

        self.assertFalse(decision["keep"])
        self.assertEqual(decision["reason"], "irrelevant office inbox")
        self.assertFalse(is_valid_contact(contact))
        self.assertFalse(is_university_qualified_contact(contact))

    def test_suspicious_named_mailbox_is_rejected(self):
        contact = {
            "name": "Direttore di Ragioneria",
            "role": "international mobility",
            "email": "direttore.ragioneria@consno.it",
            "candidate_type": "named_contact",
            "page_context": "international mobility office",
            "country": "IT",
        }

        decision = explain_contact_decision(contact, "consno.it", allow_generic=True, country="IT")

        self.assertFalse(decision["keep"])
        self.assertEqual(decision["reason"], "suspicious name")
        self.assertFalse(is_valid_contact(contact))

    def test_build_person_candidate_blocks_page_labels(self):
        privacy_policy = {
            "name": "Privacy Policy",
            "role": "international mobility",
            "candidate_type": "person_without_email",
            "cleanup_flags": [],
        }
        venue_label = {
            "name": "Casale di San Pio",
            "role": "international mobility",
            "candidate_type": "person_without_email",
            "cleanup_flags": [],
        }

        self.assertIsNone(build_person_candidate(privacy_policy, "https://example.it", "International mobility", country="IT"))
        self.assertIsNone(build_person_candidate(venue_label, "https://example.it", "International mobility", country="IT"))

    def test_build_person_candidate_blocks_admin_and_academic_labels(self):
        blocked_names = [
            "Amministrazione Trasparente",
            "Bienni Musicoterapia",
            "Dottorati di Ricerca",
            "European Credit Transfer System",
            "Inter-Istitutional Agreement",
            "Organizzazione Interna",
            "Personale Tecnico",
            "Richiedi Info",
            "Sant'Anna Magazine",
            "Studi di Sassari",
        ]

        for name in blocked_names:
            with self.subTest(name=name):
                candidate = {
                    "name": name,
                    "role": "international mobility",
                    "candidate_type": "person_without_email",
                    "cleanup_flags": [],
                }
                self.assertIsNone(build_person_candidate(candidate, "https://example.it", "International mobility", country="IT"))

    def test_build_role_holder_candidate_accepts_relevant_international_head(self):
        contact = {
            "name": "",
            "role": "Responsabile relazioni internazionali",
            "raw_role": "Responsabile relazioni internazionali",
            "email": "responsabile.internazionale@example.it",
            "candidate_type": "office_contact",
            "cleanup_flags": [],
            "source_strategies": ["visible_regex"],
        }

        candidate = build_role_holder_candidate(
            contact,
            "https://example.it/internazionale/contatti",
            "Ufficio relazioni internazionali",
            expected_yield=4.0,
            country="IT",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["candidate_kind"], "role_holder")
        self.assertEqual(candidate["status"], "pending")
        self.assertEqual(candidate["next_action"], "search_governance_pages")
        self.assertIn("responsabile relazioni internazionali", candidate["role_search_terms"])

    def test_build_role_holder_candidate_rejects_generic_international_mailbox(self):
        contact = {
            "name": "",
            "role": "international office",
            "raw_role": "international office",
            "email": "international@example.it",
            "candidate_type": "office_contact",
            "cleanup_flags": [],
        }

        candidate = build_role_holder_candidate(
            contact,
            "https://example.it/international/contact",
            "International office",
            expected_yield=4.0,
            country="IT",
        )

        self.assertIsNone(candidate)

    def test_email_match_requires_name_alignment(self):
        candidate = {
            "name": "Casale di San Pio",
            "role": "international mobility",
        }

        self.assertEqual(_score_email_match("a.alfano@unilink.it", candidate, "unilink.it"), 0)

    def test_email_match_accepts_real_name_alignment(self):
        candidate = {
            "name": "Andrea Alfano",
            "role": "international mobility",
        }

        self.assertGreaterEqual(_score_email_match("a.alfano@unilink.it", candidate, "unilink.it"), 5)

    def test_email_match_ignores_honorific_prefixes(self):
        candidate = {
            "name": "Amb. Umberto Vattani",
            "role": "international relations",
            "country": "IT",
        }

        self.assertGreaterEqual(_score_email_match("u.vattani@example.it", candidate, "example.it"), 5)

    def test_unicode_name_detection_handles_arabic_script(self):
        self.assertTrue(looks_like_person_name("محمد علي", country="AE"))

    def test_office_contact_priority_caps_at_medium(self):
        contact = {
            "name": "",
            "role": "International Office",
            "email": "international@uni.example",
            "candidate_type": "office_contact",
            "page_context": "international partnerships and exchange",
        }

        self.assertTrue(is_valid_contact(contact))
        self.assertTrue(is_university_qualified_contact(contact))
        self.assertEqual(contact_priority(contact, "university"), "medium")

    def test_recovered_contact_preserves_candidate_provenance(self):
        candidate = {
            "name": "Andrea Alfano",
            "role": "Head of International Relations",
            "page_url": "https://unilink.it/international",
            "page_context": "International relations office",
            "candidate_type": "person_without_email",
            "source_strategies": ["llm_structured"],
            "cleanup_flags": ["llm_named_without_email"],
            "raw_name": "Andrea Alfano",
            "raw_role": "Head of International Relations",
            "clean_name": "Andrea Alfano",
            "evidence_items": [],
        }
        evidence = [
            {
                "email": "a.alfano@unilink.it",
                "evidence_url": "https://unilink.it/staff",
                "evidence_type": "same_domain_page",
                "confidence": "high",
                "score": 7,
                "recovery_reason": "direct email evidence on related page",
            }
        ]

        recovered = apply_evidence_to_candidate(candidate, evidence, "site_search")

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["candidate_type"], "named_contact")
        self.assertEqual(recovered["cleanup_flags"], ["llm_named_without_email"])
        self.assertEqual(recovered["email_normalized"], "a.alfano@unilink.it")

    def test_role_holder_name_only_evidence_moves_candidate_to_pattern_pending(self):
        candidate = {
            "name": "",
            "role": "Direttore",
            "email": "direttore@example.it",
            "office_email": "direttore@example.it",
            "page_url": "https://example.it/organigramma",
            "page_context": "Organigramma di ateneo",
            "candidate_type": "person_without_email",
            "candidate_kind": "role_holder",
            "source_strategies": ["visible_regex"],
            "cleanup_flags": [],
            "raw_name": "",
            "raw_role": "Direttore",
            "clean_name": "",
            "evidence_items": [],
            "status": "pending",
        }
        evidence = [
            {
                "name": "Mario Rossi",
                "evidence_url": "https://example.it/organigramma",
                "evidence_type": "role_holder_page",
                "confidence": "medium",
                "score": 4,
                "recovery_reason": "named role-holder on related page",
            }
        ]

        recovered = apply_evidence_to_candidate(candidate, evidence, "site_search")

        self.assertIsNone(recovered)
        self.assertEqual(candidate["status"], "pattern_pending")
        self.assertEqual(candidate["name"], "Mario Rossi")
        self.assertEqual(candidate["candidate_kind"], "person_name")
        self.assertTrue(candidate["directory_ready"])
        self.assertEqual(candidate["next_action"], "query_directory_by_name")

    def test_enrichment_planner_prefers_directory_after_name_resolution(self):
        candidate = {
            "name": "Mario Rossi",
            "role": "Direttore relazioni internazionali",
            "country": "IT",
            "candidate_type": "person_without_email",
            "candidate_kind": "person_name",
            "status": "pattern_pending",
            "directory_ready": True,
            "name_confidence": 0.8,
            "action_budget_remaining": 3,
            "attempts": {
                "governance": 1,
                "international": 0,
                "people": 0,
                "directory": 0,
                "web_search": 0,
                "pattern": 0,
            },
        }

        action = plan_next_enrichment_action(candidate)

        self.assertEqual(action, "search_people_pages")

        candidate["attempts"]["people"] = 1
        action = plan_next_enrichment_action(candidate)

        self.assertEqual(action, "query_directory_by_name")

    def test_enrichment_planner_starts_role_holders_with_governance_then_international(self):
        candidate = {
            "name": "",
            "role": "Responsabile relazioni internazionali",
            "country": "IT",
            "email": "responsabile.internazionale@example.it",
            "office_email": "responsabile.internazionale@example.it",
            "candidate_type": "person_without_email",
            "candidate_kind": "role_holder",
            "status": "pending",
            "directory_ready": False,
            "name_confidence": 0.0,
            "action_budget_remaining": 4,
            "role_search_terms": ["responsabile relazioni internazionali", "internazionale"],
            "attempts": {
                "governance": 0,
                "international": 0,
                "people": 0,
                "directory": 0,
                "web_search": 0,
                "pattern": 0,
            },
        }

        self.assertEqual(plan_next_enrichment_action(candidate), "search_governance_pages")
        candidate["attempts"]["governance"] = 1
        self.assertEqual(plan_next_enrichment_action(candidate), "search_international_pages")

    def test_pattern_inference_rejects_suspicious_candidate_name(self):
        candidate = {
            "name": "Privacy Policy",
            "role": "international mobility",
        }
        known_contacts = [
            {"name": "Andrea Alfano", "email": "a.alfano@unilink.it"},
            {"name": "Maria Rossi", "email": "m.rossi@unilink.it"},
        ]

        inferred = infer_email_pattern(candidate, known_contacts, "unilink.it")

        self.assertFalse(inferred["resolved"])
        self.assertEqual(inferred["reason"], "candidate name unsuitable for inference")


if __name__ == "__main__":
    unittest.main()
