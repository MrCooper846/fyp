from flask import Flask, render_template, request, send_from_directory, redirect, url_for, abort, jsonify
import json, os, sys, asyncio, subprocess
from datetime import datetime
from pathlib import Path

# Try to import the refactored crawler module
try:
    from gc_contacts.main import run_all as crawler_run_all  # async coroutine
except Exception:
    crawler_run_all = None

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
