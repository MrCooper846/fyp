"""
Staged contact extraction helpers.

This module now separates:
1. raw evidence extraction
2. candidate assembly
3. candidate cleanup
4. candidate typing

It also keeps the legacy helper surface (`decode_js_emails`,
`simple_regex_contacts`, `deobfuscate`, etc.) for compatibility.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, unquote, urlparse

from bs4 import BeautifulSoup, FeatureNotFound

import gc_contacts.config as config
from gc_contacts.core.acquisition import iter_embedded_json_documents
from gc_contacts.localisation import get_country_contact_pack


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EXPLICIT_OBFUSCATED_EMAIL_RE = re.compile(
    r"""
    \b
    [A-Za-z0-9._%+-]+
    \s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\s+at\s+)\s*
    [A-Za-z0-9.-]+
    (?:
        \s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\s+dot\s+)\s*
        [A-Za-z0-9.-]+
    )+
    \b
    """,
    re.I | re.X,
)
OBFUSCATED = EXPLICIT_OBFUSCATED_EMAIL_RE
GENERIC_EMAIL = re.compile(
    r"^(info|enquiries|enquiry|contact|office|support|hello|admissions|international|ug|pg|postgrad|undergrad|apply|students|noreply)[\+\.\-]?",
    re.I,
)

RAW_EXTRACTION_STRATEGIES = (
    "mailto_explicit",
    "visible_regex",
    "html_attribute",
    "embedded_json",
    "js_decode",
    "explicit_obfuscation",
    "llm_structured",
)

_ROLE_CONTEXT_TAGS = ("li", "p", "dd", "td", "th", "div", "section", "article", "address")
_STRUCTURED_EMAIL_ATTRS = ("data-email", "data-mail", "data-email-address")
_STRUCTURED_NAME_ATTRS = ("data-name", "data-contact-name", "data-person-name")
_STRUCTURED_ROLE_ATTRS = ("data-role", "data-title", "data-unit", "data-office")
_EMAIL_QUERY_PARAM_HINTS = ("mailto", "email", "mail", "recipient", "destinataire")
_POTENTIAL_ANCHOR_PARAM_HINTS = (
    "recipient",
    "contact",
    "contactid",
    "contact_id",
    "dest",
    "destinataire",
    "mail",
    "mailto",
    "email",
)
_PRIMARY_STRATEGY_ORDER = {
    "mailto_explicit": 6,
    "html_attribute": 5,
    "embedded_json": 4,
    "visible_regex": 3,
    "explicit_obfuscation": 2,
    "js_decode": 1,
    "llm_structured": 1,
}
_NAME_CLEAN_CACHE: dict[tuple[str, str, str, str], str] = {}
_EMBEDDED_EMAIL_KEYS = ("email", "mail", "contactemail", "emailaddress", "mailaddress")
_EMBEDDED_NAME_KEYS = ("name", "fullname", "displayname", "personname", "contactname")
_EMBEDDED_ROLE_KEYS = ("role", "title", "position", "jobtitle", "office", "unit", "department", "team")
_EMBEDDED_CONTEXT_KEYS = (
    "label",
    "description",
    "summary",
    "context",
    "office",
    "unit",
    "department",
    "team",
    "location",
)
_BOILERPLATE_CONTEXT_MARKERS = (
    "apply now",
    "cookie",
    "find a course",
    "main menu",
    "menu",
    "news",
    "privacy",
    "search",
    "skip to",
    "social media",
    "toggle navigation",
    "webmail",
)


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _looks_like_xml_document(value: str) -> bool:
    text = str(value or "").lstrip()
    if not text.startswith("<"):
        return False
    lowered = text[:400].lower()
    return (
        lowered.startswith("<?xml")
        or lowered.startswith("<urlset")
        or lowered.startswith("<sitemapindex")
        or lowered.startswith("<rss")
        or lowered.startswith("<feed")
    )


def _parse_markup_document(markup: str) -> BeautifulSoup:
    if _looks_like_xml_document(markup):
        try:
            return BeautifulSoup(markup, "xml")
        except FeatureNotFound:
            pass
    try:
        return BeautifulSoup(markup, "lxml")
    except FeatureNotFound:
        return BeautifulSoup(markup, "html.parser")


def _unique_preserve_order(values: List[str]) -> List[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['`.-][^\W\d_]+)*", re.UNICODE)


def _contact_locale(country: Optional[str] = None) -> dict[str, Any]:
    return get_country_contact_pack(country)


def _locale_terms(locale_pack: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(
        str(item or "").strip().lower()
        for item in locale_pack.get(key, [])
        if str(item or "").strip()
    )


def _locale_term_set(locale_pack: dict[str, Any], key: str) -> set[str]:
    return set(_locale_terms(locale_pack, key))


def _locale_role_aliases(locale_pack: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    aliases = locale_pack.get("role_aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {
        str(canonical or "").strip().lower(): tuple(
            str(alias or "").strip().lower()
            for alias in value
            if str(alias or "").strip()
        )
        for canonical, value in aliases.items()
        if str(canonical or "").strip()
    }


def _word_tokens(value: str) -> list[str]:
    return [token for token in _WORD_TOKEN_RE.findall(_normalize_space(value)) if token]


def _normalise_person_name_case(value: str) -> str:
    parts: list[str] = []
    for token in str(value or "").split():
        bare = token.strip(".,")
        if len(bare) > 1 and bare.isupper():
            parts.append(token[: len(token) - len(token.lstrip("(["))] + bare.title())
        else:
            parts.append(token)
    return _normalize_space(" ".join(parts))


def _has_strong_role_signal(
    value: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> bool:
    text = _normalize_space(value)
    if not text:
        return False
    locale_pack = _contact_locale(country)
    lowered = text.lower()
    if role_keywords and any(keyword.lower() in lowered for keyword in role_keywords):
        return True
    if re.search(config.ALLOWED_ROLE_WORDS, text, re.I):
        return True
    if re.search(config.SENIORITY, text, re.I):
        return True
    if re.search(config.INTL_HINTS, text, re.I):
        return True
    if any(term in lowered for term in _locale_terms(locale_pack, "role_signal_terms")):
        return True
    if any(term in lowered for term in _locale_terms(locale_pack, "office_role_terms")):
        return True
    if any(term in lowered for term in _locale_terms(locale_pack, "relevant_office_role_terms")):
        return True
    if any(term in lowered for term in _locale_terms(locale_pack, "international_markers")) and any(
        term in lowered for term in _locale_terms(locale_pack, "office_markers")
    ):
        return True
    if any(term in lowered for term in _locale_terms(locale_pack, "admissions_markers")) and any(
        term in lowered for term in _locale_terms(locale_pack, "international_markers")
    ):
        return True
    return False


def _looks_like_boilerplate_context_line(
    value: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> bool:
    text = _normalize_space(value)
    if not text:
        return True
    lowered = text.lower()
    tokens = _word_tokens(text)
    if _has_strong_role_signal(text, role_keywords=role_keywords, country=country):
        return False
    marker_hits = sum(1 for marker in _BOILERPLATE_CONTEXT_MARKERS if marker in lowered)
    if any(lowered == marker or lowered.startswith(f"{marker} ") for marker in _BOILERPLATE_CONTEXT_MARKERS):
        return True
    if marker_hits >= 1 and len(tokens) <= 4 and len(text) <= 40:
        return True
    if marker_hits >= 2:
        return True
    if len(tokens) >= 28 or len(text) >= 220:
        return True
    if len(tokens) >= 18 and len(text) >= 120:
        return True
    if EMAIL_RE.findall(text) and len(EMAIL_RE.findall(text)) > 1:
        return True
    if lowered.count("|") >= 2 or lowered.count(" > ") >= 2:
        return True
    return False


def _filtered_context_lines(
    lines: List[str],
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = _normalize_space(line)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if _looks_like_boilerplate_context_line(normalized, role_keywords=role_keywords, country=country):
            continue
        filtered.append(normalized)
    return filtered


def clean_context_text(
    value: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
    max_lines: int = 6,
    max_chars: int = 320,
) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    lines = [_normalize_space(line) for line in raw.splitlines() if _normalize_space(line)]
    filtered = _filtered_context_lines(lines, role_keywords=role_keywords, country=country)
    if not filtered:
        filtered = []
        for line in lines:
            if EMAIL_RE.search(line):
                filtered.append(line)
            elif _has_strong_role_signal(line, role_keywords=role_keywords, country=country):
                filtered.append(line)
    filtered = filtered[:max_lines]
    text = "\n".join(filtered)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:/|-")
    return text


def _matchable_name_tokens(name: str, country: Optional[str] = None) -> list[str]:
    locale_pack = _contact_locale(country)
    particles = _locale_term_set(locale_pack, "name_particles")
    tokens = [tok.lower() for tok in _word_tokens(name)]
    filtered = [tok for tok in tokens if tok not in particles]
    return filtered if len(filtered) >= 2 else tokens


def _infer_name_from_email_localpart(email: str, country: Optional[str] = None) -> str:
    email = str(email or "").strip().lower()
    if not email or "@" not in email:
        return ""
    local = email.split("@", 1)[0]
    if GENERIC_EMAIL.match(local):
        return ""
    if any(ch.isdigit() for ch in local):
        return ""
    local = re.sub(r"[+].*$", "", local)
    parts = [part for part in re.split(r"[._-]+", local) if part]
    if len(parts) < 2:
        return ""
    if any(len(part) < 2 for part in parts):
        return ""
    if len(parts) > 4:
        return ""
    candidate = " ".join(part.capitalize() for part in parts)
    cleaned = clean_contact_name(candidate, country=country)
    if not cleaned or not _looks_like_person_name_fast(cleaned, country=country):
        return ""
    if not _email_matches_name(email, cleaned, country=country):
        return ""
    return cleaned


def normalize_email_value(value: str) -> str:
    candidate = unquote(str(value or "").strip())
    if not candidate:
        return ""
    if candidate.lower().startswith("mailto:"):
        candidate = candidate[7:]
    candidate = candidate.split("?", 1)[0]
    candidate = candidate.strip().strip(".,;:()[]<>\"'")
    if EMAIL_RE.fullmatch(candidate):
        return candidate.lower()
    return ""


def _is_titleish_name_token(token: str) -> bool:
    token = token.strip(".,")
    if not token:
        return False
    alpha_chars = [ch for ch in token if ch.isalpha()]
    if not alpha_chars:
        return False
    if not any(ch.islower() or ch.isupper() for ch in alpha_chars):
        return True
    first = alpha_chars[0]
    rest = alpha_chars[1:]
    return first.isupper() and (not rest or any(ch.islower() for ch in rest) or all(ch.isupper() for ch in rest))


def _name_window_valid(tokens: List[str], locale_pack: dict[str, Any]) -> bool:
    particles = _locale_term_set(locale_pack, "name_particles")
    stopwords = _locale_term_set(locale_pack, "name_stopwords")
    upper_tokens = 0
    for token in tokens:
        lowered = token.lower().strip(".,")
        if lowered in particles:
            continue
        if lowered in stopwords:
            return False
        if not _is_titleish_name_token(token):
            return False
        upper_tokens += 1
    if upper_tokens < 2:
        return False
    candidate = " ".join(tokens)
    if re.search(config.ALLOWED_ROLE_WORDS, candidate, re.I) or re.search(config.SENIORITY, candidate, re.I):
        return False
    return True


def _looks_address_like_name(value: str) -> bool:
    text = _normalize_space(value)
    if not text:
        return False
    locale_pack = _contact_locale(None)
    return _looks_address_like_name_for_locale(text, locale_pack)


def _looks_address_like_name_for_locale(value: str, locale_pack: dict[str, Any]) -> bool:
    text = _normalize_space(value)
    if not text:
        return False
    lowered = text.lower()
    tokens = [tok.lower() for tok in _word_tokens(text)]
    if any(term in lowered for term in _locale_terms(locale_pack, "boilerplate_name_fragments")):
        return True
    if any(phrase in lowered for phrase in _locale_terms(locale_pack, "address_like_phrases")):
        return True
    if any(token in _locale_term_set(locale_pack, "address_terms") for token in tokens):
        return True
    if re.search(r"\b\d{1,5}\b", lowered):
        return True
    if "," in text and any(term in lowered for term in _locale_terms(locale_pack, "address_terms")):
        return True
    return False


def _looks_office_or_unit_label(value: str) -> bool:
    text = _normalize_space(value)
    if not text:
        return False
    locale_pack = _contact_locale(None)
    return _looks_office_or_unit_label_for_locale(text, locale_pack)


def _looks_office_or_unit_label_for_locale(value: str, locale_pack: dict[str, Any]) -> bool:
    text = _normalize_space(value)
    if not text:
        return False
    lowered = text.lower()
    if any(term in lowered for term in _locale_terms(locale_pack, "boilerplate_name_fragments")):
        return True
    tokens = [tok.lower() for tok in _word_tokens(text)]
    if not tokens:
        return True
    if any(token in _locale_term_set(locale_pack, "office_name_terms") for token in tokens):
        return True
    if re.search(config.ALLOWED_ROLE_WORDS, text, re.I) or re.search(config.SENIORITY, text, re.I):
        return True
    return False


def _looks_like_person_name_fast(name: str, country: Optional[str] = None) -> bool:
    if not name:
        return False
    try:
        from gc_contacts.core.filtering import looks_like_person_name

        return bool(looks_like_person_name(name, country=country))
    except Exception:
        tokens = [t for t in re.split(r"[\s\-]+", name.strip()) if t]
        return len(tokens) >= 2 and sum(1 for token in tokens if token[:1].isupper()) >= 2


def clean_contact_name(value: str, country: Optional[str] = None) -> str:
    """
    Extract the best personal-name-looking span from a noisy text fragment.
    Returns an empty string when the fragment looks like a label, address,
    building, or office name.
    """
    text = _normalize_space(value)
    if not text:
        return ""
    locale_pack = _contact_locale(country)
    if _looks_address_like_name_for_locale(text, locale_pack) or _looks_office_or_unit_label_for_locale(text, locale_pack):
        return ""

    text = EMAIL_RE.sub(" ", text)
    text = re.sub(r"[_|/]+", " ", text)
    text = re.sub(
        r"\b(?:e-?mail|email|mail|tel|telephone|phone|fax|contact|contacts|responsabile|role|title|name)\b[:\-]?",
        " ",
        text,
        flags=re.I,
    )
    text = _normalize_space(text)
    tokens = _word_tokens(text)
    if len(tokens) < 2:
        return ""

    candidates: list[tuple[int, int, str]] = []
    for start in range(len(tokens)):
        for length in range(2, min(5, len(tokens) - start + 1)):
            window = tokens[start : start + length]
            if not _name_window_valid(window, locale_pack):
                continue
            visible_tokens = [
                tok
                for tok in window
                if tok.lower().strip(".,") not in _locale_term_set(locale_pack, "name_particles")
            ]
            score = len(visible_tokens) * 10
            if len(window) == 2:
                score += 4
            elif len(window) == 3:
                score += 2
            candidates.append((score, -start, " ".join(window)))

    if not candidates:
        return ""

    candidates.sort(reverse=True)
    candidate = _normalise_person_name_case(candidates[0][2])
    if _looks_address_like_name_for_locale(candidate, locale_pack) or _looks_office_or_unit_label_for_locale(candidate, locale_pack):
        return ""
    return candidate


def _score_role_line(line: str, role_keywords: Optional[List[str]] = None, country: Optional[str] = None) -> int:
    text = _normalize_space(line)
    if len(text) < 3:
        return -10
    if _looks_like_boilerplate_context_line(text, role_keywords=role_keywords, country=country):
        return -6

    locale_pack = _contact_locale(country)
    lowered = text.lower()
    score = 0
    if role_keywords and any(keyword.lower() in lowered for keyword in role_keywords):
        score += 5
    if re.search(config.ALLOWED_ROLE_WORDS, text, re.I):
        score += 4
    if re.search(config.SENIORITY, text, re.I):
        score += 2
    if re.search(config.INTL_HINTS, text, re.I):
        score += 4
    if any(term in lowered for term in _locale_terms(locale_pack, "role_signal_terms")):
        score += 4
    if any(term in lowered for term in _locale_terms(locale_pack, "office_role_terms")):
        score += 1
    if "@" in text:
        score -= 1
    if len(text) > 180:
        score -= 1
    return score


def clean_role_text(value: str, role_keywords: Optional[List[str]] = None, country: Optional[str] = None) -> str:
    """
    Clean and lightly normalize role/unit text.
    Keeps useful office labels for office contacts while suppressing boilerplate.
    """
    text = _normalize_space(value)
    if not text:
        return ""

    locale_pack = _contact_locale(country)
    text = EMAIL_RE.sub(" ", text)
    text = re.sub(r"\b(?:e-?mail|email|mail|tel|telephone|phone|fax)\b[:\-]?", " ", text, flags=re.I)
    text = re.sub(r"\s*[:|]\s*", " ", text)
    text = _normalize_space(text)
    if _looks_like_boilerplate_context_line(text, role_keywords=role_keywords, country=country):
        return ""
    lowered = text.lower()

    if len(text) > 200:
        text = text[:200]
        lowered = text.lower()

    for canonical, aliases in _locale_role_aliases(locale_pack).items():
        if any(alias in lowered for alias in aliases):
            return canonical
    if any(term in lowered for term in _locale_terms(locale_pack, "international_markers")) and any(
        term in lowered for term in _locale_terms(locale_pack, "office_markers")
    ):
        return "international office"
    if any(term in lowered for term in _locale_terms(locale_pack, "admissions_markers")) and any(
        term in lowered for term in _locale_terms(locale_pack, "international_markers")
    ):
        return "international admissions"
    if role_keywords:
        for keyword in role_keywords:
            if keyword.lower() in lowered:
                return _normalize_space(keyword)
    return text[:180]


def clean_contact_role(value: str, role_keywords: Optional[List[str]] = None, country: Optional[str] = None) -> str:
    return clean_role_text(value, role_keywords=role_keywords, country=country)


def _extract_role_from_lines(
    lines: List[str],
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> str:
    lines = _filtered_context_lines(lines, role_keywords=role_keywords, country=country)
    best_line = ""
    best_score = 0
    for line in lines:
        score = _score_role_line(line, role_keywords=role_keywords, country=country)
        if score > best_score:
            best_score = score
            best_line = line
    if best_score < 2:
        return ""
    return clean_role_text(best_line, role_keywords=role_keywords, country=country)


def _line_windows(text: str) -> List[tuple[int, int, str]]:
    windows: List[tuple[int, int, str]] = []
    cursor = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        line_len = len(raw_line)
        if line:
            leading_ws = len(raw_line) - len(raw_line.lstrip())
            start = cursor + leading_ws
            end = start + len(line)
            windows.append((start, end, line))
        cursor += line_len + 1
    return windows


def _line_index_for_offset(lines: List[tuple[int, int, str]], offset: int) -> int:
    for idx, (start, end, _) in enumerate(lines):
        if start <= offset <= end:
            return idx
    return 0


def _context_lines_around(lines: List[tuple[int, int, str]], offset: int, radius: int = 2) -> List[str]:
    if not lines:
        return []
    idx = _line_index_for_offset(lines, offset)
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    return [line for _, _, line in lines[start:end]]


def _best_name_from_lines(lines: List[str], email: str = "", country: Optional[str] = None) -> str:
    cleaned_lines = []
    for line in lines:
        normalized = _normalize_space(line)
        if not normalized:
            continue
        if _looks_like_boilerplate_context_line(normalized, country=country):
            continue
        if email:
            normalized = normalized.replace(email, " ")
            normalized = _normalize_space(normalized)
        if normalized and len(_word_tokens(normalized)) <= 8:
            cleaned_lines.append(normalized)

    best_name = ""
    best_score = -1
    for idx, line in enumerate(cleaned_lines):
        candidate = clean_contact_name(line, country=country)
        if not candidate:
            continue
        score = 10 - idx
        if len(candidate.split()) == 2:
            score += 2
        elif len(candidate.split()) >= 4:
            score -= 1
        if score > best_score:
            best_name = candidate
            best_score = score
    return best_name


def _best_context_text(element: Any) -> str:
    fallback = ""
    for parent in element.parents:
        if getattr(parent, "name", None) not in _ROLE_CONTEXT_TAGS:
            continue
        raw_text = parent.get_text("\n", strip=True)
        text = "\n".join(cleaned for cleaned in (_normalize_space(line) for line in raw_text.splitlines()) if cleaned)
        if not text:
            continue
        if not fallback:
            fallback = text
        if 40 <= len(text) <= 1200:
            return text
    return fallback


def _link_target_emails(value: str) -> list[str]:
    raw = unquote(str(value or "").strip())
    if not raw:
        return []
    emails: list[str] = []

    direct = normalize_email_value(raw)
    if direct:
        emails.append(direct)

    for match in re.finditer(r"mailto:([^\"'\s>]+)", raw, re.I):
        direct_match = normalize_email_value(match.group(1))
        if direct_match:
            emails.append(direct_match)

    parsed = urlparse(raw)
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        key_l = str(key or "").strip().lower()
        if any(hint in key_l for hint in _EMAIL_QUERY_PARAM_HINTS):
            direct_match = normalize_email_value(query_value)
            if direct_match:
                emails.append(direct_match)
            else:
                emails.extend(match.lower() for match in EMAIL_RE.findall(query_value))

    emails.extend(match.lower() for match in EMAIL_RE.findall(raw))
    return _unique_preserve_order(email for email in emails if normalize_email_value(email))


def _primary_strategy(source_strategies: List[str]) -> str:
    strategies = _unique_preserve_order(source_strategies)
    if not strategies:
        return ""
    return sorted(strategies, key=lambda item: _PRIMARY_STRATEGY_ORDER.get(item, 0), reverse=True)[0]


def _new_provisional_record(
    *,
    page_url: str,
    strategy: str,
    name: str = "",
    role: str = "",
    email: str = "",
    context: str = "",
    evidence_type: str = "",
) -> Dict[str, Any]:
    raw_name = _normalize_space(name)
    raw_role = _normalize_space(role)
    context_text = "\n".join(
        cleaned
        for cleaned in (_normalize_space(line) for line in str(context or "").splitlines())
        if cleaned
    )
    return {
        "name": raw_name,
        "role": raw_role,
        "email": _normalize_space(email),
        "page_url": str(page_url or "").strip(),
        "source_strategies": [strategy],
        "candidate_type": "",
        "evidence_type": evidence_type or strategy,
        "raw_name": raw_name,
        "raw_role": raw_role,
        "clean_name": "",
        "email_normalized": "",
        "cleanup_flags": [],
        "context": context_text,
        "source": strategy,
        "reached_filtering": False,
        "missing_email_candidate": False,
    }


def _embedded_key_matches(key: str, terms: tuple[str, ...]) -> bool:
    lowered = re.sub(r"[^a-z0-9]+", "", str(key or "").strip().lower())
    return any(term in lowered for term in terms)


def _emails_from_embedded_value(value: Any) -> list[str]:
    if value is None:
        return []
    text = _normalize_space(value)
    if not text:
        return []
    direct = normalize_email_value(text)
    if direct:
        return [direct]
    return _unique_preserve_order(match.lower() for match in EMAIL_RE.findall(text))


def _embedded_scalar_text(value: Any) -> str:
    if isinstance(value, str):
        return _normalize_space(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _normalize_space(value)
    return ""


def _first_embedded_scalar(record: Dict[str, Any], terms: tuple[str, ...]) -> str:
    for key, value in record.items():
        if not _embedded_key_matches(key, terms):
            continue
        text = _embedded_scalar_text(value)
        if text:
            return text
    return ""


def _embedded_context_text(record: Dict[str, Any], path: tuple[str, ...]) -> str:
    parts: list[str] = []
    path_hint = " > ".join(segment for segment in path[-3:] if segment)
    if path_hint:
        parts.append(path_hint)
    for key, value in record.items():
        if not (
            _embedded_key_matches(key, _EMBEDDED_NAME_KEYS)
            or _embedded_key_matches(key, _EMBEDDED_ROLE_KEYS)
            or _embedded_key_matches(key, _EMBEDDED_CONTEXT_KEYS)
            or _embedded_key_matches(key, _EMBEDDED_EMAIL_KEYS)
        ):
            continue
        text = _embedded_scalar_text(value)
        if not text:
            continue
        if len(text) > 240:
            text = text[:240]
        parts.append(f"{key}: {text}")
        if len(parts) >= 6:
            break
    return "\n".join(_unique_preserve_order(parts))


def _walk_embedded_records(
    node: Any,
    *,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
    path: tuple[str, ...] = (),
    seen: Optional[set[tuple[str, str, str]]] = None,
    limit: int = 48,
) -> list[Dict[str, Any]]:
    if seen is None:
        seen = set()
    if len(seen) >= limit:
        return []

    contacts: list[dict[str, Any]] = []
    if isinstance(node, dict):
        embedded_emails: list[str] = []
        for key, value in node.items():
            key_text = str(key or "")
            email_hits = _emails_from_embedded_value(value)
            if email_hits and (_embedded_key_matches(key_text, _EMBEDDED_EMAIL_KEYS) or any("@" in hit for hit in email_hits)):
                embedded_emails.extend(email_hits)

        if embedded_emails:
            raw_name = _first_embedded_scalar(node, _EMBEDDED_NAME_KEYS)
            raw_role = _first_embedded_scalar(node, _EMBEDDED_ROLE_KEYS)
            if not raw_role:
                raw_role = _extract_role_from_lines(
                    [line for line in _embedded_context_text(node, path).splitlines() if line.strip()],
                    role_keywords=role_keywords,
                    country=country,
                )
            context = _embedded_context_text(node, path)
            for email in embedded_emails:
                key = (email, raw_name.lower(), raw_role.lower())
                if key in seen:
                    continue
                seen.add(key)
                contacts.append(
                    _new_provisional_record(
                        page_url=page_url,
                        strategy="embedded_json",
                        name=raw_name,
                        role=raw_role,
                        email=email,
                        context=context,
                        evidence_type="embedded_json",
                    )
                )
                if len(seen) >= limit:
                    return contacts

        for key, value in node.items():
            if isinstance(value, (dict, list)):
                contacts.extend(
                    _walk_embedded_records(
                        value,
                        page_url=page_url,
                        role_keywords=role_keywords,
                        country=country,
                        path=path + (_normalize_space(key).lower(),),
                        seen=seen,
                        limit=limit,
                    )
                )
                if len(seen) >= limit:
                    return contacts
        return contacts

    if isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                contacts.extend(
                    _walk_embedded_records(
                        item,
                        page_url=page_url,
                        role_keywords=role_keywords,
                        country=country,
                        path=path,
                        seen=seen,
                        limit=limit,
                    )
                )
                if len(seen) >= limit:
                    return contacts
    return contacts


def _record_score(record: Dict[str, Any]) -> int:
    score = 0
    if record.get("email"):
        score += 12
    if record.get("raw_name"):
        score += 8
    if record.get("raw_role"):
        score += 5
    score += _PRIMARY_STRATEGY_ORDER.get(_primary_strategy(record.get("source_strategies", [])), 0)
    score += min(len(str(record.get("context", ""))), 240) // 60
    return score


def _cleanup_score(record: Dict[str, Any]) -> int:
    score = 0
    if record.get("email_normalized"):
        score += 12
    if record.get("clean_name"):
        score += 10
    if record.get("role"):
        score += 5
    score += len(record.get("source_strategies", []))
    if "generic_inbox" not in record.get("cleanup_flags", []):
        score += 1
    return score


def _assembly_key(record: Dict[str, Any]) -> tuple[Any, ...]:
    page_url = str(record.get("page_url", "")).strip()
    email = normalize_email_value(str(record.get("email", "")))
    raw_name = _normalize_space(record.get("raw_name") or record.get("name"))
    raw_role = _normalize_space(record.get("raw_role") or record.get("role"))
    if email:
        return ("email", email)
    if raw_name:
        return ("person", page_url, raw_name.lower(), raw_role.lower())
    if raw_role:
        return ("role", page_url, raw_role.lower())
    context = _normalize_space(record.get("context", ""))[:160].lower()
    return ("context", page_url, context)


def _cleanup_key(record: Dict[str, Any]) -> tuple[Any, ...]:
    email = str(record.get("email_normalized", "")).strip().lower()
    clean_name = str(record.get("clean_name", "")).strip().lower()
    role = str(record.get("role", "")).strip().lower()
    if email:
        return ("email", email)
    if clean_name:
        return ("person", clean_name, role)
    return ("fallback", str(record.get("page_url", "")), str(record.get("raw_name", "")).strip().lower(), role)


def _merge_record(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(existing)
    merged["source_strategies"] = _unique_preserve_order(
        list(existing.get("source_strategies", [])) + list(incoming.get("source_strategies", []))
    )
    merged["source"] = _primary_strategy(merged["source_strategies"])

    existing_score = _record_score(existing)
    incoming_score = _record_score(incoming)
    preferred = incoming if incoming_score > existing_score else existing
    fallback = existing if preferred is incoming else incoming

    for key in ("name", "role", "email", "raw_name", "raw_role", "context"):
        merged[key] = str(preferred.get(key) or fallback.get(key) or "").strip()

    merged["page_url"] = str(existing.get("page_url") or incoming.get("page_url") or "").strip()
    merged["evidence_type"] = str(preferred.get("evidence_type") or fallback.get("evidence_type") or "").strip()
    return merged


def _finalize_assembled_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    assembled = copy.deepcopy(candidate)
    assembled["source_strategies"] = _unique_preserve_order(list(candidate.get("source_strategies", [])))
    assembled["source"] = _primary_strategy(assembled["source_strategies"])
    assembled.setdefault("candidate_type", "")
    assembled.setdefault("clean_name", "")
    assembled.setdefault("email_normalized", "")
    assembled.setdefault("cleanup_flags", [])
    assembled.setdefault("reached_filtering", False)
    assembled.setdefault("missing_email_candidate", False)
    return assembled


def _decode_js_concatenated_emails(html: str) -> List[str]:
    emails = set()

    for match in re.finditer(
        r'["\']([A-Za-z0-9._%+-]+)["\']\s*\+\s*["\']@["\']\s*\+\s*["\']([A-Za-z0-9.-]+\.[A-Za-z]{2,})["\']',
        html,
    ):
        emails.add((match.group(1) + "@" + match.group(2)).lower())

    for match in re.finditer(
        r'["\']([A-Za-z0-9._%+-]+)["\']\s*\+\s*["\']@["\']\s*\+\s*["\']([A-Za-z0-9.-]+)["\']\s*\+\s*["\']\.([A-Za-z]{2,})["\']',
        html,
    ):
        emails.add((match.group(1) + "@" + match.group(2) + "." + match.group(3)).lower())

    return sorted({email for email in emails if EMAIL_RE.fullmatch(email)})


def decode_js_emails(html: str) -> List[str]:
    """
    Legacy helper.
    Keeps JS concatenation decoding and explicit structured attribute recovery.
    """
    emails = set(_decode_js_concatenated_emails(html))

    try:
        soup = BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html, "html.parser")

    for element in soup.select("[data-user][data-domain]"):
        email = normalize_email_value(f"{element.get('data-user', '')}@{element.get('data-domain', '')}")
        if email:
            emails.add(email)

    for attr_name in _STRUCTURED_EMAIL_ATTRS:
        for element in soup.select(f"[{attr_name}]"):
            email = normalize_email_value(element.get(attr_name, ""))
            if email:
                emails.add(email)

    return sorted(emails)


def extract_mailto_contacts(
    html: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Extract provisional contact evidence from explicit mailto links.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html, "html.parser")

    contacts: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in soup.select("a[href], [onclick]"):
        raw_targets = [
            str(anchor.get("href", "") or "").strip(),
            str(anchor.get("onclick", "") or "").strip(),
        ]
        raw_addresses: list[str] = []
        for raw_target in raw_targets:
            raw_addresses.extend(_link_target_emails(raw_target))
        if not raw_addresses:
            continue
        context_text = _best_context_text(anchor)
        context_lines = [
            line
            for line in clean_context_text(context_text, role_keywords=role_keywords, country=country, max_lines=8).splitlines()
            if line.strip()
        ]
        role = _extract_role_from_lines(context_lines, role_keywords=role_keywords, country=country)
        anchor_text = _normalize_space(anchor.get_text(" ", strip=True))
        anchor_name = clean_contact_name(anchor_text, country=country)
        for raw_email in raw_addresses:
            email = normalize_email_value(raw_email)
            if not email:
                continue
            key = (email, str(anchor))
            if key in seen:
                continue
            seen.add(key)
            name = anchor_name or _best_name_from_lines(context_lines[:8], email=email, country=country)
            contacts.append(
                _new_provisional_record(
                    page_url=page_url,
                    strategy="mailto_explicit",
                    name=name,
                    role=role,
                    email=email,
                    context="\n".join(context_lines[:10]),
                    evidence_type="mailto",
                )
            )
    return contacts


def detect_potential_anchor_patterns(
    html: str,
    page_url: str,
    extracted_emails: Optional[List[str]] = None,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    soup = _parse_markup_document(html)

    extracted = {normalize_email_value(email) for email in (extracted_emails or []) if normalize_email_value(email)}
    findings: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add_finding(pattern_type: str, details: str, *, url: str = "", attr_name: str = "", snippet: str = "") -> None:
        normalized_details = _normalize_space(details)[:220]
        normalized_snippet = _normalize_space(snippet)[:220]
        key = (pattern_type, normalized_details or normalized_snippet or attr_name)
        if not (normalized_details or normalized_snippet or attr_name) or key in seen or len(findings) >= limit:
            return
        seen.add(key)
        findings.append(
            {
                "pattern_type": pattern_type,
                "page_url": page_url,
                "url": url,
                "attr_name": attr_name,
                "details": normalized_details,
                "snippet": normalized_snippet,
                "already_extracted_email": any(email in normalized_details.lower() for email in extracted if email),
            }
        )

    for anchor in soup.select("a[href], [onclick]"):
        href = str(anchor.get("href", "") or "").strip()
        onclick = str(anchor.get("onclick", "") or "").strip()
        anchor_text = _normalize_space(anchor.get_text(" ", strip=True))
        parsed_emails = set(_link_target_emails(href)) | set(_link_target_emails(onclick))

        if href.lower().startswith("javascript:") and not parsed_emails:
            lowered = href.lower()
            if any(token in lowered for token in ("mail", "contact", "recipient")):
                add_finding(
                    "javascript_contact_anchor",
                    href,
                    url=href,
                    snippet=anchor_text or _best_context_text(anchor),
                )

        if onclick and not parsed_emails:
            lowered = onclick.lower()
            if any(token in lowered for token in ("mail", "contact", "recipient")):
                add_finding(
                    "onclick_contact_handler",
                    onclick,
                    attr_name="onclick",
                    snippet=anchor_text or _best_context_text(anchor),
                )

        for raw_target in filter(None, (href, onclick)):
            parsed = urlparse(raw_target)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                key_lower = key.strip().lower()
                if key_lower not in _POTENTIAL_ANCHOR_PARAM_HINTS:
                    continue
                value_text = unquote(str(value or "")).strip()
                if normalize_email_value(value_text):
                    continue
                add_finding(
                    "contact_param_link",
                    f"{key}={value_text}",
                    url=raw_target,
                    attr_name=key,
                    snippet=anchor_text or _best_context_text(anchor),
                )

    for element in soup.select("*"):
        for attr_name, attr_value in list(getattr(element, "attrs", {}).items()):
            attr_name_str = str(attr_name or "").strip().lower()
            if not attr_name_str.startswith("data-") or attr_name_str in _STRUCTURED_EMAIL_ATTRS:
                continue
            if not any(token in attr_name_str for token in ("mail", "email", "recipient", "contact")):
                continue
            if isinstance(attr_value, list):
                value_text = " ".join(str(item) for item in attr_value)
            else:
                value_text = str(attr_value or "")
            if normalize_email_value(value_text):
                continue
            add_finding(
                "data_attribute_contact_hint",
                f"{attr_name_str}={value_text}",
                attr_name=attr_name_str,
                snippet=_best_context_text(element),
            )

    for form in soup.select("form[action]"):
        action = str(form.get("action", "") or "").strip()
        parsed = urlparse(action)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            key_lower = key.strip().lower()
            if key_lower not in _POTENTIAL_ANCHOR_PARAM_HINTS:
                continue
            value_text = unquote(str(value or "")).strip()
            if normalize_email_value(value_text):
                continue
            add_finding(
                "contact_form_param",
                f"{key}={value_text}",
                url=action,
                attr_name=key,
                snippet=_best_context_text(form),
            )

    for element in soup.select(".h-card, .vcard, .contact-card, [itemtype*='schema.org/Person'], [itemtype*='schema.org/Organization']"):
        add_finding(
            "structured_contact_markup",
            _normalize_space(element.get("itemtype", "") or "structured contact markup"),
            attr_name="class",
            snippet=_best_context_text(element),
        )

    return findings


def extract_html_attribute_contacts(
    html: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Narrow deterministic recovery from explicit structured HTML attributes.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html, "html.parser")

    contacts: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_record(element: Any, email_value: str) -> None:
        email = normalize_email_value(email_value)
        if not email:
            return
        context_text = _best_context_text(element) or _normalize_space(element.get_text(" ", strip=True))
        context_lines = [
            line
            for line in clean_context_text(context_text, role_keywords=role_keywords, country=country, max_lines=8).splitlines()
            if line.strip()
        ]
        raw_name = ""
        raw_role = ""
        for attr_name in _STRUCTURED_NAME_ATTRS:
            raw_name = _normalize_space(element.get(attr_name, ""))
            if raw_name:
                break
        for attr_name in _STRUCTURED_ROLE_ATTRS:
            raw_role = _normalize_space(element.get(attr_name, ""))
            if raw_role:
                break
        if not raw_role:
            raw_role = _extract_role_from_lines(context_lines, role_keywords=role_keywords, country=country)
        if not raw_name:
            raw_name = clean_contact_name(_normalize_space(element.get_text(" ", strip=True)), country=country)
        key = (email, raw_name.lower(), raw_role.lower())
        if key in seen:
            return
        seen.add(key)
        contacts.append(
            _new_provisional_record(
                page_url=page_url,
                strategy="html_attribute",
                name=raw_name,
                role=raw_role,
                email=email,
                context="\n".join(context_lines[:10]),
                evidence_type="html_attribute",
            )
        )

    for element in soup.select("[data-user][data-domain]"):
        add_record(element, f"{element.get('data-user', '')}@{element.get('data-domain', '')}")

    for attr_name in _STRUCTURED_EMAIL_ATTRS:
        for element in soup.select(f"[{attr_name}]"):
            add_record(element, element.get(attr_name, ""))

    return contacts


def extract_embedded_json_contacts(
    html: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for document in iter_embedded_json_documents(html):
        contacts.extend(
            _walk_embedded_records(
                document,
                page_url=page_url,
                role_keywords=role_keywords,
                country=country,
                seen=seen,
            )
        )
        if len(seen) >= 48:
            break
    return contacts


def extract_js_contacts(html: str, page_url: str) -> List[Dict[str, Any]]:
    """
    Recover only full emails from explicit JavaScript string concatenation.
    """
    return [
        _new_provisional_record(
            page_url=page_url,
            strategy="js_decode",
            email=email,
            evidence_type="js_decode",
        )
        for email in _decode_js_concatenated_emails(html)
    ]


def extract_visible_regex_contacts(
    text: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Extract contacts from visible text email matches only.
    """
    contacts: List[Dict[str, Any]] = []
    line_windows = _line_windows(text)
    for match in EMAIL_RE.finditer(text):
        email = match.group(0).lower()
        near_name_lines = _context_lines_around(line_windows, match.start(), radius=2)
        near_role_lines = _context_lines_around(line_windows, match.start(), radius=5)
        filtered_role_lines = _filtered_context_lines(near_role_lines, role_keywords=role_keywords, country=country)
        filtered_name_lines = _filtered_context_lines(near_name_lines, country=country) or near_name_lines
        context_lines = (filtered_role_lines or filtered_name_lines or near_role_lines)[:6]
        contacts.append(
            _new_provisional_record(
                page_url=page_url,
                strategy="visible_regex",
                name=_best_name_from_lines(filtered_name_lines, email=email, country=country),
                role=_extract_role_from_lines(filtered_role_lines or near_role_lines, role_keywords=role_keywords, country=country),
                email=email,
                context="\n".join(context_lines),
                evidence_type="visible_text",
            )
        )
    return contacts


