"""
CRM exporter.

Formats and writes the output of the NAFSA outreach pipeline into
formats suitable for import into CRM systems or direct outreach tooling.

Supported output formats:
  - CSV   (default, universally compatible)
  - JSON  (full record, machine-readable)

Output fields (CRM schema):
  organisation, contact_name, title, email, org_type, country, source,
  confidence, page_url, [priority, classifier_reason] (if classifier used)
"""

from __future__ import annotations
import csv
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

LOG = logging.getLogger("gc.exporter.crm")

# Canonical field order for CRM CSV output
CRM_FIELDS = [
    "organisation",
    "org_type",
    "country",
    "source",
    "contact_name",
    "title",
    "email",
    "confidence",
    "score",
    "page_url",
    "email_source",
    "evidence_url",
    "evidence_type",
    "recovery_reason",
    "candidate_status",
    "priority",
    "classifier_reason",
]


class CRMExporter:
    """
    Writes CRM-ready contact datasets to CSV and/or JSON.
    """

    def export(
        self,
        contacts: List[Dict],
        output_path: str | Path,
        also_json: bool = True,
    ) -> None:
        """
        Write contacts to output_path (CSV) and optionally a matching .json.

        Args:
            contacts:     List of contact dicts from NafsaPipeline.run().
            output_path:  Destination file path (should end in .csv).
            also_json:    If True, also write a .json alongside the CSV.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not contacts:
            LOG.warning("CRMExporter: no contacts to export.")
            return

        # Determine actual fields present (may include extra from classifier)
        fields = [f for f in CRM_FIELDS if any(f in c for c in contacts)]
        # Also include any extra keys not in CRM_FIELDS
        extra = []
        for c in contacts:
            for k in c:
                if k not in fields and k not in extra:
                    extra.append(k)
        all_fields = fields + extra

        # ── CSV ───────────────────────────────────────────────────────────────
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
            writer.writeheader()
            for c in contacts:
                row = {k: c.get(k, "") for k in all_fields}
                writer.writerow(row)

        LOG.info("CRM CSV written: %s (%d rows)", output_path, len(contacts))
        print(f"  → CSV:  {output_path}  ({len(contacts)} contacts)")

        # ── JSON ──────────────────────────────────────────────────────────────
        if also_json:
            json_path = output_path.with_suffix(".json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(contacts, f, indent=2, ensure_ascii=False)
            LOG.info("CRM JSON written: %s", json_path)
            print(f"  → JSON: {json_path}")
