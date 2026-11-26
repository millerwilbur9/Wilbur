import base64
import json
import time

import pytest

from salesync.jobber import JobberClient, JobberError


def jwt(exp):
    part = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{part}.s"


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class Session:
    def __init__(self, responses):
        self.responses, self.posts = list(responses), []

    def post(self, url, **kw):
        self.posts.append((url, kw))
        return self.responses.pop(0)


def seed(conn, access, refresh="R1", expires="now() + interval '1 hour'"):
    conn.execute(f"insert into oauth_token (access_token, refresh_token, expires_at) "
                 f"values (%s, %s, {expires})", (access, refresh))
    conn.commit()


def test_expired_token_refreshes_and_keeps_rotated_refresh_token(conn):
    seed(conn, "old", expires="now() - interval '1 minute'")
    new_access = jwt(int(time.time()) + 3600)
    s = Session([Resp(200, {"access_token": new_access, "refresh_token": "R2"}),
                 Resp(200, {"data": {"ok": True}})])
    c = JobberClient(conn, "id", "secret", session=s, sleep=lambda _: None)
    assert c.query("{ ok }") == {"ok": True}
    assert conn.execute("select access_token, refresh_token from oauth_token").fetchone() == (
        new_access, "R2")
    assert s.posts[0][1]["data"]["refresh_token"] == "R1"


def test_throttled_then_succeeds(conn):
    seed(conn, "tok")
    throttled = {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                 "extensions": {"cost": {"requestedQueryCost": 3000, "throttleStatus": {
                     "currentlyAvailable": 500, "restoreRate": 500}}}}
    waits = []
    s = Session([Resp(200, throttled), Resp(200, {"data": {"ok": 1}})])
    c = JobberClient(conn, "id", "secret", session=s, sleep=waits.append)
    assert c.query("{ ok }") == {"ok": 1}
    assert waits == [6.0]


def test_401_refreshes_once(conn):
    seed(conn, "tok")
    s = Session([Resp(401, {}), Resp(200, {"access_token": jwt(int(time.time()) + 3600),
                                           "refresh_token": "R2"}),
                 Resp(200, {"data": {"ok": 1}})])
    c = JobberClient(conn, "id", "secret", session=s, sleep=lambda _: None)
    assert c.query("{ ok }") == {"ok": 1}


def test_graphql_error_raises(conn):
    seed(conn, "tok")
    s = Session([Resp(200, {"errors": [{"message": "Field 'sentAt' doesn't exist"}]})])
    c = JobberClient(conn, "id", "secret", session=s, sleep=lambda _: None)
    with pytest.raises(JobberError, match="sentAt"):
        c.query("{ x }")


def test_not_connected(conn):
    c = JobberClient(conn, "id", "secret", session=Session([]), sleep=lambda _: None)
    with pytest.raises(JobberError, match="Connect Jobber"):
        c.query("{ x }")
