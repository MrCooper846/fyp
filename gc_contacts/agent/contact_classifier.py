"""
LLM contact classifier.

Evaluates extracted contacts and assigns a priority label
(high / medium / ignore) with a brief reason.

Used as an optional post-processing step in the NAFSA pipeline after
regex filtering.  Only applied to contacts that already passed the
keep_contact() filter — this is an enhancement layer, not a replacement.

Usage:
    from gc_contacts.agent.contact_classifier import classify_contact

    result = classify_contact({
        "contact_name": "Sarah Jones",
        "title": "Head of International Partnerships",
        "email": "s.jones@uni.ac.uk",
        "page_url": "https://uni.ac.uk/international/team",
        "organisation": "University of Example",
    })
    # {"priority": "high", "reason": "Senior role directly relevant to partnerships"}
"""

from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Dict

import gc_contacts.config as config
from gc_contacts.core.utils import tokens_of

LOG = logging.getLogger("gc.agent.classifier")

_PRIORITY_SCHEMA = {
    "name": "contact_classification",
    "schema": {
        "type": "object",
        "properties": {
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "ignore"],
            },
            "reason": {"type": "string"},
        },
        "required": ["priority", "reason"],
    },
}

_SYSTEM_PROMPT = (
    "You are an expert in international higher-education partnerships and NAFSA outreach. "
    "Given a contact's details, classify them as:\n"
    "  high   — senior decision-maker directly involved in international partnerships, "
    "global engagement, mobility, or study-abroad programmes.\n"
    "  medium — relevant but not a primary decision-maker (coordinator-level, regional officer).\n"
    "  ignore — not relevant to international partnerships (pure domestic admissions, "
    "IT staff, student support, etc.) or the contact looks malformed.\n"
    "Return JSON with 'priority' and 'reason'."
)


def classify_contact(contact: Dict) -> Dict[str, str]:
    """
    Classify a single contact using synchronous wrapper around async GPT call.

    Args:
        contact:  Dict with keys: contact_name, title, email, page_url, organisation.

    Returns:
        Dict {"priority": "high"|"medium"|"ignore", "reason": str}
    """
    try:
        return asyncio.get_event_loop().run_until_complete(_classify_async(contact))
    except RuntimeError:
        # If inside an already-running event loop (e.g. pipeline context),
        # use asyncio.run() in a thread instead.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _classify_async(contact))
            return future.result()


async def _classify_async(contact: Dict) -> Dict[str, str]:
    """Async GPT classification call."""
    user_payload = {
        "name":         contact.get("contact_name") or contact.get("name", ""),
        "title":        contact.get("title") or contact.get("role", ""),
        "email":        contact.get("email", ""),
        "organisation": contact.get("organisation", ""),
        "page_url":     contact.get("page_url", ""),
    }
    prompt = json.dumps(user_payload, ensure_ascii=False)
    need = tokens_of(prompt) + 100
    from gc_contacts.core.llm import reserve_for_model, _chat_with_retries
    await reserve_for_model(config.MODEL_LIGHT, need)
    try:
        resp = await _chat_with_retries(
            model=config.MODEL_LIGHT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": _PRIORITY_SCHEMA},
        )
        obj = json.loads(resp.choices[0].message.content)
        priority = obj.get("priority", "medium")
        reason = (obj.get("reason") or "").strip()
        if priority not in ("high", "medium", "ignore"):
            priority = "medium"
        return {"priority": priority, "reason": reason}
    except Exception as e:
        LOG.debug("classify_contact error: %s", e)
        return {"priority": "medium", "reason": "classification unavailable"}
