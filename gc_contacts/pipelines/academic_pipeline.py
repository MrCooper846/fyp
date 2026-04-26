"""
Academic benchmark pipeline.

Flow:
    OpenAlexSource -> benchmark_methods() -> ComparisonReport -> BenchmarkExporter
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from gc_contacts.benchmark import ComparisonReport, benchmark_methods
from gc_contacts.exporters.benchmark_exporter import BenchmarkExporter
from gc_contacts.pipelines.shared import collect_targets, pipeline_runtime
from gc_contacts.profiles.academic_profile import ACADEMIC_PROFILE
from gc_contacts.profiles.base_profile import CrawlProfile
from gc_contacts.sources.base import TargetSource
from gc_contacts.sources.openalex_source import OpenAlexSource

LOG = logging.getLogger("gc.pipeline.academic")


class AcademicPipeline:
    """
    Benchmarking pipeline.
    """

    def __init__(
        self,
        source: Optional[TargetSource] = None,
        profile: Optional[CrawlProfile] = None,
        methods: Optional[List[str]] = None,
        probe_max: int = 10,
        concurrent: bool | int = False,
        verbose: bool = False,
    ):
        self.source = source or OpenAlexSource()
        self.profile = profile or ACADEMIC_PROFILE
        self.methods = methods or ["heuristic", "ai_slug", "ai_crawler"]
        self.probe_max = probe_max
        self.concurrent = concurrent
        self.verbose = verbose

    async def run(
        self,
        country: str,
        limit: Optional[int] = None,
        output_dir: str = "benchmark_runs",
        ignore_robots: bool = False,
    ) -> ComparisonReport:
        from datetime import datetime
        import json

        async with pipeline_runtime(ignore_robots=ignore_robots):
            run_name = f"{country.upper()}_{limit}unis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            run_dir = Path(output_dir) / run_name
            run_dir.mkdir(parents=True, exist_ok=True)
            exporter = BenchmarkExporter(run_dir)

            run_metadata = {
                "timestamp": datetime.now().isoformat(),
                "country": country.upper(),
                "num_universities": limit,
                "methods": self.methods,
                "probe_max": self.probe_max,
                "run_name": run_name,
                "concurrent": self.concurrent,
                "profile": self.profile.name,
            }
            with open(run_dir / "run_info.json", "w", encoding="utf-8") as handle:
                json.dump(run_metadata, handle, indent=2)
            with open(run_dir / "progress.json", "w", encoding="utf-8") as handle:
                json.dump({**run_metadata, "results_written": 0, "status": {"status": "starting"}}, handle, indent=2)

            def _progress_callback(report: ComparisonReport, status: dict[str, object]) -> None:
                progress_payload = dict(run_metadata)
                progress_payload.update(
                    {
                        "results_written": len(report.results),
                        "status": status,
                    }
                )
                with open(run_dir / "progress.json", "w", encoding="utf-8") as handle:
                    json.dump(progress_payload, handle, indent=2)
                if status.get("status") == "target_complete":
                    exporter.export_progress(report)

            LOG.info("Fetching universities for %s ...", country)
            targets = await collect_targets(self.source, country, limit)
            if not targets:
                LOG.error("No universities found for country: %s", country)
                return ComparisonReport()

            LOG.info("Found %d universities", len(targets))
            mode = "concurrent" if self.concurrent else "sequential"
            print(
                f"\nRunning academic benchmark ({mode}) on {len(targets)} universities "
                f"with methods: {', '.join(self.methods)}"
            )

            report = await benchmark_methods(
                targets,
                methods=self.methods,
                max_candidates_to_probe=self.probe_max,
                concurrent=self.concurrent,
                verbose=self.verbose,
                country=country.upper(),
                profile=self.profile,
                progress_callback=_progress_callback,
            )
            report.run_name = run_name
            report.run_dir = str(run_dir)
            report.metadata = run_metadata

            exporter.export(report)
            report.print_summary()
            return report
