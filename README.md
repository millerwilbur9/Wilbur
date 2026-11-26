# SmartStyle Sales Reporting

Copies every Jobber quote into a database every hour. Applies SmartStyle's close-rate rules in one place. Sends each salesperson a daily email. Feeds a Looker Studio dashboard.

```
Jobber ──(every hour)──> Postgres database ──> Looker Studio dashboards
                                   └──(6 AM)──> daily emails to reps and owners
```

- **Setup, step by step:** [docs/SETUP.md](docs/SETUP.md)
- **The rules (what counts as won, lost, open):** [docs/RULES.md](docs/RULES.md)
- **Dashboard setup:** [docs/LOOKER_STUDIO.md](docs/LOOKER_STUDIO.md)
- **Who gets emails:** [config/report.yml](config/report.yml)

## How it stays accurate

1. **Full re-check every hour.** The sync reads every quote, not just new ones. Archived, edited and reversed quotes are always caught. Zapier can't see those changes.
2. **One row per Jobber quote.** The database won't accept duplicates.
3. **Deletes are double-checked.** If a quote disappears from Jobber's list, the sync asks Jobber about that one quote before marking it deleted. If a lot of quotes vanish at once, it marks nothing deleted and fails loudly instead.
4. **Failures email you.** A failed sync emails the repo owner through GitHub. The daily report shows a red warning if data is more than 3 hours old.
5. **The first time a customer saw the price is locked in.** A resend or a price change doesn't move a quote into a different month.

## Commands (for whoever maintains this)

```
python -m salesync migrate        # create/update tables
python -m salesync check          # read 3 quotes to prove Jobber works
python -m salesync sync           # full sync
python -m salesync report --dry-run out/   # write emails to files instead of sending
python -m pytest -q               # tests (needs TEST_DATABASE_URL)
```
