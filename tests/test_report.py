from datetime import date

from salesync.report import build_all, metrics
from salesync.sync import run_sync
from tests.test_sync import CASES, FakeJobber

CFG = {"owners": ["owner@example.com"], "follow_up_after_days": 7,
       "salespeople": {"Jackson Price": "jackson@example.com", "Nobody Here": "x@example.com"}}


def test_month_metrics(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    m = metrics(conn, date(2026, 11, 1), date(2026, 11, 30))
    # presented in Nov: 2031 won, 1990 won, 2029 open, 1992 lost, 2001 open
    assert (m["presented"], m["won"], m["open"], m["lost"]) == (5, 2, 2, 1)
    assert m["close_rate"] == 0.4
    assert m["sold_count"] == 2 and float(m["sold_dollars"]) == 9313.0


def test_emails_build(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    msgs = build_all(conn, date(2026, 11, 26), CFG)
    subjects = [m[1] for m in msgs]
    assert subjects[0].startswith("SmartStyle sales")
    assert "Your sales — Nov 26" in subjects
    assert "Report setup problem: Nobody Here" in subjects
    company = msgs[0][2]
    assert "Leaderboard" in company and "Contractor" in company
    assert "has not succeeded" not in company   # sync just ran
    jackson = [m for m in msgs if m[0] == ["jackson@example.com"]][0][2]
    assert "#1867" in jackson                   # open 70+ days, on the follow-up list
    assert "#2029" not in jackson               # only 2 days old


def test_stale_sync_warning(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    conn.execute("update sync_run set finished_at = now() - interval '5 hours'")
    msgs = build_all(conn, date(2026, 11, 26), CFG)
    assert "has not succeeded in over 3 hours" in msgs[0][2]
