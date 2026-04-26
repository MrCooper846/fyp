#!/usr/bin/env python3
"""
Smoke tests for embedded-data acquisition and extraction fallback.
"""

import asyncio
from unittest.mock import patch

from gc_contacts.core.acquisition import acquire_page_content
from gc_contacts.core.extraction import extract_embedded_json_contacts, run_contact_extraction_pipeline
from gc_contacts.core.harvest import fetch_and_extract_contacts, is_weak_llm_shell_inference, looks_like_zero_evidence_shell


HTML = """
<!DOCTYPE html>
<html>
<head><title>Example University</title></head>
<body>
  <div id="app">Loading...</div>
  <script type="application/json">
    {
      "contacts": [
        {
          "name": "Maria Rossi",
          "role": "Director of International Relations",
          "email": "maria.rossi@example.edu",
          "office": "International Office"
        }
      ]
    }
  </script>
</body>
</html>
"""


def test_acquisition_overlay():
    acquired = acquire_page_content(HTML)
    assert acquired.embedded_document_count >= 1
    assert "maria.rossi@example.edu" in acquired.embedded_text
    assert acquired.acquisition_mode in {"embedded_app_state_overlay", "static_html_plus_embedded_hints"}
    print("[ok] acquisition fallback surfaces embedded app-state text")


def test_embedded_json_contacts():
    contacts = extract_embedded_json_contacts(
        HTML,
        "https://example.edu/international",
        country="IT",
    )
    assert any(contact.get("email") == "maria.rossi@example.edu" for contact in contacts)
    print("[ok] embedded JSON contact extraction recovered a structured email")


def test_pipeline_includes_embedded_json():
    acquired = acquire_page_content(HTML)

    async def _run():
        result = await run_contact_extraction_pipeline(
            HTML,
            acquired.effective_text,
            "https://example.edu/international",
            role_keywords=["international"],
            country="IT",
            allow_generic_emails=False,
            llm_extractor=None,
            llm_name_cleaner=None,
        )
        assert result["assembled_candidate_count"] >= 1
        assert any(
            candidate.get("email_normalized") == "maria.rossi@example.edu"
            for candidate in result["typed_candidates"]
        )

    asyncio.run(_run())
    print("[ok] staged extraction pipeline keeps embedded JSON evidence in the shared path")


def test_shell_pages_with_only_llm_name_inference_count_as_zero_evidence():
    shell_payload = {
        "raw_evidence": [
            {
                "name": "Donatella Sciuto",
                "role": "Rector",
                "email": "",
                "source_strategies": ["llm_structured"],
            }
        ],
        "typed_candidates": [
            {
                "name": "Donatella Sciuto",
                "role": "rector",
                "email": "",
                "candidate_type": "person_without_email",
                "source_strategies": ["llm_structured"],
            }
        ],
        "missing_email_candidates": [
            {
                "name": "Donatella Sciuto",
                "role": "rector",
                "email": "",
                "source_strategies": ["llm_structured"],
            }
        ],
        "junk_candidates": [],
        "candidates_for_filtering": [],
        "assembled_candidate_count": 1,
        "raw_evidence_count_by_strategy": {
            "mailto_explicit": 0,
            "visible_regex": 0,
            "html_attribute": 0,
            "embedded_json": 0,
            "js_decode": 0,
            "explicit_obfuscation": 0,
            "llm_structured": 1,
        },
    }
    assert is_weak_llm_shell_inference(shell_payload)
    assert looks_like_zero_evidence_shell(shell_payload, shell_like=True, text="x" * 1200)
    print("[ok] shell-only LLM name inference is treated as zero-evidence shell")


def test_render_fallback_promotes_shell_page_when_renderer_finds_real_content():
    static_shell = (
        "<html><body>"
        + ("<script>var payload='" + ("x" * 8000) + "';</script>") * 24
        + "<div>Loading...</div></body></html>"
    )
    rendered_html = """
    <html><body>
      <div>International Office</div>
      <a href="mailto:jane.doe@example.edu">jane.doe@example.edu</a>
    </body></html>
    """

    async def _run():
        with patch("gc_contacts.core.harvest.fetch_page", side_effect=[static_shell]), patch(
            "gc_contacts.core.harvest.try_render_page_html", return_value=rendered_html
        ):
            fetched = await fetch_and_extract_contacts(
                "https://example.edu/international",
                country="FR",
                use_llm=False,
            )
        assert any(mode.startswith("rendered_") for mode in fetched.acquisition_modes)
        assert "jane.doe@example.edu" in fetched.text

    asyncio.run(_run())
    print("[ok] rendered fallback can upgrade a shell page into a useful acquisition mode")


if __name__ == "__main__":
    test_acquisition_overlay()
    test_embedded_json_contacts()
    test_pipeline_includes_embedded_json()
    test_shell_pages_with_only_llm_name_inference_count_as_zero_evidence()
    test_render_fallback_promotes_shell_page_when_renderer_finds_real_content()
