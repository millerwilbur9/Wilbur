import pytest

from salesync.sync import SyncError, run_sync
from tests.conftest import FakeJobber, contractor, node

# Modeled on real cases found in SmartStyle's Jobber on Nov 26, 2026.
CASES = [
    # closed at the kitchen table: approved, never sent (like #2031)
    node("Q1", 2031, "approved", 4313, "2026-11-25T14:18:24Z", approved="2026-11-25T14:41:59Z"),
    # sent, approved, job created -> status converted
    node("Q2", 1990, "converted", 5000, "2026-11-02T14:00:00Z", sent="2026-11-02T15:00:00Z",
         approved="2026-11-03T15:00:00Z", converted="2026-11-03T16:00:00Z"),
    # sent, still waiting
    node("Q3", 2029, "awaiting_response", 4472, "2026-11-24T21:08:04Z", sent="2026-11-24T23:38:32Z"),
    # sent, never approved, archived -> lost
    node("Q4", 1992, "archived", 2597, "2026-11-03T16:36:17Z", sent="2026-11-03T17:20:48Z"),
    # approved, later archived (like #1437) -> still won
    node("Q5", 1437, "archived", 3200, "2026-05-04T20:20:56Z", sent="2026-05-04T20:30:14Z",
         approved="2026-05-04T21:11:55Z"),
    # approved then re-opened (like #1867) -> open
    node("Q6", 1867, "awaiting_response", 4340.7, "2026-09-14T18:31:36Z",
         sent="2026-09-14T22:04:37Z", approved="2026-09-14T21:55:13Z"),
    # $0 quote (like #1850) -> excluded
    node("Q7", 1850, "awaiting_response", 0, "2026-09-08T19:03:02Z", sent="2026-09-08T19:03:06Z"),
    # draft -> not counted
    node("Q8", 2032, "draft", 1500, "2026-11-25T18:00:00Z"),
    # contractor client, late-night send: 11:30 PM Eastern Nov 6 = 04:30Z Nov 7
    node("Q9", 2001, "awaiting_response", 2000, "2026-11-06T20:00:00Z",
         sent="2026-11-07T04:30:00Z", rep=("U2", "Wilbur Miller"), client=contractor("C99")),
]


def outcomes(conn):
    return dict(conn.execute("select quote_number, outcome from v_quote").fetchall())


