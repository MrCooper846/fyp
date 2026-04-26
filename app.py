from flask import Flask, render_template, request, send_from_directory, redirect, url_for, abort, jsonify
import json, os, sys, asyncio, subprocess, threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from uuid import uuid4

# Try to import the refactored crawler module
try:
    from gc_contacts.main import run_all as crawler_run_all  # async coroutine
except Exception:
    crawler_run_all = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__)

BASE_DIR  = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "downloads"
DEBUG_ROOT = OUTPUT_DIR / "debug"
WORKSPACE_DEBUG_ROOT = BASE_DIR / "debug_logs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_ROOT.mkdir(parents=True, exist_ok=True)
WORKSPACE_DEBUG_ROOT.mkdir(parents=True, exist_ok=True)

TRACE_SOURCES = {
    "workspace": WORKSPACE_DEBUG_ROOT,
    "downloads": DEBUG_ROOT,
}

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
JOB_LOCK = threading.Lock()
JOBS: dict[str, dict] = {}
JOB_LOG_ROOT = BASE_DIR / "logs" / "web_jobs"
JOB_LOG_ROOT.mkdir(parents=True, exist_ok=True)

def _db_available() -> bool:
    return bool(DATABASE_URL)

def _db_connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:
        raise RuntimeError("psycopg is not installed. Install requirements.txt first.") from exc
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def _db_query(sql: str, params: tuple = ()) -> list[dict]:
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

def _db_one(sql: str, params: tuple = ()) -> dict | None:
    rows = _db_query(sql, params)
    return rows[0] if rows else None

def _jsonify_db_row(row: dict) -> dict:
    converted = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            converted[key] = value.isoformat()
        elif isinstance(value, Decimal):
            converted[key] = float(value)
        elif isinstance(value, UUID):
            converted[key] = str(value)
        else:
            converted[key] = value
    return converted

def _parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def _job_snapshot(job: dict) -> dict:
    snapshot = dict(job)
    for key in ("created_at", "started_at", "finished_at"):
        if isinstance(snapshot.get(key), datetime):
            snapshot[key] = snapshot[key].isoformat()
    return snapshot

def _latest_live_run_for_job(job: dict) -> dict | None:
    if not _db_available():
        return None
    started_at = job.get("started_at")
    country = job.get("country")
    try:
        return _db_one(
            """
            select
                id::text,
                status::text,
                started_at,
                finished_at,
                (
                    select count(*) from run_targets rt where rt.run_id = runs.id
                ) as targets,
                (
                    select count(*) from contact_observations co where co.run_id = runs.id
                ) as contacts,
                (
                    select count(*) from page_observations po where po.run_id = runs.id
                ) as pages
            from runs
            where source_type = 'nafsa_live_pipeline'
              and country_code = %s
              and started_at >= %s
            order by started_at desc, id desc
            limit 1
            """,
            (country, started_at),
        )
    except Exception:
        return None