def deobfuscate(text: str) -> List[str]:
    """
    Find explicitly obfuscated emails such as 'name [at] domain [dot] com'.
    Deliberately ignores loose prose so it does not invent addresses.
    """
    emails = set()
    for match in EXPLICIT_OBFUSCATED_EMAIL_RE.finditer(text):
        candidate = match.group(0)
        candidate = re.sub(r"\[\s*at\s*\]|\(\s*at\s*\)|\s+at\s+", "@", candidate, flags=re.I)
        candidate = re.sub(r"\[\s*dot\s*\]|\(\s*dot\s*\)|\s+dot\s+", ".", candidate, flags=re.I)
        candidate = re.sub(r"\s+", "", candidate)
        if EMAIL_RE.fullmatch(candidate):
            emails.add(candidate.lower())
    return sorted(emails)


def extract_explicit_obfuscated_contacts(
    text: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Recover emails only from clear [at]/(at)/at + [dot]/(dot)/dot patterns.
    """
    contacts: List[Dict[str, Any]] = []
    line_windows = _line_windows(text)

    for match in EXPLICIT_OBFUSCATED_EMAIL_RE.finditer(text):
        raw_candidate = match.group(0)
        email = raw_candidate
        email = re.sub(r"\[\s*at\s*\]|\(\s*at\s*\)|\s+at\s+", "@", email, flags=re.I)
        email = re.sub(r"\[\s*dot\s*\]|\(\s*dot\s*\)|\s+dot\s+", ".", email, flags=re.I)
        email = re.sub(r"\s+", "", email)
        email = normalize_email_value(email)
        if not email:
            continue
        near_name_lines = _context_lines_around(line_windows, match.start(), radius=2)
        near_role_lines = _context_lines_around(line_windows, match.start(), radius=5)
        filtered_role_lines = _filtered_context_lines(near_role_lines, role_keywords=role_keywords, country=country)
        filtered_name_lines = _filtered_context_lines(near_name_lines, country=country) or near_name_lines
        context_lines = (filtered_role_lines or filtered_name_lines or near_role_lines)[:6]
        contacts.append(
            _new_provisional_record(
                page_url=page_url,
                strategy="explicit_obfuscation",
                name=_best_name_from_lines(filtered_name_lines, email=email, country=country),
                role=_extract_role_from_lines(filtered_role_lines or near_role_lines, role_keywords=role_keywords, country=country),
                email=email,
                context="\n".join(context_lines),
                evidence_type="explicit_obfuscation",
            )
        )
    return contacts


async def extract_llm_contacts(
    text: str,
    page_url: str,
    allow_generic_emails: bool,
    llm_extractor: Optional[Callable[[str, str, bool], Awaitable[List[Dict[str, Any]]]]] = None,
) -> List[Dict[str, Any]]:
    """
    GPT extraction as one evidence strategy among several.
    """
    if llm_extractor is None:
        return []

    try:
        llm_contacts = await llm_extractor(text, page_url, allow_generic_emails)
    except Exception:
        return []

    output: List[Dict[str, Any]] = []
    for item in llm_contacts or []:
        output.append(
            _new_provisional_record(
                page_url=str(item.get("page_url", page_url)).strip() or page_url,
                strategy="llm_structured",
                name=str(item.get("name", "")),
                role=str(item.get("role", "")),
                email=str(item.get("email", "")),
                context=str(item.get("context", "")),
                evidence_type=str(item.get("evidence_type", "llm_structured")),
            )
        )
    return output


def raw_evidence_extraction(
    html: str,
    text: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Deterministic evidence collection stages that do not need the LLM.
    """
    evidence: list[dict[str, Any]] = []
    evidence.extend(extract_mailto_contacts(html, page_url, role_keywords=role_keywords, country=country))
    evidence.extend(extract_visible_regex_contacts(text, page_url, role_keywords=role_keywords, country=country))
    evidence.extend(extract_html_attribute_contacts(html, page_url, role_keywords=role_keywords, country=country))
    evidence.extend(extract_embedded_json_contacts(html, page_url, role_keywords=role_keywords, country=country))
    evidence.extend(extract_js_contacts(html, page_url))
    evidence.extend(extract_explicit_obfuscated_contacts(text, page_url, role_keywords=role_keywords, country=country))
    return evidence


async def raw_evidence_extraction_with_llm(
    html: str,
    text: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
    allow_generic_emails: bool = False,
    llm_extractor: Optional[Callable[[str, str, bool], Awaitable[List[Dict[str, Any]]]]] = None,
) -> List[Dict[str, Any]]:
    evidence = raw_evidence_extraction(html, text, page_url, role_keywords=role_keywords, country=country)
    evidence.extend(await extract_llm_contacts(text, page_url, allow_generic_emails, llm_extractor=llm_extractor))
    return evidence


def candidate_assembly(raw_evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: dict[tuple[Any, ...], Dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []

    for record in raw_evidence:
        candidate = _finalize_assembled_candidate(record)
        key = _assembly_key(candidate)
        if key not in grouped:
            grouped[key] = candidate
            order.append(key)
            continue
        grouped[key] = _merge_record(grouped[key], candidate)

    return [_finalize_assembled_candidate(grouped[key]) for key in order]


def _name_flags(raw_name: str, clean_name: str, country: Optional[str] = None) -> List[str]:
    locale_pack = _contact_locale(country)
    flags: list[str] = []
    if raw_name and not clean_name and _looks_address_like_name_for_locale(raw_name, locale_pack):
        flags.append("address_like_name")
    if raw_name and not clean_name and _looks_office_or_unit_label_for_locale(raw_name, locale_pack):
        flags.append("office_label_name")
    return flags


def _role_looks_relevant_office(
    role: str,
    raw_role: str = "",
    context: str = "",
    email: str = "",
    country: Optional[str] = None,
) -> bool:
    locale_pack = _contact_locale(country)
    haystack = " ".join(part for part in (role, raw_role, context) if part).lower()
    if any(term in haystack for term in _locale_terms(locale_pack, "office_role_terms")):
        return True
    local = str(email or "").split("@", 1)[0].lower()
    if GENERIC_EMAIL.match(local):
        return True
    return False


def _looks_close_to_person_name(value: str, country: Optional[str] = None) -> bool:
    text = _normalize_space(value)
    if not text or len(text) > 80:
        return False
    locale_pack = _contact_locale(country)
    if _looks_address_like_name_for_locale(text, locale_pack) or _looks_office_or_unit_label_for_locale(text, locale_pack):
        return False
    tokens = _word_tokens(text)
    if not 2 <= len(tokens) <= 6:
        return False
    titleish = sum(1 for token in tokens if _is_titleish_name_token(token))
    return titleish >= max(2, len(tokens) - 1)


def _should_attempt_llm_name_cleanup(candidate: Dict[str, Any], country: Optional[str] = None) -> bool:
    raw_name = str(candidate.get("raw_name", "")).strip()
    clean_name = str(candidate.get("clean_name", "")).strip()
    email = str(candidate.get("email_normalized", "")).strip()
    if not raw_name or clean_name:
        return False
    locale_pack = _contact_locale(country)
    if _looks_address_like_name_for_locale(raw_name, locale_pack) or _looks_office_or_unit_label_for_locale(raw_name, locale_pack):
        return False
    if "@" in raw_name or len(raw_name) > 80:
        return False
    if email:
        if GENERIC_EMAIL.match(email.split("@", 1)[0]):
            return False
        return _looks_close_to_person_name(raw_name, country=country)
    return _looks_close_to_person_name(raw_name, country=country)


async def _maybe_llm_clean_name(
    candidate: Dict[str, Any],
    llm_name_cleaner: Optional[Callable[[str, str, str, str], Awaitable[str]]] = None,
    country: Optional[str] = None,
) -> str:
    if llm_name_cleaner is None or not _should_attempt_llm_name_cleanup(candidate, country=country):
        return str(candidate.get("clean_name", "")).strip()

    cache_key = (
        str(candidate.get("raw_name", "")).strip(),
        str(candidate.get("raw_role", "")).strip(),
        str(candidate.get("email_normalized", "")).strip(),
        str(candidate.get("page_url", "")).strip(),
    )
    if cache_key in _NAME_CLEAN_CACHE:
        return _NAME_CLEAN_CACHE[cache_key]

    try:
        cleaned = await llm_name_cleaner(
            cache_key[0],
            cache_key[1],
            cache_key[2],
            cache_key[3],
        )
    except Exception:
        cleaned = ""

    cleaned = _normalize_space(cleaned)
    if not _looks_like_person_name_fast(cleaned, country=country):
        cleaned = ""
    _NAME_CLEAN_CACHE[cache_key] = cleaned
    return cleaned


async def candidate_cleanup(
    assembled_candidates: List[Dict[str, Any]],
    role_keywords: Optional[List[str]] = None,
    llm_name_cleaner: Optional[Callable[[str, str, str, str], Awaitable[str]]] = None,
    country: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cleaned_pre_dedupe: list[dict[str, Any]] = []

    for candidate in assembled_candidates:
        cleaned = copy.deepcopy(candidate)
        cleaned["raw_name"] = _normalize_space(candidate.get("raw_name") or candidate.get("name"))
        cleaned["raw_role"] = _normalize_space(candidate.get("raw_role") or candidate.get("role"))
        cleaned["email"] = _normalize_space(candidate.get("email", ""))
        cleaned["email_normalized"] = normalize_email_value(cleaned["email"])
        cleaned["clean_name"] = clean_contact_name(cleaned["raw_name"], country=country)
        if not cleaned["clean_name"] and cleaned["email_normalized"]:
            inferred_name = _infer_name_from_email_localpart(cleaned["email_normalized"], country=country)
            if inferred_name:
                cleaned["clean_name"] = inferred_name
                cleaned["raw_name"] = inferred_name
        cleaned["name"] = cleaned["clean_name"]
        cleaned["role"] = clean_role_text(cleaned["raw_role"], role_keywords=role_keywords, country=country)
        cleaned_context = clean_context_text(str(candidate.get("context", "")), role_keywords=role_keywords, country=country)
        cleaned["context"] = cleaned_context
        cleaned["cleanup_flags"] = _unique_preserve_order(
            list(candidate.get("cleanup_flags", [])) + _name_flags(cleaned["raw_name"], cleaned["clean_name"], country=country)
        )
        if cleaned["clean_name"] and not _normalize_space(candidate.get("raw_name") or candidate.get("name")):
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["name_inferred_from_email_localpart"])
        if str(candidate.get("context", "")).strip() and cleaned_context != _normalize_space(str(candidate.get("context", ""))):
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["context_sanitized"])
        if str(candidate.get("context", "")).strip() and not cleaned_context:
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["context_sanitized_empty"])

        if cleaned["email"] and not cleaned["email_normalized"]:
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["invalid_email_shape"])
        if cleaned["email_normalized"] and GENERIC_EMAIL.match(cleaned["email_normalized"].split("@", 1)[0]):
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["generic_inbox"])
        if "explicit_obfuscation" in cleaned.get("source_strategies", []):
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["obfuscation_recovered"])
        if cleaned.get("source_strategies", []) == ["mailto_explicit"]:
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["mailto_only"])

        llm_cleaned_name = await _maybe_llm_clean_name(cleaned, llm_name_cleaner=llm_name_cleaner, country=country)
        if llm_cleaned_name:
            cleaned["clean_name"] = llm_cleaned_name
            cleaned["name"] = llm_cleaned_name

        if (
            "llm_structured" in cleaned.get("source_strategies", [])
            and not cleaned["email_normalized"]
            and _looks_like_person_name_fast(cleaned["clean_name"], country=country)
        ):
            cleaned["cleanup_flags"] = _unique_preserve_order(cleaned["cleanup_flags"] + ["llm_named_without_email"])

        if not cleaned["name"]:
            cleaned["name"] = ""
        if not cleaned["role"]:
            cleaned["role"] = ""
        cleaned["source"] = _primary_strategy(cleaned.get("source_strategies", []))
        cleaned_pre_dedupe.append(cleaned)

    groups: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = {}
    for idx, candidate in enumerate(cleaned_pre_dedupe):
        groups.setdefault(_cleanup_key(candidate), []).append((idx, candidate))

    kept_indices: set[int] = set()
    merged_best_by_index: dict[int, dict[str, Any]] = {}
    final_cleaned: list[dict[str, Any]] = []
    for items in groups.values():
        items_sorted = sorted(items, key=lambda item: (_cleanup_score(item[1]), -item[0]), reverse=True)
        best_idx, best_candidate = items_sorted[0]
        merged_best = copy.deepcopy(best_candidate)
        kept_indices.add(best_idx)
        for _, duplicate in items_sorted[1:]:
            merged_best["source_strategies"] = _unique_preserve_order(
                list(merged_best.get("source_strategies", [])) + list(duplicate.get("source_strategies", []))
            )
            merged_best["cleanup_flags"] = _unique_preserve_order(
                list(merged_best.get("cleanup_flags", [])) + list(duplicate.get("cleanup_flags", []))
            )
            if not merged_best.get("raw_role") and duplicate.get("raw_role"):
                merged_best["raw_role"] = duplicate["raw_role"]
            if not merged_best.get("role") and duplicate.get("role"):
                merged_best["role"] = duplicate["role"]
            if not merged_best.get("context") and duplicate.get("context"):
                merged_best["context"] = duplicate["context"]
        merged_best["source"] = _primary_strategy(merged_best.get("source_strategies", []))
        merged_best_by_index[best_idx] = merged_best
        final_cleaned.append(merged_best)

    cleaned_trace: list[dict[str, Any]] = []
    for idx, candidate in enumerate(cleaned_pre_dedupe):
        item = copy.deepcopy(merged_best_by_index[idx] if idx in merged_best_by_index else candidate)
        if idx not in kept_indices:
            item["cleanup_flags"] = _unique_preserve_order(list(item.get("cleanup_flags", [])) + ["duplicate_candidate"])
        cleaned_trace.append(item)

    return final_cleaned, cleaned_trace


