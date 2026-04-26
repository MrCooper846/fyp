-- Postgres schema proposal for a persistent university contact intelligence system.
-- This schema keeps canonical entities separate from per-run observations and crawl memory.

create extension if not exists pgcrypto;

begin;

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create type source_system_enum as enum (
    'openalex',
    'manual',
    'crm',
    'import',
    'derived'
);

create type institution_status_enum as enum (
    'active',
    'inactive',
    'merged',
    'suppressed'
);

create type institution_domain_relationship_enum as enum (
    'primary',
    'secondary',
    'subsite',
    'affiliate',
    'redirect',
    'partner'
);

create type run_mode_enum as enum (
    'seed_country',
    'ttl_refresh',
    'failure_retry',
    'deep_retry',
    'targeted',
    'manual',
    'backfill'
);

create type run_status_enum as enum (
    'queued',
    'running',
    'completed',
    'completed_partial',
    'failed',
    'cancelled'
);

create type run_target_status_enum as enum (
    'queued',
    'running',
    'completed',
    'completed_no_contacts',
    'failed',
    'cancelled',
    'skipped'
);

create type contact_kind_enum as enum (
    'person',
    'office',
    'role_holder',
    'generic_mailbox',
    'team'
);

create type contact_point_type_enum as enum (
    'email',
    'phone',
    'url',
    'form'
);

create type confidence_enum as enum (
    'high',
    'medium',
    'low'
);

create type priority_enum as enum (
    'high',
    'medium',
    'ignore'
);

create type recrawl_status_enum as enum (
    'queued',
    'running',
    'completed',
    'failed',
    'cancelled'
);

create type recrawl_hint_type_enum as enum (
    'seed',
    'candidate',
    'exclude'
);

create type crm_sync_status_enum as enum (
    'pending',
    'success',
    'failed',
    'skipped'
);

create table institutions (
    id uuid primary key default gen_random_uuid(),
    openalex_id text null,
    source_system source_system_enum not null,
    source_key text null,
    canonical_name text not null,
    normalized_name text not null,
    institution_type text not null,
    country_code char(2) not null,
    region text null,
    city text null,
    current_status institution_status_enum not null default 'active',
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint institutions_canonical_name_nonempty check (btrim(canonical_name) <> ''),
    constraint institutions_normalized_name_nonempty check (btrim(normalized_name) <> ''),
    constraint institutions_normalized_name_lower check (normalized_name = lower(normalized_name)),
    constraint institutions_type_nonempty check (btrim(institution_type) <> ''),
    constraint institutions_country_code_upper check (country_code ~ '^[A-Z]{2}$')
);

create unique index institutions_openalex_id_uidx
    on institutions (openalex_id)
    where openalex_id is not null;

create unique index institutions_source_key_uidx
    on institutions (source_system, source_key)
    where source_key is not null;

create index institutions_country_type_idx
    on institutions (country_code, institution_type);

create index institutions_normalized_name_idx
    on institutions (normalized_name);

create table institution_aliases (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid not null references institutions(id) on delete cascade,
    alias_name text not null,
    normalized_alias_name text not null,
    alias_type text not null,
    source_system source_system_enum null,
    source_run_id uuid null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    constraint institution_aliases_alias_name_nonempty check (btrim(alias_name) <> ''),
    constraint institution_aliases_normalized_alias_name_nonempty check (btrim(normalized_alias_name) <> ''),
    constraint institution_aliases_normalized_alias_lower check (normalized_alias_name = lower(normalized_alias_name)),
    constraint institution_aliases_alias_type_nonempty check (btrim(alias_type) <> '')
);

create unique index institution_aliases_unique_alias_idx
    on institution_aliases (institution_id, normalized_alias_name, alias_type);

create index institution_aliases_normalized_idx
    on institution_aliases (normalized_alias_name);

create table domains (
    id uuid primary key default gen_random_uuid(),
    domain text not null,
    registrable_domain text not null,
    is_subdomain boolean not null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint domains_domain_nonempty check (btrim(domain) <> ''),
    constraint domains_domain_lower check (domain = lower(domain)),
    constraint domains_registrable_nonempty check (btrim(registrable_domain) <> ''),
    constraint domains_registrable_lower check (registrable_domain = lower(registrable_domain))
);

