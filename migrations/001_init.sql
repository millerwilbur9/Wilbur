-- SmartStyle sales reporting database.
-- Every "_on" date is a calendar day in America/New_York (Middlebury / South Bend time).
create schema if not exists sales;
set search_path to sales, public;

-- Jobber OAuth tokens. Jobber rotates the refresh token, so the newest one is
-- always saved here. Exactly one row.
create table oauth_token (
  id            integer primary key default 1 check (id = 1),
  access_token  text not null,
  refresh_token text not null,
  expires_at    timestamptz,
  updated_at    timestamptz not null default now()
);

create table team_member (
  id             uuid primary key default gen_random_uuid(),
  jobber_id      text unique,
  full_name      text not null,
  active         boolean not null default true,
  is_salesperson boolean not null default true
);

create table client (
  id                uuid primary key default gen_random_uuid(),
  jobber_id         text unique not null,
  display_name      text not null,
  is_company        boolean,
  lead_source       text,              -- raw Jobber lead source
  tags              text[] not null default '{}',
  division_field    text,              -- Jobber custom field "Division"
  phone             text,
  jobber_created_at timestamptz,
  synced_at         timestamptz not null default now()
);

-- Optional clean-up of messy lead sources: 'google' and 'Google LSA' -> 'Google LSA'.
create table lead_source_alias (
  raw_value   text primary key check (raw_value = lower(trim(raw_value))),
  lead_source text not null
);

create table quote (
  id                    uuid primary key default gen_random_uuid(),
  jobber_id             text unique,
  source                text not null default 'jobber' check (source in ('jobber', 'manual')),
  quote_number          text,
  client_id             uuid references client (id),
  salesperson_id        uuid references team_member (id),
  title                 text,
  status                text not null check (status in (
                          'draft', 'awaiting_response', 'changes_requested',
                          'approved', 'converted', 'archived')),
  subtotal              numeric(14,2),
  discount              numeric(14,2),
  total                 numeric(14,2),   -- current Jobber total
  jobber_created_at     timestamptz,
  sent_at               timestamptz,
  approved_at           timestamptz,
  changes_requested_at  timestamptz,
  converted_at          timestamptz,
  jobber_updated_at     timestamptz,
  -- First time the customer saw the price: sent, or approved at the table.
  -- Set once by the sync, never moved. This is the close-rate date.
  first_presented_at    timestamptz,
  amount_presented      numeric(14,2),   -- total at first presentation, set once
  jobber_web_uri        text,
  -- Only for manual rows (deals that are not in Jobber).
  manual_client_name    text,
  manual_lead_source    text,
  manual_division       text check (manual_division in ('direct', 'contractor')),
  manual_note           text,
  synced_at             timestamptz not null default now(),
  deleted_at            timestamptz,
  check ((source = 'jobber') = (jobber_id is not null)),
  check (source = 'jobber' or manual_client_name is not null)
);

create index quote_presented_idx on quote (first_presented_at) where deleted_at is null;
create index quote_rep_idx on quote (salesperson_id) where deleted_at is null;
create index quote_client_idx on quote (client_id) where deleted_at is null;

create table quote_status_event (
  id          bigserial primary key,
  quote_id    uuid not null references quote (id),
  happened_at timestamptz not null,
  from_status text,
  to_status   text not null,
  unique (quote_id, happened_at, to_status)
);

-- Hand corrections. One row per quote; a null column means "no change".
create table quote_override (
  quote_id       uuid primary key references quote (id),
  salesperson_id uuid references team_member (id),
  division       text check (division in ('direct', 'contractor')),
  outcome        text check (outcome in ('won', 'open', 'lost', 'exclude')),
  reason         text not null,
  updated_by     text not null,
  updated_at     timestamptz not null default now()
);

-- team_member_id null = company goal. division null = both divisions.
create table sales_goal (
  id             uuid primary key default gen_random_uuid(),
  team_member_id uuid references team_member (id),
  division       text check (division in ('direct', 'contractor')),
  period_start   date not null,
  period_end     date not null,
  metric         text not null check (metric in (
                   'sold_dollars', 'sold_count', 'presented_count', 'close_rate')),
  target         numeric(14,4) not null,
  check (period_end >= period_start)
);

create unique index sales_goal_key on sales_goal (
  coalesce(team_member_id, '00000000-0000-0000-0000-000000000000'),
  coalesce(division, ''), period_start, metric);

create table sync_run (
  id            bigserial primary key,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  status        text not null check (status in ('running', 'success', 'failed')),
  quotes_seen   integer not null default 0,
  quotes_deleted integer not null default 0,
  error         text
);

-- ---------------------------------------------------------------- views

