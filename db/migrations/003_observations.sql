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
