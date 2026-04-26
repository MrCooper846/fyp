#!/usr/bin/env python3
"""
Backfill the Postgres schema from historical NAFSA debug trace folders.

This importer targets the normalized schema in `db/migrations/` and is focused on
high-value historical state:

- runs / run_targets
- institutions / domains / pages / seed URLs
- page_observations (+ acquisition modes)
- contacts / contact_points / contact_observations / contact_evidence

It intentionally treats historical imports as `run_mode = backfill`, while
preserving the original folder name and context inside the run metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit


GENERIC_EMAIL_RE = re.compile(
    r"^(info|enquiries|enquiry|contact|office|support|hello|admissions|international|ug|pg|postgrad|undergrad|apply|students|noreply)[+.\-]?",
    re.I,
)
KNOWN_SOURCE_SYSTEMS = {"openalex", "manual", "crm", "import", "derived"}
KNOWN_CONTACT_KINDS = {"person", "office", "role_holder", "generic_mailbox", "team"}
KNOWN_CONFIDENCE = {"high", "medium", "low"}
KNOWN_PRIORITY = {"high", "medium", "ignore"}


def maybe_fix_mojibake(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if not any(marker in text for marker in ("Ã", "Â", "â€™", "â€“", "â€œ", "â€")):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except Exception:
        return text
    return repaired


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", maybe_fix_mojibake(value)).strip()


def normalize_name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalize_space(normalized).lower()
    normalized = re.sub(r"[^a-z0-9 ]+", "", normalized)
    return normalize_space(normalized)


def normalize_email(value: Any) -> str:
    email = normalize_space(value).lower()
    return email if "@" in email else ""


def normalize_source_system(value: Any) -> str:
    source = normalize_space(value).lower()
    if source in KNOWN_SOURCE_SYSTEMS:
        return source
    if source:
        return "import"
    return "derived"


def normalize_contact_kind(value: Any) -> str:
    kind = normalize_space(value).lower()
    if kind in KNOWN_CONTACT_KINDS:
        return kind
    if kind in {"named_contact", "direct_contact", "recovered_contact"}:
        return "person"
    if kind in {"office_contact", "mailbox"}:
        return "office"
    return ""


def normalize_confidence(value: Any) -> Optional[str]:
    normalized = normalize_space(value).lower()
    return normalized if normalized in KNOWN_CONFIDENCE else None


def normalize_priority(value: Any) -> Optional[str]:
    normalized = normalize_space(value).lower()
    return normalized if normalized in KNOWN_PRIORITY else None


def maybe_fix_mojibake(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    markers = ("Ã", "Â", "â€™", "â€“", "â€œ", "â€", "Ã¢", "Ãƒ", "Ã‚")
    if not any(marker in text for marker in markers):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except Exception:
        return text
    return repaired


def sha256_hex(*parts: Any) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def to_aware_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def infer_registrable_domain(host: str) -> str:
    labels = [label for label in host.lower().split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and len(labels[-2]) <= 3 and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalize_url(url: Any) -> str:
    raw = normalize_space(url)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return raw
    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if not port or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def host_from_url(url: Any) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ""
    try:
        return (urlsplit(normalized).hostname or "").lower()
    except Exception:
        return ""


def url_parts(url: str) -> tuple[str | None, str, str, str | None]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower() if parsed.scheme else None,
        (parsed.hostname or "").lower(),
        parsed.path or "/",
        parsed.query or None,
    )


def infer_domain_relationship(primary_host: str, other_host: str) -> tuple[str, int, bool]:
    primary = (primary_host or "").lower()
    other = (other_host or "").lower()
    if not other:
        return ("secondary", 50, False)
    if other == primary:
        return ("primary", 100, True)
    if primary and infer_registrable_domain(primary) == infer_registrable_domain(other):
        return ("subsite", 80, False)
    return ("partner", 30, False)


def infer_contact_kind(name: str, email: str) -> str:
    if name:
        return "person"
    localpart = email.split("@", 1)[0] if "@" in email else ""
    if GENERIC_EMAIL_RE.match(localpart):
        return "generic_mailbox"
    return "office"


def infer_run_target_status(outcome: dict[str, Any]) -> str:
    if outcome.get("failed"):
        return "failed"
    if int(outcome.get("ranked_contacts", 0) or 0) > 0:
        return "completed"
    return "completed_no_contacts"


def infer_seed_type(candidate: dict[str, Any]) -> str:
    source_type = normalize_space(candidate.get("source_type"))
    page_family = normalize_space(candidate.get("page_family"))
    if source_type:
        return f"{source_type}:{page_family or 'generic'}"
    return page_family or "discovered"


def load_trace(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@dataclass
class DirectorySummary:
    debug_dir: Path
    valid_trace_count: int
    page_observation_count: int
    contact_observation_count: int
    country_code: Optional[str]
    source_type: str
    started_at: datetime
    finished_at: datetime


def summarize_debug_dir(debug_dir: Path, limit_traces: Optional[int] = None) -> DirectorySummary:
    trace_paths = sorted(debug_dir.glob("*.json"))
    valid_traces = 0
    page_observation_count = 0
    contact_observation_count = 0
    first_country: Optional[str] = None
    source_type = "nafsa_debug_trace"
    mtimes: list[float] = []

    for trace_path in trace_paths:
        if limit_traces is not None and valid_traces >= limit_traces:
            break
        payload = load_trace(trace_path)
        if not payload:
            continue
        valid_traces += 1
        target = payload.get("target", {}) or {}
        country = normalize_space(target.get("country")).upper()
        if country and not first_country:
            first_country = country
        page_observation_count += len(payload.get("extraction_trace", []) or [])
        final_contacts = payload.get("final_contacts_with_provenance") or payload.get("ranked_contacts") or []
        contact_observation_count += len(final_contacts)
        mtimes.append(trace_path.stat().st_mtime)

    if not mtimes:
        raise SystemExit(f"No valid debug traces found in {debug_dir}")

    return DirectorySummary(
        debug_dir=debug_dir,
        valid_trace_count=valid_traces,
        page_observation_count=page_observation_count,
        contact_observation_count=contact_observation_count,
        country_code=first_country or None,
        source_type=source_type,
        started_at=to_aware_datetime(min(mtimes)),
        finished_at=to_aware_datetime(max(mtimes)),
    )


def candidate_lookup_from_trace(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for page_trace in payload.get("extraction_trace", []) or []:
        page_url = normalize_url(page_trace.get("url"))
        for candidate in page_trace.get("typed_candidates", []) or []:
            email = normalize_email(candidate.get("email"))
            if not email:
                continue
            lookup[(page_url, email)] = candidate
    return lookup


def find_ranked_contact_source(payload: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
    target_email = normalize_email(contact.get("email"))
    target_source_url = normalize_url(contact.get("source_url"))
    candidate_lookup = candidate_lookup_from_trace(payload)
    return candidate_lookup.get((target_source_url, target_email), {})


def ensure_driver():
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "psycopg is not installed. Install it before running this importer, "
            "for example with `python -m pip install psycopg[binary]`."
        ) from exc
    return psycopg


class PostgresBackfiller:
    def __init__(self, conn: Any):
        self.conn = conn
        self.domain_cache: dict[str, str] = {}
        self.page_cache: dict[str, str] = {}
        self.institution_cache: dict[str, str] = {}

    def _fetch_id(self, query: str, params: tuple[Any, ...]) -> Optional[str]:
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
        return row[0] if row else None

    def get_or_create_run(self, summary: DirectorySummary, run_status: str) -> str:
        folder_marker = f"backfill:{summary.debug_dir.resolve()}"
        existing_id = self._fetch_id(
            "select id from runs where run_mode = 'backfill' and source_type = %s and notes = %s limit 1",
            (summary.source_type, folder_marker),
        )
        if existing_id:
            return existing_id

        config_snapshot = json.dumps(
            {
                "debug_dir": str(summary.debug_dir.resolve()),
                "trace_count": summary.valid_trace_count,
                "backfill_caveat": "historical import from debug traces",
            }
        )
        cli_args = json.dumps({"debug_dir": str(summary.debug_dir)})

        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into runs (
                    run_mode, source_type, country_code, discovery_mode, status,
                    started_at, finished_at, cli_args, config_snapshot, code_version, notes
                )
                values (
                    'backfill', %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s, %s
                )
                returning id
                """,
                (
                    summary.source_type,
                    summary.country_code,
                    "historical_debug_import",
                    run_status,
                    summary.started_at,
                    summary.finished_at,
                    cli_args,
                    config_snapshot,
                    "backfill_debug_v1",
                    folder_marker,
                ),
            )
            run_id = cur.fetchone()[0]
        return run_id

    def get_or_create_institution(self, payload: dict[str, Any]) -> str:
        target = payload.get("target", {}) or {}
        canonical_name = normalize_space(target.get("name"))
        normalized_name = normalize_name_key(canonical_name)
        country_code = normalize_space(target.get("country")).upper() or "ZZ"
        source_system = normalize_source_system(target.get("source"))
        source_url = normalize_url(target.get("source_url") or target.get("url"))
        source_key = source_url or f"{country_code}:{normalized_name}"
        cache_key = f"{source_system}:{source_key}"
        cached = self.institution_cache.get(cache_key)
        if cached:
            return cached

        existing_id = self._fetch_id(
            "select id from institutions where source_system = %s and source_key = %s limit 1",
            (source_system, source_key),
        )
        if existing_id:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    update institutions
                    set canonical_name = coalesce(nullif(canonical_name, ''), %s),
                        normalized_name = coalesce(nullif(normalized_name, ''), %s),
                        institution_type = coalesce(nullif(institution_type, ''), %s),
                        country_code = coalesce(nullif(country_code, ''), %s),
                        last_seen_at = now()
                    where id = %s
                    """,
                    (
                        canonical_name,
                        normalized_name,
                        normalize_space(target.get("org_type")) or "university",
                        country_code,
                        existing_id,
                    ),
                )
            self.institution_cache[cache_key] = existing_id
            return existing_id

        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into institutions (
                    source_system, source_key, canonical_name, normalized_name,
                    institution_type, country_code, current_status
                )
                values (%s, %s, %s, %s, %s, %s, 'active')
                returning id
                """,
                (
                    source_system,
                    source_key,
                    canonical_name,
                    normalized_name,
                    normalize_space(target.get("org_type")) or "university",
                    country_code,
                ),
            )
            institution_id = cur.fetchone()[0]
        self.institution_cache[cache_key] = institution_id
        return institution_id

    def get_or_create_domain(self, host: str) -> str:
        host = normalize_space(host).lower()
        if not host:
            raise ValueError("host is required")
        cached = self.domain_cache.get(host)
        if cached:
            return cached

        existing_id = self._fetch_id("select id from domains where domain = %s limit 1", (host,))
        registrable_domain = infer_registrable_domain(host)
        is_subdomain = host != registrable_domain

        if existing_id:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    update domains
                    set registrable_domain = %s,
                        is_subdomain = %s,
                        last_seen_at = now()
                    where id = %s
                    """,
                    (registrable_domain, is_subdomain, existing_id),
                )
            self.domain_cache[host] = existing_id
            return existing_id

        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into domains (domain, registrable_domain, is_subdomain)
                values (%s, %s, %s)
                returning id
                """,
                (host, registrable_domain, is_subdomain),
            )
            domain_id = cur.fetchone()[0]
        self.domain_cache[host] = domain_id
        return domain_id

    def get_or_create_page(self, url: Any) -> Optional[str]:
        normalized = normalize_url(url)
        if not normalized:
            return None
        cached = self.page_cache.get(normalized)
        if cached:
            return cached

        existing_id = self._fetch_id("select id from pages where normalized_url = %s limit 1", (normalized,))
        scheme, host, path, query_string = url_parts(normalized)
        domain_id = self.get_or_create_domain(host)

        if existing_id:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    update pages
                    set raw_url = %s,
                        domain_id = %s,
                        scheme = %s,
                        host = %s,
                        path = %s,
                        query_string = %s,
                        last_seen_at = now()
                    where id = %s
                    """,
                    (normalize_space(url), domain_id, scheme, host, path, query_string, existing_id),
                )
            self.page_cache[normalized] = existing_id
            return existing_id

        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into pages (normalized_url, raw_url, domain_id, scheme, host, path, query_string)
                values (%s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (normalized, normalize_space(url), domain_id, scheme, host, path, query_string),
            )
            page_id = cur.fetchone()[0]
        self.page_cache[normalized] = page_id
        return page_id

    def upsert_institution_domain(self, institution_id: str, host: str, *, primary_host: str) -> None:
        if not host:
            return
        domain_id = self.get_or_create_domain(host)
        relationship_type, trust_level, is_primary = infer_domain_relationship(primary_host, host)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into institution_domains (
                    institution_id, domain_id, relationship_type, trust_level, is_primary
                )
                values (%s, %s, %s, %s, %s)
                on conflict (institution_id, domain_id, relationship_type)
                do update set
                    trust_level = greatest(institution_domains.trust_level, excluded.trust_level),
                    is_primary = institution_domains.is_primary or excluded.is_primary,
                    last_seen_at = now()
                """,
                (institution_id, domain_id, relationship_type, trust_level, is_primary),
            )

    def upsert_seed_url(self, institution_id: str, url: str, seed_type: str, run_id: str) -> None:
        page_id = self.get_or_create_page(url)
        if not page_id:
            return
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into institution_seed_urls (
                    institution_id, page_id, seed_url, seed_type, source_run_id, usefulness_score, times_used, is_active
                )
                values (%s, %s, %s, %s, %s, %s, 1, true)
                on conflict (institution_id, seed_url, seed_type)
                do update set
                    page_id = excluded.page_id,
                    source_run_id = excluded.source_run_id,
                    usefulness_score = greatest(institution_seed_urls.usefulness_score, excluded.usefulness_score),
                    times_used = institution_seed_urls.times_used + 1,
                    last_used_at = now(),
                    updated_at = now()
                """,
                (institution_id, page_id, normalize_url(url), seed_type, run_id, 10.0),
            )

    def upsert_run_target(self, run_id: str, institution_id: str, payload: dict[str, Any], trace_path: Path) -> str:
        target = payload.get("target", {}) or {}
        outcome = payload.get("outcome", {}) or {}

        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into run_targets (
                    run_id, institution_id, status, homepage_url_used, source_homepage_url,
                    stop_reason, hard_success, soft_success, failed, failure_reason,
                    pages_fetched, llm_calls, ranked_contacts_count, qualified_contacts_count,
                    debug_trace_path
                )
                values (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s
                )
                on conflict (run_id, institution_id)
                do update set
                    status = excluded.status,
                    homepage_url_used = excluded.homepage_url_used,
                    source_homepage_url = excluded.source_homepage_url,
                    stop_reason = excluded.stop_reason,
                    hard_success = excluded.hard_success,
                    soft_success = excluded.soft_success,
                    failed = excluded.failed,
                    failure_reason = excluded.failure_reason,
                    pages_fetched = excluded.pages_fetched,
                    llm_calls = excluded.llm_calls,
                    ranked_contacts_count = excluded.ranked_contacts_count,
                    qualified_contacts_count = excluded.qualified_contacts_count,
                    debug_trace_path = excluded.debug_trace_path,
                    updated_at = now()
                returning id
                """,
                (
                    run_id,
                    institution_id,
                    infer_run_target_status(outcome),
                    normalize_url(target.get("url")),
                    normalize_url(target.get("source_url") or target.get("url")),
                    normalize_space(payload.get("stop_reason")),
                    bool(outcome.get("hard_success")),
                    bool(outcome.get("soft_success")),
                    bool(outcome.get("failed")),
                    normalize_space(outcome.get("failure_reason")),
                    int(outcome.get("pages_fetched", 0) or 0),
                    int(outcome.get("llm_calls", 0) or 0),
                    int(outcome.get("ranked_contacts", 0) or 0),
                    int(outcome.get("qualified_contacts", 0) or 0),
                    str(trace_path.resolve()),
                ),
            )
            return cur.fetchone()[0]

    def get_or_create_page_observation(
        self,
        *,
        page_id: str,
        institution_id: str,
        run_id: str,
        run_target_id: str,
        source_strategy: str,
        source_stage: str,
        parent_url: str,
        final_url: str,
        insert_payload: dict[str, Any],
    ) -> str:
        existing_id = self._fetch_id(
            """
            select id
            from page_observations
            where run_target_id = %s
              and page_id = %s
              and coalesce(source_strategy, '') = %s
              and coalesce(source_stage, '') = %s
              and coalesce(parent_url, '') = %s
              and coalesce(final_url, '') = %s
            limit 1
            """,
            (run_target_id, page_id, source_strategy, source_stage, parent_url, final_url),
        )
        if existing_id:
            return existing_id

        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into page_observations (
                    page_id, institution_id, run_id, run_target_id, parent_page_id, parent_url,
                    observed_at, http_status, final_url, content_type, content_hash, title,
                    source_type, source_strategy, source_stage, page_family, candidate_bucket,
                    heuristic_score, selected_for_planning, shell_like, weak_llm_shell_inference,
                    visible_text_length, embedded_text_length, embedded_document_count,
                    raw_evidence_count, clean_candidate_count, named_contact_count, office_contact_count,
                    missing_email_count, junk_candidate_count, potential_anchor_pattern_count,
                    is_useful, observation_notes
                )
                values (
                    %s, %s, %s, %s, %s, %s,
                    now(), %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s::jsonb
                )
                returning id
                """,
                (
                    page_id,
                    institution_id,
                    run_id,
                    run_target_id,
                    insert_payload.get("parent_page_id"),
                    parent_url or None,
                    insert_payload.get("http_status"),
                    final_url or None,
                    insert_payload.get("content_type"),
                    insert_payload.get("content_hash"),
                    insert_payload.get("title"),
                    insert_payload.get("source_type"),
                    source_strategy or None,
                    source_stage or None,
                    insert_payload.get("page_family"),
                    insert_payload.get("candidate_bucket"),
                    insert_payload.get("heuristic_score"),
                    bool(insert_payload.get("selected_for_planning")),
                    bool(insert_payload.get("shell_like")),
                    bool(insert_payload.get("weak_llm_shell_inference")),
                    int(insert_payload.get("visible_text_length", 0) or 0),
                    int(insert_payload.get("embedded_text_length", 0) or 0),
                    int(insert_payload.get("embedded_document_count", 0) or 0),
                    int(insert_payload.get("raw_evidence_count", 0) or 0),
                    int(insert_payload.get("clean_candidate_count", 0) or 0),
                    int(insert_payload.get("named_contact_count", 0) or 0),
                    int(insert_payload.get("office_contact_count", 0) or 0),
                    int(insert_payload.get("missing_email_count", 0) or 0),
                    int(insert_payload.get("junk_candidate_count", 0) or 0),
                    int(insert_payload.get("potential_anchor_pattern_count", 0) or 0),
                    bool(insert_payload.get("is_useful", False)),
                    json.dumps(insert_payload.get("observation_notes", {})),
                ),
            )
            return cur.fetchone()[0]

    def ingest_trace(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
        trace_path: Path,
    ) -> dict[str, int]:
        stats = {"pages": 0, "contacts": 0}

        institution_id = self.get_or_create_institution(payload)
        run_target_id = self.upsert_run_target(run_id, institution_id, payload, trace_path)

        target = payload.get("target", {}) or {}
        primary_homepage = normalize_url(target.get("source_url") or target.get("url"))
        primary_host = host_from_url(primary_homepage)
        if primary_homepage:
            self.upsert_seed_url(institution_id, primary_homepage, "source_homepage", run_id)
        if primary_host:
            self.upsert_institution_domain(institution_id, primary_host, primary_host=primary_host)

        for candidate in payload.get("discovery_trace", []) or []:
            if candidate.get("selected_for_planning"):
                candidate_url = normalize_url(candidate.get("url"))
                if candidate_url:
                    self.upsert_seed_url(institution_id, candidate_url, infer_seed_type(candidate), run_id)
                    candidate_host = host_from_url(candidate_url)
                    if candidate_host:
                        self.upsert_institution_domain(institution_id, candidate_host, primary_host=primary_host)

        page_observation_by_url: dict[str, str] = {}
        for page_trace in payload.get("extraction_trace", []) or []:
            page_url = normalize_url(page_trace.get("url"))
            page_id = self.get_or_create_page(page_url)
            if not page_id:
                continue
            parent_url = normalize_url(page_trace.get("parent_url"))
            parent_page_id = self.get_or_create_page(parent_url) if parent_url else None
            page_host = host_from_url(page_url)
            if page_host:
                self.upsert_institution_domain(institution_id, page_host, primary_host=primary_host)

            page_observation_id = self.get_or_create_page_observation(
                page_id=page_id,
                institution_id=institution_id,
                run_id=run_id,
                run_target_id=run_target_id,
                source_strategy=normalize_space(page_trace.get("source_strategy")),
                source_stage=normalize_space(page_trace.get("source_stage")),
                parent_url=parent_url,
                final_url=page_url,
                insert_payload={
                    "parent_page_id": parent_page_id,
                    "source_type": normalize_space(page_trace.get("page_type") or page_trace.get("source_type")),
                    "source_strategy": normalize_space(page_trace.get("source_strategy")),
                    "source_stage": normalize_space(page_trace.get("source_stage")),
                    "page_family": normalize_space(page_trace.get("page_family")),
                    "candidate_bucket": "content",
                    "heuristic_score": page_trace.get("expected_yield"),
                    "selected_for_planning": True,
                    "shell_like": page_trace.get("shell_like"),
                    "weak_llm_shell_inference": page_trace.get("weak_llm_shell_inference"),
                    "visible_text_length": page_trace.get("visible_text_length"),
                    "embedded_text_length": page_trace.get("embedded_text_length"),
                    "embedded_document_count": page_trace.get("embedded_document_count"),
                    "raw_evidence_count": page_trace.get("raw_contacts_found"),
                    "clean_candidate_count": page_trace.get("clean_candidate_count"),
                    "named_contact_count": page_trace.get("named_contact_count"),
                    "office_contact_count": page_trace.get("office_contact_count"),
                    "missing_email_count": page_trace.get("person_without_email_count"),
                    "junk_candidate_count": page_trace.get("junk_candidate_count"),
                    "potential_anchor_pattern_count": page_trace.get("potential_anchor_pattern_count"),
                    "is_useful": bool(page_trace.get("kept_contacts")),
                    "observation_notes": {
                        "reason": page_trace.get("reason"),
                        "shell_context_key": page_trace.get("shell_context_key"),
                        "family_signature": page_trace.get("family_signature"),
                        "raw_evidence_count_by_strategy": page_trace.get("raw_evidence_count_by_strategy", {}),
                        "potential_anchor_patterns": page_trace.get("potential_anchor_patterns", []),
                        "kept_contacts": page_trace.get("kept_contacts", []),
                        "rejected_contacts": page_trace.get("rejected_contacts", []),
                        "missing_email_candidates": page_trace.get("missing_email_candidates", []),
                        "role_holder_candidates": page_trace.get("role_holder_candidates", []),
                    },
                },
            )
            page_observation_by_url[page_url] = page_observation_id
            stats["pages"] += 1

            with self.conn.cursor() as cur:
                for mode in page_trace.get("acquisition_modes", []) or []:
                    cur.execute(
                        """
                        insert into page_observation_acquisition_modes (page_observation_id, acquisition_mode)
                        values (%s, %s)
                        on conflict (page_observation_id, acquisition_mode) do nothing
                        """,
                        (page_observation_id, normalize_space(mode)),
                    )

        final_contacts = payload.get("final_contacts_with_provenance") or payload.get("ranked_contacts") or []
        for contact in final_contacts:
            email = normalize_email(contact.get("email"))
            name = normalize_space(contact.get("name"))
            title = normalize_space(contact.get("title"))
            source_url = normalize_url(contact.get("source_url"))
            evidence_url = normalize_url(contact.get("evidence_url") or source_url)
            matched_candidate = find_ranked_contact_source(payload, contact)

            contact_kind = (
                normalize_contact_kind(matched_candidate.get("candidate_kind"))
                or infer_contact_kind(name, email)
            )
            canonical_name = name or None
            normalized_name = normalize_name_key(name) or None
            identity_hash = sha256_hex(institution_id, email or "", normalized_name or "", contact_kind)

            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    insert into contacts (
                        institution_id, contact_kind, canonical_name, normalized_name, identity_hash
                    )
                    values (%s, %s, %s, %s, %s)
                    on conflict (institution_id, identity_hash)
                    do update set
                        canonical_name = coalesce(contacts.canonical_name, excluded.canonical_name),
                        normalized_name = coalesce(contacts.normalized_name, excluded.normalized_name),
                        updated_at = now()
                    returning id
                    """,
                    (institution_id, contact_kind, canonical_name, normalized_name, identity_hash),
                )
                contact_id = cur.fetchone()[0]

                if email:
                    value_hash = sha256_hex("email", email)
                    cur.execute(
                        """
                        insert into contact_points (
                            contact_id, point_type, point_value, normalized_value, value_hash, is_primary, is_active
                        )
                        values (%s, 'email', %s, %s, %s, true, true)
                        on conflict (contact_id, point_type, normalized_value)
                        do update set
                            is_primary = contact_points.is_primary or excluded.is_primary,
                            is_active = true,
                            updated_at = now()
                        """,
                        (contact_id, email, email, value_hash),
                    )

            email_domain = email.split("@", 1)[1] if "@" in email else ""
            if email_domain:
                self.upsert_institution_domain(institution_id, email_domain, primary_host=primary_host)

            evidence_page_obs_id = page_observation_by_url.get(source_url) or page_observation_by_url.get(evidence_url)
            evidence_page_url = evidence_url or source_url
            if not evidence_page_obs_id and evidence_page_url:
                page_id = self.get_or_create_page(evidence_page_url)
                if page_id:
                    evidence_host = host_from_url(evidence_page_url)
                    if evidence_host:
                        self.upsert_institution_domain(institution_id, evidence_host, primary_host=primary_host)
                    evidence_page_obs_id = self.get_or_create_page_observation(
                        page_id=page_id,
                        institution_id=institution_id,
                        run_id=run_id,
                        run_target_id=run_target_id,
                        source_strategy="backfill_final_contact",
                        source_stage="backfill",
                        parent_url="",
                        final_url=evidence_page_url,
                        insert_payload={
                            "parent_page_id": None,
                            "source_type": "backfill",
                            "source_strategy": "backfill_final_contact",
                            "source_stage": "backfill",
                            "page_family": "",
                            "candidate_bucket": "content",
                            "heuristic_score": None,
                            "selected_for_planning": True,
                            "shell_like": False,
                            "weak_llm_shell_inference": False,
                            "visible_text_length": 0,
                            "embedded_text_length": 0,
                            "embedded_document_count": 0,
                            "raw_evidence_count": 0,
                            "clean_candidate_count": 0,
                            "named_contact_count": 0,
                            "office_contact_count": 0,
                            "missing_email_count": 0,
                            "junk_candidate_count": 0,
                            "potential_anchor_pattern_count": 0,
                            "is_useful": True,
                            "observation_notes": {"synthetic_backfill_page_observation": True},
                        },
                    )

            observation_hash = sha256_hex(
                institution_id,
                email,
                normalized_name or "",
                title,
                evidence_page_url,
                normalize_space(contact.get("candidate_status")),
                run_target_id,
            )
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    insert into contact_observations (
                        contact_id, institution_id, run_id, run_target_id, page_observation_id,
                        observed_at, observed_name, observed_title, observed_email,
                        contact_kind_observed, confidence, score, priority, candidate_status,
                        email_source, evidence_type, recovery_reason, classifier_reason,
                        source_url, evidence_url, observation_hash, was_exported
                    )
                    values (
                        %s, %s, %s, %s, %s,
                        now(), %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, true
                    )
                    on conflict (contact_id, observation_hash)
                    do update set
                        page_observation_id = coalesce(contact_observations.page_observation_id, excluded.page_observation_id),
                        was_exported = contact_observations.was_exported or excluded.was_exported
                    returning id
                    """,
                    (
                        contact_id,
                        institution_id,
                        run_id,
                        run_target_id,
                        evidence_page_obs_id,
                        canonical_name,
                        title or None,
                        email or None,
                        contact_kind,
                        normalize_confidence(contact.get("confidence")),
                        None,
                        normalize_priority(contact.get("priority")),
                        normalize_space(contact.get("candidate_status")) or None,
                        normalize_space(contact.get("email_source")) or None,
                        normalize_space(contact.get("evidence_type")) or None,
                        normalize_space(contact.get("recovery_reason")) or None,
                        normalize_space(contact.get("reason")) or None,
                        source_url or None,
                        evidence_url or None,
                        observation_hash,
                    ),
                )
                contact_observation_id = cur.fetchone()[0]

                for strategy in matched_candidate.get("source_strategies", []) or []:
                    cur.execute(
                        """
                        insert into contact_observation_strategies (contact_observation_id, strategy_name)
                        values (%s, %s)
                        on conflict (contact_observation_id, strategy_name) do nothing
                        """,
                        (contact_observation_id, normalize_space(strategy)),
                    )

                for flag in matched_candidate.get("cleanup_flags", []) or []:
                    cur.execute(
                        """
                        insert into contact_observation_flags (contact_observation_id, flag_name)
                        values (%s, %s)
                        on conflict (contact_observation_id, flag_name) do nothing
                        """,
                        (contact_observation_id, normalize_space(flag)),
                    )

                cur.execute(
                    """
                    insert into contact_evidence (
                        contact_observation_id, page_observation_id, evidence_kind, snippet, evidence_payload, page_url
                    )
                    values (%s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        contact_observation_id,
                        evidence_page_obs_id,
                        normalize_space(contact.get("evidence_type")) or "backfill_final_contact",
                        normalize_space(matched_candidate.get("context")) or None,
                        json.dumps(
                            {
                                "final_contact": contact,
                                "matched_candidate": matched_candidate,
                            }
                        ),
                        evidence_page_url or None,
                    ),
                )

            stats["contacts"] += 1

        for family_signature in payload.get("dead_candidate_signatures", []) or []:
            normalized_signature = normalize_space(family_signature)
            if not normalized_signature:
                continue
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    insert into crawl_memory_dead_families (
                        institution_id, family_signature, source_strategy, first_seen_run_id, last_seen_run_id
                    )
                    values (%s, %s, %s, %s, %s)
                    on conflict (institution_id, family_signature)
                    where institution_id is not null
                    do update set
                        last_seen_run_id = excluded.last_seen_run_id,
                        last_seen_at = now(),
                        hit_count = crawl_memory_dead_families.hit_count + 1
                    """,
                    (institution_id, normalized_signature, "historical_debug_backfill", run_id, run_id),
                )

        return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Postgres from historical NAFSA debug trace folders.")
    parser.add_argument("debug_dirs", nargs="+", help="One or more debug trace directories")
    parser.add_argument("--dsn", default=None, help="Postgres DSN. Falls back to DATABASE_URL.")
    parser.add_argument(
        "--run-status",
        default="completed_partial",
        choices=["queued", "running", "completed", "completed_partial", "failed", "cancelled"],
        help="Status to assign to imported historical runs (default: completed_partial)",
    )
    parser.add_argument("--limit-traces", type=int, default=None, help="Only import the first N valid traces per folder")
    parser.add_argument("--dry-run", action="store_true", help="Summarize what would be imported without touching Postgres")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    debug_dirs = [Path(path) for path in args.debug_dirs]
    summaries: list[DirectorySummary] = []
    total_traces = 0
    for debug_dir in debug_dirs:
        if not debug_dir.is_dir():
            raise SystemExit(f"Debug directory not found: {debug_dir}")
        summary = summarize_debug_dir(debug_dir, limit_traces=args.limit_traces)
        summaries.append(summary)
        total_traces += summary.valid_trace_count

    if args.dry_run:
        total_pages = sum(summary.page_observation_count for summary in summaries)
        total_contacts = sum(summary.contact_observation_count for summary in summaries)
        print(
            f"Dry run: {len(summaries)} folders, {total_traces} valid traces, "
            f"{total_pages} page observations, {total_contacts} contact observations"
        )
        for summary in summaries:
            print(
                f"- {summary.debug_dir} :: {summary.valid_trace_count} traces, "
                f"country={summary.country_code or 'unknown'}, "
                f"pages={summary.page_observation_count}, contacts={summary.contact_observation_count}, "
                f"window={summary.started_at.isoformat()} -> {summary.finished_at.isoformat()}"
            )
        return

    dsn = args.dsn or None
    if not dsn:
        import os

        dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Provide --dsn or set DATABASE_URL before running the importer.")

    psycopg = ensure_driver()
    imported_runs = 0
    imported_pages = 0
    imported_contacts = 0
    skipped_traces = 0

    with psycopg.connect(dsn) as conn:
        backfiller = PostgresBackfiller(conn)
        for summary in summaries:
            run_id = backfiller.get_or_create_run(summary, args.run_status)
            conn.commit()
            trace_paths = sorted(summary.debug_dir.glob("*.json"))
            valid_loaded = 0
            for trace_path in trace_paths:
                if args.limit_traces is not None and valid_loaded >= args.limit_traces:
                    break
                payload = load_trace(trace_path)
                if not payload:
                    continue
                try:
                    stats = backfiller.ingest_trace(run_id=run_id, payload=payload, trace_path=trace_path)
                except Exception as exc:
                    conn.rollback()
                    skipped_traces += 1
                    print(f"Skipped trace {trace_path.name}: {exc}", file=sys.stderr)
                    continue
                conn.commit()
                valid_loaded += 1
                imported_pages += stats["pages"]
                imported_contacts += stats["contacts"]
            imported_runs += 1
            print(
                f"Imported {valid_loaded} traces from {summary.debug_dir.name} "
                f"into run {run_id}"
            )

    print(
        f"\nBackfill complete: {imported_runs} runs, {total_traces} traces, "
        f"{imported_pages} page observations, {imported_contacts} contact observations"
    )
    if skipped_traces:
        print(f"Skipped {skipped_traces} traces due to import errors", file=sys.stderr)


if __name__ == "__main__":
    main()
