"""
LLM-powered features: slug suggestions, contact extraction, name cleaning.
"""

import json
import asyncio
import logging
import random
import time
import re
import hashlib
from typing import List, Dict, Optional

import gc_contacts.config as config
from gc_contacts.core.cache import get_json, set_json
from gc_contacts.core.utils import tokens_of
from gc_contacts.localisation import get_country_discovery_pack

LOG = logging.getLogger("gc")

_PROMISING_PAGE_TERMS = (
    "international",
    "global",
    "partnership",
    "relations",
    "mobility",
    "exchange",
    "study abroad",
    "erasmus",
    "cooperation",
    "collaboration",
    "office of",
    "team",
    "staff",
    "directory",
    "contact",
)

_PAGE_GATE_SCHEMA = {
    "name": "page_relevance_gate",
    "schema": {
        "type": "object",
        "properties": {
            "should_extract": {"type": "boolean"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["should_extract", "confidence", "reason"],
        "additionalProperties": False,
    },
}


def _model_tier(model: Optional[str]) -> str:
    if model == config.MODEL_HEAVY:
        return "heavy"
    return "light"


def _semaphore_for_model(model: Optional[str]):
    return config.HEAVY_GPT_SEM if _model_tier(model) == "heavy" else config.LIGHT_GPT_SEM


def _bucket_settings_for_model(model: Optional[str]):
    if _model_tier(model) == "heavy":
        return config.heavy_bucket_lock, "heavy_bucket_used", "heavy_bucket_reset", config.HEAVY_TOK_BUCKET
    return config.light_bucket_lock, "light_bucket_used", "light_bucket_reset", config.LIGHT_TOK_BUCKET


def _page_cache_key(prefix: str, page: str, snippet: str) -> str:
    return f"{prefix}:{page}:{hash(snippet[:4000])}"


def _page_looks_promising(text: str) -> bool:
    text_l = text.lower()
    if "@" in text_l or "mailto:" in text_l:
        return True
    return any(term in text_l for term in _PROMISING_PAGE_TERMS)


# ───────── OPENAI CALL HELPER WITH RETRIES ─────────
async def _chat_with_retries(**kwargs):
    """Call OpenAI chat API with basic retry/backoff for rate limits/5xx."""
    max_attempts = config.OAI_MAX_RETRIES
    delay = config.OAI_BACKOFF_BASE
    last_err: Optional[Exception] = None
    sem = _semaphore_for_model(kwargs.get("model"))
    for attempt in range(1, max_attempts + 1):
        try:
            async with sem:
                return await config.OAI.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            is_rate = status == 429 or "rate limit" in str(e).lower()
            is_5xx = status and 500 <= int(status) < 600
            if attempt == max_attempts or not (is_rate or is_5xx):
                raise
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, config.OAI_BACKOFF_MAX)
    if last_err:
        raise last_err


async def gpt_should_extract_contacts(text: str, page: str) -> bool:
    """
    Cheap light-model gate to decide whether a page deserves heavy extraction.
    """
    if len(text) < 100:
        return False
    if _page_looks_promising(text):
        return True

    snippet = text[:4000]
    cache_key = _page_cache_key("extract_gate", page, snippet)
    cached = get_json(cache_key)
    if isinstance(cached, dict):
        return bool(cached.get("should_extract"))

    prompt = (
        "Decide whether this page is likely to contain relevant staff contacts for "
        "international partnerships, global engagement, mobility, study abroad, "
        "international relations, or senior institutional leadership.\n"
        "Return true only if the page looks likely to contain contact details, staff names, "
        "team listings, or office information worth running a heavier extraction on.\n\n"
        f"Source: {page}\n---\n{snippet}"
    )
    need = tokens_of(prompt) + 120
    await reserve_for_model(config.MODEL_LIGHT, need)

    try:
        resp = await _chat_with_retries(
            model=config.MODEL_LIGHT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a cheap relevance gate for a contact crawler. "
                        "Only approve pages that are genuinely worth structured contact extraction."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": _PAGE_GATE_SCHEMA},
        )
        parsed = json.loads(resp.choices[0].message.content)
        set_json(cache_key, parsed)
        return bool(parsed.get("should_extract"))
    except Exception as e:
        LOG.debug("gpt_should_extract_contacts error: %s", e)
        return False


def _localized_slug_prompt_terms(country: Optional[str] = None) -> List[str]:
    pack = get_country_discovery_pack(country)
    values = []
    for key in ("slug_hints", "anchor_terms", "directory_terms", "governance_terms"):
        values.extend(str(item or "").strip() for item in pack.get(key, []) if str(item or "").strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped[:12]


async def gpt_suggest_slugs(homepage_text: str, base_url: str, country: Optional[str] = None) -> List[str]:
    """
    Use GPT to suggest likely internal URL paths for contact/staff directories.
    """
    local_terms = _localized_slug_prompt_terms(country)
    localized_hint = ""
    if local_terms:
        localized_hint = (
            f" The site may use local terms such as: {', '.join(local_terms)}."
        )
    prompt = (
        f"You are a web crawler helping find contact or staff directory pages for university leadership and international recruitment/admissions.\n"
        f"Homepage sample:\n{homepage_text[:1200]}\n\n"
        "Return up to 5 likely internal URL paths (relative) that contain people directories or contact info "
        "for leaders (chancellor/rector/president) or international/global admissions/recruitment/engagement offices. "
        "Examples: '/about/leadership', '/contact', '/international/contact', '/people', '/directory'. "
        f"{localized_hint}"
        "Only return an array of strings like '/path'."
    )

    schema_slugs = {
        "name": "slug_suggestion",
        "schema": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
    }

    need = tokens_of(prompt) + 200
    await reserve_for_model(config.MODEL_LIGHT, need)

    try:
        resp = await _chat_with_retries(
            model=config.MODEL_LIGHT,
            messages=[
                {"role": "system", "content": "You output a JSON array of slug strings per the provided schema."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": schema_slugs},
        )
        slugs = json.loads(resp.choices[0].message.content)
        if isinstance(slugs, list) and slugs:
            return [s if s.startswith("/") else f"/{s}" for s in slugs]
    except Exception:
        pass

    # fallback without structured output
    try:
        resp2 = await _chat_with_retries(
            model=config.MODEL_LIGHT,
            messages=[
                {"role": "system", "content": "Return ONLY a JSON array of strings."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        slugs2 = json.loads(resp2.choices[0].message.content)
        if isinstance(slugs2, list):
            return [s if s.startswith("/") else f"/{s}" for s in slugs2]
    except Exception:
        pass
    return []


async def gpt_extract(text: str, page: str, allow_generic_emails: bool = False) -> List[Dict[str, str]]:
    """
    Use GPT to extract relevant named people and office contacts from page content.
    """
    if len(text) < 100:
        return []
    if not await gpt_should_extract_contacts(text, page):
        return []

    snippet = text[:18000]
    cache_key = _page_cache_key(
        f"gpt_extract_v3:{'allow_generic' if allow_generic_emails else 'named_only'}",
        page,
        snippet,
    )
    cached = get_json(cache_key)
    if isinstance(cached, list):
        return cached

    generic_rule = (
        "Include relevant generic inboxes only when they are clearly tied to an international office, "
        "global engagement team, partnerships function, mobility/exchange office, study abroad office, "
        "or senior leadership office. Keep the name empty for office inboxes."
        if allow_generic_emails
        else "Exclude generic inboxes unless the page clearly pairs them with a directly relevant senior office."
    )
    prompt = (
        "Extract the strongest contact evidence candidates from this university page. "
        "Include named individuals and relevant office contacts involved in: "
        "International Recruitment/Admissions/Office, Global Engagement/Partnerships/Relations, Mobility/Exchange, Study Abroad, "
        "or top leadership (Chancellor/President/Rector/Vice-Chancellor/Provost). "
        "Return concise role/title or office/unit text, full personal name when present, email when present, the page_url, "
        "and an evidence_type label. "
        "If a relevant person is clearly named but no email is shown, include them with an empty email. "
        "Use evidence_type='person_without_email' for named people without email. "
        "Use evidence_type='office_contact' for office or team inboxes. "
        "Use evidence_type='named_contact' for a named person paired with an email that is explicitly shown on the page. "
        "If the page is not in English, normalize the role/unit into concise English where helpful for downstream filtering. "
        f"{generic_rule} "
        "Do not invent names or emails, and leave name empty rather than guessing from labels. "
        "Prefer an empty name over copying nearby addresses, offices, or building labels."
        f"\nSource: {page}\n---\n{snippet}"
    )
    need = tokens_of(prompt) + 600
    await reserve_for_model(config.MODEL_HEAVY, need)

    schema_contacts = {
        "name": "contacts_extraction",
        "schema": {
            "type": "object",
            "properties": {
                "contacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "page_url": {"type": "string"},
                            "evidence_type": {
                                "type": "string",
                                "enum": ["named_contact", "office_contact", "person_without_email"],
                            },
                        },
                        "required": ["page_url", "evidence_type"],
                        "anyOf": [
                            {"required": ["email"]},
                            {"required": ["name"]},
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["contacts"],
            "additionalProperties": False,
        },
    }

    try:
        resp = await _chat_with_retries(
            model=config.MODEL_HEAVY,
            messages=[
                {"role": "system", "content": "Output JSON strictly matching the provided schema."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": schema_contacts},
        )
        parsed = json.loads(resp.choices[0].message.content)
        contacts = parsed.get("contacts", [])
        if isinstance(contacts, list):
            set_json(cache_key, contacts)
            return contacts
    except Exception:
        pass

    # fallback
    try:
        resp2 = await _chat_with_retries(
            model=config.MODEL_HEAVY,
            messages=[
                {"role": "system", "content": "Return a JSON object with a 'contacts' array."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        parsed2 = json.loads(resp2.choices[0].message.content)
        contacts2 = parsed2.get("contacts", [])
        if isinstance(contacts2, list):
            set_json(cache_key, contacts2)
            return contacts2
    except Exception:
        pass

    return []


async def gpt_clean_name(name: str, role: str, email: str, page_url: str) -> str:
    """
    Use GPT to validate and clean a name field.
    Returns a cleaned personal name or empty string if not a person.
    """
    if not (name or "").strip():
        return ""

    base = (
        "Decide if the provided 'name' refers to a single real person. "
        "If yes, return ONLY the cleaned full name (Latin letters plus accents, spaces, hyphens, apostrophes). "
        "If not (e.g., it's a team, department, generic inbox, or contains a role/title), return an empty string."
    )
    user = {
        "name": name or "",
        "role": role or "",
        "email": email or "",
        "page_url": page_url or "",
        "rules": [
            "No roles/titles like Director, Admissions, Team, Office, Dept.",
            "No email localparts or domain fragments.",
            "Allow transliterated names and particles (de, van, bin, al-, O')."
        ]
    }
    prompt = base + "\nJSON input:\n" + json.dumps(user, ensure_ascii=False)
    cache_payload = json.dumps(user, ensure_ascii=False, sort_keys=True)
    cache_key = f"gpt_clean_name_v1:{hashlib.sha1(cache_payload.encode('utf-8')).hexdigest()}"
    cached = get_json(cache_key)
    if isinstance(cached, str):
        return cached
    schema = {
        "name": "name_cleaner",
        "schema": {
            "type": "object",
            "properties": {"clean_name": {"type": "string"}},
            "required": ["clean_name"],
            "additionalProperties": False,
        }
    }
    need = tokens_of(prompt) + 100
    await reserve_for_model(config.MODEL_LIGHT, need)
    try:
        resp = await _chat_with_retries(
            model=config.MODEL_LIGHT,
            messages=[
                {"role": "system", "content": "Return JSON with a single field 'clean_name' only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        obj = json.loads(resp.choices[0].message.content)
        clean = (obj.get("clean_name") or "").strip()
        clean = re.sub(r"\s+", " ", clean)
        from gc_contacts.core.filtering import looks_like_person_name
        if looks_like_person_name(clean):
            set_json(cache_key, clean)
            return clean
        set_json(cache_key, "")
        return ""
    except Exception as e:
        LOG.debug("gpt_clean_name error: %s", e)
        return ""


# ───────── AI CRAWLER LINK DECISION ─────────

_CRAWLER_SCHEMA = {
    "name": "crawler_decision",
    "schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["visit_url", "stop"],
            },
            "url":  {"type": "string"},
            "why":  {"type": "string"},
        },
        "required": ["action", "why"],
        "additionalProperties": False,
    },
}

_CRAWLER_SYSTEM = (
    "You are an intelligent web crawler agent looking for named staff contacts "
    "involved in international partnerships, mobility, or global engagement at "
    "universities and organisations. You choose the single most promising "
    "unvisited link per turn, or stop when you have enough contacts."
)


async def gpt_choose_next_link(
    page_text: str,
    available_links: List[Dict],
    contacts_found: int,
    contacts_summary: List[Dict],
    turn: int,
    max_turns: int,
) -> Dict:
    """
    AI crawler decision: pick the best unvisited link to follow, or stop.

    Args:
        page_text:         Plain text of current page (will be truncated).
        available_links:   [{url, text}, ...] — unvisited, same-domain links,
                           pre-sorted by heuristic score (top 20 max).
        contacts_found:    Number of qualifying contacts accumulated so far.
        contacts_summary:  Brief sample of contacts found [{role, email}, ...].
        turn:              Current turn number (0-based).
        max_turns:         Hard budget.

    Returns:
        dict with keys: action ("visit_url"|"stop"), url (if visiting), why.
    """
    link_list = "\n".join(
        f"  [{i + 1}] {lnk['url']}  ({lnk.get('text', '').strip()[:60]})"
        for i, lnk in enumerate(available_links[:20])
    )
    if contacts_summary:
        sample = "; ".join(
            f"{c.get('role', '?')} <{c.get('email', '?')}>"
            for c in contacts_summary[:3]
        )
        found_str = f"{contacts_found} contacts so far ({sample})"
    else:
        found_str = "0 contacts found yet"

    prompt = (
        f"Turn {turn + 1}/{max_turns}. {found_str}.\n"
        f"Current page preview:\n{page_text[:500].replace(chr(10), ' ')}\n\n"
        f"Unvisited links to choose from:\n{link_list}\n\n"
        "Pick the single most promising link to visit next, or output stop if "
        "you already have 5+ contacts or none of the links look relevant."
    )

    need = tokens_of(prompt) + 150
    await reserve_for_model(config.MODEL_LIGHT, need)

    try:
        resp = await _chat_with_retries(
            model=config.MODEL_LIGHT,
            messages=[
                {"role": "system", "content": _CRAWLER_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": _CRAWLER_SCHEMA},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        LOG.debug("gpt_choose_next_link error: %s", e)
        # Fallback: visit the top heuristic-scored link rather than crashing
        return {
            "action": "visit_url",
            "url":    available_links[0]["url"],
            "why":    "fallback – LLM unavailable",
        }

async def gpt_rank_candidate_pages(
    homepage_text: str,
    base_url: str,
    org_type: str,
    candidates: List[Dict],
    expected_roles: List[str],
    max_pages: int = 5,
) -> Dict:
    """
    Rank already-discovered candidate pages for the NAFSA agent.

    This is intentionally bounded:
    - it can only rank the candidates we already discovered
    - it cannot invent arbitrary browsing actions
    - it returns a small structured plan for the scout phase
    """
    trimmed_candidates = []
    for c in candidates[:20]:
        trimmed_candidates.append(
            {
                "url": c.get("url", ""),
                "source_type": c.get("source_type", ""),
                "anchor_text": c.get("anchor_text", ""),
                "heuristic_score": c.get("heuristic_score", 0),
            }
        )

    prompt = (
        "You are ranking already-discovered website pages for a bounded outreach crawler.\n"
        f"Organisation type: {org_type}\n"
        f"Base URL: {base_url}\n"
        f"Target roles: {', '.join(expected_roles[:8])}\n\n"
        "Goal:\n"
        "Select the most promising pages for finding outreach-relevant contacts.\n"
        "Prioritise pages likely to contain staff details, team contacts, office contacts, "
        "or leadership relevant to:\n"
        "- international partnerships\n"
        "- global engagement\n"
        "- international relations\n"
        "- mobility / exchange\n"
        "- study abroad\n"
        "- for companies: partnerships / business development / higher education solutions\n\n"
        "Rules:\n"
        "- Only choose from the provided candidate URLs.\n"
        "- Prefer team pages, office pages, staff directories, partnership pages, global engagement pages.\n"
        "- Avoid weak generic pages unless stronger options are missing.\n"
        "- Do not invent new URLs.\n"
        "- Return at most the requested number of ranked pages.\n\n"
        f"Homepage preview:\n{homepage_text[:2500]}\n\n"
        f"Candidate pages:\n{json.dumps(trimmed_candidates, ensure_ascii=False)}\n\n"
        f"Return at most {max_pages} ranked pages."
    )

    schema = {
        "name": "candidate_page_ranking",
        "schema": {
            "type": "object",
            "properties": {
                "strategy": {"type": "string"},
                "expected_roles": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "ranked_pages": {
                    "type": "array",
                    "maxItems": max_pages,
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "reason": {"type": "string"},
                            "expected_yield": {"type": "number"},
                            "page_type": {"type": "string"},
                        },
                        "required": ["url", "reason", "expected_yield", "page_type"],
                        "additionalProperties": False,
                    },
                },
                "stop_hint": {"type": "string"},
            },
            "required": ["strategy", "expected_roles", "ranked_pages", "stop_hint"],
            "additionalProperties": False,
        },
    }

    need = tokens_of(prompt) + 350
    await reserve_for_model(config.MODEL_LIGHT, need)

    try:
        resp = await _chat_with_retries(
            model=config.MODEL_LIGHT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a bounded page-ranking planner for a contact crawler. "
                        "You must only rank provided URLs and output strict JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        parsed = json.loads(resp.choices[0].message.content)
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        LOG.debug("gpt_rank_candidate_pages error: %s", e)

    # Fallback: just return top heuristic candidates
    fallback_ranked = []
    for c in trimmed_candidates[:max_pages]:
        fallback_ranked.append(
            {
                "url": c.get("url", ""),
                "reason": f"heuristic_fallback:{c.get('source_type', 'unknown')}",
                "expected_yield": float(c.get("heuristic_score", 0.0)),
                "page_type": c.get("source_type", "unknown"),
            }
        )

    return {
        "strategy": "heuristic_fallback",
        "expected_roles": expected_roles,
        "ranked_pages": fallback_ranked,
        "stop_hint": "Stop early if enough strong contacts are found.",
    }

# ───────── TOKEN BUCKET FOR RATE LIMITING ─────────
async def reserve_for_model(model: Optional[str], need: int):
    """Reserve tokens from the model-specific GPT token bucket."""
    lock, used_attr, reset_attr, bucket_size = _bucket_settings_for_model(model)
    while True:
        async with lock:
            now = time.monotonic()
            bucket_reset = getattr(config, reset_attr)
            bucket_used = getattr(config, used_attr)
            if now - bucket_reset >= config.TOK_REFRESH:
                bucket_used, bucket_reset = 0, now
                setattr(config, used_attr, bucket_used)
                setattr(config, reset_attr, bucket_reset)
            if bucket_used + need <= bucket_size:
                setattr(config, used_attr, bucket_used + need)
                return
            sleep = max(0.1, config.TOK_REFRESH - (now - bucket_reset))
        await asyncio.sleep(sleep)


async def reserve(need: int):
    """Backward-compatible light-model token reservation helper."""
    await reserve_for_model(config.MODEL_LIGHT, need)
