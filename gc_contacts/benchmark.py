"""
Benchmark framework for comparing localized crawl methods.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd
from tqdm.asyncio import tqdm as async_tqdm

import gc_contacts.config as config
from gc_contacts.agent import AgentBudgets, run_nafsa_agent
from gc_contacts.core.harvest import crawl_target_direct, probe_candidate_pages
from gc_contacts.core.http_client import bs_text, fetch_page
from gc_contacts.core.llm import gpt_suggest_slugs
from gc_contacts.core.models import Target
from gc_contacts.core.utils import cost_for_tokens, tokens_of
from gc_contacts.profiles.academic_profile import ACADEMIC_PROFILE

LOG = logging.getLogger("gc")

DIRECT_METHOD_MODES = {
    "heuristic": {"discovery_mode": "heuristic_only", "use_llm": False},
    "ai_slug": {"discovery_mode": "generated_slug_only", "use_llm": True},
}
AGENT_METHODS = {"agent", "ai_crawler"}
SUMMARY_ORDER = ["heuristic", "ai_slug", "agent", "ai_crawler"]


@dataclass
class DiscoveryResult:
    """Result from trying one discovery method."""

    method: str
    university_name: str
    homepage_url: str
    candidates_found: int
    probe_attempts: int
    candidates_probed: int
    contacts_extracted: int
    contacts_kept: int
    time_seconds: float
    candidates_ranked: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_dollars: float = 0.0
    best_url: Optional[str] = None
    status: str = "ok"
    error: str = ""
    source_breakdown: Dict[str, int] = field(default_factory=dict)
    contacts_list: List[Dict] = field(default_factory=list)
    diagnostics: Dict[str, object] = field(default_factory=dict)


@dataclass
class ComparisonReport:
    """Summary comparison across benchmark methods."""

    results: List[DiscoveryResult] = field(default_factory=list)
    run_name: Optional[str] = None
    run_dir: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)

    def add(self, result: DiscoveryResult):
        self.results.append(result)

    def summary_by_method(self) -> Dict[str, dict]:
        by_method = {}

        for result in self.results:
            stats = by_method.setdefault(
                result.method,
                {
                    "count": 0,
                    "ok_count": 0,
                    "failed_count": 0,
                    "timed_out_count": 0,
                    "total_candidates": 0,
                    "total_candidates_ranked": 0,
                    "total_probe_attempts": 0,
                    "total_candidates_probed": 0,
                    "total_contacts_extracted": 0,
                    "total_contacts_kept": 0,
                    "total_time": 0.0,
                    "total_tokens_in": 0,
                    "total_tokens_out": 0,
                    "total_cost": 0.0,
                    "success_rate": 0.0,
                    "avg_contacts_per_uni": 0.0,
                    "avg_time_per_uni": 0.0,
                    "avg_cost_per_uni": 0.0,
                },
            )
            stats["count"] += 1
            if result.status == "ok":
                stats["ok_count"] += 1
            elif result.status == "timeout":
                stats["timed_out_count"] += 1
            else:
                stats["failed_count"] += 1
            stats["total_candidates"] += result.candidates_found
            stats["total_candidates_ranked"] += result.candidates_ranked
            stats["total_probe_attempts"] += result.probe_attempts
            stats["total_candidates_probed"] += result.candidates_probed
            stats["total_contacts_extracted"] += result.contacts_extracted
            stats["total_contacts_kept"] += result.contacts_kept
            stats["total_time"] += result.time_seconds
            stats["total_tokens_in"] += result.tokens_in
            stats["total_tokens_out"] += result.tokens_out
            stats["total_cost"] += result.cost_dollars

        for method, stats in by_method.items():
            count = stats["count"]
            if not count:
                continue
            stats["success_rate"] = sum(
                1 for result in self.results if result.method == method and result.contacts_kept >= 1
            ) / count
            stats["avg_contacts_per_uni"] = stats["total_contacts_kept"] / count
            stats["avg_time_per_uni"] = stats["total_time"] / count
            stats["avg_cost_per_uni"] = stats["total_cost"] / count

        return by_method

    def to_csv(self, filepath: Path):
        rows = []
        for result in self.results:
            rows.append(
                {
                    "method": result.method,
                    "university": result.university_name,
                    "status": result.status,
                    "error": result.error,
                    "candidates_found": result.candidates_found,
                    "candidates_ranked": result.candidates_ranked,
                    "probe_attempts": result.probe_attempts,
                    "candidates_probed": result.candidates_probed,
                    "contacts_extracted": result.contacts_extracted,
                    "contacts_kept": result.contacts_kept,
                    "time_seconds": round(result.time_seconds, 2),
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "cost_dollars": round(result.cost_dollars, 6),
                    "best_url": result.best_url or "",
                }
            )
        df = pd.DataFrame(rows)
        try:
            df.to_csv(filepath, index=False)
        except AttributeError:
            import csv

            with open(filepath, "w", newline="", encoding="utf-8") as handle:
                if rows:
                    writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)

    def export_contacts(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)

        for result in self.results:
            if not result.contacts_list:
                continue

            safe_uni_name = result.university_name.replace(" ", "_").replace("/", "_").lower()
            contact_file = output_dir / f"{result.method}_{safe_uni_name}_contacts.json"

            contacts_data = {
                "method": result.method,
                "university": result.university_name,
                "university_url": result.homepage_url,
                "timestamp": datetime.now().isoformat(),
                "summary": {
                    "status": result.status,
                    "error": result.error,
                    "total_kept": len(result.contacts_list),
                    "candidates_found": result.candidates_found,
                    "candidates_ranked": result.candidates_ranked,
                    "probe_attempts": result.probe_attempts,
                    "candidates_probed": result.candidates_probed,
                    "contacts_extracted": result.contacts_extracted,
                    "time_seconds": result.time_seconds,
                    "cost_dollars": result.cost_dollars,
                    "source_breakdown": result.source_breakdown,
                },
                "contacts": result.contacts_list,
            }

            with open(contact_file, "w", encoding="utf-8") as handle:
                json.dump(contacts_data, handle, indent=2, ensure_ascii=False)

        LOG.info("Contacts exported to %s", output_dir)

    def export_debug(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)

        for result in self.results:
            payload = {
                "method": result.method,
                "university": result.university_name,
                "homepage_url": result.homepage_url,
                "summary": {
                    "status": result.status,
                    "error": result.error,
                    "candidates_found": result.candidates_found,
                    "candidates_ranked": result.candidates_ranked,
                    "probe_attempts": result.probe_attempts,
                    "candidates_probed": result.candidates_probed,
                    "contacts_extracted": result.contacts_extracted,
                    "contacts_kept": result.contacts_kept,
                    "time_seconds": result.time_seconds,
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "cost_dollars": result.cost_dollars,
                    "best_url": result.best_url or "",
                    "source_breakdown": result.source_breakdown,
                },
                "contacts": result.contacts_list,
                "diagnostics": result.diagnostics,
            }
            safe_uni_name = result.university_name.replace(" ", "_").replace("/", "_").lower()
            debug_file = output_dir / f"{result.method}_{safe_uni_name}_debug.json"
            with open(debug_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)

        LOG.info("Benchmark debug traces exported to %s", output_dir)

    def print_summary(self):
        summary = self.summary_by_method()
        ordered_methods = [method for method in SUMMARY_ORDER if method in summary]
        ordered_methods.extend(method for method in summary if method not in ordered_methods)

        print("\n" + "=" * 100)
        print("DISCOVERY METHOD COMPARISON REPORT")
        print("=" * 100)

        for method in ordered_methods:
            stats = summary[method]
            print(f"\n{method.upper()}:")
            print(f"  Universities tested:        {stats['count']}")
            print(f"  Completed successfully:     {stats['ok_count']}")
            print(f"  Timed out:                  {stats['timed_out_count']}")
            print(f"  Failed:                     {stats['failed_count']}")
            print(f"  Success rate:               {stats['success_rate'] * 100:.1f}%")
            print(f"  Total candidates found:     {stats['total_candidates']}")
            print(f"  Total candidates ranked:    {stats['total_candidates_ranked']}")
            print(f"  Total probe attempts:       {stats['total_probe_attempts']}")
            print(f"  Successful probes:          {stats['total_candidates_probed']}")
            print(f"  Total contacts extracted:   {stats['total_contacts_extracted']}")
            print(f"  Total contacts kept:        {stats['total_contacts_kept']}")
            print(f"  Avg contacts per uni:       {stats['avg_contacts_per_uni']:.2f}")
            print(f"  Avg time per uni:           {stats['avg_time_per_uni']:.2f}s")
            print(f"  Total time:                 {stats['total_time']:.1f}s")
            print(f"  Total tokens:               {stats['total_tokens_in']}/{stats['total_tokens_out']}")
            print(f"  Total cost:                 ${stats['total_cost']:.4f}")
            print(f"  Avg cost per uni:           ${stats['avg_cost_per_uni']:.6f}")

        print("\n" + "=" * 100)


def _coerce_target(item: dict | Target, country: Optional[str] = None) -> Target:
    if isinstance(item, Target):
        return item
    return Target(
        name=str(item.get("name", "Unknown")),
        url=str(item.get("url", "")),
        country=str(item.get("country") or country or "").strip() or None,
        org_type=str(item.get("org_type", "university") or "university"),
        source=str(item.get("source", "") or "") or None,
    )


def _failure_result(
    target: Target,
    method: str,
    *,
    started_at: float,
    status: str,
    error: str = "",
    diagnostics: Optional[dict[str, object]] = None,
) -> DiscoveryResult:
    return DiscoveryResult(
        method=method,
        university_name=target.name,
        homepage_url=target.url,
        candidates_found=0,
        probe_attempts=0,
        candidates_probed=0,
        contacts_extracted=0,
        contacts_kept=0,
        time_seconds=max(0.0, time.time() - started_at),
        status=status,
        error=error,
        diagnostics=diagnostics or {},
    )


def _concurrency_limit(concurrent: bool | int) -> int:
    if isinstance(concurrent, bool):
        return 1 if not concurrent else 6
    return max(1, int(concurrent))


async def _suggest_ai_slug_hints(target: Target) -> tuple[list[str], int, int]:
    html = await fetch_page(target.url)
    if not html:
        return [], 0, 0
    text = bs_text(html)
    slugs = await gpt_suggest_slugs(text, target.url, country=target.country)
    return slugs, tokens_of(text[:1200]) + 200, 200


def _benchmark_contact_rows(contacts: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for contact in contacts:
        rows.append(
            {
                "name": str(contact.get("name", "")),
                "email": str(contact.get("email", "")),
                "role": str(contact.get("role", "")),
                "page_url": str(contact.get("page_url", "")),
                "score": int(contact.get("score", 0) or 0),
                "reason": str(contact.get("reason", "")),
            }
        )
    return rows


def _collector_counts(collector_breakdown: dict[str, list[dict]]) -> dict[str, int]:
    return {name: len(items or []) for name, items in (collector_breakdown or {}).items()}


def _candidate_rows(candidates: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for index, candidate in enumerate(candidates):
        rows.append(
            {
                "rank": index + 1,
                "url": candidate.get("url", ""),
                "source_type": candidate.get("source_type", ""),
                "source_strategy": candidate.get("source_strategy", ""),
                "source_stage": candidate.get("source_stage", ""),
                "page_family": candidate.get("page_family", ""),
                "heuristic_score": candidate.get("heuristic_score", 0.0),
                "anchor_text": candidate.get("anchor_text", ""),
                "parent_url": candidate.get("parent_url", ""),
                "source_strategies": candidate.get("source_strategies", []),
            }
        )
    return rows


def _page_probe_rows(page_results: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for page in page_results:
        candidate = page.get("candidate", {}) or {}
        rows.append(
            {
                "url": page.get("url", ""),
                "fetch_succeeded": bool(page.get("fetch_succeeded")),
                "pages_fetched": int(page.get("pages_fetched", 0) or 0),
                "raw_contacts": int(page.get("raw_contacts", 0) or 0),
                "kept_contacts": len(page.get("kept_contacts", []) or []),
                "rejected_contacts": len(page.get("rejected_contacts", []) or []),
                "missing_email_candidates": len(page.get("missing_email_candidates", []) or []),
                "page_length": int(page.get("page_length", 0) or 0),
                "visible_text_length": int(page.get("visible_text_length", 0) or 0),
                "embedded_text_length": int(page.get("embedded_text_length", 0) or 0),
                "embedded_document_count": int(page.get("embedded_document_count", 0) or 0),
                "acquisition_modes": list(page.get("acquisition_modes", []) or []),
                "shell_like": bool(page.get("shell_like")),
                "content_signature": str(page.get("content_signature", "") or ""),
                "content_signatures": list(page.get("content_signatures", []) or []),
                "zero_evidence_shell": bool(page.get("zero_evidence_shell")),
                "weak_llm_shell_inference": bool(page.get("weak_llm_shell_inference")),
                "duplicate_of": str(page.get("duplicate_of", "") or ""),
                "shell_context_key": str(page.get("shell_context_key", "") or ""),
                "mailto_count": int(page.get("mailto_count", 0) or 0),
                "candidate_source_type": candidate.get("source_type", ""),
                "candidate_source_strategy": candidate.get("source_strategy", ""),
                "candidate_source_stage": candidate.get("source_stage", ""),
                "candidate_page_family": candidate.get("page_family", ""),
                "candidate_heuristic_score": candidate.get("heuristic_score", 0.0),
                "source_breakdown": page.get("source_breakdown", {}),
                "kept_contact_rows": _benchmark_contact_rows(page.get("kept_contacts", []) or []),
                "rejected_contact_rows": _benchmark_contact_rows(page.get("rejected_contacts", []) or []),
                "missing_email_rows": list(page.get("missing_email_candidates", []) or []),
            }
        )
    return rows


async def _run_direct_method(
    target: Target,
    method: str,
    *,
    profile=ACADEMIC_PROFILE,
    max_candidates_to_probe: int = 10,
) -> DiscoveryResult:
    config_bits = DIRECT_METHOD_MODES[method]
    start = time.time()
    extra_slugs: list[str] = []
    slug_tokens_in = 0
    slug_tokens_out = 0

    if method == "ai_slug":
        extra_slugs, slug_tokens_in, slug_tokens_out = await _suggest_ai_slug_hints(target)

    crawl = await crawl_target_direct(
        target.url,
        country=target.country,
        discovery_mode=config_bits["discovery_mode"],
        extra_slugs=extra_slugs + list(getattr(profile, "slug_hints", []) or []),
        role_keywords=list(getattr(profile, "role_positive_keywords", []) or []),
        min_score=getattr(profile, "min_contact_score", None),
        allow_generic=bool(getattr(profile, "allow_generic_emails", False)),
        allow_generic_emails=bool(getattr(profile, "allow_generic_emails", False)),
        extra_positive=list(getattr(profile, "role_positive_keywords", []) or []),
        extra_negative=list(getattr(profile, "role_negative_keywords", []) or []),
        use_llm=bool(config_bits["use_llm"]),
        max_candidates_to_probe=max_candidates_to_probe,
        include_strategy_breakdown=True,
        include_pagination=True,
    )

    tokens_in = crawl.probe_summary.tokens_in + slug_tokens_in
    tokens_out = crawl.probe_summary.tokens_out + slug_tokens_out
    kept_contacts = _benchmark_contact_rows(crawl.probe_summary.kept_contacts)
    selected_mode = crawl.selected_mode
    candidates_found = int(crawl.discovery_candidate_counts.get(selected_mode, len(crawl.candidates)) or 0)
    diagnostics = {
        "method_type": "direct",
        "selected_mode": selected_mode,
        "llm_slug_hints": extra_slugs,
        "discovery_candidate_counts": crawl.discovery_candidate_counts,
        "collector_counts": _collector_counts(crawl.collector_breakdown),
        "selected_candidates": _candidate_rows(crawl.candidates),
        "probe_summary": {
            "probe_attempts": crawl.probe_summary.probe_attempts,
            "failed_fetches": crawl.probe_summary.failed_fetches,
            "best_url": crawl.probe_summary.best_url,
            "best_url_kept": crawl.probe_summary.best_url_kept,
            "source_breakdown": crawl.probe_summary.source_breakdown,
            "dead_shell_contexts": sorted(crawl.probe_summary.dead_shell_contexts),
            "repeated_zero_evidence_signatures": crawl.probe_summary.repeated_zero_evidence_signatures,
            "pruned_candidates": list(crawl.probe_summary.pruned_candidates),
        },
        "page_results": _page_probe_rows(crawl.probe_summary.page_results),
    }

    return DiscoveryResult(
        method=method,
        university_name=target.name,
        homepage_url=target.url,
        candidates_found=candidates_found,
        probe_attempts=crawl.probe_summary.probe_attempts,
        candidates_ranked=len(crawl.candidates),
        candidates_probed=crawl.probe_summary.candidates_probed,
        contacts_extracted=crawl.probe_summary.contacts_extracted,
        contacts_kept=len(kept_contacts),
        time_seconds=time.time() - start,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_dollars=cost_for_tokens(tokens_in, tokens_out),
        best_url=crawl.probe_summary.best_url,
        source_breakdown=dict(crawl.probe_summary.source_breakdown),
        contacts_list=kept_contacts,
        diagnostics=diagnostics,
    )


async def _run_agent_method(
    target: Target,
    method: str,
    *,
    profile=ACADEMIC_PROFILE,
    max_candidates_to_probe: int = 10,
) -> DiscoveryResult:
    start = time.time()
    agent_profile = replace(profile, discovery_mode="real_link_only")
    budgets = AgentBudgets(
        max_pages_total=max(max_candidates_to_probe + 4, 15),
        max_planned_pages_initial=max(6, min(max_candidates_to_probe, 16)),
        max_gap_fill_pages=max(4, min(max_candidates_to_probe, 8)),
        max_fallback_pages=3,
        max_llm_calls_total=10,
        max_fallback_turns=2,
        target_qualified_contacts=5,
        soft_success_qualified_contacts=3,
    )
    state = await run_nafsa_agent(target, agent_profile, budgets=budgets)
    tokens_in = 0
    tokens_out = 0
    contacts_extracted = sum(int(trace.get("raw_contacts_found", 0) or 0) for trace in state.extraction_trace)
    candidates_found = len(state.discovery_trace)
    successful_fetches = sum(1 for trace in state.extraction_trace if int(trace.get("text_length", 0) or 0) > 0)
    best_url = ""
    best_count = -1
    for trace in state.extraction_trace:
        kept = len(trace.get("kept_contacts", []) or [])
        if kept > best_count:
            best_url = str(trace.get("url", ""))
            best_count = kept

    kept_contacts = []
    for contact in state.ranked_contacts:
        kept_contacts.append(
            {
                "name": contact.name or "",
                "email": (contact.email or "").lower(),
                "role": contact.title or "",
                "page_url": contact.source_url or state.homepage_url,
                "score": 0,
                "reason": contact.reason or "",
            }
        )
    diagnostics = {
        "method_type": "agent",
        "state_summary": {
            "hard_success": bool(state.hard_success),
            "soft_success": bool(state.soft_success),
            "failed": bool(state.failed),
            "failure_reason": state.failure_reason,
            "stop_reason": state.stop_reason,
            "probe_attempts": state.pages_fetched,
            "pages_fetched": state.pages_fetched,
            "successful_fetches": successful_fetches,
            "llm_calls": state.llm_calls,
        },
        "discovery_trace": list(state.discovery_trace),
        "discovery_strategy_trace": list(state.discovery_strategy_trace),
        "mode_history": list(state.mode_history),
        "action_history": list(state.action_history),
        "planner_history": list(state.planner_history),
        "extraction_trace": list(state.extraction_trace),
        "pruned_candidates": list(state.pruned_candidates),
        "missing_email_candidates": list(state.missing_email_candidates),
        "rejected_contacts": list(state.rejected_contacts),
        "deduped_contacts": list(state.deduped_contacts),
        "dead_shell_contexts": sorted(state.dead_shell_contexts),
        "repeated_zero_evidence_signatures": dict(state.repeated_zero_evidence_signatures),
    }

    return DiscoveryResult(
        method=method,
        university_name=target.name,
        homepage_url=target.url,
        candidates_found=candidates_found,
        probe_attempts=state.pages_fetched,
        candidates_ranked=candidates_found,
        candidates_probed=successful_fetches,
        contacts_extracted=contacts_extracted,
        contacts_kept=len(kept_contacts),
        time_seconds=time.time() - start,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_dollars=cost_for_tokens(tokens_in, tokens_out),
        best_url=best_url,
        contacts_list=kept_contacts,
        diagnostics=diagnostics,
    )


async def probe_candidates_regex_only(
    candidates: List[Dict],
    home_url: str,
    max_to_probe: int = 10,
    country: Optional[str] = None,
) -> tuple[int, int, str, List[Dict]]:
    summary = await probe_candidate_pages(
        list(candidates),
        home_url,
        max_to_probe=max_to_probe,
        country=country,
        use_llm=False,
        include_pagination=True,
    )
    contacts = _benchmark_contact_rows(summary.kept_contacts)
    return summary.contacts_extracted, len(contacts), summary.best_url or "", contacts


async def probe_candidates_and_extract(
    candidates: List[Dict],
    home_url: str,
    max_to_probe: int = 10,
    country: Optional[str] = None,
) -> tuple[int, int, str, int, int, List[Dict]]:
    summary = await probe_candidate_pages(
        list(candidates),
        home_url,
        max_to_probe=max_to_probe,
        country=country,
        use_llm=True,
        include_pagination=True,
    )
    contacts = _benchmark_contact_rows(summary.kept_contacts)
    return (
        summary.contacts_extracted,
        len(contacts),
        summary.best_url or "",
        summary.tokens_in,
        summary.tokens_out,
        contacts,
    )


async def benchmark_methods(
    universities: List[Dict[str, str] | Target],
    methods: List[str] | None = None,
    max_candidates_to_probe: int = 10,
    concurrent: bool | int = False,
    verbose: bool = False,
    country: Optional[str] = None,
    profile=ACADEMIC_PROFILE,
    progress_callback: Optional[Callable[[ComparisonReport, dict[str, object]], None]] = None,
) -> ComparisonReport:
    """
    Benchmark localized crawl methods on a set of universities.
    """
    methods = methods or ["heuristic", "ai_slug", "ai_crawler"]
    targets = [_coerce_target(item, country=country) for item in universities]
    report = ComparisonReport()
    concurrency = _concurrency_limit(concurrent)
    sem = asyncio.Semaphore(concurrency)
    method_timeout = max(1.0, float(getattr(config, "BENCHMARK_METHOD_TIMEOUT", 180.0) or 180.0))
    completed_targets = 0

    def _emit_progress(**state: object) -> None:
        if progress_callback is None:
            return
        progress_callback(
            report,
            {
                "timestamp": datetime.now().isoformat(),
                "completed_targets": completed_targets,
                "total_targets": len(targets),
                **state,
            },
        )

    async def _run_for_target(target: Target) -> list[DiscoveryResult]:
        async with sem:
            results: list[DiscoveryResult] = []
            for method in methods:
                _emit_progress(status="running", target=target.name, method=method, homepage_url=target.url)
                if verbose:
                    LOG.info("Benchmarking %s with %s", target.name, method)
                method_started = time.time()
                try:
                    if method in DIRECT_METHOD_MODES:
                        results.append(
                            await asyncio.wait_for(
                                _run_direct_method(
                                    target,
                                    method,
                                    profile=profile,
                                    max_candidates_to_probe=max_candidates_to_probe,
                                ),
                                timeout=method_timeout,
                            )
                        )
                    elif method in AGENT_METHODS:
                        results.append(
                            await asyncio.wait_for(
                                _run_agent_method(
                                    target,
                                    method,
                                    profile=profile,
                                    max_candidates_to_probe=max_candidates_to_probe,
                                ),
                                timeout=method_timeout,
                            )
                        )
                    _emit_progress(
                        status="method_complete",
                        target=target.name,
                        method=method,
                        homepage_url=target.url,
                        contacts_kept=results[-1].contacts_kept if results else 0,
                    )
                except asyncio.TimeoutError:
                    LOG.warning(
                        "benchmark method %s timed out for %s after %.1fs",
                        method,
                        target.name,
                        method_timeout,
                    )
                    results.append(
                        _failure_result(
                            target,
                            method,
                            started_at=method_started,
                            status="timeout",
                            error=f"timed out after {method_timeout:.1f}s",
                            diagnostics={"failure_type": "timeout", "timeout_seconds": method_timeout},
                        )
                    )
                    _emit_progress(
                        status="method_timeout",
                        target=target.name,
                        method=method,
                        homepage_url=target.url,
                        timeout_seconds=method_timeout,
                    )
                except Exception as exc:
                    LOG.warning("benchmark method %s failed for %s: %s", method, target.name, exc)
                    results.append(
                        _failure_result(
                            target,
                            method,
                            started_at=method_started,
                            status="failed",
                            error=str(exc),
                            diagnostics={"failure_type": "exception", "exception_type": type(exc).__name__},
                        )
                    )
                    _emit_progress(
                        status="method_failed",
                        target=target.name,
                        method=method,
                        homepage_url=target.url,
                        error=str(exc),
                    )
            return results

    tasks = [asyncio.create_task(_run_for_target(target)) for target in targets]

    try:
        iterator = asyncio.as_completed(tasks)
        if not verbose:
            iterator = async_tqdm(iterator, total=len(tasks), desc="Processing universities", unit="uni")
        for future in iterator:
            uni_results = await future
            for result in uni_results:
                report.add(result)
            completed_targets += 1
            _emit_progress(status="target_complete", results_added=len(uni_results))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

    return report
