from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gc_contacts.agent.models import AgentState
from gc_contacts.core.filtering import (
    _candidate_type,
    _cleanup_flags,
    _contact_locale,
    _is_unscoped_generic_localpart,
    _localpart_has_irrelevant_signal,
    _localpart_has_relevant_signal,
    _locale_terms,
    _name_has_suspicious_signals,
    _office_contact_has_relevant_scope,
)


HIGH_PRIORITIES = {"high"}
MEDIUM_OR_BETTER = {"high", "medium"}


@dataclass(frozen=True)
class AgentBudgets:
    max_pages_total: int = 36
    max_planned_pages_initial: int = 16
    max_gap_fill_pages: int = 8
    max_fallback_pages: int = 3
    max_llm_calls_total: int = 10
    max_fallback_turns: int = 2
    target_qualified_contacts: int = 8
    soft_success_qualified_contacts: int = 5


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _role_text(contact: dict[str, Any]) -> str:
    parts = [
        contact.get("title"),
        contact.get("role"),
        contact.get("position"),
        contact.get("department"),
    ]
    return " ".join(_norm(p) for p in parts if p).strip()


def _context_text(contact: dict[str, Any]) -> str:
    parts = [
        contact.get("page_context"),
        contact.get("context"),
        contact.get("source_text"),
        contact.get("source_url"),
    ]
    return " ".join(_norm(p) for p in parts if p).strip()


def _email_is_valid(email: str) -> bool:
    email = _norm(email)
    if not email or "@" not in email:
        return False
    if email.startswith("@") or email.endswith("@"):
        return False
    if " " in email:
        return False
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return True


def is_valid_contact(contact: dict[str, Any]) -> bool:
    email = _norm(contact.get("email"))
    country = contact.get("country")
    candidate_type = _candidate_type(contact)
    if candidate_type == "junk_candidate":
        return False
    if "invalid_email_shape" in _cleanup_flags(contact):
        return False
    if _name_has_suspicious_signals(str(contact.get("name", "")), country=country):
        return False
    if candidate_type == "office_contact" and not _office_contact_has_relevant_scope(contact, country=country):
        return False
    if candidate_type == "named_contact" and _is_unscoped_generic_localpart(email, country=country) and not _localpart_has_relevant_signal(
        email,
        country=country,
    ):
        return False
    if _localpart_has_irrelevant_signal(email, country=country) and not _localpart_has_relevant_signal(email, country=country):
        return False
    if not _email_is_valid(email):
        return False

    junk_prefixes = (
        "noreply@",
        "no-reply@",
        "donotreply@",
        "do-not-reply@",
    )
    if email.startswith(junk_prefixes):
        return False

    return True


def is_university_qualified_contact(contact: dict[str, Any]) -> bool:
    email = _norm(contact.get("email"))
    country = contact.get("country")
    candidate_type = _candidate_type(contact)
    cleanup_flags = _cleanup_flags(contact)
    role_text = _role_text(contact)
    context_text = _context_text(contact)
    locale_pack = _contact_locale(country)

    strong_positive_terms = [
        "international partnership",
        "international partnerships",
        "partnerships",
        "global engagement",
        "international relations",
        "international office",
        "global office",
        "international cooperation",
        "cooperation",
        "mobility",
        "exchange",
        "study abroad",
        "global opportunities",
        "internationalisation",
        "internationalization",
        "international cooperation",
        "strategic partnerships",
        "strategic partnership",
        "erasmus",
        "erasmus+",
        "office of global engagement",
    ]

    weak_positive_terms = [
        "international",
        "global",
        "overseas",
    ]

    negative_terms = [
        "foundation year",
        "international foundation year",
        "admissions",
        "applicant",
        "apply",
        "application",
        "prospective student",
        "prospective students",
        "recruitment",
        "enrolment",
        "enrollment",
        "course enquiries",
        "programme enquiries",
        "program enquiries",
        "tuition fees",
        "scholarship",
        "scholarships",
        "student welfare",
        "hr",
        "human resources",
        "it support",
        "helpdesk",
        "finance",
        "payroll",
        "registry",
    ]

    local_part = email.split("@", 1)[0]

    if candidate_type == "junk_candidate":
        return False
    if _name_has_suspicious_signals(str(contact.get("name", "")), country=country):
        return False
    if _localpart_has_irrelevant_signal(email, country=country) and not _localpart_has_relevant_signal(email, country=country):
        return False
    if candidate_type == "office_contact" and not _office_contact_has_relevant_scope(contact, country=country):
        return False
    if "generic_inbox" in cleanup_flags and not _localpart_has_relevant_signal(email, country=country):
        return False

    if any(term in role_text or term in context_text for term in negative_terms):
        return False

    if any(term in role_text for term in strong_positive_terms):
        return True

    if any(term in context_text for term in strong_positive_terms):
        return True

    senior_role_terms = _locale_terms(locale_pack, "senior_role_terms")
    international_leadership_terms = _locale_terms(locale_pack, "international_leadership_terms")
    relevant_scope_terms = _locale_terms(locale_pack, "relevant_office_role_terms")
    if candidate_type == "named_contact" and any(term in role_text for term in senior_role_terms):
        return True
    if candidate_type == "named_contact" and any(term in role_text for term in international_leadership_terms):
        return True
    if candidate_type == "named_contact" and any(term in role_text for term in senior_role_terms + international_leadership_terms):
        if any(term in context_text for term in relevant_scope_terms + _locale_terms(locale_pack, "role_signal_terms")):
            return True

    # generic but relevant mailbox fallback
    relevant_mailbox_terms = [
        "international",
        "partnership",
        "global",
        "exchange",
        "cooperation",
        "relations",
        "mobility",
        "studyabroad",
        "erasmus",
        "globalopportunities",
    ]
    if any(term in local_part for term in relevant_mailbox_terms):
        # only allow generic mailbox fallback if page context is at least weakly relevant
        if any(term in context_text for term in strong_positive_terms + weak_positive_terms):
            return True

    return False