create unique index domains_domain_uidx
    on domains (domain);

create index domains_registrable_idx
    on domains (registrable_domain);

create table institution_domains (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid not null references institutions(id) on delete cascade,
    domain_id uuid not null references domains(id) on delete restrict,
    relationship_type institution_domain_relationship_enum not null,
    trust_level smallint not null,
    is_primary boolean not null default false,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint institution_domains_trust_level_range check (trust_level between 0 and 100)
);

create unique index institution_domains_unique_relationship_idx
    on institution_domains (institution_id, domain_id, relationship_type);

create unique index institution_domains_one_primary_idx
    on institution_domains (institution_id)
    where is_primary;

create index institution_domains_domain_idx
    on institution_domains (domain_id, trust_level desc);

create table runs (
    id uuid primary key default gen_random_uuid(),
    run_mode run_mode_enum not null,
    source_type text not null,
    country_code char(2) null,
    discovery_mode text null,
    status run_status_enum not null default 'queued',
    started_at timestamptz not null default now(),
    finished_at timestamptz null,
    cli_args jsonb null,
    config_snapshot jsonb null,
    code_version text null,
    notes text null,
    created_at timestamptz not null default now(),
    constraint runs_source_type_nonempty check (btrim(source_type) <> ''),
    constraint runs_country_code_upper check (country_code is null or country_code ~ '^[A-Z]{2}$'),
    constraint runs_finished_after_started check (finished_at is null or finished_at >= started_at)
);

create index runs_mode_status_idx
    on runs (run_mode, status, started_at desc);

alter table institution_aliases
    add constraint institution_aliases_source_run_fk
    foreign key (source_run_id) references runs(id) on delete set null;

create table run_targets (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null references runs(id) on delete cascade,
    institution_id uuid not null references institutions(id) on delete restrict,
    status run_target_status_enum not null default 'queued',
    started_at timestamptz null,
    finished_at timestamptz null,
    homepage_url_used text null,
    source_homepage_url text null,
    stop_reason text null,
    hard_success boolean not null default false,
    soft_success boolean not null default false,
    failed boolean not null default false,
    failure_reason text null,
    pages_fetched integer not null default 0,
    llm_calls integer not null default 0,
    ranked_contacts_count integer not null default 0,
    qualified_contacts_count integer not null default 0,
    debug_trace_path text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint run_targets_finished_after_started check (finished_at is null or started_at is null or finished_at >= started_at),
    constraint run_targets_pages_fetched_nonnegative check (pages_fetched >= 0),
    constraint run_targets_llm_calls_nonnegative check (llm_calls >= 0),
    constraint run_targets_ranked_contacts_nonnegative check (ranked_contacts_count >= 0),
    constraint run_targets_qualified_contacts_nonnegative check (qualified_contacts_count >= 0),
    constraint run_targets_single_outcome_flag check (
        (hard_success::int + soft_success::int + failed::int) <= 1
    )
);

create unique index run_targets_run_institution_uidx
    on run_targets (run_id, institution_id);

create index run_targets_institution_status_idx
    on run_targets (institution_id, status, finished_at desc);

create table pages (
    id uuid primary key default gen_random_uuid(),
    normalized_url text not null,
    raw_url text not null,
    domain_id uuid not null references domains(id) on delete restrict,
    scheme text null,
    host text not null,
    path text not null,
    query_string text null,
    page_kind text null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint pages_normalized_url_nonempty check (btrim(normalized_url) <> ''),
    constraint pages_raw_url_nonempty check (btrim(raw_url) <> ''),
    constraint pages_host_nonempty check (btrim(host) <> ''),
    constraint pages_host_lower check (host = lower(host)),
    constraint pages_path_nonempty check (btrim(path) <> '')
);

create unique index pages_normalized_url_uidx
    on pages (normalized_url);

create index pages_domain_idx
    on pages (domain_id, path);

