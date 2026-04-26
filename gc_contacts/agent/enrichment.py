from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict
from typing import Any, Iterable, Optional
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup, FeatureNotFound

from gc_contacts.core.discovery import gather_candidates
from gc_contacts.core.extraction import (
    EMAIL_RE,
    _best_name_from_lines,
    _line_windows,
    clean_contact_name,
    clean_context_text,
    decode_js_emails,
    deobfuscate,
)
from gc_contacts.core.filtering import _cleanup_flags, _name_has_suspicious_signals, looks_like_person_name
from gc_contacts.core.http_client import bs_text, fetch_page, get_with_retry, normalize_url
from gc_contacts.core.utils import home_domain_of
from gc_contacts.localisation import get_country_contact_pack


GENERAL_SEARCH_QUERY_PATTERNS = [
    "/search?q={query}",
    "/search/?q={query}",
    "/search?query={query}",
    "/search/?query={query}",
]

DIRECTORY_SEARCH_QUERY_PATTERNS = [
    "/people/search?q={query}",
    "/directory/search?q={query}",
    "/staff/search?q={query}",
]

SEARCH_QUERY_PATTERNS = GENERAL_SEARCH_QUERY_PATTERNS + DIRECTORY_SEARCH_QUERY_PATTERNS

_ENRICHMENT_BLOCK_FLAGS = {
    "address_like_name",
    "duplicate_candidate",
    "office_label_name",
}
_ROLE_HOLDER_CANDIDATE_TYPES = {"office_contact"}
_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['`.-][^\W\d_]+)*", re.UNICODE)
_DEFAULT_ENRICHMENT_ACTION_BUDGET = 4
_PLANNER_OPEN_STATUSES = {"pending", "site_exhausted", "web_exhausted", "pattern_pending"}
_DIRECTORY_ACTIONS = {"query_directory_by_name"}
_SITE_ACTION_PRIORITIES = {
    "search_governance_pages": 4,
    "search_international_pages": 3,
    "search_people_pages": 2,
    "query_directory_by_name": 1,
}
_SITE_ACTION_FAMILIES = {
    "search_governance_pages": {"governance", "office", "international"},
    "search_international_pages": {"international", "office", "contact"},
    "search_people_pages": {"directory", "staff", "office", "international", "governance"},
    "query_directory_by_name": {"directory", "staff", "office"},
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _contact_locale(country: str | None = None) -> dict[str, Any]:
    return get_country_contact_pack(country)


def _locale_terms(locale_pack: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(
        str(item or "").strip().lower()
        for item in locale_pack.get(key, [])
        if str(item or "").strip()
    )


def _locale_term_set(locale_pack: dict[str, Any], key: str) -> set[str]:
    return set(_locale_terms(locale_pack, key))


def _candidate_country(candidate: dict[str, Any]) -> str | None:
    return _norm(candidate.get("country")) or None


def _candidate_kind(candidate: dict[str, Any]) -> str:
    return _norm(candidate.get("candidate_kind")) or "person_name"


def _candidate_status(candidate: dict[str, Any]) -> str:
    return _norm(candidate.get("status")) or "pending"


def _candidate_attempts(candidate: dict[str, Any]) -> dict[str, int]:
    attempts = candidate.setdefault("attempts", {})
    defaults = {
        "governance": 0,
        "international": 0,
        "people": 0,
        "directory": 0,
        "web_search": 0,
        "pattern": 0,
    }
    for key, value in defaults.items():
        attempts[key] = int(attempts.get(key, value) or 0)
    return attempts


def _candidate_action_budget_remaining(candidate: dict[str, Any]) -> int:
    return int(candidate.get("action_budget_remaining", _DEFAULT_ENRICHMENT_ACTION_BUDGET) or 0)


def _candidate_label(candidate: dict[str, Any]) -> str:
    return _norm(candidate.get("name")) or _norm(candidate.get("role")) or _norm(candidate.get("office_email") or candidate.get("email"))


def _candidate_name_confidence(candidate: dict[str, Any]) -> float:
    return float(candidate.get("name_confidence", 0.0) or 0.0)


def _candidate_directory_ready(candidate: dict[str, Any]) -> bool:
    name = _norm(candidate.get("name"))
    if not name:
        return False
    if bool(candidate.get("directory_ready")):
        return True
    if not looks_like_person_name(name, country=_candidate_country(candidate)):
        return False
    return _candidate_name_confidence(candidate) >= 0.55


def _name_tokens(name: str) -> list[str]:
    return [tok.lower() for tok in _WORD_TOKEN_RE.findall(_norm(name)) if tok]


def _matchable_name_tokens(name: str, country: str | None = None) -> list[str]:
    locale_pack = _contact_locale(country)
    ignored = _locale_term_set(locale_pack, "person_name_titles") | _locale_term_set(locale_pack, "name_particles")
    tokens = _name_tokens(name)
    filtered = [tok for tok in tokens if tok not in ignored]
    return filtered if len(filtered) >= 2 else tokens


def _looks_like_enrichable_person_name(name: str, country: str | None = None) -> bool:
    locale_pack = _contact_locale(country)
    lowered = _norm(name).lower()
    if not lowered:
        return False
    if any(phrase in lowered for phrase in _locale_terms(locale_pack, "enrichment_block_phrases")):
        return False

    tokens = _matchable_name_tokens(name, country=country)
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    if any(token in _locale_term_set(locale_pack, "enrichment_block_terms") for token in tokens):
        return False
    return True


def _page_mentions_name(text: str, name: str, country: str | None = None) -> bool:
    text_l = text.lower()
    tokens = _matchable_name_tokens(name, country=country)
    if not tokens:
        return False
    full = " ".join(tokens)
    if full and full in text_l:
        return True
    return sum(1 for tok in tokens if tok in text_l) >= max(2, len(tokens) - 1)


def _role_mentions_context(text: str, role: str) -> bool:
    if not role:
        return False
    text_l = text.lower()
    role_tokens = [tok for tok in _name_tokens(role) if len(tok) > 3]
    return any(tok in text_l for tok in role_tokens[:4])


def _contains_any_term(text: str, terms: Iterable[str]) -> bool:
    haystack = text.lower()
    return any(term and str(term).lower() in haystack for term in terms)


def _role_holder_haystack(contact: dict[str, Any], country: str | None = None) -> str:
    email_local = _norm(contact.get("email")).lower().split("@", 1)[0]
    email_hint = re.sub(r"[^a-z0-9]+", " ", email_local)
    locale_pack = _contact_locale(country)
    parts = [
        _norm(contact.get("role")),
        _norm(contact.get("raw_role")),
        _norm(contact.get("context")),
        _norm(contact.get("page_context")),
        email_hint,
    ]
    haystack = " ".join(part for part in parts if part).lower()
    for mailbox_term in _locale_terms(locale_pack, "relevant_mailbox_terms"):
        if mailbox_term and mailbox_term in email_local:
            haystack += f" {mailbox_term}"
    return haystack.strip()


def _role_holder_search_terms(candidate: dict[str, Any], country: str | None = None) -> list[str]:
    explicit = [str(term or "").strip().lower() for term in candidate.get("role_search_terms", []) if str(term or "").strip()]
    if explicit:
        return list(dict.fromkeys(explicit))

    locale_pack = _contact_locale(country)
    haystack = _role_holder_haystack(candidate, country=country)
    terms: list[str] = []
    for key in ("senior_role_terms", "international_leadership_terms", "relevant_office_role_terms", "role_signal_terms"):
        for term in _locale_terms(locale_pack, key):
            if term and term in haystack:
                terms.append(term)

    email_local = _norm(candidate.get("email")).lower().split("@", 1)[0]
    for token in re.split(r"[^a-z0-9]+", email_local):
        if len(token) >= 4:
            terms.append(token)

    role_text = _norm(candidate.get("role")) or _norm(candidate.get("raw_role"))
    if role_text:
        terms.append(role_text.lower())

    return list(dict.fromkeys(term for term in terms if len(term) >= 3))


def _has_role_holder_signal(contact: dict[str, Any], country: str | None = None) -> bool:
    locale_pack = _contact_locale(country)
    haystack = _role_holder_haystack(contact, country=country)
    senior = _contains_any_term(haystack, _locale_terms(locale_pack, "senior_role_terms"))
    leadership = senior or _contains_any_term(haystack, _locale_terms(locale_pack, "international_leadership_terms"))
    international_scope = (
        _contains_any_term(haystack, _locale_terms(locale_pack, "relevant_office_role_terms"))
        or _contains_any_term(haystack, _locale_terms(locale_pack, "role_signal_terms"))
        or _contains_any_term(haystack, _locale_terms(locale_pack, "international_markers"))
        or _contains_any_term(haystack, _locale_terms(locale_pack, "relevant_mailbox_terms"))
    )
    return senior or (leadership and international_scope)


def plan_next_enrichment_action(candidate: dict[str, Any]) -> str | None:
    status = _candidate_status(candidate)
    if status not in _PLANNER_OPEN_STATUSES:
        return None

    if _candidate_action_budget_remaining(candidate) <= 0:
        candidate["status"] = "exhausted"
        candidate["blocked_reason"] = "action_budget_exhausted"
        candidate["next_action"] = None
        return None

    attempts = _candidate_attempts(candidate)
    kind = _candidate_kind(candidate)
    country = _candidate_country(candidate)
    name_ready = _candidate_directory_ready(candidate)
    search_terms = _role_holder_search_terms(candidate, country=country)
    international_signal = any(
        term and any(marker in term for marker in ("international", "internationale", "internationales", "erasmus", "mobilite", "mobilité", "partenariat", "relations"))
        for term in search_terms
    )

    next_action: str | None = None
    if kind == "role_holder" and not name_ready:
        if attempts["governance"] == 0:
            next_action = "search_governance_pages"
        elif attempts["international"] == 0 and (international_signal or bool(search_terms)):
            next_action = "search_international_pages"
        elif attempts["web_search"] == 0:
            next_action = "search_web_for_person"
    else:
        if attempts["people"] == 0:
            next_action = "search_people_pages"
        elif name_ready and attempts["directory"] == 0:
            next_action = "query_directory_by_name"
        elif attempts["web_search"] == 0:
            next_action = "search_web_for_person"

    if not next_action and name_ready and attempts["pattern"] == 0:
        next_action = "infer_email_pattern"

    if not next_action:
        candidate["status"] = "exhausted"
        candidate["blocked_reason"] = candidate.get("blocked_reason") or "no_enrichment_actions_remaining"
        candidate["next_action"] = None
        return None

    candidate["next_action"] = next_action
    if kind == "role_holder" and not name_ready:
        candidate["goal"] = "resolve_person_from_role"
    else:
        candidate["goal"] = "resolve_email"
    return next_action


def _extract_candidate_emails(text: str, html: str) -> list[str]:
    emails = {m.group(0).lower() for m in EMAIL_RE.finditer(text)}
    emails.update(decode_js_emails(html))
    emails.update(deobfuscate(text))
    return sorted(emails)


def _looks_like_candidate_match(text: str, candidate: dict[str, Any], target_name: str) -> bool:
    country = _candidate_country(candidate)
    text_l = text.lower()
    target_ok = target_name.lower() in text_l or any(tok in text_l for tok in _name_tokens(target_name)[:2])
    if _candidate_kind(candidate) == "role_holder":
        search_terms = _role_holder_search_terms(candidate, country=country)
        return target_ok and _contains_any_term(text_l, search_terms)

    name = _norm(candidate.get("name"))
    role = _norm(candidate.get("role"))
    if not name:
        return False
    return _page_mentions_name(text, name, country=country) and (target_ok or _role_mentions_context(text, role))


def _extract_role_holder_names(text: str, candidate: dict[str, Any]) -> list[str]:
    country = _candidate_country(candidate)
    search_terms = _role_holder_search_terms(candidate, country=country)
    if not search_terms:
        return []

    lines = _line_windows(text)
    names: list[str] = []
    seen: set[str] = set()
    for idx, (_, _, line) in enumerate(lines):
        line_l = line.lower()
        if not _contains_any_term(line_l, search_terms):
            continue
        nearby = [item[2] for item in lines[max(0, idx - 2): min(len(lines), idx + 3)]]
        candidate_name = _best_name_from_lines(nearby, country=country)
        if not candidate_name:
            for snippet in nearby:
                extracted = clean_contact_name(snippet, country=country)
                if looks_like_person_name(extracted, country=country):
                    candidate_name = extracted
                    break
        if not candidate_name or not looks_like_person_name(candidate_name, country=country):
            continue
        key = candidate_name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(candidate_name)
        if len(names) >= 4:
            break
    return names


def _score_email_match(email: str, candidate: dict[str, Any], home_domain: str) -> int:
    name = _norm(candidate.get("name"))
    role = _norm(candidate.get("role")).lower()
    local, _, domain = email.lower().partition("@")
    tokens = _matchable_name_tokens(name, country=_candidate_country(candidate))
    if not local or not domain or len(tokens) < 2:
        return 0

    first, last = tokens[0], tokens[-1]
    initials = f"{first[:1]}{last[:1]}"
    name_score = 0
    if first in local:
        name_score += 2
    if last in local:
        name_score += 2
    if initials and initials in local:
        name_score += 1
    if f"{first}.{last}" in local or f"{first}_{last}" in local or f"{first}-{last}" in local:
        name_score += 2
    if f"{first[:1]}.{last}" in local or f"{first[:1]}{last}" in local:
        name_score += 2
    if not name_score:
        return 0

    score = name_score
    if domain == home_domain or domain.endswith("." + home_domain) or home_domain.endswith("." + domain):
        score += 2
    if any(term in role for term in ("international", "global", "partnership", "exchange", "mobility", "erasmus")):
        score += 1
    return score


def build_person_candidate(
    contact: dict[str, Any],
    page_url: str,
    page_text: str,
    expected_yield: float = 0.0,
    country: str | None = None,
) -> Optional[dict[str, Any]]:
    name = _norm(contact.get("name"))
    role = _norm(contact.get("role"))
    cleanup_flags = _cleanup_flags(contact)
    if cleanup_flags & _ENRICHMENT_BLOCK_FLAGS:
        return None
    if _name_has_suspicious_signals(name, country=country):
        return None
    if not looks_like_person_name(name, country=country):
        return None
    if not _looks_like_enrichable_person_name(name, country=country):
        return None
    page_context = clean_context_text(_norm(contact.get("context")) or page_text, country=country, max_lines=8, max_chars=500)
    return {
        "name": name,
        "role": role,
        "country": country,
        "page_url": contact.get("page_url") or page_url,
        "page_context": page_context,
        "source_strategies": list(contact.get("source_strategies", [])),
        "candidate_type": str(contact.get("candidate_type", "person_without_email")),
        "candidate_kind": "person_name",
        "cleanup_flags": list(contact.get("cleanup_flags", [])),
        "raw_name": _norm(contact.get("raw_name")),
        "raw_role": _norm(contact.get("raw_role")),
        "clean_name": _norm(contact.get("clean_name") or name),
        "status": "pending",
        "goal": "resolve_email",
        "next_action": "search_people_pages",
        "directory_ready": True,
        "name_confidence": 0.8,
        "email_confidence": 0.0,
        "evidence_strength": 0.0,
        "blocked_reason": "",
        "action_budget_remaining": _DEFAULT_ENRICHMENT_ACTION_BUDGET,
        "attempts": {
            "governance": 0,
            "international": 0,
            "people": 0,
            "directory": 0,
            "web_search": 0,
            "pattern": 0,
        },
        "best_evidence": None,
        "evidence_items": [],
        "expected_yield": expected_yield,
    }


def build_role_holder_candidate(
    contact: dict[str, Any],
    page_url: str,
    page_text: str,
    expected_yield: float = 0.0,
    country: str | None = None,
) -> Optional[dict[str, Any]]:
    if _norm(contact.get("name")):
        return None
    if _norm(contact.get("candidate_type")) not in _ROLE_HOLDER_CANDIDATE_TYPES:
        return None

    email = _norm(contact.get("email")).lower()
    role = _norm(contact.get("role")) or _norm(contact.get("raw_role"))
    if not email or "@" not in email or not role:
        return None
    if not _has_role_holder_signal(contact, country=country):
        return None

    role_search_terms = _role_holder_search_terms(contact, country=country)
    if not role_search_terms:
        return None
    page_context = clean_context_text(_norm(contact.get("context")) or page_text, country=country, max_lines=8, max_chars=500)

    return {
        "name": "",
        "role": role,
        "country": country,
        "email": email,
        "office_email": email,
        "page_url": contact.get("page_url") or page_url,
        "page_context": page_context,
        "source_strategies": list(contact.get("source_strategies", [])),
        "candidate_type": "person_without_email",
        "candidate_kind": "role_holder",
        "cleanup_flags": list(contact.get("cleanup_flags", [])),
        "raw_name": "",
        "raw_role": _norm(contact.get("raw_role") or role),
        "clean_name": "",
        "role_search_terms": role_search_terms,
        "status": "pending",
        "goal": "resolve_person_from_role",
        "next_action": "search_governance_pages",
        "directory_ready": False,
        "name_confidence": 0.0,
        "email_confidence": 0.0,
        "evidence_strength": 0.0,
        "blocked_reason": "",
        "action_budget_remaining": _DEFAULT_ENRICHMENT_ACTION_BUDGET,
        "attempts": {
            "governance": 0,
            "international": 0,
            "people": 0,
            "directory": 0,
            "web_search": 0,
            "pattern": 0,
        },
        "best_evidence": None,
        "evidence_items": [],
        "expected_yield": expected_yield + 1.5,
    }


def _candidate_query_text(candidate: dict[str, Any], target_name: str) -> str:
    candidate_kind = _candidate_kind(candidate)
    parts: list[str] = []
    if candidate_kind == "role_holder":
        parts.extend(_role_holder_search_terms(candidate, country=_candidate_country(candidate))[:3])
        if _norm(candidate.get("role")):
            parts.append(_norm(candidate.get("role")))
        office_email = _norm(candidate.get("office_email") or candidate.get("email")).lower().split("@", 1)[0]
        if office_email:
            parts.append(re.sub(r"[^a-z0-9]+", " ", office_email))
    else:
        parts.append(_norm(candidate.get("name")))
        parts.append(_norm(candidate.get("role")))
    parts.append(_norm(target_name))
    return " ".join(part for part in dict.fromkeys(part for part in parts if part) if part).strip()


async def _candidate_pages_from_site(
    target: Any,
    profile: Any,
    candidate: dict[str, Any],
    *,
    action: str,
) -> list[dict[str, Any]]:
    target_url = target.url
    home_domain = home_domain_of(target_url)
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    locale_pack = _contact_locale(_candidate_country(candidate))
    preferred_page_terms = _locale_terms(locale_pack, "role_holder_page_terms")
    directory_ready = _candidate_directory_ready(candidate)
    allowed_families = _SITE_ACTION_FAMILIES.get(action, {"governance", "international", "office", "directory", "staff", "contact"})
    allow_directory_pages = action in {"search_people_pages", "query_directory_by_name"} and directory_ready
    allow_search_endpoints = action in {"search_governance_pages", "search_international_pages", "search_people_pages", "query_directory_by_name"}

    discovered, _, _, _ = await gather_candidates(
        target_url,
        extra_slugs=getattr(profile, "slug_hints", []),
        country=getattr(target, "country", None),
        mode="generated_slug_only",
        target_name=getattr(target, "name", None),
    )
    for item in discovered:
        url = normalize_url(item.get("url", ""))
        if not url or url in seen:
            continue
        if home_domain_of(url) != home_domain:
            continue
        family = _norm(item.get("page_family"))
        if family and family not in allowed_families:
            continue
        if family in {"directory", "staff"} and not allow_directory_pages:
            continue
        seen.add(url)
        found.append(
            {
                "url": url,
                "source": "discovery_candidate",
                "page_family": family,
                "heuristic_score": float(item.get("heuristic_score", 0.0) or 0.0),
            }
        )
        if len(found) >= 18:
            break

    query = quote_plus(_candidate_query_text(candidate, target.name))
    query_patterns = []
    if allow_search_endpoints:
        if action == "query_directory_by_name":
            query_patterns.extend(DIRECTORY_SEARCH_QUERY_PATTERNS)
        elif action == "search_people_pages":
            query_patterns.extend(DIRECTORY_SEARCH_QUERY_PATTERNS)
            query_patterns.extend(GENERAL_SEARCH_QUERY_PATTERNS[:2])
        else:
            query_patterns.extend(GENERAL_SEARCH_QUERY_PATTERNS)
    for pattern in query_patterns:
        url = normalize_url(urljoin(target_url, pattern.format(query=query)))
        if not url or url in seen:
            continue
        seen.add(url)
        found.append({"url": url, "source": "site_search_endpoint", "page_family": "search", "heuristic_score": 0.0})
        if len(found) >= 24:
            break

    def _page_priority(item: dict[str, Any]) -> tuple[float, int, str]:
        url_l = str(item.get("url", "")).lower()
        family = _norm(item.get("page_family"))
        preferred = 1 if family in {"governance", "directory", "international", "office"} else 0
        if preferred_page_terms and any(term in url_l for term in preferred_page_terms):
            preferred += 1
        if _candidate_kind(candidate) == "role_holder" and any(term in url_l for term in _role_holder_search_terms(candidate, country=_candidate_country(candidate))):
            preferred += 1
        preferred += _SITE_ACTION_PRIORITIES.get(action, 0)
        if action == "query_directory_by_name" and family in {"directory", "staff", "search"}:
            preferred += 4
        if action == "search_people_pages" and family in {"directory", "staff"}:
            preferred += 3
        if action == "search_governance_pages" and family == "governance":
            preferred += 4
        if action == "search_international_pages" and family == "international":
            preferred += 4
        return (-preferred, -float(item.get("heuristic_score", 0.0) or 0.0), str(item.get("url", "")))

    found.sort(key=_page_priority)
    return found[:12]


async def inspect_evidence_page(url: str, candidate: dict[str, Any], target_name: str, home_domain: str) -> list[dict[str, Any]]:
    html = await fetch_page(url)
    if not html:
        return []
    text = bs_text(html)
    if not _looks_like_candidate_match(text, candidate, target_name):
        return []

    if _candidate_kind(candidate) == "role_holder":
        evidences: list[dict[str, Any]] = []
        names = _extract_role_holder_names(text, candidate)
        for name in names:
            name_candidate = dict(candidate)
            name_candidate["name"] = name
            matched_email = False
            for email in _extract_candidate_emails(text, html):
                score = _score_email_match(email, name_candidate, home_domain)
                if score < 5:
                    continue
                matched_email = True
                evidence_type = "same_domain_page" if home_domain in email.lower() else "related_page"
                evidences.append(
                    {
                        "name": name,
                        "email": email,
                        "evidence_url": url,
                        "evidence_type": evidence_type,
                        "score": score,
                        "confidence": "high" if score >= 7 else "medium",
                        "recovery_reason": "named role-holder with direct email evidence",
                    }
                )
            if not matched_email:
                evidences.append(
                    {
                        "name": name,
                        "evidence_url": url,
                        "evidence_type": "role_holder_page",
                        "score": 4,
                        "confidence": "medium",
                        "recovery_reason": "named role-holder on related page",
                    }
                )
        return evidences

    evidences: list[dict[str, Any]] = []
    for email in _extract_candidate_emails(text, html):
        score = _score_email_match(email, candidate, home_domain)
        if score < 5:
            continue
        evidence_type = "same_domain_page" if home_domain in email.lower() else "related_page"
        evidences.append(
            {
                "email": email,
                "evidence_url": url,
                "evidence_type": evidence_type,
                "score": score,
                "confidence": "high" if score >= 7 else "medium",
                "recovery_reason": "direct email evidence on related page",
            }
        )
    return evidences


async def search_site_for_person(target: Any, profile: Any, candidate: dict[str, Any], *, action: str) -> dict[str, Any]:
    home_domain = home_domain_of(target.url)
    checked_urls: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    for page in await _candidate_pages_from_site(target, profile, candidate, action=action):
        checked_urls.append(page["url"])
        evidence_items.extend(await inspect_evidence_page(page["url"], candidate, target.name, home_domain))
        if evidence_items:
            break
    return {
        "action": action,
        "checked_urls": checked_urls,
        "evidence_items": evidence_items,
        "resolved": bool(evidence_items),
    }


async def search_web_for_person(target: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    query_text = _candidate_query_text(candidate, target.name)
    query = quote_plus(f'"{query_text}" email')
    search_url = f"https://html.duckduckgo.com/html/?q={query}"
    checked_urls = [search_url]
    evidence_items: list[dict[str, Any]] = []
    home_domain = home_domain_of(target.url)

    response = await get_with_retry(search_url, tries=2)
    if response and response.text:
        html = response.text
        try:
            soup = BeautifulSoup(html, "lxml")
        except FeatureNotFound:
            soup = BeautifulSoup(html, "html.parser")
        result_urls: list[str] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if href.startswith("http"):
                result_urls.append(normalize_url(href))
            if len(result_urls) >= 5:
                break
        for url in result_urls:
            checked_urls.append(url)
            evidence_items.extend(await inspect_evidence_page(url, candidate, target.name, home_domain))
            if evidence_items:
                break

    return {
        "action": "search_web_for_person",
        "checked_urls": checked_urls,
        "evidence_items": evidence_items,
        "resolved": bool(evidence_items),
    }


def _pattern_candidates_for_name(name: str, country: str | None = None) -> dict[str, str]:
    tokens = _matchable_name_tokens(name, country=country)
    if len(tokens) < 2:
        return {}
    first = tokens[0]
    last = tokens[-1]
    return {
        "first.last": f"{first}.{last}",
        "f.last": f"{first[:1]}.{last}",
        "firstlast": f"{first}{last}",
        "first_last": f"{first}_{last}",
        "first-last": f"{first}-{last}",
        "flast": f"{first[:1]}{last}",
        "last.first": f"{last}.{first}",
    }


def infer_email_pattern(candidate: dict[str, Any], known_contacts: Iterable[dict[str, Any]], home_domain: str) -> dict[str, Any]:
    candidate_country = _candidate_country(candidate)
    if not looks_like_person_name(_norm(candidate.get("name")), country=candidate_country):
        return {"resolved": False, "reason": "candidate name unsuitable for inference"}

    pattern_hits: Counter[str] = Counter()
    domain_hits: Counter[str] = Counter()
    for contact in known_contacts:
        email = _norm(contact.get("email")).lower()
        name = _norm(contact.get("name"))
        contact_country = _norm(contact.get("country")) or candidate_country
        if not email or "@" not in email or not looks_like_person_name(name, country=contact_country):
            continue
        local, _, domain = email.partition("@")
        domain_hits[domain] += 1
        patterns = _pattern_candidates_for_name(name, country=contact_country)
        for label, pattern_local in patterns.items():
            if local == pattern_local:
                pattern_hits[label] += 1

    inferred_domain = domain_hits.most_common(1)[0][0] if domain_hits else home_domain
    if not pattern_hits:
        return {"resolved": False, "reason": "no stable email pattern observed"}

    pattern_label, support = pattern_hits.most_common(1)[0]
    local_candidates = _pattern_candidates_for_name(candidate.get("name", ""), country=candidate_country)
    inferred_local = local_candidates.get(pattern_label)
    if not inferred_local:
        return {"resolved": False, "reason": "candidate name unsuitable for inference"}

    confidence = "medium" if support >= 3 else "low"
    if support < 2:
        confidence = "low"

    return {
        "resolved": True,
        "email": f"{inferred_local}@{inferred_domain}",
        "pattern": pattern_label,
        "support": support,
        "confidence": confidence,
        "recovery_reason": f"inferred from {support} same-organisation contacts using {pattern_label}",
        "evidence_type": "email_pattern",
    }


def apply_evidence_to_candidate(candidate: dict[str, Any], evidence_items: list[dict[str, Any]], source: str) -> Optional[dict[str, Any]]:
    if not evidence_items:
        return None
    best = sorted(evidence_items, key=lambda item: (item.get("score", 0), item.get("confidence") == "high"), reverse=True)[0]
    best_name = _norm(best.get("name")) or _norm(candidate.get("name"))
    if best_name and looks_like_person_name(best_name, country=_candidate_country(candidate)):
        candidate["name"] = best_name
        candidate["raw_name"] = candidate.get("raw_name") or best_name
        candidate["clean_name"] = best_name
        candidate["name_confidence"] = max(
            float(candidate.get("name_confidence", 0.0) or 0.0),
            0.85 if best.get("email") else 0.7,
        )
        candidate["directory_ready"] = True
    candidate["best_evidence"] = best
    candidate["evidence_items"].extend(evidence_items)
    candidate["evidence_strength"] = max(float(candidate.get("evidence_strength", 0.0) or 0.0), float(best.get("score", 0.0) or 0.0) / 8.0)
    candidate_country = _candidate_country(candidate)
    if not best.get("email"):
        if best_name and looks_like_person_name(best_name, country=candidate_country):
            candidate["status"] = "pattern_pending"
            candidate["candidate_kind"] = "person_name"
            candidate["candidate_type"] = "person_without_email"
            candidate["goal"] = "resolve_email"
            candidate["next_action"] = "query_directory_by_name"
        return None

    candidate["status"] = "recovered"
    candidate["email_confidence"] = 0.9 if best.get("confidence") == "high" else 0.7
    return {
        "name": candidate.get("name", ""),
        "role": candidate.get("role", ""),
        "country": candidate.get("country"),
        "email": best["email"],
        "page_url": candidate.get("page_url", ""),
        "source_url": best.get("evidence_url") or candidate.get("page_url", ""),
        "evidence_url": best.get("evidence_url") or candidate.get("page_url", ""),
        "evidence_type": best.get("evidence_type", source),
        "email_source": source,
        "confidence": best.get("confidence", "medium"),
        "recovery_reason": best.get("recovery_reason", ""),
        "candidate_status": "recovered_contact",
        "candidate_type": "named_contact"
        if looks_like_person_name(_norm(candidate.get("name")), country=candidate_country)
        else _norm(candidate.get("candidate_type")),
        "source_strategies": list(candidate.get("source_strategies", [])),
        "cleanup_flags": list(candidate.get("cleanup_flags", [])),
        "raw_name": candidate.get("raw_name", candidate.get("name", "")),
        "raw_role": candidate.get("raw_role", candidate.get("role", "")),
        "clean_name": candidate.get("clean_name", candidate.get("name", "")),
        "email_normalized": best["email"],
        "context": candidate.get("page_context", ""),
        "page_context": candidate.get("page_context", ""),
    }
