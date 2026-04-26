#!/usr/bin/env python3
"""
Run benchmark to compare crawl methods.

Usage:
    python benchmark_runner.py --country GB --limit 5 --methods heuristic ai_slug ai_crawler
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from gc_contacts.pipelines.academic_pipeline import AcademicPipeline
from gc_contacts.profiles.academic_profile import ACADEMIC_PROFILE
from gc_contacts.sources.openalex_source import OpenAlexSource


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("benchmark")


async def main():
    parser = argparse.ArgumentParser(description="Benchmark localized discovery methods for finding contacts")
    parser.add_argument("--country", required=True, help="ISO country code (e.g., GB, US, IT)")
    parser.add_argument("--limit", type=int, default=5, help="Number of universities to test")
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["heuristic", "ai_slug", "ai_crawler"],
        help="Methods to test (heuristic, ai_slug, ai_crawler, agent)",
    )
    parser.add_argument(
        "--probe-max",
        type=int,
        default=10,
        help="Max candidate pages to probe per method per university",
    )
    parser.add_argument(
        "--output-dir",
        default="benchmark_runs",
        help="Base directory for benchmark runs (will create dated subdirectories)",
    )
    parser.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt")
    parser.add_argument(
        "--concurrent",
        type=int,
        default=1,
        help="How many universities to benchmark concurrently (default: 1)",
    )
    parser.add_argument("--verbose", action="store_true", help="Show detailed logging output")

    args = parser.parse_args()

    if not args.verbose:
        logging.getLogger("gc").setLevel(logging.WARNING)

    pipeline = AcademicPipeline(
        source=OpenAlexSource(),
        profile=ACADEMIC_PROFILE,
        methods=args.methods,
        probe_max=args.probe_max,
        concurrent=args.concurrent,
        verbose=args.verbose,
    )
    report = await pipeline.run(
        country=args.country.upper(),
        limit=args.limit,
        output_dir=args.output_dir,
        ignore_robots=args.ignore_robots,
    )

    if report.run_name and report.run_dir:
        print("\n" + "=" * 70)
        print("Benchmark complete!")
        print(f"  Run: {report.run_name}")
        print(f"  Results: {report.run_dir}")
        print("  - Statistics: benchmark_results.csv")
        print("  - Contacts: contacts/")
        print("  - Metadata: run_info.json")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
