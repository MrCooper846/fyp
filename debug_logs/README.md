# Seed Debug Traces

These folders are intentionally committed as a compact seed set for the Postgres
backfill/import workflow in `scripts/backfill_postgres_from_debug.py`.

The full historical `debug_logs/` archive is much larger, so `.gitignore` only
allows these benchmark and regression-oriented subsets:

- `legacy_it_generated`
- `legacy_it_heuristic`
- `legacy_it_real`
- `nafsa_it_hybrid_v2`
- `nafsa_FR_failure_subset`
- `nafsa_GB_extraction_patch2`

Example dry run:

```powershell
python scripts/backfill_postgres_from_debug.py debug_logs/nafsa_it_hybrid_v2 --dry-run
```

Example import:

```powershell
$env:DATABASE_URL="<your-postgres-dsn>"
python scripts/backfill_postgres_from_debug.py debug_logs/nafsa_it_hybrid_v2 debug_logs/nafsa_FR_failure_subset
```

If a larger historical seed is needed, copy the untracked trace folders to the
server separately or add another explicit allow-rule here rather than unignoring
all of `debug_logs/`.