def is_company_qualified_contact(contact: dict[str, Any]) -> bool:
    email = _norm(contact.get("email"))
    role_text = _role_text(contact)
    context_text = _context_text(contact)

    if _candidate_type(contact) == "junk_candidate":
        return False
    if _name_has_suspicious_signals(str(contact.get("name", ""))):
        return False
    if _localpart_has_irrelevant_signal(email) and not _localpart_has_relevant_signal(email):
        return False
    if _candidate_type(contact) == "office_contact" and not _office_contact_has_relevant_scope(contact):
        return False

    relevant_role_terms = [
        "partnership",
        "business development",
        "bd manager",
        "bd director",
        "strategic alliances",
        "higher education",
        "university relations",
        "university partnership",
        "global partnership",
        "regional partnership",
        "external relations",
    ]

    weak_but_acceptable_context_terms = [
        "partnership",
        "higher education",
        "universities",
        "institutional",
        "academic partners",
    ]

    relevant_mailbox_terms = [
        "partnership",
        "businessdevelopment",
        "bd",
    ]

    irrelevant_terms = [
        "support",
        "customer service",
        "careers",
        "recruitment",
        "hr",
        "human resources",
        "legal",
        "press",
        "media enquiries",
    ]

    if any(term in role_text or term in context_text for term in irrelevant_terms):
        return False

    if any(term in role_text for term in relevant_role_terms):
        return True

    if any(term in context_text for term in weak_but_acceptable_context_terms):
        return True

    local_part = email.split("@", 1)[0]
    if any(term in local_part for term in relevant_mailbox_terms):
        return True

    if local_part in {"hello", "info"} and any(term in context_text for term in weak_but_acceptable_context_terms):
        return True

    return False


def is_qualified_contact(contact: dict[str, Any], org_type: str) -> bool:
    if not is_valid_contact(contact):
        return False

    org_type = _norm(org_type)

    if org_type == "company":
        return is_company_qualified_contact(contact)

    # Default to university-style logic for now.
    return is_university_qualified_contact(contact)


