# Dashboard in Looker Studio

## 1. Make a read-only login for the dashboard

In Supabase → **SQL Editor** → paste this, change the password, click **Run**:

```sql
create role looker_reader with login password 'CHANGE-ME-TO-A-LONG-PASSWORD';
grant usage on schema sales to looker_reader;
grant select on sales.v_quote, sales.v_open_quotes, sales.v_goal_progress, sales.v_sync_health
  to looker_reader;
```

The dashboard can only read the finished numbers. It can't change anything.

## 2. Connect Looker Studio

1. Go to **lookerstudio.google.com** → **Create** → **Data source** → **PostgreSQL**.
2. Fill in from Supabase → **Connect** → **Session pooler**:
   - Host: like `aws-0-us-east-1.pooler.supabase.com`
   - Port: `5432`
   - Database: `postgres`
   - Username: `looker_reader.` followed by your project ID (the part after `postgres.` in your connection string), for example `looker_reader.abcxyz`
   - Password: from step 1
   - Check **Enable SSL**
3. Choose table **v_quote** → **Connect**.
4. Click **Add a field** for each of these:

| Field name | Formula |
|---|---|
| Close rate | `SUM(is_won) / SUM(counts_in_close_rate)` (set type to Percent) |
| Quotes presented | `SUM(counts_in_close_rate)` |
| Sold $ | `SUM(CASE WHEN outcome = "won" THEN amount_current ELSE 0 END)` |
| Avg sale | `SUM(CASE WHEN outcome = "won" THEN amount_current ELSE 0 END) / SUM(is_won)` |
| Avg quote | `SUM(CASE WHEN counts_in_close_rate = 1 THEN amount_quoted ELSE 0 END) / SUM(counts_in_close_rate)` |

5. Make **v_open_quotes** and **v_goal_progress** data sources the same way.

## 3. Build the pages

**Important:** for close rate, use **presented_on** as the date. For sales dollars, use **sold_on**. That's how the numbers match the daily email.

| Page | Charts |
|---|---|
| Company | Scorecards: Close rate, Quotes presented, Sold $, Avg sale. Date control. Time series of Sold $ by month. Table by division. |
| Leaderboard | Table: salesperson, Quotes presented, Close rate, Sold $, sorted by Sold $. |
| Lead sources | Table: lead_source, Quotes presented, Close rate, Sold $. |
| Year over year | Time series with **Comparison date range → Previous year** turned on. |
| Follow-up | Table from v_open_quotes: client, phone, salesperson, amount, days open. |
| Goals | Table from v_goal_progress: who, metric, target, actual. |

Add **filter controls** for salesperson, division and lead source. They work on every page.

## 4. One dashboard per rep

Share the report with the rep. Then **File → Report settings → Filter** on salesperson, or make a copy of the report filtered to that one rep. On a phone, it opens in the Looker Studio app or the browser.
