import os

import pytest

from salesync import db

URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:5499/postgres")


@pytest.fixture
def conn():
    c = db.connect(URL)
    c.execute("drop schema if exists sales cascade")
    c.commit()
    db.migrate(c)
    yield c
    c.rollback()
    c.close()


def node(jid, num, status, total, created, sent=None, approved=None, converted=None,
         changes=None, updated=None, rep=("U1", "Jackson Price"), client=None):
    """A quote shaped like Jobber's GraphQL response."""
    return {
        "id": jid, "quoteNumber": str(num), "title": "Gutters & Customer Service",
        "quoteStatus": status, "createdAt": created, "updatedAt": updated or created,
        "sentAt": sent, "jobberWebUri": f"https://secure.getjobber.com/quotes/{num}",
        "amounts": {"subtotal": total, "discountAmount": 0, "total": total},
        "lastTransitioned": {"approvedAt": approved, "changesRequestedAt": changes,
                             "convertedAt": converted},
        "salesperson": {"id": rep[0], "name": {"full": rep[1]}} if rep else None,
        "client": client or homeowner(f"C{num}"),
    }


def homeowner(cid, lead="Google LSA"):
    return {"id": cid, "name": f"Homeowner {cid}", "isCompany": False, "leadSource": lead,
            "createdAt": "2026-01-01T00:00:00Z",
            "phones": [{"number": "5745550100", "primary": True}],
            "tags": {"nodes": []},
            "customFields": [{"label": "Division", "valueDropdown": ""},
                             {"label": "Client Data", "valueText": ""}]}


def contractor(cid):
    c = homeowner(cid, lead="Contractor")
    c["isCompany"] = True
    c["tags"] = {"nodes": [{"label": "Contractor"}]}
    return c


class FakeJobber:
    """Stands in for JobberClient. Serves quotes in pages like the real API."""

    def __init__(self, quotes, page_size=2):
        self.quotes = list(quotes)
        self.page_size = page_size
        self.lookup = {}          # jobber id -> node returned by the single-quote query
        self.calls = 0

    def query(self, query, variables=None):
        self.calls += 1
        if "OneQuote" in query:
            return {"quote": self.lookup.get(variables["id"])}
        start = int(variables.get("after") or 0)
        chunk = self.quotes[start:start + self.page_size]
        end = start + len(chunk)
        return {"quotes": {"nodes": chunk,
                           "pageInfo": {"hasNextPage": end < len(self.quotes),
                                        "endCursor": str(end)}}}
