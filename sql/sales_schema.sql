-- SmartStyle sales model
-- Postgres 16. Time zone for every "on" date: America/New_York.
-- (Middlebury, South Bend, Elkhart, Goshen and SW Michigan are all Eastern.)
create schema if not exists sales;
set search_path to sales, public;

create table team_member (
  id            uuid primary key default gen_random_uuid(),
  jobber_id     text unique,
  full_name     text not null,
  email         text,
  active        boolean not null default true,
  is_salesperson boolean not null default false
);

create table lead_source (
  id    uuid primary key default gen_random_uuid(),
  name  text not null unique
);

-- "Google", "google", "PPC" all map to one source.
create table lead_source_alias (
  raw_value      text primary key,
  lead_source_id uuid not null references lead_source (id)
);

create table client (
  id               uuid primary key default gen_random_uuid(),
  jobber_id        text unique,
  display_name     text not null,
  email            text,
  phone            text,
  lead_source_id   uuid references lead_source (id),
  division         text check (division in ('direct', 'contractor')),
  jobber_created_at timestamptz,
  jobber_updated_at timestamptz,
  synced_at        timestamptz not null default now(),
  deleted_at       timestamptz
);

create table quote (
  id                    uuid primary key default gen_random_uuid(),
  jobber_id             text unique,          -- null only for sheet/manual rows
  quote_number          text,
  client_id             uuid references client (id),
  salesperson_id        uuid references team_member (id),
  lead_source_id        uuid references lead_source (id),
  division              text check (division in ('direct', 'contractor')),
  title                 text,
  status                text not null check (status in (
                          'draft', 'awaiting_response', 'changes_requested',
                          'approved', 'converted', 'archived'
                        )),
  pipeline_outcome      text check (pipeline_outcome in ('won', 'lost', 'open')),
  subtotal              numeric(14,2),
  discount              numeric(14,2),
  total                 numeric(14,2),        -- current Jobber total
  total_at_first_send   numeric(14,2),        -- frozen once, never overwritten
  sent_total_estimated  boolean not null default false,
  drafted_at            timestamptz,
  first_sent_at         timestamptz,          -- cohort date; set once
  last_sent_at          timestamptz,
  changes_requested_at  timestamptz,
  approved_at           timestamptz,
  converted_at          timestamptz,
  archived_at           timestamptz,
  lost_at               timestamptz,          -- Pipeline "mark lost" only
  lost_reason           text,
  jobber_job_id         text,
  source                text not null default 'jobber'
                          check (source in ('jobber', 'sheet_import', 'manual')),
  jobber_updated_at     timestamptz,
  synced_at             timestamptz not null default now(),
  deleted_at            timestamptz,
  -- Jobber rows must carry a Jobber id; sheet/manual rows must not.
  check ((source = 'jobber') = (jobber_id is not null))
);

-- Enforce "set once": the cohort date and the as-sent dollars never move.
create function quote_freeze_first_send() returns trigger
language plpgsql as $$
begin
  if old.first_sent_at is not null then
    new.first_sent_at := old.first_sent_at;
  end if;
  if old.total_at_first_send is not null then
    new.total_at_first_send := old.total_at_first_send;
    new.sent_total_estimated := old.sent_total_estimated;
  end if;
  return new;
end $$;

create trigger quote_freeze_first_send
before update on quote
for each row execute function quote_freeze_first_send();

create table quote_status_event (
  id          uuid primary key default gen_random_uuid(),
  quote_id    uuid not null references quote (id),
  happened_at timestamptz not null,
  from_status text,
  to_status   text not null,
  source      text not null check (source in ('webhook', 'reconcile', 'manual')),
  unique (quote_id, happened_at, to_status)
);

-- One row per quote. Null column = "no override".
create table quote_override (
  quote_id        uuid primary key references quote (id),
  salesperson_id  uuid references team_member (id),
  division        text check (division in ('direct', 'contractor')),
  outcome         text check (outcome in ('won', 'open', 'lost', 'exclude')),
  reason          text not null,
  updated_by      text not null,
  updated_at      timestamptz not null default now()
);

-- team_member_id null = company goal. division null = both divisions.
create table sales_goal (
  id              uuid primary key default gen_random_uuid(),
  team_member_id  uuid references team_member (id),
  division        text check (division in ('direct', 'contractor')),
  period_start    date not null,
  period_end      date not null,
  metric          text not null check (metric in (
                    'approved_dollars', 'sent_dollars', 'won_count',
                    'sent_count', 'close_rate'
                  )),
  target          numeric(14,4) not null,
  check (period_end >= period_start)
);

