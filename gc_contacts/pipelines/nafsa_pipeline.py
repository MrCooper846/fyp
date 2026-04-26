"""
NAFSA outreach pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Dict, List, Optional

from tqdm.asyncio import tqdm

import gc_contacts.config as config
from gc_contacts.agent import AgentBudgets, run_nafsa_agent
from gc_contacts.agent.controller import write_agent_debug_trace
from gc_contacts.core.models import Target
from gc_contacts.pipelines.shared import collect_targets, pipeline_runtime
from gc_contacts.profiles.base_profile import CrawlProfile
from gc_contacts.profiles.nafsa_profile import NAFSA_PROFILE
from gc_contacts.sources.base import TargetSource
from gc_contacts.sources.openalex_source import OpenAlexSource

LOG = logging.getLogger("gc.pipeline.nafsa")


def _normalize_target_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"\s+", " ", normalized.strip().lower())
    normalized = re.sub(r"[^a-z0-9 ]+", "", normalized)
    return normalized.strip()


def _filter_targets_by_name(targets: List[Target], target_names: Optional[List[str]]) -> List[Target]:
    requested = [name for name in (target_names or []) if str(name or "").strip()]
    if not requested:
        return targets

    requested_map: dict[str, str] = {}
    for name in requested:
        normalized = _normalize_target_name(name)
        if normalized and normalized not in requested_map:
            requested_map[normalized] = name

    filtered: List[Target] = []
    seen_normalized: set[str] = set()
    for target in targets:
        normalized_target = _normalize_target_name(target.name)
        if normalized_target in requested_map:
            filtered.append(target)
            seen_normalized.add(normalized_target)

    missing = [requested_map[key] for key in requested_map if key not in seen_normalized]
    if missing:
        LOG.warning("Requested target names not found in fetched source set: %s", ", ".join(missing))
    return filtered


def _state_to_rows(state, per_target_max: int) -> List[Dict]:
    rows: List[Dict] = []
    ranked = state.ranked_contacts[:per_target_max] if per_target_max and per_target_max > 0 else state.ranked_contacts

    for contact in ranked:
        rows.append(
            {
                "organisation": state.target.name,
                "org_type": state.target.org_type,
                "country": state.target.country or "",
                "source": state.target.source or "",
                "contact_name": contact.name or "",
                "title": contact.title or "",
                "email": (contact.email or "").lower(),
                "page_url": contact.source_url or state.homepage_url,
                "confidence": contact.confidence,
                "email_source": contact.email_source,
                "evidence_url": contact.evidence_url or "",
                "evidence_type": contact.evidence_type or "",
                "recovery_reason": contact.recovery_reason or "",
                "candidate_status": contact.candidate_status,
                "priority": contact.priority,
                "classifier_reason": contact.reason,
                "agent_outcome": (
                    "hard_success"
                    if state.hard_success
                    else "soft_success"
                    if state.soft_success
                    else "failed"
                    if state.failed
                    else "partial"
                ),
                "pages_fetched": state.pages_fetched,
            }
        )

    return rows


class NafsaPipeline:
    """
    Operational outreach pipeline using a bounded multi-phase agent.
    """

    def __init__(
        self,
        source: Optional[TargetSource] = None,
        profile: Optional[CrawlProfile] = None,
        per_target_max: int = 15,
        concurrency: int = 6,
        verbose: bool = False,
    ):
        self.source = source or OpenAlexSource()
        self.profile = profile or NAFSA_PROFILE
        self.per_target_max = per_target_max
        self.concurrency = concurrency
        self.verbose = verbose

    async def _crawl_one_target(
        self,
        target: Target,
        budgets: AgentBudgets,
        use_classifier: bool = False,
    ) -> List[Dict]:
        state = await run_nafsa_agent(target, self.profile, budgets=budgets)
        if config.DEBUG_ENABLED:
            await write_agent_debug_trace(state)
        rows = _state_to_rows(state, self.per_target_max)

        if use_classifier and rows:
            from gc_contacts.agent.contact_classifier import classify_contact

            for row in rows:
                try:
                    classification = classify_contact(row)
                    row["priority"] = classification.get("priority", row.get("priority", "medium"))
                    row["classifier_reason"] = classification.get("reason", row.get("classifier_reason", ""))
                except Exception:
                    pass

        return rows

    async def run(
        self,
        country: str,
        limit: Optional[int] = None,
        output_path: str = "nafsa_contacts.csv",
        ignore_robots: bool = False,
        use_classifier: bool = False,
        debug: bool = False,
        debug_dir: Optional[str] = None,
        target_names: Optional[List[str]] = None,
    ) -> List[Dict]:
        from gc_contacts.exporters.crm_exporter import CRMExporter

        async with pipeline_runtime(
            ignore_robots=ignore_robots,
            debug=debug,
            debug_dir=debug_dir,
            training_csv=False,
        ):
            LOG.info("Fetching targets for %s ...", country)
            targets = await collect_targets(self.source, country, limit)
            targets = _filter_targets_by_name(targets, target_names)
            if not targets:
                LOG.error("No targets found for country: %s", country)
                return []

            print(f"\nNAFSA pipeline: crawling {len(targets)} targets in {country}")

            budgets = AgentBudgets()
            sem = asyncio.Semaphore(self.concurrency)
            results: List[Dict] = []

            async def _guarded_run(target: Target) -> List[Dict]:
                async with sem:
                    try:
                        return await self._crawl_one_target(
                            target,
                            budgets=budgets,
                            use_classifier=use_classifier,
                        )
                    except Exception as exc:
                        LOG.warning("target crawl failed for %s: %s", target.name, repr(exc))
                        return []

            tasks = [_guarded_run(target) for target in targets]

            for task in tqdm(asyncio.as_completed(tasks), total=len(tasks), unit="org", desc="organisations"):
                rows = await task
                if rows:
                    results.extend(rows)

            exporter = CRMExporter()
            exporter.export(results, output_path)

            print(f"\nNAFSA pipeline complete: {len(results)} contacts -> {output_path}")
            if config.DEBUG_ENABLED:
                print(f"  -> Debug traces: {config.DEBUG_DIR}")
            return results