def _tail_file(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    return data[-max_chars:].decode("utf-8", errors="replace")

def _run_web_job(job_id: str) -> None:
    with JOB_LOCK:
        job = JOBS[job_id]
        job["status"] = "running"
        job["started_at"] = datetime.utcnow()
        log_path = Path(job["log_path"])

    env = os.environ.copy()
    if DATABASE_URL:
        env["DATABASE_URL"] = DATABASE_URL
        env["POSTGRES_DUAL_WRITE"] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")

    cmd = [
        sys.executable,
        str(BASE_DIR / "gc_contacts_cli.py"),
        "nafsa",
        job["country"],
        "--output",
        job["outfile"],
        "--debug",
        "--debug-dir",
        job["debug_dir"],
        "--discovery-mode",
        job["discovery_mode"],
        "--concurrency",
        str(job["concurrency"]),
    ]
    if job.get("limit"):
        cmd.extend(["--limit", str(job["limit"])])
    if job.get("ignore_robots"):
        cmd.append("--ignore-robots")
    if job.get("classify"):
        cmd.append("--classify")

    with JOB_LOCK:
        JOBS[job_id]["command"] = " ".join(cmd)

    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(f"Starting job {job_id} at {datetime.utcnow().isoformat()}Z\n")
            log.write(f"Command: {' '.join(cmd)}\n\n")
            log.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            with JOB_LOCK:
                JOBS[job_id]["pid"] = proc.pid
            returncode = proc.wait()

        with JOB_LOCK:
            job = JOBS[job_id]
            job["returncode"] = returncode
            job["status"] = "completed" if returncode == 0 else "failed"
            job["finished_at"] = datetime.utcnow()
    except Exception as exc:
        with JOB_LOCK:
            job = JOBS[job_id]
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = datetime.utcnow()

def safe_country(code: str) -> str:
    code = (code or "").strip().upper()
    return "".join(c for c in code if c.isalpha())[:3]

def _resolve_trace_root(source: str) -> Path:
    root = TRACE_SOURCES.get((source or "").strip().lower())
    if root is None:
        abort(400, description="Unknown trace source.")
    return root

def _safe_relative_trace_path(raw_path: str) -> Path:
    rel = Path((raw_path or "").strip())
    if rel.is_absolute():
        abort(400, description="Trace path must be relative.")
    if any(part in ("..", "") for part in rel.parts):
        abort(400, description="Unsafe trace path.")
    return rel

def _trace_file_records(root: Path):
    records = []
    if not root.exists():
        return records
    for path in root.rglob("*.json"):
        if not path.is_file():
            continue
        stat = path.stat()
        rel = path.relative_to(root).as_posix()
        records.append(
            {
                "name": path.stem,
                "path": rel,
                "directory": path.parent.relative_to(root).as_posix() if path.parent != root else "",
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size_bytes": stat.st_size,
            }
        )
    records.sort(key=lambda item: (item["directory"], item["name"].lower()))
    return records

def run_crawler(country: str, outfile: Path, limit: int|None, debug_dir: Path):
    """
    Runs the crawler with good defaults:
    - emit_all + debug on
    - ignore_robots + browser_ua on (adjust later if you want)
    """
    emit_all = True
    debug = True
    ignore_robots = True
    browser_ua = True
    verbose = False

    if crawler_run_all:
        # call the async coroutine directly
        asyncio.run(
            crawler_run_all(
                country.upper(),
                limit,
                str(outfile),
                emit_all,
                debug,
                str(debug_dir),
                ignore_robots,
                verbose,
                browser_ua,
                12,
                False,
                "heuristic_only",
            )
        )
    else:
        # fallback: shell out to the CLI script
        script = BASE_DIR / "gc_contacts_cli.py"
        if not script.exists():
            raise RuntimeError(f"Crawler script not found at {script}")
        cmd = [sys.executable, str(script), country.upper(), "--outfile", str(outfile)]
        if limit is not None:
            cmd += ["--limit", str(limit)]
        if emit_all:
            cmd.append("--emit-all")
        if debug:
            cmd += ["--debug", "--debug-dir", str(debug_dir)]
        if ignore_robots:
            cmd.append("--ignore-robots")
        if browser_ua:
            cmd.append("--browser-ua")
        if verbose:
            cmd.append("--verbose")
        subprocess.run(cmd, check=True)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        country = safe_country(request.form.get('country', ''))
        if not country:
            abort(400, description="Country code is required.")

        # 1) LIMIT (optional)
        limit_raw = (request.form.get('limit') or '').strip()
        limit = int(limit_raw) if limit_raw.isdigit() else None

        # 2) TIMESTAMPED FILENAME
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{country}_contacts_{stamp}.csv"
        outfile = OUTPUT_DIR / filename
        debug_dir = DEBUG_ROOT / f"{country}_{stamp}"
        debug_dir.mkdir(parents=True, exist_ok=True)

        try:
            run_crawler(country, outfile, limit, debug_dir)
        except Exception as e:
            return f"<h3>Run failed</h3><pre>{e}</pre>", 500

        return redirect(url_for('download_page', filename=filename))
    return render_template('index.html')

@app.route('/traces')
def traces_page():
    return render_template('trace_viewer.html')

@app.route('/db')
def db_dashboard():
    return render_template('db_dashboard.html', db_available=_db_available())

@app.route('/api/db/overview')
def db_overview():
    if not _db_available():
        return jsonify({"available": False, "error": "DATABASE_URL is not configured."}), 503
    try:
        summary = _db_one(
            """
            select
                (select count(*) from runs) as runs,
                (select count(*) from run_targets) as run_targets,
                (select count(*) from institutions) as institutions,
                (select count(*) from contacts) as contacts,
                (select count(*) from contact_observations) as contact_observations,
                (select count(*) from page_observations) as page_observations
            """
        )
        runs = _db_query(
            """
            select
                id::text,
                run_mode::text,
                source_type,
                country_code,
                discovery_mode,
                status::text,
                started_at,
                finished_at,
                code_version,
                notes,
                (
                    select count(*)
                    from run_targets rt
                    where rt.run_id = runs.id
                ) as targets,
                (
                    select count(*)
                    from contact_observations co
                    where co.run_id = runs.id
                ) as contacts,
                (
                    select count(*)
                    from page_observations po
                    where po.run_id = runs.id
                ) as pages
            from runs
            order by started_at desc, id desc
            limit 40
            """
        )
        coverage = _db_query(
            """
            select coverage_status, count(*) as count
            from institution_current_state
            group by coverage_status
            order by count desc
            """
        )
        return jsonify(
            {
                "available": True,
                "summary": _jsonify_db_row(summary or {}),
                "runs": [_jsonify_db_row(row) for row in runs],
                "coverage": [_jsonify_db_row(row) for row in coverage],
            }
        )
    except Exception as exc:
        return jsonify({"available": False, "error": str(exc)}), 500

@app.route('/api/jobs/start', methods=['POST'])
def start_job():
    payload = request.get_json(silent=True) or request.form
    country = safe_country(payload.get("country", ""))
    if not country:
        return jsonify({"error": "Country code is required."}), 400

    limit_raw = str(payload.get("limit", "") or "").strip()
    limit = int(limit_raw) if limit_raw.isdigit() else None
    concurrency_raw = str(payload.get("concurrency", "6") or "6").strip()
    concurrency = max(1, min(20, int(concurrency_raw) if concurrency_raw.isdigit() else 6))
    discovery_mode = str(payload.get("discovery_mode", "hybrid") or "hybrid")
    if discovery_mode not in {"heuristic_only", "generated_slug_only", "real_link_only", "hybrid"}:
        discovery_mode = "hybrid"

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    job_id = uuid4().hex
    outfile = OUTPUT_DIR / f"{country}_nafsa_{stamp}.csv"
    debug_dir = DEBUG_ROOT / f"{country}_nafsa_{stamp}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    log_path = JOB_LOG_ROOT / f"{job_id}.log"

    job = {
        "id": job_id,
        "status": "queued",
        "country": country,
        "limit": limit,
        "concurrency": concurrency,
        "discovery_mode": discovery_mode,
        "ignore_robots": _parse_bool(payload.get("ignore_robots"), True),
        "classify": _parse_bool(payload.get("classify"), False),
        "outfile": str(outfile),
        "debug_dir": str(debug_dir),
        "log_path": str(log_path),
        "created_at": datetime.utcnow(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "returncode": None,
        "error": "",
    }
    with JOB_LOCK:
        JOBS[job_id] = job

    thread = threading.Thread(target=_run_web_job, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({"job": _job_snapshot(job)})

@app.route('/api/jobs')
def list_jobs():
    with JOB_LOCK:
        raw_jobs = list(JOBS.values())
        jobs = [_job_snapshot(job) for job in raw_jobs]
    for snapshot, raw_job in zip(jobs, raw_jobs):
        live_run = _latest_live_run_for_job(raw_job)
        if live_run:
            snapshot["live_run"] = _jsonify_db_row(live_run)
    jobs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return jsonify({"jobs": jobs})

@app.route('/api/jobs/<job_id>')
def job_detail(job_id):
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            abort(404, description="Job not found")
        snapshot = _job_snapshot(job)
    live_run = _latest_live_run_for_job(job)
    if live_run:
        snapshot["live_run"] = _jsonify_db_row(live_run)
    snapshot["log_tail"] = _tail_file(Path(job["log_path"]))
    return jsonify({"job": snapshot})

@app.route('/api/db/contacts')
def db_contacts():
    if not _db_available():
        return jsonify({"contacts": [], "error": "DATABASE_URL is not configured."}), 503
    q = f"%{(request.args.get('q') or '').strip()}%"
    country = (request.args.get("country") or "").strip().upper()
    confidence = (request.args.get("confidence") or "").strip().lower()
    params: list = [q, q, q]
    where = [
        "(i.canonical_name ilike %s or ccs.current_name ilike %s or ccs.current_email ilike %s)"
    ]
    if country:
        where.append("i.country_code = %s")
        params.append(country)
    if confidence in {"high", "medium", "low"}:
        where.append("ccs.current_confidence = %s")
        params.append(confidence)
    rows = _db_query(
        f"""
        select
            ccs.contact_id::text,
            i.canonical_name as institution,
            i.country_code,
            ccs.current_name,
            ccs.current_title,
            ccs.current_email,
            ccs.current_confidence::text,
            ccs.current_priority::text,
            ccs.times_seen,
            ccs.last_seen_at,
            co.email_source,
            co.evidence_type,
            co.recovery_reason,
            co.source_url,
            co.evidence_url
        from contact_current_state ccs
        join institutions i on i.id = ccs.institution_id
        left join contact_observations co on co.id = ccs.latest_observation_id
        where {' and '.join(where)}
        order by
            case ccs.current_confidence when 'high' then 1 when 'medium' then 2 else 3 end,
            ccs.last_seen_at desc nulls last,
            i.canonical_name asc
        limit 250
        """,
        tuple(params),
    )
    return jsonify({"contacts": [_jsonify_db_row(row) for row in rows]})

@app.route('/api/db/institutions')
def db_institutions():
    if not _db_available():
        return jsonify({"institutions": [], "error": "DATABASE_URL is not configured."}), 503
    q = f"%{(request.args.get('q') or '').strip()}%"
    country = (request.args.get("country") or "").strip().upper()
    status = (request.args.get("status") or "").strip()
    params: list = [q]
    where = ["i.canonical_name ilike %s"]
    if country:
        where.append("i.country_code = %s")
        params.append(country)
    if status:
        where.append("ics.coverage_status = %s")
        params.append(status)
    rows = _db_query(
        f"""
        select
            i.id::text as institution_id,
            i.canonical_name,
            i.country_code,
            i.institution_type,
            ics.current_homepage_url,
            ics.primary_domain,
            ics.contact_count_current,
            ics.named_contact_count_current,
            ics.high_confidence_contact_count_current,
            ics.coverage_status,
            ics.last_any_contact_at,
            ics.last_success_at
        from institutions i
        left join institution_current_state ics on ics.institution_id = i.id
        where {' and '.join(where)}
        order by i.country_code, ics.contact_count_current desc nulls last, i.canonical_name
        limit 250
        """,
        tuple(params),
    )
    return jsonify({"institutions": [_jsonify_db_row(row) for row in rows]})

@app.route('/api/db/run/<run_id>')
def db_run_detail(run_id):
    if not _db_available():
        return jsonify({"error": "DATABASE_URL is not configured."}), 503
    run = _db_one(
        """
        select id::text, run_mode::text, source_type, country_code, discovery_mode,
               status::text, started_at, finished_at, cli_args, config_snapshot, code_version, notes
        from runs
        where id = %s
        """,
        (run_id,),
    )
    if not run:
        abort(404, description="Run not found")
    targets = _db_query(
        """
        select
            rt.id::text as run_target_id,
            i.canonical_name as institution,
            i.country_code,
            rt.status::text,
            rt.homepage_url_used,
            rt.stop_reason,
            rt.hard_success,
            rt.soft_success,
            rt.failed,
            rt.pages_fetched,
            rt.llm_calls,
            rt.ranked_contacts_count,
            rt.qualified_contacts_count
        from run_targets rt
        join institutions i on i.id = rt.institution_id
        where rt.run_id = %s
        order by rt.finished_at desc nulls last, i.canonical_name
        """,
        (run_id,),
    )
    return jsonify({"run": _jsonify_db_row(run), "targets": [_jsonify_db_row(row) for row in targets]})

@app.route('/api/db/target/<run_target_id>')
def db_target_detail(run_target_id):
    if not _db_available():
        return jsonify({"error": "DATABASE_URL is not configured."}), 503
    target = _db_one(
        """
        select
            rt.id::text as run_target_id,
            rt.run_id::text,
            i.canonical_name as institution,
            i.country_code,
            rt.status::text,
            rt.homepage_url_used,
            rt.source_homepage_url,
            rt.stop_reason,
            rt.hard_success,
            rt.soft_success,
            rt.failed,
            rt.failure_reason,
            rt.pages_fetched,
            rt.llm_calls,
            rt.ranked_contacts_count,
            rt.qualified_contacts_count,
            rt.debug_trace_path
        from run_targets rt
        join institutions i on i.id = rt.institution_id
        where rt.id = %s
        """,
        (run_target_id,),
    )
    if not target:
        abort(404, description="Run target not found")
    pages = _db_query(
        """
        select
            po.id::text as page_observation_id,
            p.normalized_url,
            po.parent_url,
            po.http_status,
            po.title,
            po.source_strategy,
            po.source_stage,
            po.page_family,
            po.candidate_bucket,
            po.heuristic_score,
            po.selected_for_planning,
            po.is_useful,
            po.raw_evidence_count,
            po.clean_candidate_count,
            po.named_contact_count,
            po.office_contact_count,
            po.missing_email_count,
            po.junk_candidate_count,
            po.observed_at
        from page_observations po
        join pages p on p.id = po.page_id
        where po.run_target_id = %s
        order by po.observed_at asc, po.id asc
        limit 400
        """,
        (run_target_id,),
    )
    contacts = _db_query(
        """
        select
            co.id::text as contact_observation_id,
            co.observed_name,
            co.observed_title,
            co.observed_email,
            co.contact_kind_observed::text,
            co.confidence::text,
            co.score,
            co.priority::text,
            co.candidate_status,
            co.email_source,
            co.evidence_type,
            co.recovery_reason,
            co.classifier_reason,
            co.source_url,
            co.evidence_url,
            co.observed_at
        from contact_observations co
        where co.run_target_id = %s
        order by
            case co.confidence when 'high' then 1 when 'medium' then 2 else 3 end,
            co.score desc nulls last,
            co.observed_at desc
        """,
        (run_target_id,),
    )
    evidence = _db_query(
        """
        select
            ce.contact_observation_id::text,
            ce.evidence_kind,
            ce.snippet,
            ce.page_url,
            ce.evidence_payload,
            ce.created_at
        from contact_evidence ce
        join contact_observations co on co.id = ce.contact_observation_id
        where co.run_target_id = %s
        order by ce.created_at asc
        limit 300
        """,
        (run_target_id,),
    )
    return jsonify(
        {
            "target": _jsonify_db_row(target),
            "pages": [_jsonify_db_row(row) for row in pages],
            "contacts": [_jsonify_db_row(row) for row in contacts],
            "evidence": [_jsonify_db_row(row) for row in evidence],
        }
    )

@app.route('/api/traces/sources')
def trace_sources():
    payload = []
    for key, root in TRACE_SOURCES.items():
        payload.append(
            {
                "id": key,
                "label": "Workspace debug_logs" if key == "workspace" else "App downloads/debug",
                "exists": root.exists(),
                "trace_count": len(_trace_file_records(root)),
            }
        )
    return jsonify({"sources": payload, "default_source": "workspace"})

@app.route('/api/traces/list')
def trace_list():
    source = request.args.get("source", "workspace")
    root = _resolve_trace_root(source)
    files = _trace_file_records(root)
    directories = sorted({item["directory"] for item in files})
    return jsonify(
        {
            "source": source,
            "root": str(root),
            "directories": directories,
            "files": files,
        }
    )

@app.route('/api/traces/load')
def trace_load():
    source = request.args.get("source", "workspace")
    rel_path = request.args.get("path", "")
    root = _resolve_trace_root(source)
    rel = _safe_relative_trace_path(rel_path)
    full_path = (root / rel).resolve()
    if root.resolve() not in full_path.parents and full_path != root.resolve():
        abort(400, description="Unsafe trace path.")
    if not full_path.exists() or not full_path.is_file():
        abort(404, description="Trace file not found.")
    return jsonify(
        {
            "source": source,
            "path": rel.as_posix(),
            "full_path": str(full_path),
            "trace": json.load(full_path.open("r", encoding="utf-8")),
        }
    )

@app.route('/download/<filename>')
def download_page(filename):
    # optional: link to the matching debug CSV if present
    try:
        # extract stamp from filename pattern *_contacts_YYYYmmdd_HHMMSS.csv
        parts = filename.split("_contacts_")
        debug_link = None
        if len(parts) == 2 and parts[1].endswith(".csv"):
            ts = parts[1].replace(".csv", "")
            country = parts[0]
            training_csv = (DEBUG_ROOT / f"{country}_{ts}" / "debug_training_data.csv")
            if training_csv.exists():
                rel = training_csv.relative_to(OUTPUT_DIR).as_posix()
                debug_link = url_for('download_any', filepath=rel)
    except Exception:
        debug_link = None
    return render_template('download.html', filename=filename, debug_link=debug_link)

# serve any file under downloads/ (including nested debug/)
@app.route('/files/<path:filepath>')
def download_any(filepath):
    return send_from_directory(OUTPUT_DIR, filepath, as_attachment=True)

# back-compat if your templates still call /files/<filename>
@app.route('/files/<filename>')
def download_file(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
