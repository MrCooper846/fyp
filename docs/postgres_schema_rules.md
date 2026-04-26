# Postgres Schema Rules And Constraints

## Normalization Rules

- Canonical entity tables hold identity, not transient crawl outcomes.
- Observation tables append run-time facts; they should not be overwritten to represent "current truth".
- Current-state objects such as `institution_current_state` and `contact_current_state` are derived views, not source-of-truth tables.
- Multi-valued attributes are split into child tables instead of being stored in arrays or JSON when they affect filtering, analytics, or indexing.

## Identity Rules

- `institutions` is canonical per organization.
- `contacts` is canonical per institution-level identity, deduped by `identity_hash`.
- `contact_points` is canonical per contact and contact-point type, deduped by `(contact_id, point_type, normalized_value)`.
- `pages` is canonical per normalized URL.
- `contact_observations` is append-only per observed contact fact, deduped by `(contact_id, observation_hash)`.

## App-Level Hashing Rules

- `contacts.identity_hash`
  - Prefer normalized email when available.
  - Fall back to `(institution_id, normalized_name, contact_kind)` when no email exists.
  - Never hash volatile fields like confidence or page URL into canonical identity.

- `contact_points.value_hash`
  - Hash normalized contact-point values only.
  - Use consistent normalization before hashing, especially for emails and phone numbers.

- `contact_observations.observation_hash`
  - Hash the observation payload that makes a run-specific fact unique.
  - Include run-scoped evidence inputs such as observed email, observed name, evidence URL, evidence type, and page observation id where appropriate.

## Freshness Rules

- Do not delete a contact just because it is stale.
- Freshness is derived from the latest observation and exposed through `contact_current_state`.
- `stale_after` is a policy output, not canonical identity.
- Recrawl policy should enqueue stale contacts or institutions, not mutate the underlying history.

## Domain Trust Rules

- Domain trust must come from `institution_domains`, not only ad hoc string checks at export time.
- Only one primary domain may exist per institution.
- Affiliate and partner domains should be represented explicitly with lower trust.
- CRM export and frontend views should prefer primary and high-trust domains unless a contact has repeated high-confidence observations on an affiliate domain.

## Run And Observation Rules

- One institution can appear only once per run via `run_targets`.
- Deleting a run cascades through run-scoped observational data.
- Deleting an institution cascades through institution-owned canonical and memory data.
- `run_targets` should be treated as the per-institution execution ledger for a run.

## Crawl Memory Rules

- Dead exact URLs are exact-memory objects and should be unique on URL.
- Dead families are scoped memory objects and should be unique within either an institution scope or a domain scope.
- Redirect memory should store canonical redirect mappings and be reused before network fetches.
- Crawl-memory rows should expire by TTL or be explicitly cleared, not silently overwritten.

## Queue Rules

- `recrawl_queue` is an operational work queue, not a run history table.
- URL hints for recrawl are normalized into `recrawl_queue_url_hints`.
- Queue items should be idempotent at the application layer, even if duplicate rows are technically possible for historical reasons.

## Recommended Application Constraints

- Enforce normalized text generation in one place before inserts.
- Enforce URL normalization before writing to `pages`.
- Enforce domain normalization before writing to `domains`.
- Enforce observation append-only behavior in the service layer.
- Prefer soft deactivation (`is_active = false`) over delete for `contact_points`, seed URLs, and crawl-memory rows.

## Recommended Materialized Views

- `institution_current_state`
  - refresh after a run completes or after batch recovery.

- `contact_current_state`
  - refresh after contact-observation inserts or after run completion.

If refresh frequency becomes a bottleneck, replace them with incrementally maintained denormalized tables, but keep the base normalized tables unchanged.
