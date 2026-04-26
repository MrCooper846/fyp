# Scripts

Helper and analysis scripts. Run from project root, e.g. `python scripts/analyze_benchmarks.py`.

- `benchmark_nafsa_tuning.py` - compare baseline vs isolated NAFSA tuning variants
- `analyze_benchmarks.py` - aggregate benchmark results (GB vs US, etc.)
- `analyze_benchmark.py` / `analyze_contacts.py` - focused analyses
- `list_runs.py` - list benchmark runs and metadata
- `serve_dashboard.py` - serve `view_benchmark_contacts.html`
- `backfill_postgres_from_debug.py` - import historical `--debug` NAFSA folders into the Postgres schema in `db/migrations`
- `debug_filtering.py`, `root_cause_analysis.py`, `check_harvard.py` - debugging helpers
# Server Bootstrap

For a fresh Ubuntu/Debian VPS, run:

```bash
bash scripts/init_fresh_server.sh
```

Optional overrides:

```bash
GC_DB_NAME=gc_contacts GC_DB_USER=gc_contacts_app GC_DB_PASSWORD='choose-a-password' bash scripts/init_fresh_server.sh
```

The script installs Python/Postgres dependencies, creates a virtualenv, creates
the database/user, applies `db/migrations/*.sql`, and writes `.env` if one does
not already exist.