create table institution_seed_urls (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid not null references institutions(id) on delete cascade,
    page_id uuid null references pages(id) on delete set null,
    seed_url text not null,
    seed_type text not null,
    source_run_id uuid null references runs(id) on delete set null,
    usefulness_score numeric(8, 3) not null default 0,
    times_used integer not null default 0,
    last_used_at timestamptz null,
    last_success_at timestamptz null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint institution_seed_urls_seed_url_nonempty check (btrim(seed_url) <> ''),
    constraint institution_seed_urls_seed_type_nonempty check (btrim(seed_type) <> ''),
    constraint institution_seed_urls_usefulness_range check (usefulness_score between -1000 and 1000),
    constraint institution_seed_urls_times_used_nonnegative check (times_used >= 0)
);

create unique index institution_seed_urls_unique_idx
    on institution_seed_urls (institution_id, seed_url, seed_type);

create index institution_seed_urls_active_idx
    on institution_seed_urls (institution_id, is_active, usefulness_score desc);

create table page_observations (
    id uuid primary key default gen_random_uuid(),
    page_id uuid not null references pages(id) on delete restrict,
    institution_id uuid not null references institutions(id) on delete restrict,
    run_id uuid not null references runs(id) on delete cascade,
    run_target_id uuid not null references run_targets(id) on delete cascade,
    parent_page_id uuid null references pages(id) on delete set null,
    parent_url text null,
    observed_at timestamptz not null default now(),
    http_status integer null,
    final_url text null,
    content_type text null,
    content_hash text null,
    title text null,
    source_type text null,
    source_strategy text null,
    source_stage text null,
    page_family text null,
    candidate_bucket text null,
    heuristic_score numeric(8, 3) null,
    selected_for_planning boolean not null default false,
    shell_like boolean not null default false,
    weak_llm_shell_inference boolean not null default false,
    visible_text_length integer not null default 0,
    embedded_text_length integer not null default 0,
    embedded_document_count integer not null default 0,
    raw_evidence_count integer not null default 0,
    clean_candidate_count integer not null default 0,
    named_contact_count integer not null default 0,
    office_contact_count integer not null default 0,
    missing_email_count integer not null default 0,
    junk_candidate_count integer not null default 0,
    potential_anchor_pattern_count integer not null default 0,
    is_useful boolean not null default false,
    observation_notes jsonb null,
    constraint page_observations_http_status_range check (http_status is null or http_status between 100 and 599),
    constraint page_observations_visible_text_nonnegative check (visible_text_length >= 0),
    constraint page_observations_embedded_text_nonnegative check (embedded_text_length >= 0),
    constraint page_observations_embedded_docs_nonnegative check (embedded_document_count >= 0),
    constraint page_observations_raw_evidence_nonnegative check (raw_evidence_count >= 0),
    constraint page_observations_clean_candidate_nonnegative check (clean_candidate_count >= 0),
    constraint page_observations_named_contact_nonnegative check (named_contact_count >= 0),
    constraint page_observations_office_contact_nonnegative check (office_contact_count >= 0),
    constraint page_observations_missing_email_nonnegative check (missing_email_count >= 0),
    constraint page_observations_junk_candidate_nonnegative check (junk_candidate_count >= 0),
    constraint page_observations_potential_anchor_nonnegative check (potential_anchor_pattern_count >= 0)
);

create index page_observations_run_target_idx
    on page_observations (run_target_id, observed_at desc);

create index page_observations_institution_useful_idx
    on page_observations (institution_id, is_useful, observed_at desc);

create index page_observations_page_family_idx
    on page_observations (page_family, source_strategy);

create table page_observation_acquisition_modes (
    id uuid primary key default gen_random_uuid(),
    page_observation_id uuid not null references page_observations(id) on delete cascade,
    acquisition_mode text not null,
    created_at timestamptz not null default now(),
    constraint page_observation_acquisition_modes_nonempty check (btrim(acquisition_mode) <> '')
);

create unique index page_observation_acquisition_modes_uidx
    on page_observation_acquisition_modes (page_observation_id, acquisition_mode);

