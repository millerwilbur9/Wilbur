-- Smoke test. Run against an empty database:
--   psql -v ON_ERROR_STOP=1 -f sql/sales_schema.sql -f sql/test_sales_schema.sql
set search_path to sales, public;
begin;

insert into team_member (id, full_name, is_salesperson)
values ('11111111-1111-1111-1111-111111111111', 'Rep A', true);

insert into quote (jobber_id, quote_number, salesperson_id, status, first_sent_at,
                   total_at_first_send, approved_at, lost_at)
values
  ('j1', 'Q1', '11111111-1111-1111-1111-111111111111', 'approved',          '2026-11-02 15:00Z', 6575, '2026-11-02 16:00Z', null),
  ('j2', 'Q2', '11111111-1111-1111-1111-111111111111', 'archived',          '2026-11-03 15:00Z', 4200, null, null),
  ('j3', 'Q3', '11111111-1111-1111-1111-111111111111', 'archived',          '2026-11-04 15:00Z', 3100, '2026-11-04 18:00Z', null),
  ('j4', 'Q4', '11111111-1111-1111-1111-111111111111', 'awaiting_response', '2026-11-05 15:00Z', 5000, null, null),
  ('j5', 'Q5', '11111111-1111-1111-1111-111111111111', 'archived',          null,               null, null, null),
  -- 11:30 PM Eastern on Nov 6 = 04:30Z Nov 7. Must land on Nov 6.
  ('j6', 'Q6', '11111111-1111-1111-1111-111111111111', 'awaiting_response', '2026-11-07 04:30Z', 2000, null, null);

do $$
declare r record;
begin
  for r in select quote_number, outcome, first_sent_on from sales.v_close_rate loop
    if (r.quote_number, r.outcome) not in (
         ('Q1','won'), ('Q2','lost'), ('Q3','won'), ('Q4','open'),
         ('Q5','archived_other'), ('Q6','open')) then
      raise exception 'bad outcome % for %', r.outcome, r.quote_number;
    end if;
  end loop;

  if (select first_sent_on from sales.v_close_rate where quote_number = 'Q6')
       <> date '2026-11-06' then
    raise exception 'time zone bucketing is wrong';
  end if;

  -- Freeze: a resend must not move the cohort date or as-sent total.
  update sales.quote set first_sent_at = now(), total_at_first_send = 9999
  where quote_number = 'Q1';
  if (select total_at_first_send from sales.quote where quote_number = 'Q1') <> 6575 then
    raise exception 'total_at_first_send was overwritten';
  end if;

  -- Jobber row without a Jobber id must be rejected.
  begin
    insert into sales.quote (status, source) values ('draft', 'jobber');
    raise exception 'jobber row without jobber_id was allowed';
  exception when check_violation then null;
  end;

  raise notice 'all checks passed';
end $$;

-- Close rate for the test data: 2 won / (2 won + 1 lost + 2 open) = 40%.
select count(*) filter (where is_won)::numeric
       / nullif(count(*) filter (where in_close_rate), 0) as close_rate
from v_close_rate;

rollback;
