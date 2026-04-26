"""
Contact validation and filtering logic.
"""

import re
from typing import Tuple, Optional

import gc_contacts.config as config
from gc_contacts.core.extraction import GENERIC_EMAIL
from gc_contacts.localisation import get_country_contact_pack


def role_score(role: str, extra_positive: Optional[list] = None, extra_negative: Optional[list] = None) -> int:
    """
    Score a role string for relevance.

    Args:
        role:           Role/title text.
        extra_positive: Profile-injected positive keywords.
        extra_negative: Profile-injected negative keywords (reduces score).
    """
    r = role.lower()
    base = 0
    if re.search(config.ALLOWED_ROLE_WORDS, r):
        base += 5
    if re.search(config.SENIORITY, r):
        base += 3
    if any(k in r for k in [
        "director", "head", "chancellor", "president", "rector",
        "provost", "vice-chancellor", "vice president", "vice-president"
    ]):
        base += 4
    if extra_positive:
        for kw in extra_positive:
            if kw.lower() in r:
                base += 3
    if extra_negative:
        for kw in extra_negative:
            if kw.lower() in r:
                base -= 4
    return base


def _candidate_type(contact: dict) -> str:
    return str(contact.get("candidate_type", "") or "").strip().lower()


def _cleanup_flags(contact: dict) -> set[str]:
    return {str(flag or "").strip().lower() for flag in contact.get("cleanup_flags", []) if str(flag or "").strip()}


