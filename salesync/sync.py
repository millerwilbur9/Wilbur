"""Pull every quote from Jobber and make the database match it exactly.

Each run reads all quotes (your volume is small), upserts them by Jobber ID,
records status changes, and marks quotes that disappeared from Jobber as
deleted. A quote is only marked deleted after Jobber confirms it is gone.
"""
from .jobber import JobberClient, JobberError

QUOTE_FIELDS = """
  id
  quoteNumber
  title
  quoteStatus
  createdAt
  updatedAt
  sentAt
  jobberWebUri
  amounts { subtotal discountAmount total }
  lastTransitioned { approvedAt changesRequestedAt convertedAt }
  salesperson { id name { full } }
  client {
    id
    name
    isCompany
    leadSource
    createdAt
    phones { number primary }
    tags(first: 20) { nodes { label } }
    customFields {
      ... on CustomFieldDropdown { label valueDropdown }
      ... on CustomFieldText { label valueText }
    }
  }
"""

QUOTES_QUERY = """
query SyncQuotes($first: Int!, $after: String) {
  quotes(first: $first, after: $after) {
    nodes { %s }
    pageInfo { hasNextPage endCursor }
  }
}
""" % QUOTE_FIELDS

ONE_QUOTE_QUERY = """
query OneQuote($id: EncodedId!) {
  quote(id: $id) { %s }
}
""" % QUOTE_FIELDS

STATUSES = {"draft", "awaiting_response", "changes_requested", "approved", "converted", "archived"}


class SyncError(RuntimeError):
    pass


def _status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s not in STATUSES:
        raise SyncError(f"Unknown Jobber quote status {raw!r}")
    return s


def _division_field(custom_fields) -> str | None:
    for f in custom_fields or []:
        if (f.get("label") or "").strip().lower() == "division":
            v = f.get("valueDropdown") or f.get("valueText")
            return v.strip() if isinstance(v, str) and v.strip() else None
    return None


def _primary_phone(phones) -> str | None:
    phones = phones or []
    for p in phones:
        if p.get("primary"):
            return p.get("number")
    return phones[0].get("number") if phones else None


def upsert_client(conn, c: dict):
    if not c:
        return None
    tags = [t["label"] for t in ((c.get("tags") or {}).get("nodes") or []) if t.get("label")]
    return conn.execute(
        """insert into client (jobber_id, display_name, is_company, lead_source, tags,
                               division_field, phone, jobber_created_at, synced_at)
           values (%s, %s, %s, %s, %s, %s, %s, %s, now())
           on conflict (jobber_id) do update set
             display_name = excluded.display_name, is_company = excluded.is_company,
             lead_source = excluded.lead_source, tags = excluded.tags,
             division_field = excluded.division_field, phone = excluded.phone,
             jobber_created_at = excluded.jobber_created_at, synced_at = now()
           returning id""",
        (c["id"], c.get("name") or "(no name)", c.get("isCompany"), c.get("leadSource"), tags,
         _division_field(c.get("customFields")), _primary_phone(c.get("phones")),
         c.get("createdAt")),
    ).fetchone()[0]


def upsert_salesperson(conn, sp: dict):
    if not sp:
        return None
    name = ((sp.get("name") or {}).get("full") or "").strip() or "(no name)"
    return conn.execute(
        """insert into team_member (jobber_id, full_name) values (%s, %s)
           on conflict (jobber_id) do update set full_name = excluded.full_name
           returning id""",
        (sp["id"], name),
    ).fetchone()[0]


