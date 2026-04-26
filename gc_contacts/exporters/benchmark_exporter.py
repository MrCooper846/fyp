"""
Benchmark exporter.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from gc_contacts.benchmark import ComparisonReport

LOG = logging.getLogger("gc.exporter.benchmark")


class BenchmarkExporter:
    """
    Writes all artefacts for a completed academic benchmark run.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)

    def _export_artifacts(self, report: ComparisonReport) -> None:
        results_csv = self.run_dir / "benchmark_results.csv"
        try:
            report.to_csv(results_csv)
            LOG.info("Benchmark CSV saved to %s", results_csv)
        except Exception as exc:
            LOG.error("Failed to save benchmark CSV: %s", exc)
            fallback = self.run_dir / "benchmark_results_fallback.json"
            with open(fallback, "w", encoding="utf-8") as handle:
                json.dump([vars(result) for result in report.results], handle, indent=2, default=str)
            LOG.info("Fallback JSON saved to %s", fallback)

        contacts_dir = self.run_dir / "contacts"
        try:
            report.export_contacts(contacts_dir)
            LOG.info("Contacts exported to %s", contacts_dir)
        except Exception as exc:
            LOG.error("Failed to export contacts: %s", exc)

        debug_dir = self.run_dir / "debug"
        try:
            report.export_debug(debug_dir)
            LOG.info("Benchmark debug traces exported to %s", debug_dir)
        except Exception as exc:
            LOG.error("Failed to export benchmark debug traces: %s", exc)

    def export_progress(self, report: ComparisonReport) -> None:
        self._export_artifacts(report)

    def export(self, report: ComparisonReport) -> None:
        self._export_artifacts(report)
        results_csv = self.run_dir / "benchmark_results.csv"

        print("\n" + "=" * 70)
        print("Benchmark complete!")
        print(f"  Run dir:     {self.run_dir}")
        print(f"  Statistics:  {results_csv.name}")
        print("  Contacts:    contacts/")
        print("  Debug:       debug/")
        print("=" * 70 + "\n")