create table contacts (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid not null references institutions(id) on delete cascade,
    contact_kind contact_kind_enum not null,
    canonical_name text null,
    normalized_name text null,
    identity_hash text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint contacts_identity_hash_nonempty check (btrim(identity_hash) <> ''),
    constraint contacts_name_pair_consistent check (
        (canonical_name is null and normalized_name is null)
        or (canonical_name is not null and normalized_name is not null)
    ),
    constraint contacts_normalized_name_lower check (
        normalized_name is null or normalized_name = lower(normalized_name)
    )
);

create unique index contacts_identity_uidx
    on contacts (institution_id, identity_hash);

create index contacts_institution_kind_idx
    on contacts (institution_id, contact_kind);

create index contacts_normalized_name_idx
    on contacts (normalized_name);

create table contact_points (
    id uuid primary key default gen_random_uuid(),
    contact_id uuid not null references contacts(id) on delete cascade,
    point_type contact_point_type_enum not null,
    point_value text not null,
    normalized_value text not null,
    value_hash text not null,
    is_primary boolean not null default false,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint contact_points_point_value_nonempty check (btrim(point_value) <> ''),
    constraint contact_points_normalized_nonempty check (btrim(normalized_value) <> ''),
    constraint contact_points_value_hash_nonempty check (btrim(value_hash) <> '')
);

create unique index contact_points_unique_value_idx
    on contact_points (contact_id, point_type, normalized_value);

create unique index contact_points_one_primary_per_type_idx
    on contact_points (contact_id, point_type)
    where is_primary;

create index contact_points_normalized_value_idx
    on contact_points (point_type, normalized_value);

create table contact_observations (
    id uuid primary key default gen_random_uuid(),
    contact_id uuid not null references contacts(id) on delete cascade,
    institution_id uuid not null references institutions(id) on delete restrict,
    run_id uuid not null references runs(id) on delete cascade,
    run_target_id uuid not null references run_targets(id) on delete cascade,
    page_observation_id uuid null references page_observations(id) on delete set null,
    observed_at timestamptz not null default now(),
    observed_name text null,
    observed_title text null,
    observed_email text null,
    observed_phone text null,
    contact_kind_observed contact_kind_enum not null,
    confidence confidence_enum null,
    score integer null,
    priority priority_enum null,
    candidate_status text null,
    email_source text null,
    evidence_type text null,
    recovery_reason text null,
    classifier_reason text null,
    source_url text null,
    evidence_url text null,
    observation_hash text not null,
    was_exported boolean not null default false,
    constraint contact_observations_observation_hash_nonempty check (btrim(observation_hash) <> '')
);

create unique index contact_observations_identity_uidx
    on contact_observations (contact_id, observation_hash);

create index contact_observations_contact_observed_idx
    on contact_observations (contact_id, observed_at desc);

create index contact_observations_run_target_idx
    on contact_observations (run_target_id, observed_at desc);

create index contact_observations_confidence_idx
    on contact_observations (confidence, priority, observed_at desc);

create table contact_observation_strategies (
    id uuid primary key default gen_random_uuid(),
    contact_observation_id uuid not null references contact_observations(id) on delete cascade,
    strategy_name text not null,
    created_at timestamptz not null default now(),
    constraint contact_observation_strategies_nonempty check (btrim(strategy_name) <> '')
);

create unique index contact_observation_strategies_uidx
    on contact_observation_strategies (contact_observation_id, strategy_name);

create table contact_observation_flags (
    id uuid primary key default gen_random_uuid(),
    contact_observation_id uuid not null references contact_observations(id) on delete cascade,
    flag_name text not null,
    created_at timestamptz not null default now(),
    constraint contact_observation_flags_nonempty check (btrim(flag_name) <> '')
);

create unique index contact_observation_flags_uidx
    on contact_observation_flags (contact_observation_id, flag_name);

create table contact_evidence (
    id uuid primary key default gen_random_uuid(),
    contact_observation_id uuid not null references contact_observations(id) on delete cascade,
    page_observation_id uuid null references page_observations(id) on delete set null,
    evidence_kind text not null,
    snippet text null,
    evidence_payload jsonb null,
    page_url text null,
    created_at timestamptz not null default now(),
    constraint contact_evidence_kind_nonempty check (btrim(evidence_kind) <> '')
);