def _email_matches_name(email: str, name: str, country: Optional[str] = None) -> bool:
    email = str(email or "").strip().lower()
    name = str(name or "").strip()
    if not email or "@" not in email or not name:
        return False

    local = email.split("@", 1)[0]
    if GENERIC_EMAIL.match(local):
        return False

    tokens = _matchable_name_tokens(name, country=country)
    if len(tokens) < 2:
        return False

    first = tokens[0]
    last = tokens[-1]
    patterns = {
        first,
        last,
        f"{first}.{last}",
        f"{first}_{last}",
        f"{first}-{last}",
        f"{first}{last}",
        f"{first[:1]}.{last}",
        f"{first[:1]}{last}",
        f"{last}.{first}",
    }
    return any(pattern and pattern in local for pattern in patterns)


def classify_candidate_type(candidate: Dict[str, Any], country: Optional[str] = None) -> str:
    email_valid = bool(candidate.get("email_normalized")) and "invalid_email_shape" not in candidate.get("cleanup_flags", [])
    clean_name = str(candidate.get("clean_name", "")).strip()
    person_name_valid = _looks_like_person_name_fast(clean_name, country=country)
    generic_email = "generic_inbox" in candidate.get("cleanup_flags", [])
    office_relevant = _role_looks_relevant_office(
        str(candidate.get("role", "")),
        raw_role=str(candidate.get("raw_role", "")),
        context=str(candidate.get("context", "")),
        email=str(candidate.get("email_normalized", "")),
        country=country,
    )

    if email_valid and person_name_valid and not generic_email and _email_matches_name(
        candidate["email_normalized"],
        clean_name,
        country=country,
    ):
        return "named_contact"
    if email_valid and not person_name_valid and (office_relevant or generic_email):
        return "office_contact"
    if email_valid and person_name_valid and (office_relevant or generic_email):
        return "office_contact"
    if person_name_valid and not email_valid:
        return "person_without_email"
    return "junk_candidate"


