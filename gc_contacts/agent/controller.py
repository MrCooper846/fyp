from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any
from pathlib import Path
from urllib.parse import urlparse

import gc_contacts.config as config
from gc_contacts.agent.models import AgentState, GapPlan, PlannedPage, RankedContact, ScoutPlan
from gc_contacts.agent.rules import (
    AgentBudgets,
    contact_priority,
    evaluate_outcome,
    is_qualified_contact,
    should_stop,
    update_contact_buckets,
)
from gc_contacts.agent.enrichment import (
    apply_evidence_to_candidate,
    build_person_candidate,
    build_role_holder_candidate,
    infer_email_pattern,
    plan_next_enrichment_action,
    search_site_for_person,
    search_web_for_person,
)
from gc_contacts.core.debug import write_debug_json
from gc_contacts.core.discovery import gather_candidates_bundle
from gc_contacts.core.extraction import (
    GENERIC_EMAIL,
    clean_contact_name,
    clean_contact_role,
    decode_js_emails,
    extract_mailto_contacts,
    normalize_email_value,
    run_contact_extraction_pipeline,
    simple_regex_contacts,
)
from gc_contacts.core.filtering import explain_contact_decision, looks_like_person_name
from gc_contacts.core.harvest import (
    empty_extraction_result,
    fetch_and_extract_contacts,
    looks_like_zero_evidence_shell,
)
from gc_contacts.core.http_client import bs_text, fetch_page
from gc_contacts.core.llm import gpt_clean_name, gpt_extract, gpt_rank_candidate_pages
from gc_contacts.core.models import Target
from gc_contacts.core.utils import home_domain_of
from gc_contacts.localisation import get_country_discovery_pack

HEURISTIC_LIKE_PAGE_TYPES = {"heuristic", "heuristic_us", "profile_slug", "family_template"}
LANG_PREFIX_SEGMENTS = {"en", "it", "fr", "de", "es", "pt", "nl"}
GENERIC_BRANCH_SEGMENTS = {
    "page",
    "node",
    "details",
    "page",
    "it",
    "en",
    "fr",
    "de",
    "es",
    "pt",
    "nl",
}
PAGE_FAMILY_BONUS = {
    "contact": 2.8,
    "international": 2.6,
    "directory": 2.2,
    "office": 2.0,
    "staff": 1.8,
    "governance": 1.4,
    "admissions": -0.6,
    "generic": 0.0,
}
SOURCE_STRATEGY_BONUS = {
    "real_link_multihop": 3.4,
    "cms": 2.0,
    "sitemap": 1.4,
    "subdomains": 1.0,
    "profile_slugs": -1.6,
    "heuristic_slugs": -2.6,
}
ZERO_EVIDENCE_SHELL_TEXT_FLOOR = 1000
ZERO_EVIDENCE_SHELL_DUPLICATE_THRESHOLD = 2


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def _item_value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _target_country(state: AgentState) -> str | None:
    return _safe_getattr(_safe_getattr(state, "target", None), "country", None)


def _discovery_locale(country: str | None = None) -> dict[str, Any]:
    return get_country_discovery_pack(country)


def _locale_term_set(locale_pack: dict[str, Any], key: str) -> set[str]:
    return {
        str(item or "").strip().lower()
        for item in locale_pack.get(key, [])
        if str(item or "").strip()
    }


def _record_mode(state: AgentState, mode: str, reason: str, goal: str | None = None) -> None:
    if state.mode != mode:
        state.mode = mode
    state.current_goal = goal
    state.mode_history.append({"mode": mode, "reason": reason, "goal": goal})


def _record_action(state: AgentState, action: str, reason: str, details: dict[str, Any] | None = None) -> None:
    state.action_history.append({"action": action, "reason": reason, "details": details or {}})


def _pending_person_candidates(state: AgentState) -> list[dict[str, Any]]:
    candidates = list(state.person_candidates) + list(state.role_holder_candidates)
    return [
        candidate
        for candidate in candidates
        if candidate.get("status") in {"pending", "site_exhausted", "web_exhausted", "pattern_pending"}
        and int(candidate.get("action_budget_remaining", 0) or 0) > 0
    ]


def _enrichment_candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float, float, str]:
    kind = str(candidate.get("candidate_kind", "") or "")
    kind_bonus = 2.0 if kind == "role_holder" else 1.0
    if bool(candidate.get("directory_ready")):
        kind_bonus += 0.4
    if str(candidate.get("goal", "") or "") == "resolve_person_from_role":
        kind_bonus += 0.5
    attempts = candidate.get("attempts", {}) or {}
    total_attempts = sum(int(attempts.get(key, 0) or 0) for key in ("governance", "international", "people", "directory", "web_search", "pattern"))
    return (
        -(float(candidate.get("expected_yield", 0.0) or 0.0) + kind_bonus),
        total_attempts,
        -float(candidate.get("name_confidence", 0.0) or 0.0),
        str(candidate.get("name") or candidate.get("role") or candidate.get("office_email") or ""),
    )


def _should_continue_enrichment(state: AgentState) -> bool:
    return not bool(state.failed) and bool(_pending_person_candidates(state))


def _is_heuristic_like(page_type: str) -> bool:
    return str(page_type or "").strip().lower() in HEURISTIC_LIKE_PAGE_TYPES


def _candidate_source_priority(page_type: str) -> int:
    page_type = str(page_type or "").strip().lower()
    if page_type == "nav":
        return 7
    if page_type in {"header", "footer"}:
        return 6
    if page_type in {"body", "contact page", "office page", "leadership page", "administration page"}:
        return 5
    if page_type in {"wp", "drupal", "sitemap", "subdomain"}:
        return 6
    if page_type == "profile_slug":
        return 5
    if page_type in {"heuristic", "heuristic_us"}:
        return 2
    return 4


def _normalize_path_segment(segment: str) -> str:
    cleaned = re.sub(r"(?i)\.(html?|php|aspx?)$", "", str(segment or "").strip().lower())
    tokens = re.findall(r"[a-z0-9]+", cleaned)
    return "-".join(tokens)


def _candidate_family_signature(url: str, page_type: str) -> str | None:
    if not _is_heuristic_like(page_type):
        return None
    parsed = urlparse(url)
    segments = [_normalize_path_segment(segment) for segment in parsed.path.split("/")]
    segments = [
        segment
        for segment in segments
        if segment and segment not in LANG_PREFIX_SEGMENTS and segment not in {"index", "home"}
    ]
    if not segments:
        return f"{parsed.netloc}:root"
    key = segments[-1]
    if key in {"about", "leadership", "office", "services", "service", "contacts", "contact"} and len(segments) >= 2:
        key = f"{segments[-2]}::{key}"
    return f"{parsed.netloc}:{key}"


def _dead_family_cache_path(signature: str) -> Path:
    return config.DEAD_FAMILY_CACHE_DIR / (hashlib.sha1(signature.encode("utf-8")).hexdigest() + ".json")


def _is_persistently_dead_candidate_family(signature: str) -> bool:
    if not signature:
        return False
    path = _dead_family_cache_path(signature)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return False
    cached_at = float(payload.get("cached_at", 0.0) or 0.0)
    ttl = max(0.0, float(getattr(config, "DEAD_FAMILY_CACHE_TTL", 0.0) or 0.0))
    if ttl <= 0:
        return False
    if cached_at <= 0 or (time.time() - cached_at) >= ttl:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return False
    return True


def _persist_dead_candidate_family(signature: str, reason: str, details: dict[str, Any] | None = None) -> None:
    if not signature:
        return
    payload = {
        "signature": signature,
        "reason": reason,
        "details": details or {},
        "cached_at": time.time(),
    }
    _dead_family_cache_path(signature).write_text(json.dumps(payload), encoding="utf-8")


