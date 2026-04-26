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

create table institution_aliases (
    id uuid primary key default gen_random_uuid(),
    institution_id uuid not null references institutions(id) on delete cascade,
    alias_name text not null,
    normalized_alias_name text not null,
    alias_type text not null,
    source_system source_system_enum null,
    source_run_id uuid null references runs(id) on delete set null,
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
