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

## Notes

- This folder is the migration-friendly version of the schema in `docs/postgres_schema.sql`.
- These files do not include seed data or backfill logic yet.
- In practice, run them through a migration tool or execute them in order inside transactions.
