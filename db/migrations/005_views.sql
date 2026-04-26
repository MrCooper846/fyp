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
