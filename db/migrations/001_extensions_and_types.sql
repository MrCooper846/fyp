create extension if not exists pgcrypto;

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
