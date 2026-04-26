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