def contact_priority(contact: dict[str, Any], org_type: str) -> str:
    if not is_qualified_contact(contact, org_type):
        return "ignore"

    candidate_type = _candidate_type(contact)
    role_text = _role_text(contact)
    context_text = _context_text(contact)
    email_local = _norm(contact.get("email")).split("@", 1)[0]

    if org_type == "company":
        high_terms = [
            "director",
            "head",
            "lead",
            "manager",
            "strategic alliances",
            "business development",
            "partnership",
            "university relations",
            "higher education",
        ]
        medium_terms = [
            "external relations",
            "regional partnership",
            "institutional",
            "academic partners",
        ]
        negative_priority_terms = [
            "support",
            "customer service",
            "careers",
            "hr",
        ]
    else:
        high_terms = [
            "director",
            "head",
            "vice rector",
            "vice president",
            "pro vice chancellor",
            "international partnerships",
            "international partnership",
            "global engagement",
            "international relations",
            "international office",
            "mobility",
            "exchange",
            "study abroad",
            "international cooperation",
            "internationalization",
            "internationalisation",
            "strategic partnerships",
            "office of global engagement",
            "erasmus",
        ]
        medium_terms = [
            "international",
            "global",
            "cooperation",
            "relations",
            "partnership",
            "mobility",
            "exchange",
            "study abroad",
            "erasmus",
        ]
        negative_priority_terms = [
            "foundation year",
            "admissions",
            "applicant",
            "apply",
            "application",
            "recruitment",
            "programme enquiries",
            "program enquiries",
            "scholarship",
            "scholarships",
        ]

    if any(term in role_text or term in context_text for term in negative_priority_terms):
        return "ignore"

    if candidate_type == "office_contact":
        if not _office_contact_has_relevant_scope(contact):
            return "ignore"
        return "medium"

    if any(term in role_text for term in high_terms):
        return "high"

    if any(term in context_text for term in high_terms):
        return "high"

    if any(term in role_text for term in medium_terms):
        return "medium"

    if any(term in context_text for term in medium_terms):
        return "medium"

    if any(term in email_local for term in ("international", "partnership", "exchange", "global", "cooperation", "relations", "mobility")):
        return "medium"

    return "medium"


def update_contact_buckets(state: AgentState, contacts: list[dict[str, Any]]) -> AgentState:
    for contact in contacts:
        state.discovered_contacts.append(contact)

        if is_valid_contact(contact):
            state.valid_contacts.append(contact)

        if is_qualified_contact(contact, state.org_type):
            contact_copy = dict(contact)
            priority = contact_priority(contact_copy, state.org_type)
            if priority == "ignore":
                continue
            contact_copy["priority"] = priority
            state.qualified_contacts.append(contact_copy)

    return state


def unique_contacts_by_email(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []

    for contact in contacts:
        email = _norm(contact.get("email"))
        if not email or email in seen:
            continue
        seen.add(email)
        output.append(contact)

    return output


def evaluate_outcome(state: AgentState, budgets: AgentBudgets) -> AgentState:
    deduped_qualified = unique_contacts_by_email(state.qualified_contacts)
    state.qualified_contacts = deduped_qualified

    priorities = [contact_priority(c, state.org_type) for c in deduped_qualified]
    high_count = sum(1 for p in priorities if p == "high")
    medium_or_better_count = sum(1 for p in priorities if p in MEDIUM_OR_BETTER)
    qualified_count = len(deduped_qualified)
    direct_or_high_confidence = sum(
        1
        for contact in deduped_qualified
        if str(contact.get("email_source", "direct")) == "direct" or str(contact.get("confidence", "high")) == "high"
    )
    unresolved_candidates = sum(
        1
        for candidate in getattr(state, "person_candidates", [])
        if candidate.get("status") in {"pending", "site_exhausted", "web_exhausted", "pattern_pending"}
    )

    state.hard_success = False
    state.soft_success = False
    state.failed = False
    state.failure_reason = None

    if qualified_count >= budgets.target_qualified_contacts and direct_or_high_confidence >= max(2, budgets.soft_success_qualified_contacts - 1):
        state.hard_success = True
        return state

    if high_count >= 2 and medium_or_better_count >= 3:
        state.hard_success = True
        return state

    if qualified_count >= budgets.soft_success_qualified_contacts and (direct_or_high_confidence >= 1 or unresolved_candidates == 0):
        state.soft_success = True
        return state

    if state.pages_fetched >= budgets.max_pages_total:
        state.failed = True
        state.failure_reason = "page_budget_exhausted"
        return state

    if state.llm_calls >= budgets.max_llm_calls_total:
        state.failed = True
        state.failure_reason = "llm_budget_exhausted"
        return state

    if state.fallback_turns >= budgets.max_fallback_turns and qualified_count < budgets.soft_success_qualified_contacts:
        state.failed = True
        state.failure_reason = "fallback_budget_exhausted"
        return state

    return state


def should_stop(state: AgentState) -> bool:
    return bool(state.hard_success or state.soft_success or state.failed)


def remaining_page_budget(state: AgentState, budgets: AgentBudgets) -> int:
    return max(0, budgets.max_pages_total - state.pages_fetched)


def remaining_llm_budget(state: AgentState, budgets: AgentBudgets) -> int:
    return max(0, budgets.max_llm_calls_total - state.llm_calls)


def can_use_fallback(state: AgentState, budgets: AgentBudgets) -> bool:
    return state.fallback_turns < budgets.max_fallback_turns