-- One row per quote, with every rule applied. Dashboards read this.
create view v_quote as
with base as (
  select q.*, c.display_name, c.lead_source as client_lead_source, c.tags,
         c.division_field, c.phone,
         o.outcome as o_outcome, o.salesperson_id as o_salesperson,
         o.division as o_division, o.reason as override_reason,
         coalesce(c.lead_source, q.manual_lead_source) as raw_lead_source
  from quote q
  left join client c on c.id = q.client_id
  left join quote_override o on o.quote_id = q.id
  where q.deleted_at is null
), shaped as (
  select b.*,
    case
      when b.o_outcome = 'exclude' then 'excluded'
      when b.o_outcome is not null then b.o_outcome
      when b.status = 'draft' then 'draft'
      when coalesce(b.total, 0) <= 0 then 'excluded'
      when b.status in ('approved', 'converted') then 'won'
      -- Approved, then archived: still a sale.
      when b.status = 'archived' and b.approved_at is not null then 'won'
      -- Presented, never approved, archived: lost.
      when b.status = 'archived' then 'lost'
      -- Includes quotes approved once and then re-opened (approval undone).
      when b.status in ('awaiting_response', 'changes_requested') then 'open'
      else 'unknown'
    end as outcome
  from base b
)
select
  s.id                                         as quote_id,
  s.quote_number,
  s.source,
  coalesce(s.display_name, s.manual_client_name) as client_name,
  s.phone                                      as client_phone,
  coalesce(s.o_salesperson, s.salesperson_id)  as salesperson_id,
  coalesce(tm.full_name, 'Unassigned')         as salesperson,
  coalesce(
    s.o_division,
    s.manual_division,
    case when s.division_field ilike '%contract%' then 'contractor'
         when s.division_field ilike any (array['%direct%', '%resid%', '%home%']) then 'direct' end,
    case when exists (select 1 from unnest(s.tags) t where t ilike 'contractor%')
           or s.client_lead_source ilike 'contractor%' then 'contractor' end,
    'direct')                                  as division,
  coalesce(a.lead_source, nullif(trim(s.raw_lead_source), ''), 'Unknown') as lead_source,
  s.status,
  s.title,
  s.outcome,
  s.override_reason,
  coalesce(s.amount_presented, s.total)        as amount_quoted,
  s.total                                      as amount_current,
  s.first_presented_at,
  (s.first_presented_at at time zone 'America/New_York')::date as presented_on,
  (s.jobber_created_at at time zone 'America/New_York')::date as created_on,
  -- Day the sale happened. Falls back to presented date for won rows with no stamp.
  case when s.outcome = 'won' then
    (coalesce(s.approved_at, s.converted_at, s.first_presented_at) at time zone 'America/New_York')::date
  end                                          as sold_on,
  (s.outcome in ('won', 'open', 'lost') and s.first_presented_at is not null) as in_close_rate,
  (s.outcome = 'won')::int                     as is_won,
  (s.outcome = 'open')::int                    as is_open,
  (s.outcome = 'lost')::int                    as is_lost,
  ((s.outcome in ('won', 'open', 'lost') and s.first_presented_at is not null))::int as counts_in_close_rate,
  case when s.outcome = 'open' then
    (now() at time zone 'America/New_York')::date - (s.first_presented_at at time zone 'America/New_York')::date
  end                                          as days_open,
  s.jobber_web_uri
from shaped s
left join team_member tm on tm.id = coalesce(s.o_salesperson, s.salesperson_id)
left join lead_source_alias a on a.raw_value = lower(trim(s.raw_lead_source));

-- Follow-up list: every open quote, oldest first.
create view v_open_quotes as
select quote_id, quote_number, client_name, client_phone, salesperson, division,
       lead_source, amount_current, presented_on, days_open, jobber_web_uri
from v_quote
where outcome = 'open' and presented_on is not null
order by days_open desc nulls last;

-- Goals vs actual. Sold metrics count by sold date; close rate and presented
-- count use the presented date.
create view v_goal_progress as
select
  g.id as goal_id,
  coalesce(tm.full_name, 'Company') as who,
  coalesce(g.division, 'all') as division,
  g.metric, g.period_start, g.period_end, g.target,
  case g.metric
    when 'sold_dollars' then (select coalesce(sum(v.amount_current), 0) from v_quote v
      where v.outcome = 'won' and v.sold_on between g.period_start and g.period_end
        and (g.team_member_id is null or v.salesperson_id = g.team_member_id)
        and (g.division is null or v.division = g.division))
    when 'sold_count' then (select count(*) from v_quote v
      where v.outcome = 'won' and v.sold_on between g.period_start and g.period_end
        and (g.team_member_id is null or v.salesperson_id = g.team_member_id)
        and (g.division is null or v.division = g.division))
    when 'presented_count' then (select count(*) from v_quote v
      where v.in_close_rate and v.presented_on between g.period_start and g.period_end
        and (g.team_member_id is null or v.salesperson_id = g.team_member_id)
        and (g.division is null or v.division = g.division))
    when 'close_rate' then (select round(sum(v.is_won)::numeric / nullif(count(*), 0), 4) from v_quote v
      where v.in_close_rate and v.presented_on between g.period_start and g.period_end
        and (g.team_member_id is null or v.salesperson_id = g.team_member_id)
        and (g.division is null or v.division = g.division))
  end as actual
from sales_goal g
left join team_member tm on tm.id = g.team_member_id;

create view v_sync_health as
select
  (select max(finished_at) from sync_run where status = 'success') as last_success_at,
  (select status from sync_run order by id desc limit 1) as last_status,
  (select error from sync_run order by id desc limit 1) as last_error;