create unique index sales_goal_key on sales_goal (
  coalesce(team_member_id, '00000000-0000-0000-0000-000000000000'),
  coalesce(division, ''),
  period_start,
  metric
);

-- Needed later for lead-to-quote. Not used by the close rate.
create table request (
  id                    uuid primary key default gen_random_uuid(),
  jobber_id             text unique,
  client_id             uuid references client (id),
  lead_source_id        uuid references lead_source (id),
  status                text,
  jobber_created_at     timestamptz,
  converted_to_quote_at timestamptz,
  synced_at             timestamptz not null default now(),
  deleted_at            timestamptz
);

-- Invoiced revenue. Separate from sold quotes.
create table invoice (
  id              uuid primary key default gen_random_uuid(),
  jobber_id       text unique,
  client_id       uuid references client (id),
  salesperson_id  uuid references team_member (id),
  jobber_job_id   text,
  status          text,
  total           numeric(14,2),
  issued_at       timestamptz,
  synced_at       timestamptz not null default now(),
  deleted_at      timestamptz
);

-- Last Jobber payload, off the fact table so scans stay narrow.
create table quote_payload (
  quote_id   uuid primary key references quote (id),
  payload    jsonb not null,
  fetched_at timestamptz not null default now()
);

create table sync_run (
  id            uuid primary key default gen_random_uuid(),
  mode          text not null check (mode in ('backfill', 'webhook', 'reconcile')),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  status        text not null check (status in ('running', 'success', 'failed')),
  rows_upserted integer not null default 0,
  rows_deleted  integer not null default 0,
  error         text
);

-- Dedupe key: topic + Jobber id + occurred_at.
create table sync_event (
  dedupe_key   text primary key,
  topic        text not null,
  jobber_id    text,
  occurred_at  timestamptz,
  received_at  timestamptz not null default now(),
  processed_at timestamptz,
  status       text not null check (status in ('pending', 'done', 'failed')),
  error        text
);

create index quote_sent_idx on quote (first_sent_at) where deleted_at is null;
create index quote_rep_idx on quote (salesperson_id) where deleted_at is null;
create index quote_status_idx on quote (status) where deleted_at is null;
create index quote_client_idx on quote (client_id) where deleted_at is null;
create index quote_event_idx on quote_status_event (quote_id, happened_at);

create view v_sales_quote as
select
  q.id,
  q.quote_number,
  q.jobber_id,
  q.source,
  c.display_name as client_name,
  coalesce(o.salesperson_id, q.salesperson_id) as salesperson_id,
  tm.full_name as salesperson_name,
  coalesce(o.division, q.division, c.division, 'unknown') as division,
  ls.name as lead_source,
  q.status,
  q.title,
  (q.drafted_at at time zone 'America/New_York')::date as drafted_on,
  (q.first_sent_at at time zone 'America/New_York')::date as first_sent_on,
  (q.approved_at at time zone 'America/New_York')::date as approved_on,
  (q.converted_at at time zone 'America/New_York')::date as converted_on,
  (q.lost_at at time zone 'America/New_York')::date as lost_on,
  (q.archived_at at time zone 'America/New_York')::date as archived_on,
  q.total_at_first_send as amount_quoted,
  q.total as amount_current,
  q.sent_total_estimated,
  case
    when o.outcome = 'exclude' then 'excluded'
    when o.outcome is not null then o.outcome
    -- Approved then archived is still a sale.
    when q.status in ('approved', 'converted') or q.approved_at is not null
      then 'won'
    when q.lost_at is not null or q.pipeline_outcome = 'lost' then 'lost'
    -- Sent, never approved, then archived = lost. Without this, dead quotes
    -- fall out of the denominator and the close rate looks better than it is.
    when q.status = 'archived' and q.first_sent_at is not null then 'lost'
    when q.status in ('awaiting_response', 'changes_requested') then 'open'
    when q.status = 'draft' then 'draft'
    when q.status = 'archived' then 'archived_other'  -- archived, never sent
    else 'unknown'
  end as outcome
from quote q
left join quote_override o on o.quote_id = q.id
left join client c on c.id = q.client_id
left join team_member tm
  on tm.id = coalesce(o.salesperson_id, q.salesperson_id)
left join lead_source ls on ls.id = coalesce(q.lead_source_id, c.lead_source_id)
where q.deleted_at is null;

create view v_close_rate as
select
  *,
  (outcome in ('won', 'open', 'lost') and first_sent_on is not null) as in_close_rate,
  (outcome = 'won') as is_won,
  (outcome = 'open') as is_open,
  (outcome = 'lost') as is_lost
from v_sales_quote;
