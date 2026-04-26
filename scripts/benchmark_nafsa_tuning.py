#!/usr/bin/env python3
"""
Compare NAFSA tuning variants on the same target set.

This script is designed to answer:
  - How much lift comes from a higher export cap?
  - How much lift comes from deeper crawl budgets?
  - How much lift comes from broader discovery/profile coverage?
  - What is the combined lift of the full tuned configuration?

Example:
    python scripts/benchmark_nafsa_tuning.py --country GB --limit 10
    python scripts/benchmark_nafsa_tuning.py --country AE --source companies --companies-csv data/companies.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gc_contacts.config as config
from gc_contacts.agent import AgentBudgets, run_nafsa_agent
from gc_contacts.core.models import Target
from gc_contacts.profiles.base_profile import CrawlProfile
from gc_contacts.profiles.nafsa_profile import NAFSA_PROFILE
from gc_contacts.sources.company_source import CompanySource
from gc_contacts.sources.openalex_source import OpenAlexSource


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("gc.tuning_benchmark")


BASELINE_PROFILE = CrawlProfile(
    name="nafsa_baseline",
    slug_hints=[
        "/international-office",
        "/international-relations",
        "/global-engagement",
        "/international-partnerships",
        "/global-partnerships",
        "/mobility",
        "/exchange",
        "/study-abroad",
        "/collaboration",
        "/global-strategy",
        "/international/partnerships",
        "/international/team",
        "/partnerships",
        "/global",
        "/contact/international",
        "/about/international",
        "/about/global",
        "/offices/international",
        "/offices/global-engagement",
        "/offices/international-relations",
    ],
    role_positive_keywords=[
        "international partnerships",
        "global engagement",
        "international relations",
        "exchange programs",
        "exchange programme",
        "mobility programs",
        "mobility programme",
        "partnerships",
        "business development",
        "global strategy",
        "institutional advancement",
        "study abroad",
        "collaboration",
        "external relations",
        "international cooperation",
    ],
    role_negative_keywords=[
        "student recruitment",
        "undergraduate admissions",
        "postgraduate admissions",
        "student services",
        "student support",
        "domestic admissions",
    ],
    min_contact_score=4,
    allow_generic_emails=True,
)

BASELINE_BUDGETS = AgentBudgets(
    max_pages_total=20,
    max_planned_pages_initial=10,
    max_gap_fill_pages=4,
    max_fallback_pages=3,
    max_llm_calls_total=10,
    max_fallback_turns=2,
    target_qualified_contacts=5,
    soft_success_qualified_contacts=3,
)

BASELINE_CONFIG = {
    "CMS_SEARCH_TERMS": [
        "international",
        "admissions",
        "recruitment",
        "contact",
        "directory",
        "people",
        "global",
        "engagement",
        "partnerships",
    ],
    "TOKENS": [
        "staff",
        "directory",
        "people",
        "leadership",
        "administration",
        "contacts",
        "recruitment",
        "admissions",
        "rector",
        "chancellor",
        "governance",
        "executive",
        "faculty",
        "personnel",
        "international",
        "global",
        "partnerships",
        "relations",
        "engagement",
    ],
}

TUNED_CONFIG = {
    "CMS_SEARCH_TERMS": list(config.CMS_SEARCH_TERMS),
    "TOKENS": list(config.TOKENS),
}

VARIANTS = [
    {
        "name": "baseline",
        "per_target_max": 8,
        "profile": BASELINE_PROFILE,
        "budgets": BASELINE_BUDGETS,
        "config": BASELINE_CONFIG,
        "notes": "Original export cap, original crawl budget, original discovery/profile scope.",
    },
    {
        "name": "export_cap_only",
        "per_target_max": 15,
        "profile": BASELINE_PROFILE,
        "budgets": BASELINE_BUDGETS,
        "config": BASELINE_CONFIG,
        "notes": "Only raises the number of contacts exported per target.",
    },
    {
        "name": "crawl_budget_only",
        "per_target_max": 8,
        "profile": BASELINE_PROFILE,
        "budgets": AgentBudgets(),
        "config": BASELINE_CONFIG,
        "notes": "Only increases page-planning and stop budgets.",
    },
    {
        "name": "discovery_only",
        "per_target_max": 8,
        "profile": NAFSA_PROFILE,
        "budgets": BASELINE_BUDGETS,
        "config": TUNED_CONFIG,
        "notes": "Only widens profile slug hints, role keywords, and discovery terms.",
    },
    {
        "name": "full_tuned",
        "per_target_max": 15,
        "profile": NAFSA_PROFILE,
        "budgets": AgentBudgets(),
        "config": TUNED_CONFIG,
        "notes": "All current tuned changes together.",
    },
]


@contextmanager
def patched_config(overrides: Dict[str, object]):
    original = {key: getattr(config, key) for key in overrides}
    try:
        for key, value in overrides.items():
            setattr(config, key, value)
        yield
    finally:
        for key, value in original.items():
            setattr(config, key, value)


def choose_source(kind: str, companies_csv: Optional[str]):
    if kind == "companies":
        return CompanySource(csv_path=companies_csv)
    return OpenAlexSource()


async def load_targets(source, country: str, limit: Optional[int]) -> List[Target]:
    targets: List[Target] = []
    async for target in source.fetch_targets(country, limit):
        targets.append(target)
    return targets


def summarize_rows(rows: Iterable[Dict]) -> Dict[str, object]:
    rows = list(rows)
    if not rows:
        return {
            "targets": 0,
            "targets_with_contacts": 0,
            "success_rate": 0.0,
            "total_ranked_contacts": 0,
            "total_exported_contacts": 0,
            "avg_exported_per_target": 0.0,
            "avg_pages_fetched": 0.0,
            "high_priority_contacts": 0,
            "medium_priority_contacts": 0,
            "hard_success_targets": 0,
            "soft_success_targets": 0,
            "failed_targets": 0,
        }

    total_targets = len(rows)
    total_exported = sum(int(row["exported_contacts"]) for row in rows)
    total_ranked = sum(int(row["ranked_contacts"]) for row in rows)
    targets_with_contacts = sum(1 for row in rows if int(row["exported_contacts"]) > 0)
    high_priority = sum(int(row["high_priority_contacts"]) for row in rows)
    medium_priority = sum(int(row["medium_priority_contacts"]) for row in rows)
    hard_success = sum(1 for row in rows if row["agent_outcome"] == "hard_success")
    soft_success = sum(1 for row in rows if row["agent_outcome"] == "soft_success")
    failed = sum(1 for row in rows if row["agent_outcome"] == "failed")

    return {
        "targets": total_targets,
        "targets_with_contacts": targets_with_contacts,
        "success_rate": round(targets_with_contacts / total_targets * 100, 1),
        "total_ranked_contacts": total_ranked,
        "total_exported_contacts": total_exported,
        "avg_exported_per_target": round(total_exported / total_targets, 2),
        "avg_pages_fetched": round(sum(int(row["pages_fetched"]) for row in rows) / total_targets, 2),
        "high_priority_contacts": high_priority,
        "medium_priority_contacts": medium_priority,
        "hard_success_targets": hard_success,
        "soft_success_targets": soft_success,
        "failed_targets": failed,
    }


def build_markdown_report(
    country: str,
    source_name: str,
    limit: Optional[int],
    summary_rows: List[Dict],
    detail_rows: List[Dict],
) -> str:
    baseline = next((row for row in summary_rows if row["variant"] == "baseline"), None)
    lines = [
        "# NAFSA Tuning Benchmark",
        "",
        f"- Country: `{country}`",
        f"- Source: `{source_name}`",
        f"- Target limit: `{limit if limit is not None else 'all available'}`",
        "",
        "## Variant Summary",
        "",
        "| Variant | Exported | Delta vs Baseline | Avg/Target | Success Rate | Avg Pages | Notes |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]

    for row in summary_rows:
        delta = row.get("delta_exported_vs_baseline", 0)
        delta_label = f"{delta:+d}" if baseline else "n/a"
        lines.append(
            f"| {row['variant']} | {row['total_exported_contacts']} | {delta_label} | "
            f"{row['avg_exported_per_target']:.2f} | {row['success_rate']:.1f}% | "
            f"{row['avg_pages_fetched']:.2f} | {row['notes']} |"
        )

    isolated = [row for row in summary_rows if row["variant"] in {"export_cap_only", "crawl_budget_only", "discovery_only"}]
    isolated.sort(key=lambda row: row.get("delta_exported_vs_baseline", 0), reverse=True)
    if isolated:
        lines.extend(
            [
                "",
                "## Biggest Isolated Lift",
                "",
                f"- Top isolated lever: `{isolated[0]['variant']}` with `{isolated[0]['delta_exported_vs_baseline']:+d}` exported contacts vs baseline.",
            ]
        )

    strongest_targets = sorted(
        [row for row in detail_rows if row["variant"] == "full_tuned"],
        key=lambda row: (row["exported_contacts"] - row["baseline_exported_contacts"], row["exported_contacts"]),
        reverse=True,
    )[:10]

    if strongest_targets:
        lines.extend(
            [
                "",
                "## Top Target Gains (Full Tuned)",
                "",
                "| Target | Exported | Baseline | Delta | Outcome | Pages |",
                "|---|---:|---:|---:|---|---:|",
            ]
        )
        for row in strongest_targets:
            lines.append(
                f"| {row['target_name']} | {row['exported_contacts']} | {row['baseline_exported_contacts']} | "
                f"{row['exported_contacts'] - row['baseline_exported_contacts']:+d} | {row['agent_outcome']} | {row['pages_fetched']} |"
            )

    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark which NAFSA tuning changes add the most contact lift.")
    parser.add_argument("--country", required=True, help="ISO country code, e.g. GB, AE, US")
    parser.add_argument("--source", choices=["universities", "companies"], default="universities")
    parser.add_argument("--limit", type=int, default=10, help="Max targets to benchmark")
    parser.add_argument("--companies-csv", default=None, help="CSV path for company targets when --source=companies")
    parser.add_argument("--output-dir", default="benchmark_runs", help="Directory to write comparison artefacts")
    parser.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt")
    parser.add_argument("--verbose", action="store_true", help="Enable INFO logging")
    args = parser.parse_args()

    if not args.verbose:
        LOG.setLevel(logging.WARNING)

    config.IGNORE_ROBOTS = args.ignore_robots
    config.HTTP = httpx.AsyncClient(
        headers=config.HEADERS,
        timeout=config.TIMEOUT,
        follow_redirects=True,
        http2=True,
    )

    try:
        source = choose_source(args.source, args.companies_csv)
        targets = await load_targets(source, args.country.upper(), args.limit)
        if not targets:
            print("No targets found for the requested benchmark.")
            return

        run_name = f"nafsa_tuning_{args.country.upper()}_{len(targets)}targets_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run_dir = Path(args.output_dir) / run_name
        run_dir.mkdir(parents=True, exist_ok=True)

        detail_rows: List[Dict] = []

        print(f"\nBenchmarking {len(targets)} targets across {len(VARIANTS)} NAFSA variants...")

        for target in targets:
            print(f"  Target: {target.name}")
            baseline_exported = 0
            for variant in VARIANTS:
                with patched_config(variant["config"]):
                    state = await run_nafsa_agent(target, variant["profile"], budgets=variant["budgets"])

                exported = state.ranked_contacts[: variant["per_target_max"]]
                high_count = sum(1 for contact in exported if contact.priority == "high")
                medium_count = sum(1 for contact in exported if contact.priority == "medium")
                outcome = (
                    "hard_success"
                    if state.hard_success
                    else "soft_success"
                    if state.soft_success
                    else "failed"
                    if state.failed
                    else "partial"
                )

                if variant["name"] == "baseline":
                    baseline_exported = len(exported)

                detail_rows.append(
                    {
                        "variant": variant["name"],
                        "target_name": target.name,
                        "org_type": target.org_type,
                        "country": target.country or args.country.upper(),
                        "source": target.source or "",
                        "ranked_contacts": len(state.ranked_contacts),
                        "exported_contacts": len(exported),
                        "baseline_exported_contacts": baseline_exported,
                        "high_priority_contacts": high_count,
                        "medium_priority_contacts": medium_count,
                        "pages_fetched": state.pages_fetched,
                        "llm_calls": state.llm_calls,
                        "agent_outcome": outcome,
                        "failure_reason": state.failure_reason or "",
                        "per_target_max": variant["per_target_max"],
                        "profile_name": variant["profile"].name,
                        "budgets": json.dumps(asdict(variant["budgets"]), sort_keys=True),
                    }
                )

        summary_rows: List[Dict] = []
        baseline_total = 0

        for variant in VARIANTS:
            rows = [row for row in detail_rows if row["variant"] == variant["name"]]
            summary = summarize_rows(rows)
            summary["variant"] = variant["name"]
            summary["notes"] = variant["notes"]
            if variant["name"] == "baseline":
                baseline_total = int(summary["total_exported_contacts"])
            summary_rows.append(summary)

        for summary in summary_rows:
            summary["delta_exported_vs_baseline"] = int(summary["total_exported_contacts"]) - baseline_total

        detail_csv = run_dir / "variant_target_results.csv"
        with open(detail_csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(detail_rows[0].keys()))
            writer.writeheader()
            writer.writerows(detail_rows)

        summary_csv = run_dir / "variant_summary.csv"
        with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)

        report_md = run_dir / "report.md"
        report_md.write_text(
            build_markdown_report(args.country.upper(), args.source, args.limit, summary_rows, detail_rows),
            encoding="utf-8",
        )

        metadata = {
            "timestamp": datetime.now().isoformat(),
            "country": args.country.upper(),
            "source": args.source,
            "limit": args.limit,
            "targets_benchmarked": len(targets),
            "variants": [
                {
                    "name": variant["name"],
                    "per_target_max": variant["per_target_max"],
                    "profile": asdict(variant["profile"]),
                    "budgets": asdict(variant["budgets"]),
                    "config": variant["config"],
                    "notes": variant["notes"],
                }
                for variant in VARIANTS
            ],
        }
        (run_dir / "run_info.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        print("\nVariant summary:")
        for row in summary_rows:
            print(
                f"  {row['variant']:16s} exported={row['total_exported_contacts']:4d}  "
                f"delta={row['delta_exported_vs_baseline']:+4d}  "
                f"avg/target={row['avg_exported_per_target']:.2f}  success={row['success_rate']:.1f}%"
            )

        print(f"\nSaved tuning benchmark artefacts to: {run_dir}")
        print(f"  - Summary CSV: {summary_csv.name}")
        print(f"  - Detail CSV:  {detail_csv.name}")
        print(f"  - Report:      {report_md.name}")

    finally:
        await config.OAI.close()
        if config.HTTP:
            await config.HTTP.aclose()


if __name__ == "__main__":
    asyncio.run(main())
