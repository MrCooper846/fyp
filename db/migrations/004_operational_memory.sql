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
