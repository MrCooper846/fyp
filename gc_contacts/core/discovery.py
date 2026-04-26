"""
Candidate page discovery: explicit strategies, localisation, and multi-hop real links.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from copy import deepcopy
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, FeatureNotFound

import gc_contacts.config as config
from gc_contacts.localisation import get_country_discovery_pack
from gc_contacts.core.http_client import fetch_page, get_with_retry, normalize_url

LOG = logging.getLogger("gc")

DISCOVERY_MODES = {
    "heuristic_only",
    "generated_slug_only",
    "real_link_only",
    "hybrid",
    "benchmark_all",
}

REAL_LINK_STRATEGIES = {"real_link_multihop", "sitemap", "cms", "structured_endpoints"}
HEURISTIC_STRATEGIES = {"heuristic_slugs", "profile_slugs", "family_templates"}

LANG_PREFIX_SEGMENTS = {"en", "it", "fr", "de", "es", "pt", "nl"}
JUNK_PATH_TERMS = {
    "privacy",
    "cookies",
    "cookie",
    "legal",
    "accessibility",
    "alumni",
    "events",
    "event",
    "news",
    "calendar",
    "login",
    "signin",
    "sign-in",
    "account",
}

GENERIC_PAGE_FAMILY_TERMS = {
    "contact": ["contact", "contacts", "contact-us"],
    "directory": ["directory", "people", "staff", "faculty", "person"],
    "staff": ["staff", "people", "team", "faculty"],
    "office": ["office", "offices", "service", "services"],
    "governance": [
        "governance",
        "leadership",
        "management",
        "administration",
        "board",
        "trustees",
        "rector",
        "president",
        "provost",
    ],
    "international": [
        "international",
        "global",
        "partnership",
        "partnerships",
        "mobility",
        "exchange",
        "study-abroad",
        "studyabroad",
        "erasmus",
    ],
    "admissions": ["admissions", "apply", "recruitment", "applicant"],
}

GENERIC_ANCHOR_TERMS = [
    "contacts",
    "directory",
    "people",
    "staff",
    "office",
    "international",
    "global",
    "partnerships",
    "mobility",
    "exchange",
    "governance",
    "leadership",
]

FAMILY_PRIORITY = {
    "contact": 8,
    "directory": 7,
    "staff": 6,
    "office": 6,
    "international": 6,
    "governance": 5,
    "admissions": 2,
    "generic": 1,
}

STRATEGY_PRIORITY = {
    "real_link_multihop": 70,
    "cms": 60,
    "sitemap": 56,
    "structured_endpoints": 54,
    "family_templates": 34,
    "subdomains": 14,
    "profile_slugs": 40,
    "heuristic_slugs": 24,
}

SOURCE_TYPE_BONUS = {
    "nav": 8,
    "header": 7,
    "footer": 6,
    "body": 4,
    "form": 6,
    "search": 5,
    "sitemap": 5,
    "wp": 6,
    "drupal": 6,
    "subdomain": 1,
    "profile_slug": 4,
    "family_template": 3,
    "heuristic": 2,
    "heuristic_us": 2,
}

MODE_TO_COLLECTORS = {
    "heuristic_only": {"real_link_multihop", "heuristic_slugs", "sitemap", "subdomains"},
    "generated_slug_only": {"real_link_multihop", "profile_slugs", "family_templates", "sitemap", "structured_endpoints", "subdomains"},
    "real_link_only": {"real_link_multihop", "sitemap", "cms", "structured_endpoints", "subdomains"},
    "hybrid": {"real_link_multihop", "heuristic_slugs", "profile_slugs", "family_templates", "sitemap", "cms", "structured_endpoints", "subdomains"},
}

SEARCH_FORM_FIELD_HINTS = {
    "annuaire",
    "employee",
    "keyword",
    "keywords",
    "name",
    "nom",
    "people",
    "person",
    "personnel",
    "q",
    "query",
    "recherche",
    "s",
    "search",
    "staff",
}

_DISCOVERY_BUNDLE_CACHE: dict[tuple, tuple[float, dict]] = {}
_DISCOVERY_COLLECTOR_COOLDOWNS: dict[tuple[str, str], float] = {}
_HOMEPAGE_RESCUE_STOPWORDS = {
    "a",
    "and",
    "de",
    "del",
    "della",
    "des",
    "di",
    "du",
    "et",
    "for",
    "la",
    "le",
    "les",
    "of",
    "the",
}


def _clear_expired_discovery_runtime_state() -> None:
    now = time.monotonic()
    cache_ttl = max(0.0, float(getattr(config, "DISCOVERY_CACHE_TTL", 0.0) or 0.0))
    if cache_ttl > 0:
        for key, (stored_at, _) in list(_DISCOVERY_BUNDLE_CACHE.items()):
            if now - stored_at >= cache_ttl:
                _DISCOVERY_BUNDLE_CACHE.pop(key, None)
    for key, until in list(_DISCOVERY_COLLECTOR_COOLDOWNS.items()):
        if until <= now:
            _DISCOVERY_COLLECTOR_COOLDOWNS.pop(key, None)


def clear_discovery_runtime_state() -> None:
    _DISCOVERY_BUNDLE_CACHE.clear()
    _DISCOVERY_COLLECTOR_COOLDOWNS.clear()


async def _run_collector_with_timeout(
    name: str,
    collector,
    *,
    timeout_seconds: float,
    home_url: str,
) -> list[dict]:
    _clear_expired_discovery_runtime_state()
    cooldown_key = _collector_cooldown_key(name, home_url)
    cooldown_until = _DISCOVERY_COLLECTOR_COOLDOWNS.get(cooldown_key, 0.0)
    now = time.monotonic()
    if cooldown_until > now:
        LOG.info(
            "discovery collector %s skipped for %s during cooldown (%.0fs remaining)",
            name,
            home_url,
            max(0.0, cooldown_until - now),
        )
        return []
    try:
        return await asyncio.wait_for(collector, timeout=max(1.0, float(timeout_seconds or 0.0)))
    except asyncio.TimeoutError:
        LOG.warning("discovery collector %s timed out for %s after %.1fs", name, home_url, timeout_seconds)
        cooldown_seconds = max(0.0, float(getattr(config, "DISCOVERY_COLLECTOR_COOLDOWN", 0.0) or 0.0))
        if cooldown_seconds > 0:
            _DISCOVERY_COLLECTOR_COOLDOWNS[cooldown_key] = time.monotonic() + cooldown_seconds
    except Exception as exc:
        LOG.warning("discovery collector %s failed for %s: %s", name, home_url, exc)
    return []


def _country_code(country: Optional[str]) -> str:
    return str(country or "").strip().upper()


def _country_pack(country: Optional[str]) -> dict:
    return get_country_discovery_pack(_country_code(country))


def _merge_unique(base: list[str], extra: list[str]) -> list[str]:
    return list(dict.fromkeys([*(base or []), *(extra or [])]))


def _country_tokens(country: Optional[str]) -> list[str]:
    pack = _country_pack(country)
    return _merge_unique(config.TOKENS, pack.get("tokens", []))


def _country_anchor_terms(country: Optional[str]) -> list[str]:
    pack = _country_pack(country)
    return _merge_unique(GENERIC_ANCHOR_TERMS, pack.get("anchor_terms", []))


def _country_cms_search_terms(country: Optional[str]) -> list[str]:
    pack = _country_pack(country)
    return _merge_unique(config.CMS_SEARCH_TERMS, pack.get("cms_search_terms", []))


def _country_subdomains(country: Optional[str]) -> list[str]:
    pack = _country_pack(country)
    return _merge_unique(config.SUBDOMS, pack.get("subdomains", []))


def _country_slug_hints(country: Optional[str]) -> list[str]:
    return list(_country_pack(country).get("slug_hints", []))


def _country_high_yield_seed_patterns(country: Optional[str]) -> list[str]:
    return list(_country_pack(country).get("high_yield_seed_patterns", []))


def _country_language_prefixes(country: Optional[str]) -> list[str]:
    return _merge_unique(["en"], list(_country_pack(country).get("language_prefixes", [])))


def _country_hreflang_preferences(country: Optional[str]) -> list[str]:
    prefs = [
        str(item or "").strip().lower()
        for item in _country_pack(country).get("hreflang_preferences", [])
        if str(item or "").strip()
    ]
    return prefs or ["en"]


def _country_negative_terms(country: Optional[str]) -> list[str]:
    return list(_country_pack(country).get("negative_terms", []))


def _country_directory_search_terms(country: Optional[str]) -> list[str]:
    families = _page_family_terms(country)
    return _merge_unique(
        ["annuaire", "directory", "people", "personnel", "recherche", "search", "staff", "team", "trombinoscope"],
        _merge_unique(families.get("directory", []), families.get("staff", [])),
    )


def _page_family_terms(country: Optional[str]) -> dict[str, list[str]]:
    pack = _country_pack(country)
    merged = {family: list(terms) for family, terms in GENERIC_PAGE_FAMILY_TERMS.items()}
    for family, terms in pack.get("page_family_terms", {}).items():
        merged[family] = _merge_unique(merged.get(family, []), list(terms))
    merged["directory"] = _merge_unique(merged.get("directory", []), list(pack.get("directory_terms", [])))
    merged["governance"] = _merge_unique(merged.get("governance", []), list(pack.get("governance_terms", [])))
    return merged


def _directory_priority_terms(country: Optional[str]) -> list[str]:
    families = _page_family_terms(country)
    return _merge_unique(
        ["directory", "docenti", "faculty", "people", "personale", "rubrica", "staff", "team"],
        _merge_unique(families.get("directory", []), families.get("staff", [])),
    )


def _service_contact_terms(country: Optional[str]) -> list[str]:
    families = _page_family_terms(country)
    return _merge_unique(
        ["callcentre", "callcenter", "contact", "contacts", "helpdesk", "protocollo", "segreteria", "service", "services", "urp"],
        _merge_unique(families.get("contact", []), families.get("office", [])),
    )


def _candidate_haystack(candidate: dict) -> str:
    return " ".join(
        str(candidate.get(key, "") or "").strip().lower()
        for key in ("url", "anchor_text", "page_family")
        if str(candidate.get(key, "") or "").strip()
    )


def _has_any_term(haystack: str, terms: list[str]) -> bool:
    return any(str(term or "").strip().lower() and str(term or "").strip().lower() in haystack for term in terms)


def _site_root(host: str) -> str:
    parts = str(host or "").lower().split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    if len(parts[-1]) == 2 and parts[-2] in {"ac", "co", "gov", "org", "edu"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _collector_cooldown_scope(home_url: str) -> str:
    parsed = urlparse(home_url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return parsed.netloc.lower()
    return f"{parsed.netloc.lower()}/{_normalize_path_token(segments[0])}"


def _collector_cooldown_key(name: str, home_url: str) -> tuple[str, str]:
    collector = str(name or "").strip().lower()
    if collector in {"sitemap", "cms"}:
        scope = _site_root(urlparse(home_url).netloc)
    else:
        scope = _collector_cooldown_scope(home_url)
    return collector, scope


def _normalise_homepage_url(url: str) -> str:
    parsed = urlparse(normalize_url(url))
    if parsed.path in {"", "/"} and not parsed.params and not parsed.query and not parsed.fragment:
        return urlunparse(parsed._replace(path=""))
    return normalize_url(url)


def _normalized_text_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", ascii_text)


def _significant_name_tokens(name: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-zÀ-ÿ0-9]+", str(name or ""))
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_tokens:
        normalized_parts = _normalized_text_tokens(raw)
        for token in normalized_parts:
            if token in _HOMEPAGE_RESCUE_STOPWORDS:
                continue
            if len(token) >= 4 or (len(token) >= 3 and raw.isupper()):
                if token not in seen:
                    seen.add(token)
                    result.append(token)
    return result


def _homepage_target_overlap_score(url: str, html: str | None, target_name: str | None) -> float:
    tokens = _significant_name_tokens(str(target_name or ""))
    if not tokens:
        return 0.0

    signal_parts = [urlparse(url).netloc, urlparse(url).path]
    if html:
        try:
            soup = BeautifulSoup(html, "lxml")
        except FeatureNotFound:
            soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        headings = " ".join(node.get_text(" ", strip=True) for node in soup.select("h1, h2")[:3])
        signal_parts.extend([title, headings])

    signal_tokens = set(_normalized_text_tokens(" ".join(part for part in signal_parts if part)))
    overlap = [token for token in tokens if token in signal_tokens]
    if not overlap:
        return 0.0

    score = float(len(overlap))
    longest = max((len(token) for token in overlap), default=0)
    if longest >= 7:
        score += 0.5
    path_segments = [segment for segment in urlparse(url).path.split("/") if segment]
    if len(path_segments) <= 1:
        score += 0.5
    return score


def _search_result_urls(search_html: str, *, max_results: int) -> list[str]:
    try:
        soup = BeautifulSoup(search_html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(search_html, "html.parser")
    result_urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = normalize_url(anchor.get("href", ""))
        if not href or href in seen:
            continue
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if _binary_or_asset_url(href):
            continue
        seen.add(href)
        result_urls.append(href)
        if len(result_urls) >= max_results:
            break
    return result_urls


async def _search_web_for_homepage(
    home_url: str,
    *,
    target_name: str | None,
    country: Optional[str],
) -> tuple[str | None, str | None, list[dict[str, str]]]:
    if not bool(getattr(config, "DISCOVERY_WEB_RESCUE_ENABLED", True)):
        return None, None, []
    target_name = str(target_name or "").strip()
    if not target_name:
        return None, None, []

    query_text = f'"{target_name}" official site'
    country_code = _country_code(country)
    if country_code:
        query_text = f"{query_text} {country_code}"
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query_text)}"
    trace: list[dict[str, str]] = [
        {"url": search_url, "reason": "web_search_query", "fetched": "false", "query": query_text}
    ]

    response = await get_with_retry(search_url, tries=2)
    if not response or not response.text:
        return None, None, trace
    trace[0]["fetched"] = "true"

    max_results = max(1, int(getattr(config, "DISCOVERY_WEB_RESCUE_RESULT_LIMIT", 4) or 4))
    original_root = _site_root(urlparse(home_url).netloc)
    for result_url in _search_result_urls(response.text, max_results=max_results):
        html = await fetch_page(result_url)
        score = _homepage_target_overlap_score(result_url, html, target_name)
        if original_root and _site_root(urlparse(result_url).netloc) == original_root:
            score += 1.0
        trace.append(
            {
                "url": result_url,
                "reason": "web_search_candidate",
                "fetched": "true" if bool(html) else "false",
                "query": query_text,
                "score": f"{score:.1f}",
            }
        )
        if html and score >= 1.5:
            return _normalise_homepage_url(result_url), html, trace
    return None, None, trace


def _homepage_rescue_candidates(home_url: str, country: Optional[str]) -> list[tuple[str, str]]:
    parsed = urlparse(normalize_url(home_url))
    if not parsed.netloc:
        return []

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(url: str, reason: str) -> None:
        normalized = normalize_url(url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append((normalized, reason))

    original = normalize_url(home_url)
    root_path = "/"
    host = parsed.netloc
    scheme = parsed.scheme or "https"

    add(original, "original")
    if scheme != "https":
        add(urlunparse(parsed._replace(scheme="https")), "upgrade_https")
    if parsed.path and parsed.path not in {"", "/"}:
        add(urlunparse(parsed._replace(path=root_path, params="", query="", fragment="")), "strip_path_to_root")
        add(urlunparse(parsed._replace(scheme="https", path=root_path, params="", query="", fragment="")), "upgrade_https_strip_path")

    labels = host.split(".")
    if labels and labels[0].lower() == "www":
        host_no_www = ".".join(labels[1:])
        add(urlunparse(parsed._replace(netloc=host_no_www, path=root_path, params="", query="", fragment="")), "drop_www_root")
        add(urlunparse(parsed._replace(scheme="https", netloc=host_no_www, path=root_path, params="", query="", fragment="")), "drop_www_https_root")
    else:
        host_www = f"www.{host}"
        add(urlunparse(parsed._replace(netloc=host_www, path=root_path, params="", query="", fragment="")), "add_www_root")
        add(urlunparse(parsed._replace(scheme="https", netloc=host_www, path=root_path, params="", query="", fragment="")), "add_www_https_root")

    if labels and labels[0].lower() in (set(_country_language_prefixes(country)) | LANG_PREFIX_SEGMENTS):
        host_no_lang = ".".join(labels[1:])
        if host_no_lang:
            add(urlunparse(parsed._replace(netloc=host_no_lang, path=root_path, params="", query="", fragment="")), "drop_lang_subdomain_root")
            add(urlunparse(parsed._replace(scheme="https", netloc=host_no_lang, path=root_path, params="", query="", fragment="")), "drop_lang_subdomain_https_root")

    return candidates


async def _resolve_homepage_entrypoint(
    home_url: str,
    country: Optional[str],
    *,
    target_name: str | None = None,
) -> tuple[str, str | None, list[dict[str, str]]]:
    rescue_trace: list[dict[str, str]] = []
    for candidate_url, reason in _homepage_rescue_candidates(home_url, country):
        html = await fetch_page(candidate_url)
        rescue_trace.append({"url": candidate_url, "reason": reason, "fetched": "true" if bool(html) else "false"})
        if html:
            return _normalise_homepage_url(candidate_url), html, rescue_trace
    search_url, search_html, search_trace = await _search_web_for_homepage(home_url, target_name=target_name, country=country)
    if search_trace:
        rescue_trace.extend(search_trace)
    if search_url and search_html:
        return search_url, search_html, rescue_trace
    return _normalise_homepage_url(home_url), None, rescue_trace


def _same_site(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    return parsed.scheme in {"http", "https"} and _site_root(parsed.netloc) == _site_root(base.netloc)


def _binary_or_asset_url(url: str) -> bool:
    url_l = url.lower()
    return any(
        url_l.endswith(ext)
        for ext in (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".js", ".css")
    )


def _normalize_path_token(token: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(token or "").strip().lower()).strip("-")


def _normalized_path_segments(url_or_path: str) -> list[str]:
    raw = urlparse(url_or_path).path if "://" in str(url_or_path or "") else str(url_or_path or "")
    return [_normalize_path_token(segment) for segment in raw.split("/") if _normalize_path_token(segment)]


def _language_prefix(url: str, country: Optional[str]) -> str:
    segments = [seg for seg in urlparse(url).path.split("/") if seg]
    if not segments:
        return ""
    head = _normalize_path_token(segments[0])
    if head in set(_country_language_prefixes(country)) | LANG_PREFIX_SEGMENTS:
        return head
    return ""


def _is_generated_subdomain_candidate(candidate: dict) -> bool:
    return (
        str(candidate.get("source_strategy", "")).strip().lower() == "subdomains"
        and str(candidate.get("source_stage", "")).strip().lower() == "generated_subdomain"
    )


def _candidate_rank_adjustment(candidate: dict) -> float:
    adjustment = 0.0
    source_strategies = {str(item or "").strip().lower() for item in candidate.get("source_strategies", [])}
    anchor_text = str(candidate.get("anchor_text", "")).strip()
    parent_url = str(candidate.get("parent_url", "")).strip()
    source_strategy = str(candidate.get("source_strategy", "")).strip().lower()
    source_type = str(candidate.get("source_type", "")).strip().lower()
    country = candidate.get("country")
    page_family = str(candidate.get("page_family", "")).strip().lower()
    haystack = _candidate_haystack(candidate)
    directory_like = page_family in {"directory", "staff"} or _has_any_term(haystack, _directory_priority_terms(country))
    service_like = _has_any_term(haystack, _service_contact_terms(country))

    if len(source_strategies) > 1:
        adjustment += 9.0
    if anchor_text:
        adjustment += 3.0
    if parent_url:
        adjustment += 3.0
    if source_strategy == "real_link_multihop":
        adjustment += 4.0
    if source_strategy in {"sitemap", "cms"}:
        adjustment += 2.5
    if source_type in {"nav", "header", "footer", "body"}:
        adjustment += 1.5
    if directory_like:
        adjustment += 5.0
    if page_family == "directory":
        adjustment += 2.0
    if service_like and not directory_like:
        adjustment -= 4.0
    if _is_generated_subdomain_candidate(candidate):
        adjustment -= 18.0
        if not anchor_text and not parent_url and len(source_strategies) <= 1:
            adjustment -= 8.0
    if source_strategy == "family_templates":
        if parent_url:
            adjustment += 2.0
        if page_family in {"international", "directory", "staff", "contact", "governance", "office"}:
            adjustment += 1.5
    return adjustment


def classify_page_family(
    url: str,
    anchor_text: str = "",
    page_text: str = "",
    country: Optional[str] = None,
) -> tuple[str, float]:
    haystack_url = url.lower()
    haystack_anchor = anchor_text.lower()
    haystack_text = page_text.lower()[:1200]
    scores: dict[str, float] = {}
    for family, terms in _page_family_terms(country).items():
        score = 0.0
        for term in terms:
            term_l = term.lower()
            if not term_l:
                continue
            if term_l in haystack_url:
                score += 2.6
            if term_l in haystack_anchor:
                score += 2.0
            if term_l in haystack_text:
                score += 0.8
        if score > 0:
            scores[family] = score

    negative_terms = JUNK_PATH_TERMS | set(_country_negative_terms(country))
    if any(term in haystack_url or term in haystack_anchor for term in negative_terms):
        scores["generic"] = max(scores.get("generic", 0.0), 1.0)
        for family in ("contact", "directory", "staff", "office", "international", "governance"):
            if family in scores:
                scores[family] *= 0.65

    if not scores:
        return "generic", 0.0

    family, raw_score = max(scores.items(), key=lambda item: (item[1], FAMILY_PRIORITY.get(item[0], 0)))
    return family, min(1.0, raw_score / 5.0)


def score_candidate(
    url: str,
    anchor_text: str = "",
    country: Optional[str] = None,
    page_family: Optional[str] = None,
) -> float:
    url_l = url.lower()
    anchor_text_l = anchor_text.lower()
    tokens = _merge_unique(_country_tokens(country), _country_anchor_terms(country))
    score = 0.0
    for term in tokens:
        term_l = term.lower()
        if f"/{term_l}" in url_l or url_l.endswith(term_l) or term_l in anchor_text_l:
            score += 2.2

    family = page_family or classify_page_family(url, anchor_text, country=country)[0]
    haystack = f"{url_l} {anchor_text_l} {family}"
    directory_like = family in {"directory", "staff"} or _has_any_term(haystack, _directory_priority_terms(country))
    service_like = _has_any_term(haystack, _service_contact_terms(country))
    score += FAMILY_PRIORITY.get(family, 1) * 0.55
    if _language_prefix(url, country):
        score += 0.8
    depth = url_l.count("/")
    score -= max(0, depth - 6) * 0.45
    if any(term in url_l for term in ("/international", "/mobility", "/erasmus", "/global", "/directory", "/people", "/contact")):
        score += 1.6
    if any(term in url_l for term in ("/about", "/leadership", "/governance")):
        score += 1.2
    if directory_like:
        score += 2.2
    if service_like and not directory_like:
        score -= 1.8
    if _binary_or_asset_url(url_l):
        score -= 5.0
    if any(term in url_l for term in ("/events", "/news", "/calendar", "/blog")):
        score -= 1.5
    if "/student" in url_l and "/international" not in url_l and family not in {"international", "contact"}:
        score -= 0.6
    return score


def _candidate_rank(candidate: dict) -> tuple[float, float, float, str]:
    source_priority = float(candidate.get("source_priority", 0.0) or 0.0) + _candidate_rank_adjustment(candidate)
    family_priority = float(FAMILY_PRIORITY.get(str(candidate.get("page_family", "generic")), 1))
    heuristic_score = float(candidate.get("heuristic_score", 0.0) or 0.0)
    return (-source_priority, -family_priority, -heuristic_score, str(candidate.get("url", "")))


def _enrich_candidate(
    *,
    url: str,
    source_type: str,
    source_strategy: str,
    source_stage: str,
    country: Optional[str],
    anchor_text: str = "",
    parent_url: str = "",
    page_text: str = "",
) -> dict:
    family, confidence = classify_page_family(url, anchor_text, page_text, country=country)
    source_priority = STRATEGY_PRIORITY.get(source_strategy, 20) + SOURCE_TYPE_BONUS.get(source_type, 0)
    if source_stage.startswith("first_hop"):
        source_priority += 4
    if family in {"contact", "directory", "staff", "office", "international", "governance"}:
        source_priority += 4
    return {
        "url": normalize_url(url),
        "source_type": source_type,
        "source_strategy": source_strategy,
        "source_stage": source_stage,
        "country": country or "",
        "anchor_text": anchor_text or "",
        "parent_url": parent_url or "",
        "page_family": family,
        "family_confidence": round(confidence, 3),
        "locale_prefix": _language_prefix(url, country),
        "heuristic_score": round(score_candidate(url, anchor_text, country=country, page_family=family), 3),
        "source_priority": source_priority,
        "source_strategies": [source_strategy],
        "candidate_bucket": "content",
    }


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for candidate in candidates:
        url = normalize_url(candidate.get("url", ""))
        if not url:
            continue
        if url not in merged:
            merged[url] = dict(candidate, url=url)
            continue
        existing = merged[url]
        existing["source_strategies"] = sorted(
            set(existing.get("source_strategies", [])) | set(candidate.get("source_strategies", []))
        )
        existing["source_priority"] = max(
            float(existing.get("source_priority", 0.0) or 0.0),
            float(candidate.get("source_priority", 0.0) or 0.0),
        )
        if _candidate_rank(candidate) < _candidate_rank(existing):
            replacement = dict(candidate, url=url)
            replacement["source_strategies"] = existing["source_strategies"]
            replacement["source_priority"] = existing["source_priority"]
            merged[url] = replacement
    return sorted(merged.values(), key=_candidate_rank)


def _cap_candidates(candidates: list[dict], limit: int) -> list[dict]:
    return _dedupe_candidates(candidates)[:limit]


def _compose_mode_result(
    collectors: dict[str, list[dict]],
    mode: str,
) -> dict[str, list[dict] | int]:
    if mode not in MODE_TO_COLLECTORS and mode != "benchmark_all":
        mode = "hybrid"
    if mode == "benchmark_all":
        mode = "hybrid"

    selected = [candidate for name, items in collectors.items() if name in MODE_TO_COLLECTORS[mode] for candidate in items]
    deduped = _dedupe_candidates(selected)
    limit = getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60)

    if mode != "hybrid":
        corroborated_candidates = [c for c in deduped if len(c.get("source_strategies", []) or []) > 1]
        real_candidates = [c for c in deduped if c.get("source_strategy") in REAL_LINK_STRATEGIES]
        heuristic_candidates = [c for c in deduped if c.get("source_strategy") in HEURISTIC_STRATEGIES]
        speculative_subdomains = [c for c in deduped if _is_generated_subdomain_candidate(c)]
        selected_candidates: list[dict] = []
        selected_candidates.extend(corroborated_candidates[: max(2, min(8, limit // 5 or 1))])
        selected_candidates.extend(real_candidates[: max(0, limit - 10)])
        selected_candidates.extend(heuristic_candidates[: min(8, max(0, limit // 4))])
        selected_candidates.extend(speculative_subdomains[: min(3, max(1, limit // 20 or 1))])
        return {"uncapped": deduped, "capped": _dedupe_candidates(selected_candidates)[:limit]}

    real_candidates = [c for c in deduped if c.get("source_strategy") in REAL_LINK_STRATEGIES]
    heuristic_candidates = [c for c in deduped if c.get("source_strategy") in HEURISTIC_STRATEGIES]
    if not real_candidates:
        return {"uncapped": deduped, "capped": deduped[:limit]}

    heuristic_cap = max(8, int(limit * 0.4))
    selected_candidates = real_candidates[: max(0, limit - heuristic_cap)]
    selected_candidates.extend(heuristic_candidates[:heuristic_cap])
    return {"uncapped": deduped, "capped": _dedupe_candidates(selected_candidates)[:limit]}


def _compose_mode(
    collectors: dict[str, list[dict]],
    mode: str,
) -> list[dict]:
    return list(_compose_mode_result(collectors, mode)["capped"])


def _same_domain_or_allowed_subdomain(url: str, base_url: str, country: Optional[str]) -> bool:
    if not _same_site(url, base_url):
        return False
    host = urlparse(url).netloc.lower()
    base_root = _site_root(urlparse(base_url).netloc)
    if _site_root(host) != base_root:
        return False
    return True


def _parse_xml_loc_values(xml_text: str) -> list[str]:
    try:
        soup = BeautifulSoup(xml_text or "", "xml")
        if soup.find():
            return [
                normalize_url(loc.get_text(" ", strip=True))
                for loc in soup.find_all("loc")
                if normalize_url(loc.get_text(" ", strip=True))
            ]
    except Exception:
        pass
    return [
        normalize_url(match.group(1).strip())
        for match in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text or "", re.I)
        if normalize_url(match.group(1).strip())
    ]


async def _robots_sitemap_urls(base_url: str) -> list[str]:
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    robots_url = f"{root}/robots.txt"
    txt = await fetch_page(robots_url, expect_html=False)
    if not txt:
        return []
    urls: list[str] = []
    for line in str(txt).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() != "sitemap":
            continue
        sitemap_url = normalize_url(value.strip())
        if sitemap_url:
            urls.append(sitemap_url)
    return list(dict.fromkeys(urls))


async def _extract_structured_links(
    html: str,
    base_url: str,
    country: Optional[str] = None,
    *,
    keyword_filter: bool = True,
    stage_prefix: str = "homepage",
    parent_url: str = "",
    body_limit: Optional[int] = None,
) -> list[dict]:
    try:
        soup = BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html, "html.parser")

    terms = _merge_unique(_country_tokens(country), _country_anchor_terms(country))
    seen: set[str] = set()
    out: list[dict] = []

    section_specs = [
        ("nav", soup.find_all("nav")),
        ("header", soup.find_all("header")),
        ("footer", soup.find_all("footer")),
    ]
    for source_type, sections in section_specs:
        for section in sections:
            for anchor in section.find_all("a", href=True):
                href = anchor.get("href", "")
                text = anchor.get_text(" ", strip=True)[:160]
                url = normalize_url(urljoin(base_url, href))
                if not url or url in seen or _binary_or_asset_url(url):
                    continue
                if not _same_domain_or_allowed_subdomain(url, base_url, country):
                    continue
                if keyword_filter and not any(term.lower() in (href.lower() + " " + text.lower()) for term in terms):
                    continue
                seen.add(url)
                out.append(
                    _enrich_candidate(
                        url=url,
                        source_type=source_type,
                        source_strategy="real_link_multihop",
                        source_stage=f"{stage_prefix}_{source_type}",
                        country=country,
                        anchor_text=text,
                        parent_url=parent_url or base_url,
                    )
                )

    body_count = 0
    for anchor in soup.find_all("a", href=True, limit=500):
        href = anchor.get("href", "")
        text = anchor.get_text(" ", strip=True)[:160]
        url = normalize_url(urljoin(base_url, href))
        if not url or url in seen or _binary_or_asset_url(url):
            continue
        if not _same_domain_or_allowed_subdomain(url, base_url, country):
            continue
        if keyword_filter and not any(term.lower() in (href.lower() + " " + text.lower()) for term in terms):
            continue
        seen.add(url)
        out.append(
            _enrich_candidate(
                url=url,
                source_type="body",
                source_strategy="real_link_multihop",
                source_stage=f"{stage_prefix}_body",
                country=country,
                anchor_text=text,
                parent_url=parent_url or base_url,
            )
        )
        body_count += 1
        if body_limit and body_count >= body_limit:
            break

    return sorted(out, key=_candidate_rank)


def pick_preferred_hreflang(home_html: str, home_url: str, country: Optional[str] = None) -> Tuple[str, bool]:
    """Find the best alternate hreflang page for the current country's preferences."""
    try:
        soup = BeautifulSoup(home_html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(home_html, "html.parser")
    links = soup.find_all("link", rel=lambda v: v and "alternate" in v)
    alternates: list[tuple[str, str]] = []
    for link in links:
        lang = str(link.get("hreflang") or "").strip().lower()
        href = str(link.get("href") or "").strip()
        if not lang or not href:
            continue
        alternates.append((lang, href))

    current_url = normalize_url(home_url)
    for pref in _country_hreflang_preferences(country):
        exact_match = next((href for lang, href in alternates if lang == pref), None)
        prefixed_match = next((href for lang, href in alternates if lang.startswith(f"{pref}-")), None)
        best = exact_match or prefixed_match
        if not best:
            continue
        resolved = normalize_url(urljoin(home_url, best))
        if resolved != current_url:
            return resolved, True
        return home_url, False

    return home_url, False


async def extract_nav_candidates(html: str, base_url: str, country: Optional[str] = None) -> List[Tuple[str, str]]:
    """Backward-compatible keyword-filtered nav/footer/header extraction."""
    rows = await _extract_structured_links(
        html,
        base_url,
        country=country,
        keyword_filter=True,
        stage_prefix="homepage",
        body_limit=300,
    )
    return [(row["url"], row.get("anchor_text", "")) for row in rows]


async def discover_sitemap_urls(
    base_url: str,
    country: Optional[str] = None,
    *,
    keyword_filter: bool = True,
) -> List[str]:
    """Extract URLs from sitemap(s), including robots.txt and nested sitemap indexes."""
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    candidates = list(
        dict.fromkeys(
            [
                *await _robots_sitemap_urls(base_url),
                *[root + p for p in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"]],
            ]
        )
    )
    urls = []
    seen_sitemaps: set[str] = set()
    queue = list(candidates)
    nested_limit = max(4, int(getattr(config, "DISCOVERY_SITEMAP_NESTED_LIMIT", 16) or 16))
    while queue and len(seen_sitemaps) < nested_limit:
        sitemap_url = normalize_url(queue.pop(0))
        if not sitemap_url or sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        xml = await fetch_page(sitemap_url, expect_html=False)
        if not xml:
            continue
        for url in _parse_xml_loc_values(xml):
            if not url:
                continue
            if url.lower().endswith(".xml") and _same_domain_or_allowed_subdomain(url, root, country):
                queue.append(url)
                continue
            if not _same_domain_or_allowed_subdomain(url, root, country):
                continue
            if keyword_filter:
                family, confidence = classify_page_family(url, country=country)
                terms = _country_tokens(country)
                if confidence <= 0 and not any(term.lower() in url.lower() for term in terms):
                    continue
                if family == "generic" and confidence < 0.45:
                    continue
            urls.append(url)
        if len(urls) > 5000:
            break
    urls = sorted(set(urls), key=lambda url: -score_candidate(url, country=country))
    return urls[:300]


def subdomain_candidates(base_url: str, country: Optional[str] = None) -> List[str]:
    """Generate subdomain variants to probe."""
    parsed = urlparse(base_url)
    base_root = _site_root(parsed.netloc)
    out = []
    for subdomain in _country_subdomains(country):
        sub = _normalize_path_token(subdomain)
        if not sub:
            continue
        out.append(f"{parsed.scheme}://{sub}.{base_root}")
        out.append(f"{parsed.scheme}://{sub}.{base_root}/")
    return sorted({normalize_url(url) for url in out})


def is_wordpress(html: str) -> bool:
    return "wp-content" in html or "wordpress" in html.lower() or "wp-json" in html


def is_drupal(html: str) -> bool:
    return "drupal-settings-json" in html or "drupal" in html.lower()


async def wp_api_root(base_url: str) -> Optional[str]:
    root = urljoin(base_url, "/wp-json/")
    response = await get_with_retry(root)
    if response and (response.headers.get("Content-Type") or "").lower().startswith("application/json"):
        return root
    return None


async def wp_search_urls(base_url: str, country: Optional[str] = None) -> List[str]:
    root = await wp_api_root(base_url)
    if not root:
        return []
    urls = []
    for term in _country_cms_search_terms(country):
        for query in (
            f"{root}wp/v2/search?search={term}&per_page=20&subtype=page",
            f"{root}wp/v2/pages?search={term}&per_page=20&_fields=link",
        ):
            response = await get_with_retry(query)
            if not response or response.status_code != 200:
                continue
            try:
                payload = response.json()
            except Exception:
                continue
            items = payload if isinstance(payload, list) else []
            for item in items:
                url = item.get("url") or item.get("link")
                if url and _same_domain_or_allowed_subdomain(url, base_url, country):
                    urls.append(normalize_url(url))
    return sorted(set(urls), key=lambda url: -score_candidate(url, country=country))


async def drupal_jsonapi_root(base_url: str) -> Optional[str]:
    for path in ("/jsonapi/", "/jsonapi"):
        root = urljoin(base_url, path)
        response = await get_with_retry(root, tries=1)
        if response and response.status_code == 200 and (response.headers.get("Content-Type") or "").lower().startswith("application/vnd.api+json"):
            return root if root.endswith("/") else root + "/"
    return None


async def drupal_search_urls(base_url: str, country: Optional[str] = None) -> List[str]:
    root = await drupal_jsonapi_root(base_url)
    if not root:
        return []
    urls = []
    node_types = ["page", "news", "person", "people", "staff", "team", "directory"]
    for node_type in node_types:
        probe = await get_with_retry(f"{root}node/{node_type}?page[limit]=1", tries=1)
        if not probe or probe.status_code != 200:
            continue
        consecutive_empty = 0
        for term in _country_cms_search_terms(country):
            query = f"{root}node/{node_type}?filter[fulltext]={term}&page[limit]=25"
            response = await get_with_retry(query, tries=1)
            if not response or response.status_code != 200:
                continue
            try:
                data = response.json()
            except Exception:
                continue
            items = data.get("data", [])
            if not items:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                continue
            consecutive_empty = 0
            for item in items:
                attrs = item.get("attributes", {})
                alias = attrs.get("path", {}).get("alias")
                if alias:
                    url = normalize_url(urljoin(base_url, alias))
                else:
                    node_id = attrs.get("drupal_internal__nid")
                    if not node_id:
                        continue
                    url = normalize_url(urljoin(base_url, f"/node/{node_id}"))
                if _same_domain_or_allowed_subdomain(url, base_url, country):
                    urls.append(url)
    return sorted(set(urls), key=lambda url: -score_candidate(url, country=country))


def _form_signal_text(form) -> str:
    pieces = [
        form.get("id", ""),
        form.get("name", ""),
        form.get("role", ""),
        form.get("aria-label", ""),
        form.get_text(" ", strip=True)[:240],
    ]
    for field in form.find_all(["input", "select", "textarea"]):
        pieces.extend(
            [
                field.get("name", ""),
                field.get("placeholder", ""),
                field.get("aria-label", ""),
            ]
        )
    return " ".join(str(piece or "").strip().lower() for piece in pieces if str(piece or "").strip())


def _looks_like_directory_or_search_form(form, country: Optional[str]) -> bool:
    method = str(form.get("method", "get") or "get").strip().lower()
    signal_text = _form_signal_text(form)
    directory_terms = _country_directory_search_terms(country)
    has_directory_term = any(term in signal_text for term in directory_terms if term)
    field_names = {
        _normalize_path_token(field.get("name", "") or field.get("id", "") or "")
        for field in form.find_all(["input", "select", "textarea"])
    }
    has_search_field = any(name in SEARCH_FORM_FIELD_HINTS for name in field_names if name)
    return method == "get" and (has_directory_term or has_search_field)


async def _collect_structured_endpoint_candidates(home_html: str, home_url: str, country: Optional[str]) -> list[dict]:
    try:
        home_soup = BeautifulSoup(home_html, "lxml")
    except FeatureNotFound:
        home_soup = BeautifulSoup(home_html, "html.parser")

    candidates: list[dict] = []
    seen_urls: set[str] = set()

    def add_candidate(url: str, *, source_stage: str, anchor_text: str = "", parent_url: str = "") -> None:
        normalized = normalize_url(url)
        if not normalized or normalized in seen_urls or _binary_or_asset_url(normalized):
            return
        if not _same_domain_or_allowed_subdomain(normalized, home_url, country):
            return
        seen_urls.add(normalized)
        candidate = _enrich_candidate(
            url=normalized,
            source_type="form" if "form" in source_stage else "search",
            source_strategy="structured_endpoints",
            source_stage=source_stage,
            country=country,
            anchor_text=anchor_text,
            parent_url=parent_url or home_url,
        )
        candidate["candidate_bucket"] = "search_interface"
        candidate["page_family"] = "directory"
        candidate["interface_only"] = True
        candidates.append(candidate)

    pages_to_scan: list[tuple[str, Any, str, str]] = [(home_url, home_soup, "homepage", home_url)]
    first_hop_limit = max(2, int(getattr(config, "DISCOVERY_STRUCTURED_ENDPOINT_FETCH_LIMIT", 4) or 4))
    root_candidates = await _extract_structured_links(
        home_html,
        home_url,
        country=country,
        keyword_filter=False,
        stage_prefix="homepage",
        body_limit=getattr(config, "DISCOVERY_ROOT_LINK_LIMIT", 80),
    )
    preferred_first_hops = [
        candidate
        for candidate in root_candidates
        if str(candidate.get("page_family", "")).strip().lower() in {"contact", "directory", "staff", "international", "office", "governance"}
    ][:first_hop_limit]
    for candidate in preferred_first_hops:
        html = await fetch_page(candidate["url"])
        if not html:
            continue
        try:
            soup = BeautifulSoup(html, "lxml")
        except FeatureNotFound:
            soup = BeautifulSoup(html, "html.parser")
        pages_to_scan.append((candidate["url"], soup, "first_hop", candidate["url"]))

    directory_terms = _country_directory_search_terms(country)
    for page_url, soup, stage_prefix, parent_url in pages_to_scan:
        for form in soup.find_all("form"):
            if not _looks_like_directory_or_search_form(form, country):
                continue
            action = str(form.get("action") or "").strip()
            resolved = normalize_url(urljoin(page_url, action or page_url))
            add_candidate(
                resolved,
                source_stage=f"{stage_prefix}_form",
                anchor_text=(form.get("aria-label") or form.get("name") or form.get("id") or "directory search"),
                parent_url=parent_url,
            )

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            text = anchor.get_text(" ", strip=True)[:160]
            haystack = f"{href.lower()} {text.lower()}"
            if not any(term in haystack for term in directory_terms if term):
                continue
            if "?" in href or "search" in haystack or "recherche" in haystack or "annuaire" in haystack:
                add_candidate(
                    urljoin(page_url, href),
                    source_stage=f"{stage_prefix}_search_link",
                    anchor_text=text,
                    parent_url=parent_url,
                )

    return _cap_candidates(candidates, getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60))


async def _collect_real_link_multihop(home_html: str, home_url: str, country: Optional[str]) -> list[dict]:
    root_candidates = await _extract_structured_links(
        home_html,
        home_url,
        country=country,
        keyword_filter=False,
        stage_prefix="homepage",
        body_limit=getattr(config, "DISCOVERY_ROOT_LINK_LIMIT", 80),
    )
    root_candidates = _cap_candidates(root_candidates, getattr(config, "DISCOVERY_ROOT_LINK_LIMIT", 80))

    children: list[dict] = []
    for candidate in root_candidates[: getattr(config, "DISCOVERY_FIRST_HOP_FETCH_LIMIT", 6)]:
        html = await fetch_page(candidate["url"])
        if not html:
            continue
        child_candidates = await _extract_structured_links(
            html,
            candidate["url"],
            country=country,
            keyword_filter=False,
            stage_prefix="first_hop",
            parent_url=candidate["url"],
            body_limit=getattr(config, "DISCOVERY_CHILD_LINK_LIMIT", 25),
        )
        for child in child_candidates:
            child["source_strategy"] = "real_link_multihop"
            child["source_strategies"] = ["real_link_multihop"]
        children.extend(child_candidates)

    combined = root_candidates + children
    return _cap_candidates(combined, getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60))