create index contact_evidence_observation_idx
    on contact_evidence (contact_observation_id, evidence_kind);

create table crawl_memory_dead_urls (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid null references institutions(id) on delete cascade,
    domain_id uuid null references domains(id) on delete cascade,
    url text not null,
    http_status integer not null,
    source_component text not null,
    first_seen_run_id uuid null references runs(id) on delete set null,
    last_seen_run_id uuid null references runs(id) on delete set null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    hit_count integer not null default 1,
    ttl_expires_at timestamptz null,
    is_active boolean not null default true,
    cleared_at timestamptz null,
    constraint crawl_memory_dead_urls_url_nonempty check (btrim(url) <> ''),
    constraint crawl_memory_dead_urls_http_status_range check (http_status between 100 and 599),
    constraint crawl_memory_dead_urls_source_component_nonempty check (btrim(source_component) <> ''),
    constraint crawl_memory_dead_urls_hit_count_positive check (hit_count >= 1),
    constraint crawl_memory_dead_urls_scope_present check (
        institution_id is not null or domain_id is not null
    )
);

create unique index crawl_memory_dead_urls_url_uidx
    on crawl_memory_dead_urls (url);

create index crawl_memory_dead_urls_active_idx
    on crawl_memory_dead_urls (is_active, ttl_expires_at, last_seen_at desc);

create table crawl_memory_dead_families (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid null references institutions(id) on delete cascade,
    domain_id uuid null references domains(id) on delete cascade,
    family_signature text not null,
    page_family text null,
    source_strategy text null,
    first_seen_run_id uuid null references runs(id) on delete set null,
    last_seen_run_id uuid null references runs(id) on delete set null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    hit_count integer not null default 1,
    ttl_expires_at timestamptz null,
    is_active boolean not null default true,
    constraint crawl_memory_dead_families_signature_nonempty check (btrim(family_signature) <> ''),
    constraint crawl_memory_dead_families_hit_count_positive check (hit_count >= 1),
    constraint crawl_memory_dead_families_scope_present check (
        institution_id is not null or domain_id is not null
    )
);

create unique index crawl_memory_dead_families_institution_uidx
    on crawl_memory_dead_families (institution_id, family_signature)
    where institution_id is not null;

create unique index crawl_memory_dead_families_domain_uidx
    on crawl_memory_dead_families (domain_id, family_signature)
    where institution_id is null and domain_id is not null;

create index crawl_memory_dead_families_active_idx
    on crawl_memory_dead_families (is_active, ttl_expires_at, last_seen_at desc);

create table crawl_memory_redirects (
    id uuid primary key default gen_random_uuid(),
    source_url text not null,
    target_url text not null,
    source_domain_id uuid null references domains(id) on delete set null,
    target_domain_id uuid null references domains(id) on delete set null,
    http_status integer not null,
    first_seen_run_id uuid null references runs(id) on delete set null,
    last_seen_run_id uuid null references runs(id) on delete set null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    hit_count integer not null default 1,
    is_active boolean not null default true,
    constraint crawl_memory_redirects_source_nonempty check (btrim(source_url) <> ''),
    constraint crawl_memory_redirects_target_nonempty check (btrim(target_url) <> ''),
    constraint crawl_memory_redirects_http_status_valid check (http_status in (301, 302, 307, 308)),
    constraint crawl_memory_redirects_hit_count_positive check (hit_count >= 1)
);

create unique index crawl_memory_redirects_source_uidx
    on crawl_memory_redirects (source_url);

create index crawl_memory_redirects_target_idx
    on crawl_memory_redirects (target_url);