_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['`.-][^\W\d_]+)*", re.UNICODE)


def _contact_locale(country: Optional[str] = None) -> dict:
    return get_country_contact_pack(country)


def _locale_terms(locale_pack: dict, key: str) -> tuple[str, ...]:
    return tuple(
        str(item or "").strip().lower()
        for item in locale_pack.get(key, [])
        if str(item or "").strip()
    )


def _locale_term_set(locale_pack: dict, key: str) -> set[str]:
    return set(_locale_terms(locale_pack, key))


def _word_tokens(value: str) -> list[str]:
    return [token for token in _WORD_TOKEN_RE.findall(str(value or "").strip()) if token]


def _is_name_like_token(token: str) -> bool:
    token = str(token or "").strip(".,")
    if not token:
        return False
    alpha_chars = [ch for ch in token if ch.isalpha()]
    if not alpha_chars:
        return False
    if not any(ch.islower() or ch.isupper() for ch in alpha_chars):
        return True
    first = alpha_chars[0]
    rest = alpha_chars[1:]
    return first.isupper() and (not rest or any(ch.islower() for ch in rest))


def _normalized_localpart(email: str) -> str:
    local = str(email or "").split("@", 1)[0].lower()
    return re.sub(r"[^a-z0-9]+", "", local)


def _name_has_suspicious_signals(name: str, country: Optional[str] = None) -> bool:
    locale_pack = _contact_locale(country)
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    if any(phrase in lowered for phrase in _locale_terms(locale_pack, "suspicious_name_phrases")):
        return True
    tokens = [tok.lower() for tok in _word_tokens(lowered)]
    return any(token in _locale_term_set(locale_pack, "suspicious_name_terms") for token in tokens)


def _localpart_has_relevant_signal(email: str, country: Optional[str] = None) -> bool:
    locale_pack = _contact_locale(country)
    local = _normalized_localpart(email)
    return any(term in local for term in _locale_terms(locale_pack, "relevant_mailbox_terms"))


def _localpart_has_irrelevant_signal(email: str, country: Optional[str] = None) -> bool:
    locale_pack = _contact_locale(country)
    local = _normalized_localpart(email)
    return any(term in local for term in _locale_terms(locale_pack, "irrelevant_mailbox_terms"))


def _is_unscoped_generic_localpart(email: str, country: Optional[str] = None) -> bool:
    locale_pack = _contact_locale(country)
    local = str(email or "").split("@", 1)[0].lower()
    return local in _locale_term_set(locale_pack, "generic_unscoped_localparts")


def _office_contact_has_relevant_scope(contact: dict, country: Optional[str] = None) -> bool:
    locale_pack = _contact_locale(country)
    role = str(contact.get("role", "") or "").lower()
    context = " ".join(
        str(contact.get(key, "") or "").lower()
        for key in ("page_context", "context", "raw_role")
    )
    if _localpart_has_irrelevant_signal(contact.get("email", ""), country=country) and not _localpart_has_relevant_signal(
        contact.get("email", ""),
        country=country,
    ):
        return False
    if _localpart_has_relevant_signal(contact.get("email", ""), country=country):
        return True
    if any(term in role for term in _locale_terms(locale_pack, "relevant_office_role_terms")):
        return True
    if any(term in context for term in _locale_terms(locale_pack, "relevant_office_role_terms")):
        return True
    return False


def _hard_reject_reason(contact: dict, allow_generic: bool = False, country: Optional[str] = None) -> str:
    email = (contact.get("email") or "").lower()
    name = (contact.get("name") or "").strip()
    candidate_type = _candidate_type(contact)
    flags = _cleanup_flags(contact)

    if candidate_type == "junk_candidate":
        return "junk candidate"
    if "invalid_email_shape" in flags:
        return "invalid email shape"
    if _name_has_suspicious_signals(name, country=country):
        return "suspicious name"
    if candidate_type == "named_contact" and (
        ("generic_inbox" in flags)
        or (_is_unscoped_generic_localpart(email, country=country) and not _localpart_has_relevant_signal(email, country=country))
    ):
        return "named generic inbox"
    if candidate_type == "named_contact" and _localpart_has_irrelevant_signal(email, country=country) and not _localpart_has_relevant_signal(
        email,
        country=country,
    ):
        return "irrelevant named mailbox"
    if candidate_type == "office_contact":
        if _is_unscoped_generic_localpart(email, country=country) and not _localpart_has_relevant_signal(email, country=country):
            return "generic office inbox"
        if not _office_contact_has_relevant_scope(contact, country=country):
            return "irrelevant office inbox"
    if not allow_generic and email_is_generic(email, allow_generic=allow_generic):
        return "generic inbox"
    return ""


def email_is_generic(addr: str, allow_generic: bool = False) -> bool:
    """
    Check if email is a generic inbox (not personal).

    Args:
        addr:          Email address.
        allow_generic: If True, generic emails are allowed (NAFSA profile).
    """
    if allow_generic:
        return False
    local = addr.split("@", 1)[0]
    return bool(GENERIC_EMAIL.match(local))


def looks_like_person_name(name: str, country: Optional[str] = None) -> bool:
    """Basic heuristic: does this look like a personal name?"""
    if not name:
        return False
    if _name_has_suspicious_signals(name, country=country):
        return False
    if any(ch.isdigit() for ch in name):
        return False
    if len(name) > 80:
        return False
    locale_pack = _contact_locale(country)
    tokens = _word_tokens(name)
    particles = _locale_term_set(locale_pack, "name_particles")
    visible_tokens = [token for token in tokens if token.lower().strip(".,") not in particles]
    if len(visible_tokens) < 2:
        return False
    if sum(1 for token in visible_tokens if _is_name_like_token(token)) < 2:
        return False
    # Contaminated with role/title?
    if re.search(config.ALLOWED_ROLE_WORDS, name, re.I) or re.search(config.SENIORITY, name, re.I):
        return False
    return True


def domain_match(email_domain: str, home_domain: str) -> bool:
    """
    Check if email domain matches home domain.
    Handles common US abbreviations (e.g., uw.edu vs washington.edu).
    """
    ed = email_domain.lower()
    hd = home_domain.lower()

    # Exact match or subdomain
    if ed == hd or ed.endswith("." + hd) or hd.endswith("." + ed):
        return True

    email_parts = ed.split(".")
    home_parts = hd.split(".")

    if email_parts[-1:] != home_parts[-1:]:
        return False

    if hd.endswith(".ac.uk"):
        email_root = ".".join(email_parts[:-2]) if len(email_parts) > 2 else ""
        home_root = ".".join(home_parts[:-2]) if len(home_parts) > 2 else ""
    else:
        email_root = ".".join(email_parts[:-1]) if len(email_parts) > 1 else ""
        home_root = ".".join(home_parts[:-1]) if len(home_parts) > 1 else ""

    return email_root == home_root if (email_root and home_root) else False


def _supporting_text(contact: dict) -> str:
    return " ".join(
        str(contact.get(key, "") or "").strip()
        for key in ("role", "raw_role", "context", "page_context", "page_url")
        if str(contact.get(key, "") or "").strip()
    ).lower()


def _name_email_alignment(name: str, email: str, country: Optional[str] = None) -> bool:
    tokens = [token.lower() for token in _word_tokens(name)]
    if not tokens:
        return False
    particles = _locale_term_set(_contact_locale(country), "name_particles")
    visible = [token for token in tokens if token not in particles]
    visible = visible if len(visible) >= 2 else tokens
    local = _normalized_localpart(email)
    if not local or len(local) < 4:
        return False

    strong_hits = 0
    initials = 0
    for token in visible:
        normalized = re.sub(r"[^a-z0-9]+", "", token.lower())
        if len(normalized) >= 3 and normalized in local:
            strong_hits += 1
            continue
        if normalized and normalized[0] in local:
            initials += 1

    return strong_hits >= 2 or (strong_hits >= 1 and initials >= 1)


def _supporting_signal_bonus(
    contact: dict,
    extra_positive: Optional[list] = None,
    extra_negative: Optional[list] = None,
    country: Optional[str] = None,
) -> tuple[int, list[str]]:
    locale_pack = _contact_locale(country)
    supporting_text = _supporting_text(contact)
    reasons: list[str] = []
    bonus = 0

    if any(term in supporting_text for term in _locale_terms(locale_pack, "role_signal_terms")):
        bonus += 2
        reasons.append("role_signal_terms")
    if any(term in supporting_text for term in _locale_terms(locale_pack, "relevant_office_role_terms")):
        bonus += 2
        reasons.append("relevant_office_role_terms")
    if any(term in supporting_text for term in _locale_terms(locale_pack, "international_markers")):
        bonus += 1
        reasons.append("international_markers")
    if any(term in supporting_text for term in _locale_terms(locale_pack, "admissions_markers")):
        bonus += 1
        reasons.append("admissions_markers")
    if extra_positive:
        positive_hits = sum(1 for kw in extra_positive if kw and kw.lower() in supporting_text)
        if positive_hits:
            bonus += min(2, positive_hits)
            reasons.append("profile_positive_keywords")
    if extra_negative:
        negative_hits = sum(1 for kw in extra_negative if kw and kw.lower() in supporting_text)
        if negative_hits:
            bonus -= min(2, negative_hits)
            reasons.append("profile_negative_keywords")

    return bonus, reasons


def _score_threshold(home_domain: str, email_domain: str, min_score: Optional[int] = None) -> int:
    if min_score is not None:
        return min_score
    is_edu = home_domain.endswith(".edu") or email_domain.endswith(".edu")
    return 5 if is_edu else 6


def _should_salvage_named_contact(
    contact: dict,
    *,
    score: int,
    threshold: int,
    ok_domain: bool,
    allow_generic: bool,
    extra_positive: Optional[list] = None,
    extra_negative: Optional[list] = None,
    country: Optional[str] = None,
) -> tuple[bool, list[str]]:
    if _candidate_type(contact) != "named_contact":
        return False, []
    if allow_generic:
        return False, []
    if threshold <= score or threshold - score > 2:
        return False, []

    name = str(contact.get("name", "") or "").strip()
    email = str(contact.get("email", "") or "").strip().lower()
    if not name or not email or not ok_domain:
        return False, []
    if email_is_generic(email, allow_generic=allow_generic):
        return False, []
    if not _name_email_alignment(name, email, country=country):
        return False, []

    bonus, reasons = _supporting_signal_bonus(
        contact,
        extra_positive=extra_positive,
        extra_negative=extra_negative,
        country=country,
    )
    if bonus < 2:
        return False, reasons
    return True, reasons


def _evaluate_contact(
    c: dict,
    home_domain: str,
    min_score: Optional[int] = None,
    allow_generic: bool = False,
    extra_positive: Optional[list] = None,
    extra_negative: Optional[list] = None,
    country: Optional[str] = None,
) -> dict:
    name = (c.get("name") or "").strip()
    email = (c.get("email") or "").lower()
    role = (c.get("role") or "")
    details = {
        "name": name,
        "email": email,
        "role": role,
        "page_url": c.get("page_url", ""),
        "allow_generic": allow_generic,
        "candidate_type": _candidate_type(c),
        "cleanup_flags": sorted(_cleanup_flags(c)),
    }

    if not email or "@" not in email:
        details.update(
            {
                "keep": False,
                "score": 0,
                "reason": "no email",
                "domain_match": False,
                "is_generic": False,
                "threshold": min_score if min_score is not None else None,
            }
        )
        return details

    hard_reject = _hard_reject_reason(c, allow_generic=allow_generic, country=country)
    if hard_reject:
        details.update(
            {
                "keep": False,
                "score": 0,
                "reason": hard_reject,
                "domain_match": False,
                "is_generic": email_is_generic(email, allow_generic=allow_generic),
                "threshold": min_score if min_score is not None else None,
            }
        )
        return details

    is_generic = email_is_generic(email, allow_generic=allow_generic)
    if is_generic:
        details.update(
            {
                "keep": False,
                "score": 0,
                "reason": "generic inbox",
                "domain_match": False,
                "is_generic": True,
                "threshold": min_score if min_score is not None else None,
            }
        )
        return details

    name_token_count = len(_word_tokens(name))
    if not allow_generic and name_token_count < 2:
        details.update(
            {
                "keep": False,
                "score": 0,
                "reason": "no personal name",
                "domain_match": False,
                "is_generic": False,
                "threshold": min_score if min_score is not None else None,
                "name_token_count": name_token_count,
            }
        )
        return details

    dom = email.split("@", 1)[1]
    ok_domain = domain_match(dom, home_domain)
    base_role_score = role_score(role, extra_positive=extra_positive, extra_negative=extra_negative)
    supporting_bonus, supporting_reasons = _supporting_signal_bonus(
        c,
        extra_positive=extra_positive,
        extra_negative=extra_negative,
        country=country,
    )
    score = base_role_score + supporting_bonus

    if not ok_domain:
        score -= 2
    intl_boost = 2 if re.search(config.INTL_HINTS, role, re.I) else 0
    score += intl_boost

    threshold = _score_threshold(home_domain, dom, min_score=min_score)
    salvage_keep, salvage_reasons = _should_salvage_named_contact(
        c,
        score=score,
        threshold=threshold,
        ok_domain=ok_domain,
        allow_generic=allow_generic,
        extra_positive=extra_positive,
        extra_negative=extra_negative,
        country=country,
    )
    keep = score >= threshold or salvage_keep
    reason = "ok" if score >= threshold else "salvaged named contact" if salvage_keep else "low score"

    details.update(
        {
            "keep": keep,
            "score": score,
            "reason": reason,
            "domain_match": ok_domain,
            "is_generic": False,
            "threshold": threshold,
            "name_token_count": name_token_count,
            "base_role_score": base_role_score,
            "supporting_bonus": supporting_bonus,
            "supporting_reasons": supporting_reasons,
            "intl_boost": intl_boost,
            "name_email_alignment": _name_email_alignment(name, email, country=country),
            "salvage_considered": score < threshold,
            "salvage_reasons": salvage_reasons,
        }
    )
    return details


def keep_contact(
    c: dict,
    home_domain: str,
    min_score: Optional[int] = None,
    allow_generic: bool = False,
    extra_positive: Optional[list] = None,
    extra_negative: Optional[list] = None,
    country: Optional[str] = None,
) -> Tuple[bool, int, str]:
    """
    Determine if a contact should be kept.
    Returns: (keep_bool, score, reason)

    Args:
        c:              Contact dict (name, email, role, page_url).
        home_domain:    Domain of the institution being crawled.
        min_score:      Override minimum score threshold.
        allow_generic:  Allow generic inboxes (profile override).
        extra_positive: Profile-injected positive role keywords.
        extra_negative: Profile-injected negative role keywords.
    """
    details = _evaluate_contact(
        c,
        home_domain,
        min_score=min_score,
        allow_generic=allow_generic,
        extra_positive=extra_positive,
        extra_negative=extra_negative,
        country=country,
    )
    return bool(details["keep"]), int(details["score"]), str(details["reason"])


def explain_contact_decision(
    c: dict,
    home_domain: str,
    min_score: Optional[int] = None,
    allow_generic: bool = False,
    extra_positive: Optional[list] = None,
    extra_negative: Optional[list] = None,
    country: Optional[str] = None,
) -> dict:
    """
    Return detailed filtering diagnostics for a contact candidate.
    """
    return _evaluate_contact(
        c,
        home_domain,
        min_score=min_score,
        allow_generic=allow_generic,
        extra_positive=extra_positive,
        extra_negative=extra_negative,
        country=country,
    )