def _collect_heuristic_slugs(home_url: str, country: Optional[str]) -> list[dict]:
    candidates: list[dict] = []
    for slug in config.SLUGS:
        url = normalize_url(urljoin(home_url, slug))
        candidates.append(
            _enrich_candidate(
                url=url,
                source_type="heuristic",
                source_strategy="heuristic_slugs",
                source_stage="generic_slug_template",
                country=country,
            )
        )
    if home_url.endswith(".edu") or ".edu/" in home_url:
        for slug in config.US_SLUGS:
            url = normalize_url(urljoin(home_url, slug))
            candidates.append(
                _enrich_candidate(
                    url=url,
                    source_type="heuristic_us",
                    source_strategy="heuristic_slugs",
                    source_stage="us_slug_template",
                    country=country,
                )
            )
    return _cap_candidates(candidates, getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60))


def _collect_profile_slugs(home_url: str, extra_slugs: Optional[List[str]], country: Optional[str]) -> list[dict]:
    slugs = []
    slugs.extend(_country_slug_hints(country))
    slugs.extend(list(extra_slugs or []))
    candidates: list[dict] = []
    for slug in slugs:
        if not slug:
            continue
        url = normalize_url(urljoin(home_url, slug if slug.startswith("/") else f"/{slug}"))
        candidates.append(
            _enrich_candidate(
                url=url,
                source_type="profile_slug",
                source_strategy="profile_slugs",
                source_stage="profile_or_localised_slug",
                country=country,
            )
        )
    return _cap_candidates(candidates, getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60))


