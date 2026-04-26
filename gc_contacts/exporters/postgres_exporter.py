"""
Optional live Postgres exporter for NAFSA runs.

This deliberately reuses the historical debug backfill mapper so live writes and
backfilled debug traces land in the same normalized tables.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gc_contacts.config as config
from gc_contacts.agent.controller import agent_state_to_debug_payload
from gc_contacts.agent.models import AgentState

LOG = logging.getLogger("gc.exporter.postgres")


def _ensure_driver():
    try:
        import psycopg  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("psycopg is not installed; install psycopg[binary] to enable Postgres dual-write") from exc
    return psycopg


def _safe_debug_trace_path(state: AgentState) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in state.target.name).strip("_")
    return config.DEBUG_DIR / f"{safe_name or 'target'}.json"


class PostgresLiveExporter:
    """Small live-run wrapper around the existing normalized backfill importer."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        country: str | None = None,
        discovery_mode: str | None = None,
        cli_args: dict[str, Any] | None = None,
    ) -> None:
        self.dsn = (dsn or config.DATABASE_URL or "").strip()
        self.country = (country or "").strip().upper() or None
        self.discovery_mode = discovery_mode
        self.cli_args = cli_args or {}
        self.conn: Any = None
        self.backfiller: Any = None
        self.run_id: str | None = None
        self.enabled = bool(self.dsn and config.POSTGRES_DUAL_WRITE)

    def __enter__(self) -> "PostgresLiveExporter":
        if not self.enabled:
            return self

        psycopg = _ensure_driver()
        from scripts.backfill_postgres_from_debug import PostgresBackfiller

        self.conn = psycopg.connect(self.dsn)
        self.backfiller = PostgresBackfiller(self.conn)
        self.run_id = self._create_run()
        self.conn.commit()
        LOG.info("Postgres dual-write enabled for run %s", self.run_id)
        return self

    def __exit__(self, exc_type: Any, exc: Any, _tb: Any) -> None:
        if not self.enabled or self.conn is None or self.run_id is None:
            return
        status = "failed" if exc_type else "completed_partial"
        try:
            self._finish_run(status)
            self.conn.commit()
        except Exception:
            LOG.exception("Failed to finalize Postgres live run")
            self.conn.rollback()
        finally:
            self.conn.close()

    def _create_run(self) -> str:
        assert self.conn is not None
        config_snapshot = {
            "postgres_dual_write": True,
            "debug_enabled": config.DEBUG_ENABLED,
            "debug_dir": str(config.DEBUG_DIR),
            "model": config.MODEL,
            "model_light": config.MODEL_LIGHT,
            "model_heavy": config.MODEL_HEAVY,
            "concurrency": config.CONCURRENCY,
            "gpt_concurrency": config.GPT_CONCURRENCY,
        }
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into runs (
                    run_mode, source_type, country_code, discovery_mode, status,
                    started_at, cli_args, config_snapshot, code_version, notes
                )
                values (
                    'seed_country', 'nafsa_live_pipeline', %s, %s, 'running',
                    %s, %s::jsonb, %s::jsonb, %s, %s
                )
                returning id
                """,
                (
                    self.country,
                    self.discovery_mode,
                    datetime.now(timezone.utc),
                    json.dumps(self.cli_args),
                    json.dumps(config_snapshot),
                    "live_postgres_dual_write_v1",
                    "live dual-write from NAFSA pipeline",
                ),
            )
            return str(cur.fetchone()[0])

    def _finish_run(self, status: str) -> None:
        assert self.conn is not None and self.run_id is not None
        with self.conn.cursor() as cur:
            cur.execute(
                "update runs set status = %s, finished_at = %s where id = %s",
                (status, datetime.now(timezone.utc), self.run_id),
            )

    def ingest_state(self, state: AgentState) -> dict[str, int]:
        if not self.enabled or self.conn is None or self.backfiller is None or self.run_id is None:
            return {"pages": 0, "contacts": 0}
        payload = agent_state_to_debug_payload(state)
        trace_path = _safe_debug_trace_path(state)
        try:
            stats = self.backfiller.ingest_trace(run_id=self.run_id, payload=payload, trace_path=trace_path)
            self.conn.commit()
            return stats
        except Exception:
            self.conn.rollback()
            LOG.exception("Postgres dual-write failed for %s; CSV/debug output will continue", state.target.name)
            return {"pages": 0, "contacts": 0}
