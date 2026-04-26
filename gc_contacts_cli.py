#!/usr/bin/env python3
"""
CLI entry point for gc_contacts crawler.

Two operational modes are available via subcommands:

  academic   – Run the academic benchmark pipeline (FYP mode).
               Fetches universities from OpenAlex, runs multi-method comparison,
               writes benchmark_results.csv and per-method contact JSON files.

               python gc_contacts_cli.py academic GB --limit 200

  nafsa      – Run the NAFSA outreach pipeline.
               Crawls universities (or companies) and produces a CRM-ready
               contact CSV suitable for direct outreach.

               python gc_contacts_cli.py nafsa --source universities GB --limit 100
               python gc_contacts_cli.py nafsa --source companies AE --limit 50

  (no subcommand)
             – Legacy single-mode for backward compatibility.
               Calls gc_contacts.main.run_all() directly.

               python gc_contacts_cli.py GB --limit 20 --outfile contacts.csv
"""

import argparse
import asyncio
import logging
from dataclasses import replace
from pathlib import Path


# ─── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


# ─── Academic subcommand ───────────────────────────────────────────────────────

def _add_academic_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "academic",
        help="Run the academic benchmark pipeline (FYP mode).",
        description=(
            "Fetch universities from OpenAlex, run the multi-method benchmark, "
            "and write artefacts to a timestamped run directory."
        ),
    )
    p.add_argument("country", help="ISO country code, e.g. GB, US, AE")
    p.add_argument("--limit", type=int, default=None, help="Max universities to process")
    p.add_argument(
        "--output-dir",
        default="benchmark_runs",
        help="Parent directory for run output (default: benchmark_runs/)",
    )
    p.add_argument(
        "--probe-max",
        type=int,
        default=24,
        help="Max candidate pages to probe per university (default: 24)",
    )
    p.add_argument(
        "--concurrent",
        type=int,
        default=12,
        help="Concurrency limit for university crawls (default: 12)",
    )
    p.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Ignore robots.txt (testing only)",
    )
    p.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")


async def _run_academic(args: argparse.Namespace) -> None:
    from pathlib import Path
    from gc_contacts.pipelines.academic_pipeline import AcademicPipeline
    from gc_contacts.sources.openalex_source import OpenAlexSource
    from gc_contacts.profiles.academic_profile import ACADEMIC_PROFILE

    _setup_logging(args.verbose)
    pipeline = AcademicPipeline(
        source=OpenAlexSource(),
        profile=ACADEMIC_PROFILE,
        probe_max=args.probe_max,
        concurrent=args.concurrent,
        verbose=args.verbose,
    )
    report = await pipeline.run(
        country=args.country.upper(),
        limit=args.limit,
        output_dir=Path(args.output_dir),
        ignore_robots=args.ignore_robots,
    )
    if report:
        if getattr(report, "run_dir", None):
            print(f"\nBenchmark complete. Results in: {report.run_dir}")


# ─── NAFSA subcommand ──────────────────────────────────────────────────────────

def _add_nafsa_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "nafsa",
        help="Run the NAFSA outreach pipeline (CRM lead generation).",
        description=(
            "Crawl universities or companies and produce a CRM-ready contact CSV "
            "for NAFSA / international-partnership outreach."
        ),
    )
    p.add_argument("country", help="ISO country code, e.g. GB, US, AE, EG")
    p.add_argument(
        "--source",
        choices=["universities", "companies"],
        default="universities",
        help="Target source: universities (OpenAlex) or companies (default: universities)",
    )
    p.add_argument("--limit", type=int, default=None, help="Max targets to process")
    p.add_argument(
        "--output",
        default="nafsa_contacts.csv",
        help="Output CSV path (a matching .json is also written)",
    )
    p.add_argument(
        "--per-target-max",
        type=int,
        default=15,
        help="Max contacts to keep per target organisation (default: 15)",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Crawl concurrency limit (default: 10)",
    )
    p.add_argument(
        "--classify",
        action="store_true",
        help="Run LLM classifier on extracted contacts to add priority/reason fields",
    )
    p.add_argument(
        "--companies-csv",
        default=None,
        help="Path to CSV with company targets (used when --source=companies)",
    )
    p.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Ignore robots.txt (testing only)",
    )
    p.add_argument(
        "--discovery-mode",
        choices=["heuristic_only", "generated_slug_only", "real_link_only", "hybrid"],
        default="hybrid",
        help="Discovery strategy mode for NAFSA runs (default: hybrid)",
    )
    p.add_argument("--debug", action="store_true", help="Write per-target debug JSON traces")
    p.add_argument("--debug-dir", default="debug_logs", help="Directory for NAFSA debug JSON")
    p.add_argument(
        "--target-name",
        action="append",
        default=None,
        help="Run only the specified target name(s). Repeat the flag to pass multiple names.",
    )
    p.add_argument(
        "--target-file",
        default=None,
        help="Path to a newline-delimited text file of target names to run.",
    )
    p.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")