def test_outcomes_follow_the_written_rules(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    assert outcomes(conn) == {
        "2031": "won", "1990": "won", "2029": "open", "1992": "lost", "1437": "won",
        "1867": "open", "1850": "excluded", "2032": "draft", "2001": "open"}


def test_table_close_counts_in_close_rate(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    row = conn.execute("select in_close_rate, presented_on::text from v_quote "
                       "where quote_number = '2031'").fetchone()
    assert row == (True, "2026-11-25")


def test_presented_date_uses_eastern_time(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    assert conn.execute("select presented_on::text from v_quote where quote_number = '2001'"
                        ).fetchone()[0] == "2026-11-06"


def test_close_rate_math(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    won, total = conn.execute(
        "select sum(is_won), count(*) from v_quote where in_close_rate").fetchone()
    # won: 2031, 1990, 1437 ; open: 2029, 1867, 2001 ; lost: 1992
    assert (won, total) == (3, 7)


def test_division(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    assert conn.execute("select division from v_quote where quote_number = '2001'"
                        ).fetchone()[0] == "contractor"
    assert conn.execute("select division from v_quote where quote_number = '2031'"
                        ).fetchone()[0] == "direct"


def test_division_custom_field_wins(conn):
    q = node("Q1", 1, "approved", 100, "2026-11-01T12:00:00Z", approved="2026-11-01T13:00:00Z",
             client=contractor("C1"))
    q["client"]["customFields"] = [{"label": "Division", "valueDropdown": "Direct"}]
    run_sync(conn, FakeJobber([q]), log=lambda *_: None)
    assert conn.execute("select division from v_quote").fetchone()[0] == "direct"


def test_sync_twice_makes_no_duplicates(conn):
    fake = FakeJobber(CASES)
    run_sync(conn, fake, log=lambda *_: None)
    run_sync(conn, fake, log=lambda *_: None)
    assert conn.execute("select count(*) from quote").fetchone()[0] == len(CASES)
    assert conn.execute("select count(*) from client").fetchone()[0] == len(CASES)
    assert conn.execute("select count(*) from team_member").fetchone()[0] == 2


def test_resend_and_price_change_do_not_move_first_presentation(conn):
    q = node("Q3", 2029, "awaiting_response", 4472, "2026-11-24T21:08:04Z",
             sent="2026-11-24T23:38:32Z")
    run_sync(conn, FakeJobber([q]), log=lambda *_: None)
    q2 = dict(q, sentAt="2026-12-01T15:00:00Z", amounts={"subtotal": 3900, "discountAmount": 0,
                                                         "total": 3900})
    run_sync(conn, FakeJobber([q2]), log=lambda *_: None)
    presented, amount_quoted, current = conn.execute(
        "select presented_on::text, amount_quoted, amount_current from v_quote").fetchone()
    assert presented == "2026-11-24"
    assert (float(amount_quoted), float(current)) == (4472.0, 3900.0)


def test_status_changes_are_recorded(conn):
    q = node("Q3", 2029, "awaiting_response", 4472, "2026-11-24T21:08:04Z",
             sent="2026-11-24T23:38:32Z")
    run_sync(conn, FakeJobber([q]), log=lambda *_: None)
    q2 = dict(q, quoteStatus="approved", updatedAt="2026-11-26T15:00:00Z",
              lastTransitioned={"approvedAt": "2026-11-26T15:00:00Z"})
    run_sync(conn, FakeJobber([q2]), log=lambda *_: None)
    events = conn.execute("select from_status, to_status from quote_status_event "
                          "order by happened_at").fetchall()
    assert events == [(None, "awaiting_response"), ("awaiting_response", "approved")]


def test_deleted_quote_is_confirmed_then_marked(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    fake = FakeJobber(CASES[1:])          # Q1 no longer in Jobber's list
    run_sync(conn, fake, log=lambda *_: None)
    assert conn.execute("select deleted_at is not null from quote where jobber_id = 'Q1'"
                        ).fetchone()[0]
    assert "2031" not in outcomes(conn)


def test_missing_but_still_exists_is_not_deleted(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    fake = FakeJobber(CASES[1:])
    fake.lookup["Q1"] = CASES[0]          # list skipped it, but Jobber still has it
    run_sync(conn, fake, log=lambda *_: None)
    assert conn.execute("select deleted_at from quote where jobber_id = 'Q1'").fetchone()[0] is None


def test_mass_disappearance_marks_nothing_deleted(conn):
    many = [node(f"Q{i}", i, "awaiting_response", 100, "2026-11-01T12:00:00Z",
                 sent="2026-11-01T13:00:00Z") for i in range(300)]
    run_sync(conn, FakeJobber(many, page_size=50), log=lambda *_: None)
    with pytest.raises(SyncError):
        run_sync(conn, FakeJobber(many[:100], page_size=50), log=lambda *_: None)
    assert conn.execute("select count(*) from quote where deleted_at is not null").fetchone()[0] == 0
    assert conn.execute("select status from sync_run order by id desc limit 1").fetchone()[0] == "failed"


def test_override_wins(conn):
    run_sync(conn, FakeJobber(CASES), log=lambda *_: None)
    qid = conn.execute("select id from quote where quote_number = '2029'").fetchone()[0]
    conn.execute("insert into quote_override (quote_id, outcome, reason, updated_by) "
                 "values (%s, 'exclude', 'Duplicate quote', 'Wilbur')", (qid,))
    assert outcomes(conn)["2029"] == "excluded"


def test_manual_deal(conn):
    conn.execute("""insert into quote (source, quote_number, status, total, first_presented_at,
                    approved_at, manual_client_name, manual_lead_source, manual_division)
                    values ('manual', 'M-1', 'approved', 9000, '2026-11-10T15:00:00Z',
                    '2026-11-10T16:00:00Z', 'Home show deal', 'Home Show', 'direct')""")
    row = conn.execute("select outcome, lead_source, client_name from v_quote").fetchone()
    assert row == ("won", "Home Show", "Home show deal")


def test_unknown_status_fails_loudly(conn):
    bad = node("Q1", 1, "pending_signature", 100, "2026-11-01T12:00:00Z")
    with pytest.raises(SyncError):
        run_sync(conn, FakeJobber([bad]), log=lambda *_: None)
