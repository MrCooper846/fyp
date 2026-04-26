#!/usr/bin/env python3
"""
Rebuild a partial NAFSA CSV/JSON export from per-target debug traces.

This is useful when a long-running `gc_contacts_cli.py nafsa ... --debug`
run is stopped before the final exporter flushes the consolidated output.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from gc_contacts.exporters.crm_exporter import CRM_FIELDS


def _rows_from_trace(payload: dict[str, Any], per_target_max: int) -> list[dict[str, Any]]:
    target = payload.get("target", {}) or {}
    outcome = payload.get("outcome", {}) or {}
    ranked_contacts = payload.get("ranked_contacts", []) or []
    if per_target_max and per_target_max > 0:
        ranked_contacts = ranked_contacts[:per_target_max]

    rows: list[dict[str, Any]] = []
    for contact in ranked_contacts:
        rows.append(
            {
                "organisation": target.get("name", "") or "",
                "org_type": target.get("org_type", "") or "",
                "country": target.get("country", "") or "",
                "source": target.get("source", "") or "",
                "contact_name": contact.get("name", "") or "",
                "title": contact.get("title", "") or "",
                "email": str(contact.get("email", "") or "").lower(),
                "page_url": contact.get("source_url") or target.get("url", "") or "",
                "confidence": contact.get("confidence", "") or "",
                "email_source": contact.get("email_source", "") or "",
                "evidence_url": contact.get("evidence_url", "") or "",
                "evidence_type": contact.get("evidence_type", "") or "",
                "recovery_reason": contact.get("recovery_reason", "") or "",
                "candidate_status": contact.get("candidate_status", "") or "",
                "priority": contact.get("priority", "") or "",
                "classifier_reason": contact.get("reason", "") or "",
                "agent_outcome": (
                    "hard_success"
                    if outcome.get("hard_success")
                    else "soft_success"
                    if outcome.get("soft_success")
                    else "failed"
                    if outcome.get("failed")
                    else "partial"
                ),
                "pages_fetched": outcome.get("pages_fetched", 0) or 0,
            }
        )
    return rows


def recover_partial_run(debug_dir: Path, output_path: Path, per_target_max: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    processed_traces = 0

    for trace_path in sorted(debug_dir.glob("*.json")):
        try:
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        processed_traces += 1
        rows.extend(_rows_from_trace(payload, per_target_max=per_target_max))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fields = [field for field in CRM_FIELDS if any(field in row for row in rows)]
        extra_fields: list[str] = []
        for row in rows:
            for key in row:
                if key not in fields and key not in extra_fields:
                    extra_fields.append(key)
        all_fields = fields + extra_fields

        with open(output_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=all_fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in all_fields})

        json_path = output_path.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=2, ensure_ascii=False)
    return rows, processed_traces


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover a partial NAFSA export from debug traces.")
    parser.add_argument("debug_dir", help="Debug trace directory from a --debug NAFSA run")
    parser.add_argument(
        "--output",
        default="nafsa_partial_recovered.csv",
        help="Output CSV path (matching .json is also written)",
    )
    parser.add_argument(
        "--per-target-max",
        type=int,
        default=15,
        help="Max contacts to keep per target organisation (default: 15)",
    )
    args = parser.parse_args()

    debug_dir = Path(args.debug_dir)
    if not debug_dir.is_dir():
        raise SystemExit(f"Debug directory not found: {debug_dir}")

    rows, processed_traces = recover_partial_run(
        debug_dir=debug_dir,
        output_path=Path(args.output),
        per_target_max=args.per_target_max,
    )
    print(
        f"\nRecovered {len(rows)} contacts from {processed_traces} completed debug traces in {debug_dir}"
    )


if __name__ == "__main__":
    main()
