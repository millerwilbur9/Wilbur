# The rules

These are written into the database (`migrations/001_init.sql`, view `v_quote`). Every dashboard and email uses them, so every report gives the same number.

| A quote is… | When |
|---|---|
| **Won** | Status is Approved or Converted. Or it was approved, then archived. |
| **Open** | Awaiting response or Changes requested. This includes a quote that was approved and then re-opened. |
| **Lost** | Archived without ever being approved. |
| **Not counted** | Drafts, $0 quotes, deleted quotes, anything excluded by hand. |

**Presented date** = the first time the customer saw the price. That's the day it was sent, or the day it was approved at the table if it was never sent. It's locked once set.

**Close rate** = Won ÷ (Won + Open + Lost), for quotes presented in the period.

**Sold date** = the day it was approved. Sales dollars count on this date.

**Division**, checked in this order:
1. A hand correction (`quote_override.division`)
2. The Jobber client custom field **Division** (anything with "contract" = Contractor; "direct", "residential" or "homeowner" = Direct)
3. The client has a **Contractor** tag, or a lead source starting with "Contractor" = Contractor
4. Otherwise Direct

**Lead source** = the Jobber client's lead source. To merge messy names, add rows to `lead_source_alias` (for example `google` → `Google LSA`).

## Fixing a single quote by hand

Supabase → Table Editor → `quote_override` → Insert row:
- `quote_id`: copy from `quote` table
- `outcome`: `won`, `lost`, `open` or `exclude`
- `salesperson_id` / `division`: fill in only to change them
- `reason` and `updated_by`: required, so there's always a record of who changed what and why

## Adding a deal that isn't in Jobber

Table Editor → `quote` → Insert row: `source` = `manual`, `quote_number` (like `M-1`), `status`, `total`, `first_presented_at`, `approved_at` (if won), `manual_client_name`, `manual_lead_source`, `manual_division`.

## Goals

Table Editor → `sales_goal` → Insert row. Leave `team_member_id` empty for a company goal. Leave `division` empty for both divisions.
Metrics: `sold_dollars`, `sold_count`, `presented_count`, `close_rate` (enter 0.40 for 40%).