def _page_text_signature(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not normalized:
        return None
    return hashlib.sha1(normalized[:2500].encode("utf-8", "ignore")).hexdigest()


def _shell_context_key(item: Any, homepage_url: str = "") -> str:
    source_strategy = str(_item_value(item, "source_strategy", "") or "").strip().lower()
    source_stage = str(_item_value(item, "source_stage", "") or "").strip().lower()
    parent_url = str(_item_value(item, "parent_url", "") or "").strip() or homepage_url
    if not source_strategy and not source_stage and not parent_url:
        return ""
    return "|".join((source_strategy, source_stage, parent_url))


def _looks_like_zero_evidence_shell(page_trace: dict[str, Any]) -> bool:
    return looks_like_zero_evidence_shell(
        page_trace,
        shell_like=bool(page_trace.get("shell_like")),
        text="",
    )


def _note_pruned_candidate(
    state: AgentState,
    page: PlannedPage,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if any(item.get("url") == page.url and item.get("reason") == reason for item in state.pruned_candidates):
        return
    state.pruned_candidates.append(
        {
            "url": page.url,
            "page_type": page.page_type,
            "expected_yield": page.expected_yield,
            "reason": reason,
            "details": details or {},
        }
    )


def _mark_dead_candidate_family(
    state: AgentState,
    page: PlannedPage,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    signature = _candidate_family_signature(page.url, page.page_type)
    if not signature:
        return
    state.dead_candidate_signatures.add(signature)
    _persist_dead_candidate_family(signature, reason, details)
    _note_pruned_candidate(
        state,
        page,
        reason,
        {"family_signature": signature, **(details or {})},
    )


def _productive_nonheuristic_seen(state: AgentState) -> bool:
    return any(
        not _is_heuristic_like(str(trace.get("page_type", "")))
        and (int(trace.get("raw_contacts_found", 0) or 0) > 0 or int(trace.get("text_length", 0) or 0) > 0)
        for trace in state.extraction_trace
    )


def _branch_key(url: str, parent_url: str = "", homepage_url: str = "", country: str | None = None) -> str:
    locale_pack = _discovery_locale(country)
    generic_branch_segments = GENERIC_BRANCH_SEGMENTS | _locale_term_set(locale_pack, "generic_branch_segments")
    branch_root_segments = _locale_term_set(locale_pack, "branch_root_segments")
    for candidate_url in (url, parent_url, homepage_url):
        parsed = urlparse(candidate_url or "")
        segments = []
        for raw_segment in parsed.path.split("/"):
            segment = _normalize_path_segment(raw_segment)
            if not segment or segment in LANG_PREFIX_SEGMENTS or segment in generic_branch_segments:
                continue
            segments.append(segment)
        if segments:
            if len(segments) >= 2 and segments[0] in branch_root_segments:
                return f"{segments[0]}/{segments[1]}"
            return segments[0]
    return ""


def _productive_real_link_context(state: AgentState) -> tuple[set[str], set[str]]:
    productive_urls: set[str] = set()
    productive_branches: set[str] = set()
    country = _target_country(state)
    for trace in state.extraction_trace:
        if str(trace.get("source_strategy", "")) != "real_link_multihop":
            continue
        raw_contacts = int(trace.get("raw_contacts_found", 0) or 0)
        text_length = int(trace.get("text_length", 0) or 0)
        kept = len(trace.get("kept_contacts", []) or [])
        missing = len(trace.get("missing_email_candidates", []) or [])
        if raw_contacts <= 0 and kept <= 0 and missing <= 0 and text_length < 250:
            continue
        url = str(trace.get("url", "")).strip()
        if url:
            productive_urls.add(url)
        branch = _branch_key(url, str(trace.get("parent_url", "")), state.homepage_url, country=country)
        if branch:
            productive_branches.add(branch)
    return productive_urls, productive_branches


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float, float, str]:
    return (
        -float(candidate.get("source_priority", 0.0) or 0.0),
        -float(candidate.get("heuristic_score", 0.0) or 0.0),
        -float(candidate.get("family_confidence", 0.0) or 0.0),
        str(candidate.get("url", "")),
    )


def _is_search_interface_candidate(candidate: dict[str, Any]) -> bool:
    return (
        str(candidate.get("candidate_bucket", "") or "").strip().lower() == "search_interface"
        or bool(candidate.get("interface_only"))
        or str(candidate.get("source_stage", "") or "").strip().lower().endswith(("_form", "_search_link"))
    )


def _remember_deferred_search_interface(state: AgentState, candidate: dict[str, Any], reason: str) -> None:
    url = str(candidate.get("url", "") or "").strip()
    if not url:
        return
    if any(item.get("url") == url for item in state.deferred_search_interfaces):
        return
    state.deferred_search_interfaces.append(
        {
            "url": url,
            "source_type": str(candidate.get("source_type", "") or ""),
            "source_strategy": str(candidate.get("source_strategy", "") or ""),
            "source_stage": str(candidate.get("source_stage", "") or ""),
            "anchor_text": str(candidate.get("anchor_text", "") or ""),
            "parent_url": str(candidate.get("parent_url", "") or ""),
            "page_family": str(candidate.get("page_family", "generic") or "generic"),
            "heuristic_score": float(candidate.get("heuristic_score", 0.0) or 0.0),
            "reason": reason,
        }
    )
    for item in state.discovery_trace:
        if item.get("url") == url:
            item["deferred_for_later"] = True
            item["deferred_reason"] = reason


def _page_sort_key(page: PlannedPage, state: AgentState) -> tuple[float, float, str]:
    source_priority = float(_candidate_source_priority(page.page_type))
    source_priority += float(SOURCE_STRATEGY_BONUS.get(str(page.source_strategy or ""), 0.0))
    source_priority += float(PAGE_FAMILY_BONUS.get(str(page.page_family or "generic"), 0.0))

    productive_urls, productive_branches = _productive_real_link_context(state)
    page_branch = _branch_key(page.url, page.parent_url, state.homepage_url, country=_target_country(state))

    if str(page.source_strategy or "") == "real_link_multihop":
        if page.parent_url and page.parent_url in productive_urls:
            source_priority += 5.0
        elif page_branch and page_branch in productive_branches:
            source_priority += 4.0
        elif productive_branches:
            source_priority += 1.0

    if _productive_nonheuristic_seen(state):
        if _is_heuristic_like(page.page_type):
            source_priority -= 2.0
        else:
            source_priority += 1.0
        if str(page.source_strategy or "") in {"profile_slugs", "heuristic_slugs"}:
            source_priority -= 3.5
        elif str(page.source_strategy or "") == "real_link_multihop":
            source_priority += 1.5

    return (-source_priority, -float(page.expected_yield or 0.0), page.url)


def _sort_pages_for_execution(state: AgentState, pages: list[PlannedPage]) -> list[PlannedPage]:
    return sorted(pages, key=lambda page: _page_sort_key(page, state))


def _prune_candidate_pages(state: AgentState, pages: list[PlannedPage]) -> list[PlannedPage]:
    kept: list[PlannedPage] = []
    seen_urls: set[str] = set()
    for page in pages:
        if not page.url or page.url in seen_urls or page.url in state.visited_urls:
            continue
        seen_urls.add(page.url)
        if page.url in state.dead_urls:
            _note_pruned_candidate(
                state,
                page,
                "dead_url",
                {"url": page.url},
            )
            continue
        shell_context_key = _shell_context_key(page, state.homepage_url)
        if shell_context_key and shell_context_key in state.dead_shell_contexts:
            _note_pruned_candidate(
                state,
                page,
                "dead_shell_context",
                {"shell_context_key": shell_context_key},
            )
            continue
        signature = _candidate_family_signature(page.url, page.page_type)
        if signature and signature not in state.dead_candidate_signatures and _is_persistently_dead_candidate_family(signature):
            state.dead_candidate_signatures.add(signature)
        if signature and signature in state.dead_candidate_signatures:
            _note_pruned_candidate(
                state,
                page,
                "dead_candidate_family",
                {"family_signature": signature},
            )
            continue
        kept.append(page)
    return kept


def _needs_senior_contact(state: AgentState) -> bool:
    senior_terms = ("director", "head", "vice", "president", "rector")
    for contact in state.qualified_contacts:
        title = str(contact.get("title") or contact.get("role") or "").lower()
        if any(term in title for term in senior_terms):
            return False
    return True


def _build_initial_state(target: Target, profile: Any) -> AgentState:
    homepage_url = getattr(target, "url", "") or ""
    org_type = getattr(target, "org_type", "university") or "university"
    profile_name = _safe_getattr(profile, "name", profile.__class__.__name__)

    state = AgentState(
        target=target,
        org_type=org_type,
        profile_name=profile_name,
        homepage_url=homepage_url,
        source_homepage_url=homepage_url,
    )
    _record_mode(state, "office_discovery", "initial_state", goal="find direct outreach contacts")
    return state


def _expected_roles_for_org_type(org_type: str) -> list[str]:
    org_type = (org_type or "").strip().lower()
    if org_type == "company":
        return [
            "Director of Partnerships",
            "Business Development Lead",
            "University Partnerships Manager",
            "Higher Education Solutions Lead",
        ]
    return [
        "Head of International Partnerships",
        "Director of Global Engagement",
        "International Relations Lead",
        "Mobility / Exchange Coordinator",
    ]

async def scout_plan(state: AgentState, profile: Any, budgets: AgentBudgets) -> ScoutPlan:
    """
    Hybrid bounded scout phase.

    Strategy:
    1. deterministically gather candidates
    2. always include a few high-yield seed pages
    3. always include top heuristic pages
    4. ask LLM to rank only among discovered candidates
    5. merge deterministically and dedupe

    This keeps the agent useful while reducing run-to-run variance.
    """
    if not state.homepage_url:
        return ScoutPlan(
            strategy="no_homepage_url",
            org_type=state.org_type,
            expected_roles=[],
            ranked_pages=[],
            stop_hint="No homepage URL available",
        )

    discovery_mode = _safe_getattr(profile, "discovery_mode", "hybrid")
    bundle = await gather_candidates_bundle(
        state.homepage_url,
        extra_slugs=_safe_getattr(profile, "slug_hints", []),
        country=getattr(state.target, "country", None),
        mode=discovery_mode,
        include_strategy_breakdown=True,
        target_name=state.target.name,
    )
    resolved_home_url = str(bundle.get("resolved_home_url", "") or "").strip()
    if resolved_home_url and resolved_home_url != state.homepage_url:
        previous_homepage = state.homepage_url
        rescue_method = next(
            (
                str(item.get("reason", "") or "")
                for item in (bundle.get("homepage_rescue_trace", []) or [])
                if str(item.get("fetched", "") or "").strip().lower() == "true"
            ),
            "",
        )
        state.homepage_url = resolved_home_url
        _record_action(
            state,
            "rescue_homepage",
            "discovery_entrypoint_recovered",
            {"from": previous_homepage, "to": resolved_home_url, "method": rescue_method},
        )
    state.homepage_rescue_trace = list(bundle.get("homepage_rescue_trace", []) or [])
    candidates = bundle.get("candidates", [])
    state.discovery_trace = [
        {
            "url": str(c.get("url", "")),
            "source_type": str(c.get("source_type", "unknown")),
            "source_strategy": str(c.get("source_strategy", "")),
            "source_stage": str(c.get("source_stage", "")),
            "anchor_text": str(c.get("anchor_text", "")),
            "parent_url": str(c.get("parent_url", "")),
            "page_family": str(c.get("page_family", "generic")),
            "candidate_bucket": str(c.get("candidate_bucket", "content")),
            "heuristic_score": float(c.get("heuristic_score", 0.0) or 0.0),
            "selected_for_planning": False,
        }
        for c in candidates
    ]
    state.discovery_strategy_trace = {
        name: [
            {
                "url": str(c.get("url", "")),
                "source_type": str(c.get("source_type", "unknown")),
                "source_strategy": str(c.get("source_strategy", "")),
                "source_stage": str(c.get("source_stage", "")),
                "anchor_text": str(c.get("anchor_text", "")),
                "parent_url": str(c.get("parent_url", "")),
                "page_family": str(c.get("page_family", "generic")),
                "candidate_bucket": str(c.get("candidate_bucket", "content")),
                "heuristic_score": float(c.get("heuristic_score", 0.0) or 0.0),
            }
            for c in rows
        ]
        for name, rows in bundle.get("by_strategy", {}).items()
    }

    content_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        if _is_search_interface_candidate(candidate):
            _remember_deferred_search_interface(state, candidate, "search_interface_requires_target")
            continue
        content_candidates.append(candidate)
    candidates = content_candidates

    if not candidates:
        return ScoutPlan(
            strategy="no_candidates_found",
            org_type=state.org_type,
            expected_roles=_expected_roles_for_org_type(state.org_type),
            ranked_pages=[],
            stop_hint="No candidate pages discovered",
        )

    expected_roles = _expected_roles_for_org_type(state.org_type)

    homepage_html = await fetch_page(state.homepage_url)
    homepage_text = bs_text(homepage_html) if homepage_html else ""
    state.homepage_text = homepage_text[:4000]

    # Stable candidate ordering first
    candidates_sorted = sorted(candidates, key=_candidate_sort_key)

    ranked_pages: list[PlannedPage] = []

    candidate_lookup = {str(c.get("url", "")).strip(): c for c in candidates_sorted}

    def add_page_if_new(url: str, reason: str, expected_yield: float, page_type: str) -> None:
        if not url:
            return
        if any(p.url == url for p in ranked_pages):
            return
        for candidate in state.discovery_trace:
            if candidate["url"] == url:
                candidate["selected_for_planning"] = True
                candidate["selection_reason"] = reason
        original = candidate_lookup.get(url, {})
        ranked_pages.append(
            PlannedPage(
                url=url,
                reason=reason,
                expected_yield=expected_yield,
                expected_roles=expected_roles,
                page_type=page_type,
                source_strategy=str(original.get("source_strategy", "")),
                source_stage=str(original.get("source_stage", "")),
                parent_url=str(original.get("parent_url", "")),
                page_family=str(original.get("page_family", "generic")),
            )
        )

    # 1) Deterministic high-yield seeds
    locale_pack = _discovery_locale(_target_country(state))
    seed_patterns = [
        "international-office",
        "international",
        "global",
        "partnership",
        "exchange",
        "mobility",
        "study-abroad",
        "team",
        "staff",
        "people",
        "directory",
        "contact",
    ]
    seed_patterns.extend(
        str(item or "").strip().lower()
        for item in locale_pack.get("high_yield_seed_patterns", [])
        if str(item or "").strip()
    )

    for c in candidates_sorted:
        url = str(c.get("url", "")).lower()
        if any(pattern in url for pattern in seed_patterns):
            add_page_if_new(
                url=str(c.get("url", "")),
                reason="deterministic_high_yield_seed",
                expected_yield=float(c.get("heuristic_score", 0.0) or 0.0),
                page_type=str(c.get("source_type", "unknown")),
            )
        if len(ranked_pages) >= 2:
            break

    # 2) Deterministic top heuristic pages
    for c in candidates_sorted:
        add_page_if_new(
            url=str(c.get("url", "")),
            reason=f"deterministic_top_heuristic:{c.get('source_type', 'unknown')}",
            expected_yield=float(c.get("heuristic_score", 0.0) or 0.0),
            page_type=str(c.get("source_type", "unknown")),
        )
        if len(ranked_pages) >= 4:
            break

    # 3) LLM-ranked additions only for the remaining slots
    remaining_slots = max(0, budgets.max_planned_pages_initial - len(ranked_pages))
    plan_strategy = "hybrid_deterministic_plus_llm"
    stop_hint = "Stop early if enough strong contacts are found."

    if remaining_slots > 0:
        try:
            plan = await gpt_rank_candidate_pages(
                homepage_text=state.homepage_text,
                base_url=state.homepage_url,
                org_type=state.org_type,
                candidates=candidates_sorted,
                expected_roles=expected_roles,
                max_pages=remaining_slots,
            )
            state.llm_calls += 1
            plan_strategy = str(plan.get("strategy", plan_strategy))
            stop_hint = str(plan.get("stop_hint", stop_hint))

            for item in plan.get("ranked_pages", []):
                url = str(item.get("url", "")).strip()
                if not url:
                    continue
                original = candidate_lookup.get(url, {})
                add_page_if_new(
                    url=url,
                    reason=str(item.get("reason", f"planner:{original.get('source_type', 'unknown')}")),
                    expected_yield=float(item.get("expected_yield", original.get("heuristic_score", 0.0)) or 0.0),
                    page_type=str(item.get("page_type", original.get("source_type", "unknown"))),
                )
                if len(ranked_pages) >= budgets.max_planned_pages_initial:
                    break
        except Exception:
            pass

    return ScoutPlan(
        strategy=plan_strategy,
        org_type=state.org_type,
        expected_roles=expected_roles,
        ranked_pages=ranked_pages[: budgets.max_planned_pages_initial],
        stop_hint=stop_hint,
    )


def _empty_extraction_result() -> dict[str, Any]:
    return {
        "raw_evidence": [],
        "assembled_candidates": [],
        "cleaned_candidates": [],
        "typed_candidates": [],
        "candidates_for_filtering": [],
        "named_contacts": [],
        "office_contacts": [],
        "missing_email_candidates": [],
        "junk_candidates": [],
        "raw_evidence_count_by_strategy": {
            "mailto_explicit": 0,
            "visible_regex": 0,
            "html_attribute": 0,
            "js_decode": 0,
            "explicit_obfuscation": 0,
            "llm_structured": 0,
        },
        "assembled_candidate_count": 0,
        "clean_candidate_count": 0,
        "named_contact_count": 0,
        "office_contact_count": 0,
        "person_without_email_count": 0,
        "junk_candidate_count": 0,
    }


def _normalize_contact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _contact_merge_key(contact: dict[str, Any]) -> tuple[str, str, str]:
    email = str(contact.get("email", "")).strip().lower()
    if email:
        return ("email", email, "")
    name = str(contact.get("_raw_name") or contact.get("name") or "").strip().lower()
    role = str(contact.get("role", "")).strip().lower()
    return ("person", name, role)


def _contact_merge_score(contact: dict[str, Any]) -> int:
    score = 0
    if contact.get("email"):
        score += 3
    if contact.get("role"):
        score += 3
    if contact.get("name"):
        score += 5
    elif contact.get("_raw_name"):
        score += 1
    source = str(contact.get("source", "")).strip().lower()
    if source == "gpt":
        score += 3
    elif source == "mailto":
        score += 2
    elif source == "regex":
        score += 1
    return score


def _should_attempt_llm_name_clean(name: str) -> bool:
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'`.-]+", str(name or ""))
    return 2 <= len(tokens) <= 6 and len(str(name or "").strip()) <= 80


def _email_matches_name(email: str, name: str) -> bool:
    email = str(email or "").strip().lower()
    name = str(name or "").strip()
    if not email or "@" not in email or not name:
        return False

    local = email.split("@", 1)[0]
    if GENERIC_EMAIL.match(local):
        return False

    tokens = [tok.lower() for tok in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", name) if tok]
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


async def _cleanup_extracted_contacts(
    contacts: list[dict[str, Any]],
    profile: Any,
    country: str | None = None,
) -> list[dict[str, Any]]:
    role_keywords = _safe_getattr(profile, "role_positive_keywords", [])
    staged_contacts: list[dict[str, Any]] = []

    for contact in contacts:
        page_url = str(contact.get("page_url", "")).strip()
        raw_name = _normalize_contact_text(contact.get("name", ""))
        cleaned_name = clean_contact_name(raw_name, country=country)
        cleaned_role = clean_contact_role(contact.get("role", ""), role_keywords=role_keywords, country=country)
        normalized_email = normalize_email_value(contact.get("email", ""))

        if not normalized_email and not raw_name and not cleaned_name:
            continue

        staged = {
            "name": cleaned_name,
            "_raw_name": raw_name,
            "role": cleaned_role,
            "email": normalized_email,
            "context": _normalize_contact_text(contact.get("context", "")),
            "page_url": page_url,
            "source": str(contact.get("source", "")).strip().lower(),
        }
        staged["_merge_score"] = _contact_merge_score(staged)
        staged_contacts.append(staged)

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for contact in staged_contacts:
        key = _contact_merge_key(contact)
        existing = deduped.get(key)
        if existing is None or int(contact.get("_merge_score", 0)) > int(existing.get("_merge_score", 0)):
            deduped[key] = contact

    cleaned_contacts: list[dict[str, Any]] = []
    for contact in deduped.values():
        raw_name = str(contact.pop("_raw_name", "")).strip()
        contact.pop("_merge_score", None)
        if (
            raw_name
            and not contact.get("name")
            and _should_attempt_llm_name_clean(raw_name)
            and (str(contact.get("source", "")) == "gpt" or bool(contact.get("role")))
        ):
            try:
                contact["name"] = await gpt_clean_name(
                    raw_name,
                    str(contact.get("role", "")),
                    str(contact.get("email", "")),
                    str(contact.get("page_url", "")),
                )
            except Exception:
                contact["name"] = ""

        if not contact.get("name"):
            contact["name"] = ""
        if not contact.get("role"):
            contact["role"] = ""
        if not contact.get("email") and not contact.get("name"):
            continue

        if contact.get("email") and contact.get("name") and looks_like_person_name(str(contact.get("name", "")), country=country):
            if not _email_matches_name(str(contact.get("email", "")), str(contact.get("name", ""))):
                person_only = {
                    "name": str(contact.get("name", "")),
                    "role": str(contact.get("role", "")),
                    "email": "",
                    "context": str(contact.get("context", "")),
                    "page_url": str(contact.get("page_url", "")),
                    "source": f"{contact.get('source', '')}_person_candidate",
                }
                contact["name"] = ""
                cleaned_contacts.append(person_only)

        cleaned_contacts.append(contact)

    return cleaned_contacts


async def _legacy_fetch_and_extract_contacts(url: str, profile: Any, country: str | None = None) -> tuple[str, list[dict], str]:
    """
    Fetch a page and extract contacts using the same shared logic already used
    in the current NAFSA pipeline.
    """
    html = await fetch_page(url)
    if not html:
        return url, [], ""

    text = bs_text(html)
    extra_emails = decode_js_emails(html)
    mailto_contacts = extract_mailto_contacts(
        html,
        url,
        role_keywords=_safe_getattr(profile, "role_positive_keywords", []),
        country=country,
    )

    try:
        gpt_contacts = await gpt_extract(
            text,
            url,
            allow_generic_emails=_safe_getattr(profile, "allow_generic_emails", False),
        )
        for contact in gpt_contacts:
            contact.setdefault("source", "gpt")
    except Exception:
        gpt_contacts = []

    regex_contacts = simple_regex_contacts(
        text,
        url,
        extra_emails=extra_emails,
        role_keywords=_safe_getattr(profile, "role_positive_keywords", []),
        country=country,
    )

    merged_contacts = await _cleanup_extracted_contacts(gpt_contacts + mailto_contacts + regex_contacts, profile, country=country)
    return url, merged_contacts, text


async def _fetch_and_extract_contacts(
    url: str,
    profile: Any,
    country: str | None = None,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    """
    Fetch a page once and run the staged extraction pipeline.
    """
    fetched = await fetch_and_extract_contacts(
        url,
        role_keywords=_safe_getattr(profile, "role_positive_keywords", []),
        country=country,
        allow_generic_emails=_safe_getattr(profile, "allow_generic_emails", False),
        use_llm=True,
        include_pagination=False,
    )
    extraction_result = fetched.extraction_result if fetched.pages_fetched > 0 else empty_extraction_result()
    acquisition = {
        "acquisition_modes": list(fetched.acquisition_modes),
        "shell_like": bool(fetched.shell_like),
        "weak_llm_shell_inference": bool(fetched.weak_llm_shell_inference),
        "visible_text_length": int(fetched.visible_text_length or 0),
        "embedded_text_length": int(fetched.embedded_text_length or 0),
        "embedded_document_count": int(fetched.embedded_document_count or 0),
    }
    return url, extraction_result, fetched.text, acquisition


async def _execute_candidate_page(
    state: AgentState,
    page: PlannedPage,
    profile: Any,
    budgets: AgentBudgets,
) -> AgentState:
    """
    Execute one candidate page:
    - fetch
    - extract contacts
    - apply existing keep_contact scoring
    - update agent buckets
    """
    if page.url in state.visited_urls:
        return state

    if state.pages_fetched >= budgets.max_pages_total:
        return evaluate_outcome(state, budgets)

    if page.url in state.dead_urls:
        _record_action(
            state,
            "visit_page",
            "skipped dead url",
            {"url": page.url, "page_type": page.page_type},
        )
        _note_pruned_candidate(
            state,
            page,
            "dead_url",
            {"url": page.url},
        )
        return state

    family_signature = _candidate_family_signature(page.url, page.page_type)
    if family_signature and family_signature not in state.dead_candidate_signatures and _is_persistently_dead_candidate_family(family_signature):
        state.dead_candidate_signatures.add(family_signature)
    if family_signature and family_signature in state.dead_candidate_signatures:
        _record_action(
            state,
            "visit_page",
            "skipped dead candidate family",
            {"url": page.url, "page_type": page.page_type, "family_signature": family_signature},
        )
        _note_pruned_candidate(
            state,
            page,
            "dead_candidate_family",
            {"family_signature": family_signature},
        )
        return state

    _record_mode(
        state,
        "staff_page_hunt" if any(token in page.url.lower() for token in ("staff", "people", "directory", "team")) else "office_discovery",
        "visiting_candidate_page",
        goal=f"inspect {page.url}",
    )
    _record_action(
        state,
        "visit_page",
        page.reason,
        {"url": page.url, "page_type": page.page_type, "expected_yield": page.expected_yield},
    )
    state.visited_urls.add(page.url)
    state.pages_fetched += 1

    page_url, extraction_result, text, acquisition = await _fetch_and_extract_contacts(
        page.url,
        profile,
        country=getattr(state.target, "country", None),
    )
    if not text and not acquisition.get("acquisition_modes"):
        state.dead_urls.add(page.url)
    home_dom = home_domain_of(state.homepage_url)
    typed_candidates = [dict(candidate) for candidate in extraction_result.get("typed_candidates", [])]

    kept_contacts: list[dict] = []
    rejected_count = 0
    page_trace = {
        "url": page_url,
        "reason": page.reason,
        "page_type": page.page_type,
        "source_strategy": page.source_strategy,
        "source_stage": page.source_stage,
        "parent_url": page.parent_url,
        "shell_context_key": _shell_context_key(page, state.homepage_url),
        "page_family": page.page_family,
        "expected_yield": page.expected_yield,
        "family_signature": family_signature,
        "raw_contacts_found": len(extraction_result.get("raw_evidence", [])),
        "text_length": len(text or ""),
        "acquisition_modes": list(acquisition.get("acquisition_modes", [])),
        "shell_like": bool(acquisition.get("shell_like")),
        "weak_llm_shell_inference": bool(acquisition.get("weak_llm_shell_inference")),
        "visible_text_length": int(acquisition.get("visible_text_length", 0) or 0),
        "embedded_text_length": int(acquisition.get("embedded_text_length", 0) or 0),
        "embedded_document_count": int(acquisition.get("embedded_document_count", 0) or 0),
        "raw_evidence": extraction_result.get("raw_evidence", []),
        "assembled_candidates": extraction_result.get("assembled_candidates", []),
        "cleaned_candidates": extraction_result.get("cleaned_candidates", []),
        "typed_candidates": typed_candidates,
        "raw_evidence_count_by_strategy": extraction_result.get("raw_evidence_count_by_strategy", {}),
        "potential_anchor_patterns": extraction_result.get("potential_anchor_patterns", []),
        "potential_anchor_pattern_count": int(extraction_result.get("potential_anchor_pattern_count", 0) or 0),
        "assembled_candidate_count": int(extraction_result.get("assembled_candidate_count", 0) or 0),
        "clean_candidate_count": int(extraction_result.get("clean_candidate_count", 0) or 0),
        "named_contact_count": int(extraction_result.get("named_contact_count", 0) or 0),
        "office_contact_count": int(extraction_result.get("office_contact_count", 0) or 0),
        "person_without_email_count": int(extraction_result.get("person_without_email_count", 0) or 0),
        "junk_candidate_count": int(extraction_result.get("junk_candidate_count", 0) or 0),
        "kept_contacts": [],
        "rejected_contacts": [],
        "missing_email_candidates": [],
        "role_holder_candidates": [],
    }

    for contact in typed_candidates:
        candidate_type = str(contact.get("candidate_type", "")).strip()
        email = (contact.get("email") or "").strip().lower()
        contact_context = str(contact.get("context") or "").strip() or text[:1500]
        contact["country"] = getattr(state.target, "country", None)
        if candidate_type == "person_without_email":
            candidate = build_person_candidate(
                contact,
                page_url,
                contact_context,
                page.expected_yield,
                country=getattr(state.target, "country", None),
            )
            if candidate:
                candidate.update(
                    {
                        "source_strategies": list(contact.get("source_strategies", [])),
                        "candidate_type": candidate_type,
                        "cleanup_flags": list(contact.get("cleanup_flags", [])),
                        "raw_name": str(contact.get("raw_name", "")),
                        "raw_role": str(contact.get("raw_role", "")),
                        "clean_name": str(contact.get("clean_name", contact.get("name", ""))),
                    }
                )
                exists = any(
                    str(existing.get("name", "")).lower() == str(candidate.get("name", "")).lower()
                    and str(existing.get("role", "")).lower() == str(candidate.get("role", "")).lower()
                    for existing in state.person_candidates
                )
                if not exists:
                    state.person_candidates.append(candidate)
                    state.missing_email_candidates.append(candidate)
                contact["missing_email_candidate"] = True
                page_trace["missing_email_candidates"].append(candidate)
            continue
        if candidate_type not in {"named_contact", "office_contact"}:
            continue

        contact["reached_filtering"] = True

        decision = explain_contact_decision(
            contact,
            home_dom,
            min_score=_safe_getattr(profile, "min_contact_score", None),
            allow_generic=_safe_getattr(profile, "allow_generic_emails", False),
            extra_positive=_safe_getattr(profile, "role_positive_keywords", []),
            extra_negative=_safe_getattr(profile, "role_negative_keywords", []),
            country=getattr(state.target, "country", None),
        )
        ok = bool(decision["keep"])
        score = int(decision["score"])
        reason = str(decision["reason"])
        decision_record = {
            "name": contact.get("name", ""),
            "role": contact.get("role", ""),
            "email": email,
            "page_url": contact.get("page_url", page_url),
            "score": score,
            "reason": reason,
            "threshold": decision.get("threshold"),
            "domain_match": decision.get("domain_match"),
            "is_generic": decision.get("is_generic"),
            "candidate_type": candidate_type,
            "source_strategies": list(contact.get("source_strategies", [])),
            "cleanup_flags": list(contact.get("cleanup_flags", [])),
            "evidence_type": contact.get("evidence_type"),
            "raw_name": contact.get("raw_name", ""),
            "raw_role": contact.get("raw_role", ""),
            "clean_name": contact.get("clean_name", ""),
            "email_normalized": contact.get("email_normalized", email),
        }

        if not ok:
            state.rejected_contacts.append(decision_record)
            page_trace["rejected_contacts"].append(decision_record)
            rejected_count += 1
            continue

        contact_copy = dict(contact)
        contact_copy["source_url"] = contact.get("page_url") or page_url
        contact_copy["page_url"] = contact.get("page_url") or page_url
        contact_copy["page_context"] = contact_context
        contact_copy["context"] = contact_context
        contact_copy["score"] = score
        contact_copy["reason"] = reason
        contact_copy["email_source"] = "direct"
        contact_copy["confidence"] = "high"
        contact_copy["evidence_url"] = contact.get("page_url") or page_url
        contact_copy["evidence_type"] = contact.get("evidence_type") or "page"
        contact_copy["recovery_reason"] = "direct email observed on page"
        contact_copy["candidate_status"] = "direct_contact"
        if candidate_type == "office_contact":
            role_holder_candidate = build_role_holder_candidate(
                contact_copy,
                page_url,
                contact_context,
                page.expected_yield,
                country=getattr(state.target, "country", None),
            )
            if role_holder_candidate:
                exists = any(
                    str(existing.get("office_email", existing.get("email", ""))).lower()
                    == str(role_holder_candidate.get("office_email", role_holder_candidate.get("email", ""))).lower()
                    and str(existing.get("role", "")).lower() == str(role_holder_candidate.get("role", "")).lower()
                    for existing in state.role_holder_candidates
                )
                if not exists:
                    state.role_holder_candidates.append(role_holder_candidate)
                    page_trace["role_holder_candidates"].append(role_holder_candidate)
        kept_contacts.append(contact_copy)
        page_trace["kept_contacts"].append(decision_record)

    state = update_contact_buckets(state, kept_contacts)
    state.extraction_trace.append(page_trace)

    state.evidence_log.append(
        {
            "phase": "execute",
            "url": page_url,
            "reason": page.reason,
            "page_type": page.page_type,
            "source_strategy": page.source_strategy,
            "source_stage": page.source_stage,
            "parent_url": page.parent_url,
            "page_family": page.page_family,
            "expected_yield": page.expected_yield,
            "raw_contacts_found": len(extraction_result.get("raw_evidence", [])),
            "kept_contacts_found": len(kept_contacts),
            "rejected_contacts": rejected_count,
        }
    )

    page_text_signature = _page_text_signature(text)
    page_trace["content_signature"] = page_text_signature
    existing_url = ""
    if page_text_signature:
        existing_url = str(state.seen_content_signatures.get(page_text_signature, "") or "")
        if existing_url and existing_url != page_url:
            page_trace["duplicate_of"] = existing_url
        else:
            state.seen_content_signatures[page_text_signature] = page_url

    if page_text_signature and _looks_like_zero_evidence_shell(page_trace):
        signature_state = state.repeated_zero_evidence_signatures.setdefault(
            page_text_signature,
            {"urls": [], "shell_contexts": [], "page_families": []},
        )
        if page_url not in signature_state["urls"]:
            signature_state["urls"].append(page_url)
        shell_context_key = str(page_trace.get("shell_context_key", "") or "")
        if shell_context_key and shell_context_key not in signature_state["shell_contexts"]:
            signature_state["shell_contexts"].append(shell_context_key)
        page_family = str(page.page_family or "")
        if page_family and page_family not in signature_state["page_families"]:
            signature_state["page_families"].append(page_family)
        signature_state["count"] = len(signature_state["urls"])
        page_trace["zero_evidence_shell"] = True
        page_trace["zero_evidence_shell_repeat_count"] = signature_state["count"]
        if signature_state["count"] >= ZERO_EVIDENCE_SHELL_DUPLICATE_THRESHOLD and shell_context_key:
            state.dead_shell_contexts.add(shell_context_key)
            page_trace["shell_context_pruned"] = True
            _note_pruned_candidate(
                state,
                page,
                "repeated_zero_evidence_shell_content",
                {
                    "content_signature": page_text_signature,
                    "duplicate_of": existing_url or signature_state["urls"][0],
                    "shell_context_key": shell_context_key,
                    "repeat_count": signature_state["count"],
                },
            )
    if family_signature and not text.strip():
        _mark_dead_candidate_family(
            state,
            page,
            "zero_text_heuristic_family",
            {"text_length": 0},
        )
    elif family_signature and page_text_signature:
        if existing_url and existing_url != page_url:
            _mark_dead_candidate_family(
                state,
                page,
                "duplicate_content_heuristic_family",
                {"duplicate_of": existing_url},
            )

    return evaluate_outcome(state, budgets)


async def _recover_candidate_via_site_search(state: AgentState, profile: Any, candidate: dict[str, Any]) -> AgentState:
    label = candidate.get("name") or candidate.get("role") or candidate.get("office_email") or ""
    action = str(candidate.get("next_action") or "search_people_pages")
    action_reason = {
        "search_governance_pages": "governance-first enrichment",
        "search_international_pages": "international-page enrichment",
        "search_people_pages": "people-page enrichment",
        "query_directory_by_name": "directory lookup after name resolution",
    }.get(action, "site-first enrichment")
    _record_mode(state, "person_enrichment", action, goal=f"recover {label}")
    _record_action(state, action, action_reason, {"candidate": label})
    outcome = await search_site_for_person(state.target, profile, candidate, action=action)
    attempt_key = {
        "search_governance_pages": "governance",
        "search_international_pages": "international",
        "search_people_pages": "people",
        "query_directory_by_name": "directory",
    }.get(action, "people")
    candidate["attempts"][attempt_key] = int(candidate.get("attempts", {}).get(attempt_key, 0) or 0) + 1
    candidate["action_budget_remaining"] = max(0, int(candidate.get("action_budget_remaining", 0) or 0) - 1)
    state.search_attempts.append(outcome)
    state.enrichment_trace.append({"candidate": label, "phase": action, "outcome": outcome})

    recovered = apply_evidence_to_candidate(candidate, outcome.get("evidence_items", []), "site_search")
    if recovered:
        recovered["score"] = int(candidate.get("expected_yield", 0.0) or 0) + 6
        recovered["reason"] = "role_holder_site_search_recovery" if candidate.get("candidate_kind") == "role_holder" else "site_search_recovery"
        recovered["source_url"] = recovered.get("evidence_url") or recovered.get("source_url")
        state = update_contact_buckets(state, [recovered])
        return state
    if candidate.get("status") == "pattern_pending":
        return state
    if candidate.get("action_budget_remaining", 0) <= 0:
        candidate["status"] = "exhausted"
        candidate["blocked_reason"] = "action_budget_exhausted"
    else:
        candidate["status"] = "site_exhausted"
        candidate["next_action"] = None
    return state


async def _recover_candidate_via_web_search(state: AgentState, candidate: dict[str, Any]) -> AgentState:
    label = candidate.get("name") or candidate.get("role") or candidate.get("office_email") or ""
    _record_mode(state, "person_enrichment", "site_search_exhausted", goal=f"web-search recover {label}")
    _record_action(state, "search_web_for_person", "broader web enrichment", {"candidate": label})
    outcome = await search_web_for_person(state.target, candidate)
    candidate["attempts"]["web_search"] = int(candidate.get("attempts", {}).get("web_search", 0) or 0) + 1
    candidate["action_budget_remaining"] = max(0, int(candidate.get("action_budget_remaining", 0) or 0) - 1)
    state.search_attempts.append(outcome)
    state.enrichment_trace.append({"candidate": label, "phase": "web_search", "outcome": outcome})

    recovered = apply_evidence_to_candidate(candidate, outcome.get("evidence_items", []), "web_search")
    if recovered:
        recovered["score"] = int(candidate.get("expected_yield", 0.0) or 0) + 5
        recovered["reason"] = "role_holder_web_search_recovery" if candidate.get("candidate_kind") == "role_holder" else "web_search_recovery"
        recovered["source_url"] = recovered.get("evidence_url") or recovered.get("source_url")
        state = update_contact_buckets(state, [recovered])
        return state
    if candidate.get("status") == "pattern_pending":
        return state
    if candidate.get("action_budget_remaining", 0) <= 0:
        candidate["status"] = "exhausted"
        candidate["blocked_reason"] = "action_budget_exhausted"
    else:
        candidate["status"] = "web_exhausted"
        candidate["next_action"] = None
    return state


async def _recover_candidate_via_pattern(state: AgentState, candidate: dict[str, Any]) -> AgentState:
    _record_mode(state, "email_pattern_recovery", "search_exhausted", goal=f"infer email for {candidate.get('name', '')}")
    _record_action(state, "infer_email_pattern", "recover from known organisation patterns", {"candidate": candidate.get("name", "")})
    inferred = infer_email_pattern(candidate, state.qualified_contacts, home_domain_of(state.homepage_url))
    candidate["attempts"]["pattern"] = int(candidate.get("attempts", {}).get("pattern", 0) or 0) + 1
    candidate["action_budget_remaining"] = max(0, int(candidate.get("action_budget_remaining", 0) or 0) - 1)
    state.pattern_inference_trace.append({"candidate": candidate.get("name", ""), "outcome": inferred})
    if not inferred.get("resolved"):
        candidate["status"] = "exhausted"
        candidate["blocked_reason"] = inferred.get("reason", "pattern_inference_failed")
        return state

    candidate["status"] = "recovered"
    recovered = {
        "name": candidate.get("name", ""),
        "role": candidate.get("role", ""),
        "email": inferred["email"],
        "page_url": candidate.get("page_url", ""),
        "source_url": candidate.get("page_url", ""),
        "evidence_url": candidate.get("page_url", ""),
        "evidence_type": inferred.get("evidence_type", "email_pattern"),
        "email_source": "inferred",
        "confidence": inferred.get("confidence", "low"),
        "recovery_reason": inferred.get("recovery_reason", ""),
        "candidate_status": "recovered_contact",
        "candidate_type": (
            "named_contact"
            if looks_like_person_name(str(candidate.get("name", "")), country=getattr(state.target, "country", None))
            else str(candidate.get("candidate_type", ""))
        ),
        "source_strategies": list(candidate.get("source_strategies", [])),
        "cleanup_flags": list(candidate.get("cleanup_flags", [])),
        "raw_name": candidate.get("raw_name", candidate.get("name", "")),
        "raw_role": candidate.get("raw_role", candidate.get("role", "")),
        "clean_name": candidate.get("clean_name", candidate.get("name", "")),
        "email_normalized": inferred["email"],
        "context": candidate.get("page_context", ""),
        "page_context": candidate.get("page_context", ""),
        "score": int(candidate.get("expected_yield", 0.0) or 0) + (4 if inferred.get("confidence") == "medium" else 3),
        "reason": "pattern_inference_recovery",
    }
    state.inferred_email_patterns[candidate.get("name", "")] = inferred
    state = update_contact_buckets(state, [recovered])
    return state


def _next_pending_candidate(state: AgentState) -> dict[str, Any] | None:
    pending = _pending_person_candidates(state)
    if not pending:
        return None
    actionable: list[dict[str, Any]] = []
    for candidate in pending:
        planned = plan_next_enrichment_action(candidate)
        if planned:
            actionable.append(candidate)
    if not actionable:
        return None
    actionable.sort(key=_enrichment_candidate_sort_key)
    return actionable[0]


async def _execute_next_best_action(
    state: AgentState,
    profile: Any,
    budgets: AgentBudgets,
    queued_pages: list[PlannedPage],
) -> tuple[AgentState, list[PlannedPage], bool]:
    queued_pages = _sort_pages_for_execution(state, _prune_candidate_pages(state, queued_pages))

    candidate = _next_pending_candidate(state)
    if candidate:
        next_action = str(candidate.get("next_action") or "")
        if next_action in {
            "search_governance_pages",
            "search_international_pages",
            "search_people_pages",
            "query_directory_by_name",
        }:
            return await _recover_candidate_via_site_search(state, profile, candidate), queued_pages, True
        if next_action == "search_web_for_person":
            return await _recover_candidate_via_web_search(state, candidate), queued_pages, True
        if next_action == "infer_email_pattern":
            return await _recover_candidate_via_pattern(state, candidate), queued_pages, True

    if queued_pages:
        page = queued_pages.pop(0)
        return await _execute_candidate_page(state, page, profile, budgets), queued_pages, True

    if not should_stop(state):
        gap = await gap_fill_plan(state, profile, budgets)
        state.planner_history.append(
            {"phase": "gap_fill", "strategy": gap.strategy, "ranked_pages": [page.url for page in gap.ranked_pages]}
        )
        _record_action(state, "rank_candidates", "gap_fill", {"count": len(gap.ranked_pages)})
        queued_pages = _sort_pages_for_execution(state, _prune_candidate_pages(state, gap.ranked_pages))
        if queued_pages:
            return state, queued_pages, True

    state.stop_reason = state.failure_reason or "no_high_value_actions_remaining"
    _record_mode(state, "stop", "no_actions_remaining", goal=state.stop_reason)
    _record_action(state, "stop_with_reason", state.stop_reason, {})
    return evaluate_outcome(state, budgets), queued_pages, False


async def execute_plan(
    state: AgentState,
    plan: ScoutPlan,
    profile: Any,
    budgets: AgentBudgets,
) -> AgentState:
    """
    Execute the scout plan deterministically.
    """
    state.queued_candidates = list(plan.ranked_pages)

    ordered_pages = _sort_pages_for_execution(state, _prune_candidate_pages(state, plan.ranked_pages))

    for page in ordered_pages:
        if should_stop(state):
            break
        state = await _execute_candidate_page(state, page, profile, budgets)

    return evaluate_outcome(state, budgets)


async def gap_fill_plan(state: AgentState, profile: Any, budgets: AgentBudgets) -> GapPlan:
    """
    Simple bounded gap-fill plan.

    For now this re-runs discovery and proposes unvisited pages from the next
    tier of candidates rather than using an LLM planner.
    """
    if should_stop(state):
        return GapPlan(
            strategy="not_needed",
            missing_roles=[],
            alternate_page_patterns=[],
            ranked_pages=[],
            fallback_generic_allowed=False,
        )

    discovery_mode = _safe_getattr(profile, "discovery_mode", "hybrid")
    bundle = await gather_candidates_bundle(
        state.homepage_url,
        extra_slugs=_safe_getattr(profile, "slug_hints", []),
        country=getattr(state.target, "country", None),
        mode=discovery_mode,
        include_strategy_breakdown=False,
        target_name=state.target.name,
    )
    candidates = bundle.get("candidates", [])

    ranked_pages: list[PlannedPage] = []
    candidates_sorted = sorted(candidates, key=_candidate_sort_key)
    for candidate in candidates_sorted:
        if _is_search_interface_candidate(candidate):
            _remember_deferred_search_interface(state, candidate, "gap_fill_search_interface_requires_target")
            continue
        url = candidate.get("url", "")
        if not url or url in state.visited_urls:
            continue

        page = PlannedPage(
            url=url,
            reason=f"gap_fill:{candidate.get('source_strategy', candidate.get('source_type', 'unknown'))}",
            expected_yield=float(candidate.get("heuristic_score", 0.0)),
            expected_roles=_expected_roles_for_org_type(state.org_type),
            page_type=str(candidate.get("source_type", "unknown")),
            source_strategy=str(candidate.get("source_strategy", "")),
            source_stage=str(candidate.get("source_stage", "")),
            parent_url=str(candidate.get("parent_url", "")),
            page_family=str(candidate.get("page_family", "generic")),
        )
        signature = _candidate_family_signature(page.url, page.page_type)
        if signature and signature in state.dead_candidate_signatures:
            _note_pruned_candidate(
                state,
                page,
                "dead_candidate_family",
                {"family_signature": signature},
            )
            continue
        ranked_pages.append(page)

        if len(ranked_pages) >= budgets.max_gap_fill_pages:
            break

    return GapPlan(
        strategy="deterministic_unvisited_candidates",
        missing_roles=[],
        alternate_page_patterns=[],
        ranked_pages=ranked_pages,
        fallback_generic_allowed=_safe_getattr(profile, "allow_generic_emails", False),
    )


async def execute_gap_plan(
    state: AgentState,
    gap_plan: GapPlan,
    profile: Any,
    budgets: AgentBudgets,
) -> AgentState:
    ordered_pages = sorted(
        _prune_candidate_pages(state, gap_plan.ranked_pages),
        key=lambda p: _page_sort_key(p, state),
    )

    for page in ordered_pages:
        if should_stop(state):
            break
        state = await _execute_candidate_page(state, page, profile, budgets)

    return evaluate_outcome(state, budgets)


async def verify_and_rank(state: AgentState, profile: Any, budgets: AgentBudgets) -> AgentState:
    """
    Deterministic final ranking for now.
    """
    ranked: list[RankedContact] = []

    deduped_by_email: dict[str, dict] = {}
    for contact in state.qualified_contacts:
        email = str(contact.get("email", "")).strip().lower()
        if not email:
            continue

        existing = deduped_by_email.get(email)
        if existing is None:
            deduped_by_email[email] = contact
            continue

        existing_score = int(existing.get("score", 0) or 0)
        new_score = int(contact.get("score", 0) or 0)
        if new_score > existing_score:
            deduped_by_email[email] = contact
            state.deduped_contacts.append(
                {
                    "email": email,
                    "kept_score": new_score,
                    "dropped_score": existing_score,
                    "reason": "higher score duplicate kept",
                }
            )

    for contact in deduped_by_email.values():
        priority = contact.get("priority") or contact_priority(contact, state.org_type)
        ranked.append(
            RankedContact(
                email=str(contact.get("email", "")),
                source_url=str(contact.get("source_url", contact.get("page_url", state.homepage_url))),
                priority=str(priority),
                reason=str(contact.get("reason", "qualified_contact")),
                name=contact.get("name"),
                title=contact.get("title") or contact.get("role") or contact.get("position"),
                qualification_level="qualified",
                confidence=str(contact.get("confidence", "high")),
                email_source=str(contact.get("email_source", "direct")),
                evidence_url=contact.get("evidence_url") or contact.get("source_url") or contact.get("page_url"),
                evidence_type=contact.get("evidence_type"),
                recovery_reason=contact.get("recovery_reason"),
                candidate_status=str(contact.get("candidate_status", "direct_contact")),
            )
        )

    ranked.sort(key=lambda c: {"high": 2, "medium": 1, "ignore": 0}.get(c.priority, 0), reverse=True)
    state.ranked_contacts = ranked

    return evaluate_outcome(state, budgets)


def agent_state_to_debug_payload(state: AgentState) -> dict[str, Any]:
    """Convert a completed agent state into the canonical trace payload."""
    return {
            "target": {
                "name": state.target.name,
                "url": state.homepage_url,
                "source_url": state.source_homepage_url or state.homepage_url,
                "org_type": state.org_type,
                "country": state.target.country,
                "source": state.target.source,
                "profile": state.profile_name,
            },
            "outcome": {
                "hard_success": state.hard_success,
                "soft_success": state.soft_success,
                "failed": state.failed,
                "failure_reason": state.failure_reason,
                "pages_fetched": state.pages_fetched,
                "llm_calls": state.llm_calls,
                "ranked_contacts": len(state.ranked_contacts),
                "qualified_contacts": len(state.qualified_contacts),
            },
            "discovery_trace": state.discovery_trace,
            "discovery_strategy_trace": state.discovery_strategy_trace,
            "homepage_rescue_trace": state.homepage_rescue_trace,
            "dead_urls": sorted(state.dead_urls),
            "mode_history": state.mode_history,
            "action_history": state.action_history,
            "planner_history": state.planner_history,
            "evidence_log": state.evidence_log,
            "extraction_trace": state.extraction_trace,
            "pruned_candidates": state.pruned_candidates,
            "person_candidates": state.person_candidates,
            "role_holder_candidates": state.role_holder_candidates,
            "enrichment_trace": state.enrichment_trace,
            "search_attempts": state.search_attempts,
            "pattern_inference_trace": state.pattern_inference_trace,
            "missing_email_candidates": state.missing_email_candidates,
            "rejected_contacts": state.rejected_contacts,
            "deduped_contacts": state.deduped_contacts,
            "dead_candidate_signatures": sorted(state.dead_candidate_signatures),
            "dead_shell_contexts": sorted(state.dead_shell_contexts),
            "repeated_zero_evidence_signatures": state.repeated_zero_evidence_signatures,
            "stop_reason": state.stop_reason,
            "ranked_contacts": [
                {
                    "email": contact.email,
                    "name": contact.name,
                    "title": contact.title,
                    "priority": contact.priority,
                    "reason": contact.reason,
                    "source_url": contact.source_url,
                    "confidence": contact.confidence,
                    "email_source": contact.email_source,
                    "evidence_url": contact.evidence_url,
                    "evidence_type": contact.evidence_type,
                    "recovery_reason": contact.recovery_reason,
                    "candidate_status": contact.candidate_status,
                }
                for contact in state.ranked_contacts
            ],
            "final_contacts_with_provenance": state.final_contacts_with_provenance,
        }


async def write_agent_debug_trace(state: AgentState) -> None:
    """Persist per-target agent trace to debug JSON when enabled."""
    await write_debug_json(state.target.name, agent_state_to_debug_payload(state))


async def run_nafsa_agent(
    target: Target,
    profile: Any,
    budgets: AgentBudgets | None = None,
) -> AgentState:
    """
    Real bounded NAFSA agent controller using:
    - deterministic scout
    - deterministic execute
    - deterministic gap fill
    - deterministic verify/rank

    This gives you an actual multi-phase pipeline now, while still keeping
    LLM-heavy planning for a later iteration.
    """
    budgets = budgets or AgentBudgets()
    state = _build_initial_state(target, profile)

    scout = await scout_plan(state, profile, budgets)
    state.planner_history.append(
        {
            "phase": "scout",
            "strategy": scout.strategy,
            "expected_roles": scout.expected_roles,
            "stop_hint": scout.stop_hint,
            "ranked_pages": [
                {
                    "url": page.url,
                    "reason": page.reason,
                    "expected_yield": page.expected_yield,
                    "page_type": page.page_type,
                }
                for page in scout.ranked_pages
            ],
        }
    )
    _record_action(state, "rank_candidates", scout.strategy, {"count": len(scout.ranked_pages), "phase": "scout"})

    queued_pages = _sort_pages_for_execution(state, _prune_candidate_pages(state, scout.ranked_pages))
    loop_guard = budgets.max_pages_total + budgets.max_gap_fill_pages + 12
    while loop_guard > 0 and (not should_stop(state) or _should_continue_enrichment(state)):
        loop_guard -= 1
        state, queued_pages, progressed = await _execute_next_best_action(state, profile, budgets, queued_pages)
        state = evaluate_outcome(state, budgets)
        if not progressed:
            break
        if _needs_senior_contact(state):
            state.current_goal = "find senior partnership lead"

    state = await verify_and_rank(state, profile, budgets)
    state = evaluate_outcome(state, budgets)
    state.stop_reason = state.stop_reason or state.failure_reason or (
        "high_confidence_coverage_reached" if state.hard_success or state.soft_success else "loop_completed"
    )
    state.final_contacts_with_provenance = [
        {
            "email": contact.email,
            "name": contact.name,
            "title": contact.title,
            "priority": contact.priority,
            "reason": contact.reason,
            "source_url": contact.source_url,
            "confidence": contact.confidence,
            "email_source": contact.email_source,
            "evidence_url": contact.evidence_url,
            "evidence_type": contact.evidence_type,
            "recovery_reason": contact.recovery_reason,
            "candidate_status": contact.candidate_status,
        }
        for contact in state.ranked_contacts
    ]
    if state.mode != "stop":
        _record_mode(state, "stop", "run_complete", goal=state.stop_reason)
    return state
