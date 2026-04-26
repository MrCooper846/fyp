from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gc_contacts.core.models import Target


@dataclass
class PlannedPage:
    url: str
    reason: str
    expected_yield: float = 0.0
    expected_roles: list[str] = field(default_factory=list)
    page_type: str = "unknown"
    source_strategy: str = ""
    source_stage: str = ""
    parent_url: str = ""
    page_family: str = "generic"


@dataclass
class ScoutPlan:
    strategy: str
    org_type: str
    expected_roles: list[str] = field(default_factory=list)
    ranked_pages: list[PlannedPage] = field(default_factory=list)
    stop_hint: str | None = None


@dataclass
class GapPlan:
    strategy: str
    missing_roles: list[str] = field(default_factory=list)
    alternate_page_patterns: list[str] = field(default_factory=list)
    ranked_pages: list[PlannedPage] = field(default_factory=list)
    fallback_generic_allowed: bool = False


@dataclass
class RankedContact:
    email: str
    source_url: str
    priority: str  # "high", "medium", "ignore"
    reason: str
    name: str | None = None
    title: str | None = None
    qualification_level: str = "unknown"  # e.g. raw, valid, qualified
    confidence: str = "high"
    email_source: str = "direct"
    evidence_url: str | None = None
    evidence_type: str | None = None
    recovery_reason: str | None = None
    candidate_status: str = "direct_contact"


@dataclass
class AgentState:
    target: Target
    org_type: str
    profile_name: str

    homepage_url: str
    source_homepage_url: str = ""
    homepage_text: str = ""
    homepage_rescue_trace: list[dict[str, Any]] = field(default_factory=list)

    visited_urls: set[str] = field(default_factory=set)
    dead_urls: set[str] = field(default_factory=set)
    queued_candidates: list[PlannedPage] = field(default_factory=list)

    discovered_contacts: list[dict[str, Any]] = field(default_factory=list)
    valid_contacts: list[dict[str, Any]] = field(default_factory=list)
    qualified_contacts: list[dict[str, Any]] = field(default_factory=list)
    ranked_contacts: list[RankedContact] = field(default_factory=list)

    planner_history: list[dict[str, Any]] = field(default_factory=list)
    evidence_log: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "office_discovery"
    current_goal: str | None = None
    stop_reason: str | None = None
    mode_history: list[dict[str, Any]] = field(default_factory=list)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    discovery_trace: list[dict[str, Any]] = field(default_factory=list)
    discovery_strategy_trace: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    deferred_search_interfaces: list[dict[str, Any]] = field(default_factory=list)
    extraction_trace: list[dict[str, Any]] = field(default_factory=list)
    pruned_candidates: list[dict[str, Any]] = field(default_factory=list)
    rejected_contacts: list[dict[str, Any]] = field(default_factory=list)
    missing_email_candidates: list[dict[str, Any]] = field(default_factory=list)
    deduped_contacts: list[dict[str, Any]] = field(default_factory=list)
    person_candidates: list[dict[str, Any]] = field(default_factory=list)
    role_holder_candidates: list[dict[str, Any]] = field(default_factory=list)
    enrichment_trace: list[dict[str, Any]] = field(default_factory=list)
    search_attempts: list[dict[str, Any]] = field(default_factory=list)
    pattern_inference_trace: list[dict[str, Any]] = field(default_factory=list)
    final_contacts_with_provenance: list[dict[str, Any]] = field(default_factory=list)
    inferred_email_patterns: dict[str, Any] = field(default_factory=dict)
    dead_candidate_signatures: set[str] = field(default_factory=set)
    dead_shell_contexts: set[str] = field(default_factory=set)
    seen_content_signatures: dict[str, str] = field(default_factory=dict)
    repeated_zero_evidence_signatures: dict[str, Any] = field(default_factory=dict)

    pages_fetched: int = 0
    llm_calls: int = 0
    fallback_turns: int = 0

    hard_success: bool = False
    soft_success: bool = False
    failed: bool = False
    failure_reason: str | None = None