def _family_template_terms(country: Optional[str]) -> list[str]:
    families = _page_family_terms(country)
    ordered_terms: list[str] = []
    for family in ("international", "directory", "staff", "contact", "office", "governance"):
        ordered_terms.extend(families.get(family, []))
    ordered_terms.extend(_country_high_yield_seed_patterns(country))
    normalized = [_normalize_path_token(term) for term in ordered_terms]
    return [term for term in dict.fromkeys(term for term in normalized if term)]


def _branch_seed_paths(home_url: str, seed_candidates: list[dict], country: Optional[str]) -> list[str]:
    language_prefixes = set(_country_language_prefixes(country)) | LANG_PREFIX_SEGMENTS
    generic_segments = {
        _normalize_path_token(item)
        for item in _country_pack(country).get("generic_branch_segments", [])
        if _normalize_path_token(item)
    }
    branch_roots = {
        _normalize_path_token(item)
        for item in _country_pack(country).get("branch_root_segments", [])
        if _normalize_path_token(item)
    }
    negative_segments = JUNK_PATH_TERMS | {
        _normalize_path_token(item)
        for item in _country_negative_terms(country)
        if _normalize_path_token(item)
    }
    seeds: list[str] = []
    seen: set[str] = set()

    def add_seed(path_value: str) -> None:
        path_value = "/".join(segment for segment in str(path_value or "").split("/") if segment)
        if not path_value or path_value in seen:
            return
        seen.add(path_value)
        seeds.append(path_value)

    for candidate in seed_candidates:
        segments = _normalized_path_segments(candidate.get("url", ""))
        if not segments:
            continue
        while segments and segments[0] in language_prefixes:
            segments = segments[1:]
        if not segments:
            continue
        head = segments[0]
        if head in generic_segments or head in negative_segments:
            continue
        add_seed(head)
        if len(segments) >= 2 and (head in branch_roots or head in {"international", "internationale", "gouvernance", "governance"}):
            child = segments[1]
            if child not in generic_segments and child not in negative_segments:
                add_seed(f"{head}/{child}")

    for root in branch_roots:
        add_seed(root)

    return seeds[: getattr(config, "DISCOVERY_BRANCH_TEMPLATE_LIMIT", 14)]


