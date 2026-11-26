"""Command line: python -m salesync <command>"""
import argparse
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .jobber import AUTHORIZE_URL, DEFAULT_API_VERSION, JobberClient
from .report import build_all, load_config, send
from .sync import QUOTES_QUERY, run_sync


def _client(conn):
    return JobberClient(conn, os.environ["JOBBER_CLIENT_ID"], os.environ["JOBBER_CLIENT_SECRET"],
                        os.environ.get("JOBBER_API_VERSION") or DEFAULT_API_VERSION)


def main(argv=None):
    p = argparse.ArgumentParser(prog="salesync")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create or update the database tables")
    c = sub.add_parser("connect", help="finish connecting Jobber with the code from the browser")
    c.add_argument("--code", required=True)
    c.add_argument("--redirect-uri", required=True)
    a = sub.add_parser("authorize-url", help="print the Jobber link to approve access")
    a.add_argument("--redirect-uri", required=True)
    sub.add_parser("check", help="read one page of quotes to prove the connection works")
    sub.add_parser("sync", help="copy all quotes from Jobber")
    r = sub.add_parser("report", help="build and send the daily emails")
    r.add_argument("--dry-run", metavar="DIR", help="write the emails as files instead of sending")
    args = p.parse_args(argv)

    if args.cmd == "authorize-url":
        print(f"{AUTHORIZE_URL}?response_type=code&client_id={os.environ['JOBBER_CLIENT_ID']}"
              f"&redirect_uri={args.redirect_uri}")
        return 0

    conn = db.connect()
    if args.cmd == "migrate":
        applied = db.migrate(conn)
        print("Applied: " + (", ".join(applied) if applied else "nothing new"))
    elif args.cmd == "connect":
        db.migrate(conn)
        _client(conn).exchange_code(args.code.strip(), args.redirect_uri)
        print("Connected to Jobber.")
    elif args.cmd == "check":
        page = _client(conn).query(QUOTES_QUERY, {"first": 3, "after": None})["quotes"]
        for q in page["nodes"]:
            print(f"#{q['quoteNumber']} {q['quoteStatus']} {q['amounts']['total']} "
                  f"{(q.get('client') or {}).get('name')}")
        print("Connection works.")
    elif args.cmd == "sync":
        db.migrate(conn)
        run_sync(conn, _client(conn))
    elif args.cmd == "report":
        today = datetime.now(ZoneInfo("America/New_York")).date()
        send(build_all(conn, today, load_config()), dry_run_dir=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
