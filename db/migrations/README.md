# Postgres Migrations

Apply these files in lexical order:

1. `001_extensions_and_types.sql`
2. `002_core_entities.sql`
3. `003_observations.sql`
4. `004_operational_memory.sql`
5. `005_views.sql`
6. `006_triggers.sql`

## Design Intent

- The schema is normalized at the base-table level.
- Current-state projections are defined as derived views.
- The schema is intentionally compatible with later historical backfill.

That means you can load old runs into:

- `runs`
- `run_targets`
- `page_observations`
- `contacts`
- `contact_points`
- `contact_observations`
- `contact_evidence`
- crawl-memory tables

without changing the schema shape. The `run_mode_enum` includes `backfill` for exactly that reason.

## Live Dual-Write

The NAFSA pipeline can write to Postgres in parallel with the existing CSV, JSON,
and debug trace outputs. Enable it by setting both environment variables before a
run:

```powershell
$env:DATABASE_URL="<your-postgres-dsn>"
$env:POSTGRES_DUAL_WRITE="1"
```

Postgres write failures are logged and do not stop the crawl, so database
contents can be validated against the existing artifacts before making the
database the primary output.

## Notes

- This folder is the migration-friendly version of the schema in `docs/postgres_schema.sql`.
- Historical seed/debug traces are imported with `scripts/backfill_postgres_from_debug.py`.
- In practice, run them through a migration tool or execute them in order inside transactions.
