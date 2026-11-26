"""Daily sales emails: one per salesperson, one company summary for the owners."""
import html
import os
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "report.yml"

METRICS_SQL = """
select
  count(*) filter (where in_close_rate and presented_on between %(s)s and %(e)s) as presented,
  coalesce(sum(is_won) filter (where in_close_rate and presented_on between %(s)s and %(e)s), 0) as won,
  coalesce(sum(is_open) filter (where in_close_rate and presented_on between %(s)s and %(e)s), 0) as open,
  coalesce(sum(is_lost) filter (where in_close_rate and presented_on between %(s)s and %(e)s), 0) as lost,
  avg(amount_quoted) filter (where in_close_rate and presented_on between %(s)s and %(e)s) as avg_quote,
  count(*) filter (where outcome = 'won' and sold_on between %(s)s and %(e)s) as sold_count,
  coalesce(sum(amount_current) filter (where outcome = 'won' and sold_on between %(s)s and %(e)s), 0) as sold_dollars
from v_quote
where (%(rep)s::uuid is null or salesperson_id = %(rep)s::uuid)
"""


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def metrics(conn, start: date, end: date, rep_id=None) -> dict:
    cur = conn.execute(METRICS_SQL, {"s": start, "e": end, "rep": rep_id})
    row = dict(zip([d.name for d in cur.description], cur.fetchone()))
    row["close_rate"] = (row["won"] / row["presented"]) if row["presented"] else None
    row["avg_sale"] = (row["sold_dollars"] / row["sold_count"]) if row["sold_count"] else None
    return row


def periods(today: date) -> list[tuple[str, date, date]]:
    y = today - timedelta(days=1)
    return [
        ("Yesterday", y, y),
        ("Month to date", today.replace(day=1), today),
        ("Year to date", today.replace(month=1, day=1), today),
        ("Same period last year", date(today.year - 1, 1, 1), _year_ago(today)),
    ]


def _year_ago(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year - 1, day=28)


def money(x) -> str:
    return "—" if x is None else f"${x:,.0f}"


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _table(headers, rows) -> str:
    th = "".join(f'<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #0b1220;'
                 f'font-size:13px">{html.escape(h)}</th>' for h in headers)
    body = ""
    for r in rows:
        body += "<tr>" + "".join(
            f'<td style="padding:6px 10px;border-bottom:1px solid #d9e1ec;font-size:14px">'
            f'{c}</td>' for c in r) + "</tr>"
    return (f'<table style="border-collapse:collapse;width:100%;margin:8px 0 20px">'
            f"<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")


def _metric_rows(conn, today, rep_id):
    rows = []
    for label, s, e in periods(today):
        m = metrics(conn, s, e, rep_id)
        rows.append([label, m["presented"], m["won"], pct(m["close_rate"]),
                     m["sold_count"], money(m["sold_dollars"]), money(m["avg_sale"])])
    return rows


METRIC_HEADERS = ["Period", "Quotes presented", "Won", "Close rate", "Sold (#)", "Sold ($)", "Avg sale"]


def _goal_rows(conn, today, rep_id):
    cur = conn.execute(
        """select g.metric, g.target, p.actual from sales_goal g
           join v_goal_progress p on p.goal_id = g.id
           where %s between g.period_start and g.period_end
             and ((%s::uuid is null and g.team_member_id is null) or g.team_member_id = %s::uuid)
           order by g.metric""", (today, rep_id, rep_id))
    out = []
    for metric, target, actual in cur:
        fmt = pct if metric == "close_rate" else (money if metric == "sold_dollars" else str)
        hit = actual is not None and actual >= target
        out.append([metric.replace("_", " ").title(), fmt(target), fmt(actual),
                    "✅ On track" if hit else "Behind"])
    return out


def _open_rows(conn, rep_name, min_days, limit=15):
    cur = conn.execute(
        """select quote_number, client_name, client_phone, amount_current, days_open, jobber_web_uri
           from v_open_quotes where (%s::text is null or salesperson = %s) and days_open >= %s
           order by days_open desc limit %s""", (rep_name, rep_name, min_days, limit))
    rows = []
    for num, client, phone, amt, days, uri in cur:
        link = f'<a href="{html.escape(uri)}">#{html.escape(str(num))}</a>' if uri else f"#{num}"
        rows.append([link, html.escape(client or ""), html.escape(phone or ""), money(amt), days])
    return rows


def _health_banner(conn) -> str:
    row = conn.execute(
        "select last_success_at, now() - last_success_at > interval '3 hours' from v_sync_health"
    ).fetchone()
    if row[0] is None or row[1]:
        return ('<p style="background:#fde8e6;color:#b42318;padding:10px;font-weight:600">'
                "Warning: the Jobber sync has not succeeded in over 3 hours. "
                "These numbers may be out of date.</p>")
    return ""


def _wrap(title, inner) -> str:
    return (f'<div style="font-family:Arial,Helvetica,sans-serif;color:#0b1220;max-width:720px">'
            f'<h2 style="margin:0 0 4px">{html.escape(title)}</h2>{inner}'
            f'<p style="color:#4a5568;font-size:12px">Close rate = won ÷ quotes presented in the '
            f"period (won + still open + lost). Presented = the day the customer first saw "
            f"the price. Sold = counted on the day it was approved.</p></div>")