async def _collect_family_template_candidates(home_html: str, home_url: str, country: Optional[str]) -> list[dict]:
    root_candidates = await _extract_structured_links(
        home_html,
        home_url,
        country=country,
        keyword_filter=False,
        stage_prefix="homepage",
        body_limit=getattr(config, "DISCOVERY_ROOT_LINK_LIMIT", 80),
    )
    branch_paths = _branch_seed_paths(home_url, root_candidates, country)
    direct_terms = _family_template_terms(country)
    language_prefixes = [prefix for prefix in _country_language_prefixes(country) if prefix]
    limit = getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60)
    candidates: list[dict] = []
    seen_urls: set[str] = set()

    def add_candidate(path_value: str, stage: str, parent_url: str = "") -> None:
        path_value = "/".join(segment for segment in str(path_value or "").split("/") if segment)
        if not path_value:
            return
        url = normalize_url(urljoin(home_url, path_value if path_value.startswith("/") else f"/{path_value}"))
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        candidates.append(
            _enrich_candidate(
                url=url,
                source_type="family_template",
                source_strategy="family_templates",
                source_stage=stage,
                country=country,
                parent_url=parent_url,
            )
        )

    for term in direct_terms[: getattr(config, "DISCOVERY_DIRECT_TEMPLATE_TERM_LIMIT", 24)]:
        add_candidate(term, "family_template_direct")
        for prefix in language_prefixes:
            add_candidate(f"{prefix}/{term}", "family_template_language_direct")

    branch_combo_terms = [
        term
        for term in direct_terms
        if term not in branch_paths and term not in {"international", "internationale", "global"}
    ]
    per_branch_limit = getattr(config, "DISCOVERY_BRANCH_COMBO_TERM_LIMIT", 6)
    for branch_path in branch_paths:
        branch_head = branch_path.split("/", 1)[0]
        if branch_head in {"gouvernance", "governance", "presidence", "presidency"}:
            preferred_terms = [
                term for term in branch_combo_terms if term in {"annuaire", "contacts", "contact", "personnel", "personnels", "equipe", "team", "staff", "organigramme"}
            ]
        elif branch_head in {"international", "internationale", "vie-internationale", "relations-internationales"}:
            preferred_terms = [
                term for term in branch_combo_terms if term in {"annuaire", "contacts", "contact", "personnel", "personnels", "equipe", "team", "staff", "mobilite", "mobilite-internationale", "erasmus"}
            ]
        else:
            preferred_terms = branch_combo_terms
        for term in preferred_terms[:per_branch_limit]:
            add_candidate(f"{branch_path}/{term}", "family_template_branch", parent_url=home_url)
            for prefix in language_prefixes:
                add_candidate(f"{prefix}/{branch_path}/{term}", "family_template_language_branch", parent_url=home_url)

    return _cap_candidates(candidates, limit)