def candidate_typing(cleaned_candidates: List[Dict[str, Any]], country: Optional[str] = None) -> List[Dict[str, Any]]:
    typed: list[dict[str, Any]] = []

    for candidate in cleaned_candidates:
        email = str(candidate.get("email_normalized", "")).strip()
        clean_name = str(candidate.get("clean_name", "")).strip()
        person_name_valid = _looks_like_person_name_fast(clean_name, country=country)
        generic_email = "generic_inbox" in candidate.get("cleanup_flags", [])
        name_matches_email = _email_matches_name(email, clean_name, country=country) if email and clean_name else False

        if email and clean_name and person_name_valid and (generic_email or not name_matches_email):
            office_candidate = copy.deepcopy(candidate)
            office_candidate["name"] = ""
            office_candidate["clean_name"] = ""
            office_candidate["candidate_type"] = classify_candidate_type(office_candidate, country=country)
            typed.append(office_candidate)

            person_candidate = copy.deepcopy(candidate)
            person_candidate["email"] = ""
            person_candidate["email_normalized"] = ""
            person_candidate["name"] = clean_name
            person_candidate["candidate_type"] = "person_without_email"
            typed.append(person_candidate)
            continue

        typed_candidate = copy.deepcopy(candidate)
        typed_candidate["name"] = clean_name
        typed_candidate["email"] = email
        typed_candidate["candidate_type"] = classify_candidate_type(typed_candidate, country=country)
        if typed_candidate["candidate_type"] == "office_contact":
            typed_candidate["name"] = ""
            typed_candidate["clean_name"] = ""
        typed.append(typed_candidate)

    return typed