def _load_target_names(args: argparse.Namespace) -> list[str]:
    names: list[str] = []
    for name in args.target_name or []:
        value = str(name or "").strip()
        if value:
            names.append(value)

    if args.target_file:
        path = Path(args.target_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value:
                names.append(value)

    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


async def _run_nafsa(args: argparse.Namespace) -> None:
    from gc_contacts.pipelines.nafsa_pipeline import NafsaPipeline
    from gc_contacts.profiles.nafsa_profile import NAFSA_PROFILE

    _setup_logging(args.verbose)

    if args.source == "universities":
        from gc_contacts.sources.openalex_source import OpenAlexSource
        source = OpenAlexSource()
    else:
        from gc_contacts.sources.company_source import CompanySource
        source = CompanySource(csv_path=args.companies_csv)

    pipeline = NafsaPipeline(
        source=source,
        profile=replace(NAFSA_PROFILE, discovery_mode=args.discovery_mode),
        per_target_max=args.per_target_max,
        concurrency=args.concurrency,
        verbose=args.verbose,
    )
    target_names = _load_target_names(args)
    contacts = await pipeline.run(
        country=args.country.upper(),
        limit=args.limit,
        output_path=args.output,
        ignore_robots=args.ignore_robots,
        use_classifier=args.classify,
        debug=args.debug,
        debug_dir=args.debug_dir,
        target_names=target_names,
    )
    print(f"\nNAFSA pipeline complete. {len(contacts)} contacts → {args.output}")


# ─── Legacy mode (no subcommand) ──────────────────────────────────────────────

def _run_legacy() -> None:
    """Backward-compatible invocation: python gc_contacts_cli.py <country> [opts]"""
    from gc_contacts.main import run_all

    parser = argparse.ArgumentParser(
        description="[Legacy] Extract international contacts from university websites."
    )
    parser.add_argument("country", help="ISO country code, e.g., GB, US, CN")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--outfile", default="contacts.csv")
    parser.add_argument("--emit-all", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--debug-dir", default="debug_logs")
    parser.add_argument("--ignore-robots", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--browser-ua", action="store_true")
    parser.add_argument("--per-uni-max", type=int, default=12)
    parser.add_argument("--verify-names", action="store_true")
    parser.add_argument(
        "--discovery-mode",
        choices=["heuristic_only", "generated_slug_only", "real_link_only", "hybrid", "benchmark_all"],
        default="heuristic_only",
        help="Discovery strategy mode for legacy runs (default: heuristic_only)",
    )
    args = parser.parse_args()
    _setup_logging(args.verbose)
    asyncio.run(run_all(
        args.country.upper(),
        args.limit,
        args.outfile,
        args.emit_all,
        args.debug,
        args.debug_dir,
        args.ignore_robots,
        args.verbose,
        args.browser_ua,
        args.per_uni_max,
        args.verify_names,
        args.discovery_mode,
    ))


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # If the first real argument is a known subcommand use the new CLI,
    # otherwise fall back to the legacy single-positional-argument style.
    _SUBCOMMANDS = {"academic", "nafsa"}
    first = next((a for a in sys.argv[1:] if not a.startswith("-")), None)

    if first in _SUBCOMMANDS:
        parser = argparse.ArgumentParser(
            description="gc_contacts – dual-pipeline contact extraction tool.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        subparsers = parser.add_subparsers(dest="subcommand")
        _add_academic_parser(subparsers)
        _add_nafsa_parser(subparsers)
        args = parser.parse_args()
        if args.subcommand == "academic":
            asyncio.run(_run_academic(args))
        elif args.subcommand == "nafsa":
            asyncio.run(_run_nafsa(args))
        else:
            parser.print_help()
    else:
        _run_legacy()