create table recrawl_queue (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid not null references institutions(id) on delete cascade,
    reason text not null,
    priority integer not null default 100,
    status recrawl_status_enum not null default 'queued',
    scheduled_for timestamptz not null,
    not_before timestamptz null,
    attempt_count integer not null default 0,
    last_attempted_at timestamptz null,
    enqueue_run_id uuid null references runs(id) on delete set null,
    resolved_run_target_id uuid null references run_targets(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint recrawl_queue_reason_nonempty check (btrim(reason) <> ''),
    constraint recrawl_queue_attempt_count_nonnegative check (attempt_count >= 0),
    constraint recrawl_queue_not_before_valid check (not_before is null or not_before <= scheduled_for)
);

create index recrawl_queue_status_schedule_idx
    on recrawl_queue (status, scheduled_for, priority desc);

create index recrawl_queue_institution_idx
    on recrawl_queue (institution_id, created_at desc);

create table recrawl_queue_url_hints (
    id uuid primary key default gen_random_uuid(),
    recrawl_queue_id uuid not null references recrawl_queue(id) on delete cascade,
    url text not null,
    hint_type recrawl_hint_type_enum not null,
    created_at timestamptz not null default now(),
    constraint recrawl_queue_url_hints_url_nonempty check (btrim(url) <> '')
);

create unique index recrawl_queue_url_hints_uidx
    on recrawl_queue_url_hints (recrawl_queue_id, url, hint_type);

create table crm_sync_events (
    id uuid primary key default gen_random_uuid(),
    contact_id uuid not null references contacts(id) on delete cascade,
    institution_id uuid not null references institutions(id) on delete restrict,
    external_system text not null,
    external_record_id text null,
    action text not null,
    payload_hash text not null,
    synced_at timestamptz not null default now(),
    status crm_sync_status_enum not null,
    response_payload jsonb null,
    error_message text null,
    constraint crm_sync_events_external_system_nonempty check (btrim(external_system) <> ''),
    constraint crm_sync_events_action_nonempty check (btrim(action) <> ''),
    constraint crm_sync_events_payload_hash_nonempty check (btrim(payload_hash) <> '')
);

create index crm_sync_events_contact_idx
    on crm_sync_events (contact_id, synced_at desc);

create index crm_sync_events_status_idx
    on crm_sync_events (status, synced_at desc);

create or replace view contact_current_state as
with latest_observation as (
    select distinct on (co.contact_id)
        co.contact_id,
        co.id as latest_observation_id,
        co.observed_at,
        co.observed_name,
        co.observed_title,
        co.observed_email,
        co.observed_phone,
        co.confidence,
        co.priority,
        co.candidate_status,
        co.email_source,
        co.evidence_type,
        co.recovery_reason
    from contact_observations co
    order by co.contact_id, co.observed_at desc, co.id desc
),
best_observation as (
    select distinct on (co.contact_id)
        co.contact_id,
        co.id as best_observation_id,
        co.score,
        co.confidence,
        co.priority
    from contact_observations co
    order by
        co.contact_id,
        co.score desc nulls last,
        co.observed_at desc,
        co.id desc
),
primary_email as (
    select distinct on (cp.contact_id)
        cp.contact_id,
        cp.id as primary_contact_point_id,
        cp.normalized_value as current_email
    from contact_points cp
    where cp.point_type = 'email' and cp.is_active
    order by cp.contact_id, cp.is_primary desc, cp.created_at desc, cp.id desc
),
observation_rollup as (
    select
        co.contact_id,
        count(*) as times_seen,
        max(co.observed_at) as last_seen_at
    from contact_observations co
    group by co.contact_id
)
select
    c.id as contact_id,
    c.institution_id,
    pe.primary_contact_point_id,
    lo.latest_observation_id,
    bo.best_observation_id,
    coalesce(lo.observed_name, c.canonical_name) as current_name,
    lo.observed_title as current_title,
    coalesce(lo.observed_email, pe.current_email) as current_email,
    lo.observed_phone as current_phone,
    lo.confidence as current_confidence,
    lo.priority as current_priority,
    oru.times_seen,
    oru.last_seen_at,
    oru.last_seen_at as last_verified_at,
    (oru.last_seen_at + interval '90 days') as stale_after,
    ((oru.last_seen_at + interval '90 days') < now()) as is_stale
from contacts c
left join latest_observation lo on lo.contact_id = c.id
left join best_observation bo on bo.contact_id = c.id
left join primary_email pe on pe.contact_id = c.id
left join observation_rollup oru on oru.contact_id = c.id;

create or replace view institution_current_state as
with last_run as (
    select distinct on (rt.institution_id)
        rt.institution_id,
        rt.id as last_run_target_id,
        rt.finished_at,
        rt.pages_fetched
    from run_targets rt
    order by rt.institution_id, rt.finished_at desc nulls last, rt.id desc
),
last_successful_run as (
    select distinct on (rt.institution_id)
        rt.institution_id,
        rt.id as last_successful_run_target_id,
        rt.finished_at as last_successful_finished_at
    from run_targets rt
    where rt.hard_success or rt.soft_success
    order by rt.institution_id, rt.finished_at desc nulls last, rt.id desc
),
primary_domain as (
    select distinct on (idom.institution_id)
        idom.institution_id,
        d.domain as primary_domain
    from institution_domains idom
    join domains d on d.id = idom.domain_id
    where idom.is_primary
    order by idom.institution_id, idom.updated_at desc, idom.id desc
),
contact_rollup as (
    select
        c.institution_id,
        count(*) as contact_count_current,
        count(*) filter (where c.canonical_name is not null) as named_contact_count_current,
        count(*) filter (where ccs.current_confidence = 'high') as high_confidence_contact_count_current,
        max(ccs.last_seen_at) as last_any_contact_at,
        min(ccs.stale_after) filter (where not ccs.is_stale) as next_ttl_refresh_at
    from contacts c
    left join contact_current_state ccs on ccs.contact_id = c.id
    group by c.institution_id
),
next_failure_retry as (
    select
        rq.institution_id,
        min(rq.scheduled_for) as next_failure_retry_at
    from recrawl_queue rq
    where rq.status = 'queued'
    group by rq.institution_id
)
select
    i.id as institution_id,
    isu.seed_url as current_homepage_url,
    pd.primary_domain,
    lr.last_run_target_id,
    lsr.last_successful_run_target_id,
    coalesce(cr.contact_count_current, 0) as contact_count_current,
    coalesce(cr.named_contact_count_current, 0) as named_contact_count_current,
    coalesce(cr.high_confidence_contact_count_current, 0) as high_confidence_contact_count_current,
    cr.last_any_contact_at,
    lsr.last_successful_finished_at as last_success_at,
    cr.next_ttl_refresh_at,
    nfr.next_failure_retry_at,
    case
        when coalesce(cr.contact_count_current, 0) > 0 then 'covered'
        when lsr.last_successful_run_target_id is not null then 'explored_no_contacts'
        when lr.last_run_target_id is not null then 'attempted'
        else 'unseen'
    end as coverage_status
from institutions i
left join primary_domain pd on pd.institution_id = i.id
left join last_run lr on lr.institution_id = i.id
left join last_successful_run lsr on lsr.institution_id = i.id
left join contact_rollup cr on cr.institution_id = i.id
left join next_failure_retry nfr on nfr.institution_id = i.id
left join lateral (
    select isu.seed_url
    from institution_seed_urls isu
    where isu.institution_id = i.id and isu.is_active
    order by isu.usefulness_score desc, isu.last_success_at desc nulls last, isu.created_at asc
    limit 1
) isu on true;

create trigger institutions_set_updated_at
before update on institutions
for each row execute function set_updated_at();

create trigger domains_set_updated_at
before update on domains
for each row execute function set_updated_at();

create trigger institution_domains_set_updated_at
before update on institution_domains
for each row execute function set_updated_at();

create trigger run_targets_set_updated_at
before update on run_targets
for each row execute function set_updated_at();

create trigger pages_set_updated_at
before update on pages
for each row execute function set_updated_at();

create trigger institution_seed_urls_set_updated_at
before update on institution_seed_urls
for each row execute function set_updated_at();

create trigger contacts_set_updated_at
before update on contacts
for each row execute function set_updated_at();

create trigger contact_points_set_updated_at
before update on contact_points
for each row execute function set_updated_at();

create trigger recrawl_queue_set_updated_at
before update on recrawl_queue
for each row execute function set_updated_at();

commit;