def _count_by_strategy(raw_evidence: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {strategy: 0 for strategy in RAW_EXTRACTION_STRATEGIES}
    for record in raw_evidence:
        for strategy in record.get("source_strategies", []):
            counts[str(strategy)] = counts.get(str(strategy), 0) + 1
    return counts


async def run_contact_extraction_pipeline(
    html: str,
    text: str,
    page_url: str,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
    allow_generic_emails: bool = False,
    llm_extractor: Optional[Callable[[str, str, bool], Awaitable[List[Dict[str, Any]]]]] = None,
    llm_name_cleaner: Optional[Callable[[str, str, str, str], Awaitable[str]]] = None,
) -> Dict[str, Any]:
    raw_evidence = await raw_evidence_extraction_with_llm(
        html,
        text,
        page_url,
        role_keywords=role_keywords,
        country=country,
        allow_generic_emails=allow_generic_emails,
        llm_extractor=llm_extractor,
    )
    assembled_candidates = candidate_assembly(raw_evidence)
    extracted_emails = [
        str(record.get("email", "")).strip()
        for record in raw_evidence
        if str(record.get("email", "")).strip()
    ]
    potential_anchor_patterns = detect_potential_anchor_patterns(
        html,
        page_url,
        extracted_emails=extracted_emails,
    )
    cleaned_candidates, cleaned_trace = await candidate_cleanup(
        assembled_candidates,
        role_keywords=role_keywords,
        llm_name_cleaner=llm_name_cleaner,
        country=country,
    )
    typed_candidates = candidate_typing(cleaned_candidates, country=country)

    named_contacts = [candidate for candidate in typed_candidates if candidate.get("candidate_type") == "named_contact"]
    office_contacts = [candidate for candidate in typed_candidates if candidate.get("candidate_type") == "office_contact"]
    person_without_email = [
        candidate for candidate in typed_candidates if candidate.get("candidate_type") == "person_without_email"
    ]
    junk_candidates = [candidate for candidate in typed_candidates if candidate.get("candidate_type") == "junk_candidate"]

    return {
        "raw_evidence": raw_evidence,
        "assembled_candidates": assembled_candidates,
        "cleaned_candidates": cleaned_trace,
        "typed_candidates": typed_candidates,
        "candidates_for_filtering": named_contacts + office_contacts,
        "named_contacts": named_contacts,
        "office_contacts": office_contacts,
        "missing_email_candidates": person_without_email,
        "junk_candidates": junk_candidates,
        "raw_evidence_count_by_strategy": _count_by_strategy(raw_evidence),
        "potential_anchor_patterns": potential_anchor_patterns,
        "potential_anchor_pattern_count": len(potential_anchor_patterns),
        "assembled_candidate_count": len(assembled_candidates),
        "clean_candidate_count": len(cleaned_candidates),
        "named_contact_count": len(named_contacts),
        "office_contact_count": len(office_contacts),
        "person_without_email_count": len(person_without_email),
        "junk_candidate_count": len(junk_candidates),
    }


def simple_regex_contacts(
    text: str,
    page_url: str,
    extra_emails: Optional[List[str]] = None,
    role_keywords: Optional[List[str]] = None,
    country: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Backward-compatible regex extraction helper used by legacy paths.
    """
    contacts: List[Dict[str, str]] = []
    seen_text_emails: set[str] = set()

    for item in extract_visible_regex_contacts(text, page_url, role_keywords=role_keywords, country=country):
        email = str(item.get("email", "")).lower()
        if email:
            seen_text_emails.add(email)
        contacts.append(
            {
                "role": str(item.get("role", "")),
                "name": str(item.get("name", "")),
                "email": email,
                "page_url": page_url,
                "context": str(item.get("context", "")),
                "source": "regex",
            }
        )

    for email in extra_emails or []:
        normalized = normalize_email_value(email)
        if not normalized:
            continue
        contacts.append(
            {
                "role": "",
                "name": "",
                "email": normalized,
                "page_url": page_url,
                "context": "",
                "source": "decoded_js",
            }
        )

    for item in extract_explicit_obfuscated_contacts(text, page_url, role_keywords=role_keywords, country=country):
        email = str(item.get("email", "")).lower()
        if email in seen_text_emails:
            continue
        contacts.append(
            {
                "role": str(item.get("role", "")),
                "name": str(item.get("name", "")),
                "email": email,
                "page_url": page_url,
                "context": str(item.get("context", "")),
                "source": "deobfuscate",
            }
        )

    return contacts