async def _collect_sitemap_candidates(home_url: str, country: Optional[str], *, keyword_filter: bool) -> list[dict]:
    return _cap_candidates(
        [
            _enrich_candidate(
                url=url,
                source_type="sitemap",
                source_strategy="sitemap",
                source_stage="sitemap_xml",
                country=country,
            )
            for url in await discover_sitemap_urls(home_url, country=country, keyword_filter=keyword_filter)
        ],
        getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60),
    )


async def _collect_cms_candidates(home_url: str, country: Optional[str], cms_wp: bool, cms_drupal: bool) -> list[dict]:
    candidates: list[dict] = []
    if cms_wp:
        for url in await wp_search_urls(home_url, country=country):
            candidates.append(
                _enrich_candidate(
                    url=url,
                    source_type="wp",
                    source_strategy="cms",
                    source_stage="wordpress_api",
                    country=country,
                )
            )
    if cms_drupal:
        for url in await drupal_search_urls(home_url, country=country):
            candidates.append(
                _enrich_candidate(
                    url=url,
                    source_type="drupal",
                    source_strategy="cms",
                    source_stage="drupal_jsonapi",
                    country=country,
                )
            )
    return _cap_candidates(candidates, getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60))


def _collect_subdomain_candidates(home_url: str, country: Optional[str]) -> list[dict]:
    return _cap_candidates(
        [
            _enrich_candidate(
                url=url,
                source_type="subdomain",
                source_strategy="subdomains",
                source_stage="generated_subdomain",
                country=country,
            )
            for url in subdomain_candidates(home_url, country=country)
        ],
        getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60),
    )