def upsert_quote(conn, q: dict) -> str:
    client_id = upsert_client(conn, q.get("client"))
    salesperson_id = upsert_salesperson(conn, q.get("salesperson"))
    status = _status(q.get("quoteStatus"))
    amounts = q.get("amounts") or {}
    lt = q.get("lastTransitioned") or {}
    before = conn.execute("select id, status from quote where jobber_id = %s",
                          (q["id"],)).fetchone()
    row = conn.execute(
        """insert into quote (jobber_id, source, quote_number, client_id, salesperson_id, title,
                              status, subtotal, discount, total, jobber_created_at, sent_at,
                              approved_at, changes_requested_at, converted_at, jobber_updated_at,
                              first_presented_at, amount_presented, jobber_web_uri,
                              synced_at, deleted_at)
           values (%(jid)s, 'jobber', %(num)s, %(client)s, %(sp)s, %(title)s, %(status)s,
                   %(subtotal)s, %(discount)s, %(total)s, %(created)s, %(sent)s, %(approved)s,
                   %(changes)s, %(converted)s, %(updated)s,
                   least(%(sent)s::timestamptz, %(approved)s::timestamptz, %(converted)s::timestamptz),
                   case when coalesce(%(sent)s, %(approved)s, %(converted)s) is not null
                        then %(total)s::numeric end,
                   %(uri)s, now(), null)
           on conflict (jobber_id) do update set
             quote_number = excluded.quote_number, client_id = excluded.client_id,
             salesperson_id = excluded.salesperson_id, title = excluded.title,
             status = excluded.status, subtotal = excluded.subtotal,
             discount = excluded.discount, total = excluded.total,
             jobber_created_at = excluded.jobber_created_at, sent_at = excluded.sent_at,
             approved_at = excluded.approved_at,
             changes_requested_at = excluded.changes_requested_at,
             converted_at = excluded.converted_at, jobber_updated_at = excluded.jobber_updated_at,
             -- set once, never moved
             first_presented_at = coalesce(quote.first_presented_at, excluded.first_presented_at),
             amount_presented = coalesce(quote.amount_presented, excluded.amount_presented),
             jobber_web_uri = excluded.jobber_web_uri, synced_at = now(), deleted_at = null
           returning id""",
        {"jid": q["id"], "num": q.get("quoteNumber"), "client": client_id, "sp": salesperson_id,
         "title": q.get("title"), "status": status, "subtotal": amounts.get("subtotal"),
         "discount": amounts.get("discountAmount"), "total": amounts.get("total"),
         "created": q.get("createdAt"), "sent": q.get("sentAt"),
         "approved": lt.get("approvedAt"), "changes": lt.get("changesRequestedAt"),
         "converted": lt.get("convertedAt"), "updated": q.get("updatedAt"),
         "uri": q.get("jobberWebUri")},
    ).fetchone()
    quote_id = row[0]
    old_status = before[1] if before else None
    if old_status != status:
        conn.execute(
            """insert into quote_status_event (quote_id, happened_at, from_status, to_status)
               values (%s, coalesce(%s::timestamptz, now()), %s, %s)
               on conflict do nothing""",
            (quote_id, q.get("updatedAt"), old_status, status),
        )
    return quote_id


def run_sync(conn, client: JobberClient, page_size: int = 25, log=print) -> dict:
    run_id = conn.execute(
        "insert into sync_run (status) values ('running') returning id").fetchone()[0]
    conn.commit()
    seen: set[str] = set()
    try:
        after = None
        while True:
            page = client.query(QUOTES_QUERY, {"first": page_size, "after": after})["quotes"]
            for node in page["nodes"]:
                upsert_quote(conn, node)
                seen.add(node["id"])
            conn.commit()
            info = page["pageInfo"]
            if not info.get("hasNextPage"):
                break
            if not info.get("endCursor") or info["endCursor"] == after:
                raise SyncError("Jobber pagination stopped moving; refusing to continue")
            after = info["endCursor"]

        deleted = _confirm_deletions(conn, client, seen, log)
        conn.execute(
            "update sync_run set status = 'success', finished_at = now(), quotes_seen = %s,"
            " quotes_deleted = %s where id = %s", (len(seen), deleted, run_id))
        conn.commit()
        log(f"Sync OK: {len(seen)} quotes read, {deleted} marked deleted.")
        return {"seen": len(seen), "deleted": deleted}
    except Exception as e:
        conn.rollback()
        conn.execute(
            "update sync_run set status = 'failed', finished_at = now(), quotes_seen = %s,"
            " error = %s where id = %s", (len(seen), str(e)[:2000], run_id))
        conn.commit()
        raise


def _confirm_deletions(conn, client, seen: set[str], log) -> int:
    """Quotes we have that Jobber's list did not return. Ask Jobber about each one."""
    known = [r[0] for r in conn.execute(
        "select jobber_id from quote where source = 'jobber' and deleted_at is null")]
    missing = [j for j in known if j not in seen]
    if not missing:
        return 0
    if len(missing) > max(25, len(known) // 10):
        raise SyncError(
            f"{len(missing)} of {len(known)} quotes missing from Jobber's list. That looks like "
            "an API problem, not real deletes. Nothing was marked deleted.")
    deleted = 0
    for jid in missing:
        try:
            node = client.query(ONE_QUOTE_QUERY, {"id": jid}).get("quote")
        except JobberError as e:
            if "not found" not in str(e).lower():
                raise
            node = None
        if node:
            log(f"Quote {jid} was missing from the list but still exists; updated it.")
            upsert_quote(conn, node)
        else:
            conn.execute("update quote set deleted_at = now() where jobber_id = %s", (jid,))
            deleted += 1
    conn.commit()
    return deleted