def rep_email(conn, today, rep_id, rep_name, min_days) -> str:
    inner = _health_banner(conn)
    inner += _table(METRIC_HEADERS, _metric_rows(conn, today, rep_id))
    goals = _goal_rows(conn, today, rep_id)
    if goals:
        inner += "<h3>Goals</h3>" + _table(["Goal", "Target", "Actual", ""], goals)
    opens = _open_rows(conn, rep_name, min_days)
    inner += f"<h3>Open quotes {min_days}+ days old — follow up today</h3>"
    inner += _table(["Quote", "Client", "Phone", "Amount", "Days open"], opens) if opens \
        else "<p>None. Nice work.</p>"
    return _wrap(f"{rep_name} — sales for {today:%A, %B %-d}", inner)


def company_email(conn, today, min_days) -> str:
    inner = _health_banner(conn)
    inner += "<h3>Company</h3>" + _table(METRIC_HEADERS, _metric_rows(conn, today, None))
    month = today.replace(day=1)
    cur = conn.execute(
        """select salesperson,
                  count(*) filter (where in_close_rate and presented_on >= %(m)s) as presented,
                  coalesce(sum(is_won) filter (where in_close_rate and presented_on >= %(m)s), 0) as won,
                  coalesce(sum(amount_current) filter (where outcome = 'won' and sold_on >= %(m)s), 0) as sold
           from v_quote group by salesperson
           having count(*) filter (where in_close_rate and presented_on >= %(m)s) > 0
               or coalesce(sum(amount_current) filter (where outcome='won' and sold_on >= %(m)s),0) > 0
           order by sold desc""", {"m": month})
    board = [[html.escape(n), p, w, pct(w / p if p else None), money(s)] for n, p, w, s in cur]
    inner += "<h3>Leaderboard — month to date</h3>" + _table(
        ["Salesperson", "Presented", "Won", "Close rate", "Sold ($)"], board)
    cur = conn.execute(
        """select division,
                  count(*) filter (where in_close_rate and presented_on >= %(m)s),
                  coalesce(sum(is_won) filter (where in_close_rate and presented_on >= %(m)s), 0),
                  coalesce(sum(amount_current) filter (where outcome = 'won' and sold_on >= %(m)s), 0)
           from v_quote group by division order by division""", {"m": month})
    div = [[d.title(), p, w, pct(w / p if p else None), money(s)] for d, p, w, s in cur]
    inner += "<h3>Direct vs Contractor — month to date</h3>" + _table(
        ["Division", "Presented", "Won", "Close rate", "Sold ($)"], div)
    goals = _goal_rows(conn, today, None)
    if goals:
        inner += "<h3>Company goals</h3>" + _table(["Goal", "Target", "Actual", ""], goals)
    opens = _open_rows(conn, None, min_days, limit=25)
    inner += f"<h3>Oldest open quotes ({min_days}+ days)</h3>"
    inner += _table(["Quote", "Client", "Phone", "Amount", "Days open"], opens) if opens \
        else "<p>None.</p>"
    return _wrap(f"SmartStyle sales — {today:%A, %B %-d}", inner)


def build_all(conn, today: date, cfg: dict) -> list[tuple[list[str], str, str]]:
    """Returns (recipients, subject, html) for every email to send."""
    min_days = int(cfg.get("follow_up_after_days", 7))
    out = []
    owners = [e for e in (cfg.get("owners") or []) if e]
    if owners:
        out.append((owners, f"SmartStyle sales — {today:%b %-d}", company_email(conn, today, min_days)))
    reps = cfg.get("salespeople") or {}
    for name, email in reps.items():
        if not email:
            continue
        row = conn.execute("select id from team_member where full_name = %s and active",
                           (name,)).fetchone()
        if not row:
            out.append((owners or [email], f"Report setup problem: {name}",
                        _wrap("Report setup problem",
                              f"<p>No active Jobber salesperson named {html.escape(name)}. "
                              "Check the spelling in config/report.yml.</p>")))
            continue
        out.append(([email], f"Your sales — {today:%b %-d}",
                    rep_email(conn, today, row[0], name, min_days)))
    return out


def send(messages, dry_run_dir: str | None = None, log=print) -> None:
    if dry_run_dir:
        d = Path(dry_run_dir)
        d.mkdir(parents=True, exist_ok=True)
        for i, (to, subject, body) in enumerate(messages, 1):
            (d / f"{i:02d}.html").write_text(f"<!-- To: {', '.join(to)} | {subject} -->\n{body}")
        log(f"Wrote {len(messages)} emails to {d}")
        return
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user, password = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("REPORT_FROM", user)
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.starttls()
        s.login(user, password)
        for to, subject, body in messages:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject
            msg.set_content("This report needs an email app that shows HTML.")
            msg.add_alternative(body, subtype="html")
            s.send_message(msg)
    log(f"Sent {len(messages)} emails.")