def _discovery_cache_key(
    home_url: str,
    extra_slugs: Optional[List[str]],
    country: Optional[str],
    mode: str,
    target_name: Optional[str] = None,
) -> tuple:
    normalized_slugs = tuple(
        sorted(
            {
                str(slug or "").strip().lower()
                for slug in (extra_slugs or [])
                if str(slug or "").strip()
            }
        )
    )
    return (
        normalize_url(home_url),
        str(country or "").strip().upper(),
        str(mode or "hybrid").strip().lower(),
        normalized_slugs,
        " ".join(_normalized_text_tokens(target_name or "")),
    )


def _cached_discovery_bundle(cache_key: tuple, *, require_breakdown: bool) -> dict | None:
    _clear_expired_discovery_runtime_state()
    entry = _DISCOVERY_BUNDLE_CACHE.get(cache_key)
    if not entry:
        return None
    bundle = entry[1]
    if require_breakdown and not bundle.get("by_strategy"):
        return None
    return deepcopy(bundle)


def _store_discovery_bundle(cache_key: tuple, bundle: dict) -> None:
    _DISCOVERY_BUNDLE_CACHE[cache_key] = (time.monotonic(), deepcopy(bundle))


async def gather_candidates_bundle(
    home_url: str,
    extra_slugs: Optional[List[str]] = None,
    country: Optional[str] = None,
    mode: str = "hybrid",
    include_strategy_breakdown: bool = False,
    target_name: Optional[str] = None,
) -> dict:
    """
    Gather candidate pages using explicit discovery strategies.
    """
    mode = str(mode or "hybrid").strip().lower()
    if mode not in DISCOVERY_MODES:
        mode = "hybrid"

    cache_key = _discovery_cache_key(home_url, extra_slugs, country, mode, target_name)
    cached = _cached_discovery_bundle(cache_key, require_breakdown=include_strategy_breakdown or mode == "benchmark_all")
    if cached is not None:
        return cached

    home_url, home_html, homepage_rescue_trace = await _resolve_homepage_entrypoint(home_url, country, target_name=target_name)
    if not home_html:
        bundle = {
            "candidates": [],
            "by_strategy": {},
            "collector_breakdown": {},
            "cms_wp": False,
            "cms_drupal": False,
            "hreflang_hopped": False,
            "home_url": home_url,
            "resolved_home_url": home_url,
            "homepage_rescue_trace": homepage_rescue_trace,
        }
        _store_discovery_bundle(cache_key, bundle)
        return deepcopy(bundle)

    preferred_url, hopped = pick_preferred_hreflang(home_html, home_url, country)
    if preferred_url != home_url:
        home_url = preferred_url
        home_html = await fetch_page(home_url) or home_html

    cms_wp = is_wordpress(home_html)
    cms_drupal = is_drupal(home_html)

    requested_modes = (
        ["heuristic_only", "generated_slug_only", "real_link_only", "hybrid"]
        if include_strategy_breakdown or mode == "benchmark_all"
        else [mode]
    )
    needed_collectors = set()
    for requested_mode in requested_modes:
        needed_collectors.update(MODE_TO_COLLECTORS.get(requested_mode, MODE_TO_COLLECTORS["hybrid"]))

    collectors: dict[str, list[dict]] = {}
    if "real_link_multihop" in needed_collectors:
        collectors["real_link_multihop"] = await _run_collector_with_timeout(
            "real_link_multihop",
            _collect_real_link_multihop(home_html, home_url, country),
            timeout_seconds=getattr(config, "DISCOVERY_REAL_LINK_TIMEOUT", 45.0),
            home_url=home_url,
        )
    if "heuristic_slugs" in needed_collectors:
        collectors["heuristic_slugs"] = _collect_heuristic_slugs(home_url, country)
    if "profile_slugs" in needed_collectors:
        collectors["profile_slugs"] = _collect_profile_slugs(home_url, extra_slugs, country)
    if "family_templates" in needed_collectors:
        collectors["family_templates"] = await _collect_family_template_candidates(home_html, home_url, country)
    if "sitemap" in needed_collectors:
        collectors["sitemap"] = await _run_collector_with_timeout(
            "sitemap",
            _collect_sitemap_candidates(
                home_url,
                country,
                keyword_filter=mode not in {"real_link_only", "benchmark_all"} and not include_strategy_breakdown,
            ),
            timeout_seconds=getattr(config, "DISCOVERY_SITEMAP_TIMEOUT", 20.0),
            home_url=home_url,
        )
    if "cms" in needed_collectors:
        collectors["cms"] = await _run_collector_with_timeout(
            "cms",
            _collect_cms_candidates(home_url, country, cms_wp, cms_drupal),
            timeout_seconds=getattr(config, "DISCOVERY_CMS_TIMEOUT", 20.0),
            home_url=home_url,
        )
    if "structured_endpoints" in needed_collectors:
        collectors["structured_endpoints"] = await _collect_structured_endpoint_candidates(home_html, home_url, country)
    if "subdomains" in needed_collectors:
        collectors["subdomains"] = _collect_subdomain_candidates(home_url, country)

    composed_by_mode = {requested_mode: _compose_mode_result(collectors, requested_mode) for requested_mode in requested_modes}
    by_strategy = {requested_mode: list(result["capped"]) for requested_mode, result in composed_by_mode.items()}
    by_strategy_candidate_counts = {
        requested_mode: len(result["uncapped"])
        for requested_mode, result in composed_by_mode.items()
    }
    selected_mode = "hybrid" if mode == "benchmark_all" else mode
    candidates = by_strategy.get(selected_mode, _compose_mode(collectors, "hybrid"))

    bundle = {
        "candidates": candidates,
        "by_strategy": by_strategy,
        "by_strategy_candidate_counts": by_strategy_candidate_counts,
        "collector_breakdown": collectors,
        "cms_wp": cms_wp,
        "cms_drupal": cms_drupal,
        "hreflang_hopped": hopped,
        "home_url": home_url,
        "resolved_home_url": home_url,
        "homepage_rescue_trace": homepage_rescue_trace,
    }
    _store_discovery_bundle(cache_key, bundle)
    return deepcopy(bundle)


async def gather_candidates(
    home_url: str,
    extra_slugs: Optional[List[str]] = None,
    country: Optional[str] = None,
    mode: str = "hybrid",
    include_strategy_breakdown: bool = False,
    target_name: Optional[str] = None,
) -> Tuple[List[dict], bool, bool, bool]:
    bundle = await gather_candidates_bundle(
        home_url,
        extra_slugs=extra_slugs,
        country=country,
        mode=mode,
        include_strategy_breakdown=include_strategy_breakdown,
        target_name=target_name,
    )
    return (
        bundle["candidates"][: getattr(config, "DISCOVERY_FINAL_CANDIDATE_LIMIT", 60)],
        bundle["cms_wp"],
        bundle["cms_drupal"],
        bundle["hreflang_hopped"],
    )
