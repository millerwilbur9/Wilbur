"""Minimal Jobber GraphQL client: token refresh, throttling and retries."""
import base64
import json
import time
from datetime import datetime, timedelta, timezone

import requests

API_URL = "https://api.getjobber.com/api/graphql"
TOKEN_URL = "https://api.getjobber.com/api/oauth/token"
AUTHORIZE_URL = "https://api.getjobber.com/api/oauth/authorize"
DEFAULT_API_VERSION = "2025-01-20"


class JobberError(RuntimeError):
    pass


def _jwt_expiry(token: str) -> datetime | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except Exception:
        return None


class JobberClient:
    def __init__(self, conn, client_id, client_secret, api_version=DEFAULT_API_VERSION,
                 session=None, sleep=time.sleep, max_attempts=8):
        self.conn = conn
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_version = api_version
        self.http = session or requests.Session()
        self.sleep = sleep
        self.max_attempts = max_attempts

    # ------------------------------------------------------------ tokens
    def _save_tokens(self, body: dict, old_refresh: str | None = None) -> str:
        access = body["access_token"]
        # Jobber rotates refresh tokens. Always keep the newest one.
        refresh = body.get("refresh_token") or old_refresh
        if not refresh:
            raise JobberError("Jobber did not return a refresh token")
        expires = _jwt_expiry(access)
        if expires is None and body.get("expires_in"):
            expires = datetime.now(timezone.utc) + timedelta(seconds=int(body["expires_in"]))
        if expires is None:
            expires = datetime.now(timezone.utc) + timedelta(minutes=50)
        self.conn.execute(
            "insert into oauth_token (id, access_token, refresh_token, expires_at, updated_at)"
            " values (1, %s, %s, %s, now()) on conflict (id) do update set"
            " access_token = excluded.access_token, refresh_token = excluded.refresh_token,"
            " expires_at = excluded.expires_at, updated_at = now()",
            (access, refresh, expires),
        )
        self.conn.commit()  # commit right away so a later failure can't lose the new token
        return access

    def exchange_code(self, code: str, redirect_uri: str) -> None:
        r = self.http.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code,
            "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": redirect_uri}, timeout=30)
        if r.status_code != 200:
            raise JobberError(f"Jobber refused the code ({r.status_code}): {r.text[:300]}")
        self._save_tokens(r.json())

    def _refresh(self) -> str:
        row = self.conn.execute("select refresh_token from oauth_token where id = 1").fetchone()
        if not row:
            raise JobberError("Not connected to Jobber yet. Run the 'Connect Jobber' step.")
        r = self.http.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": row[0],
            "client_id": self.client_id, "client_secret": self.client_secret}, timeout=30)
        if r.status_code != 200:
            raise JobberError(
                f"Jobber token refresh failed ({r.status_code}). Reconnect Jobber. {r.text[:300]}")
        return self._save_tokens(r.json(), old_refresh=row[0])

    def _access_token(self) -> str:
        row = self.conn.execute(
            "select access_token, expires_at from oauth_token where id = 1").fetchone()
        if not row:
            raise JobberError("Not connected to Jobber yet. Run the 'Connect Jobber' step.")
        token, expires = row
        if expires is None or expires < datetime.now(timezone.utc) + timedelta(minutes=2):
            return self._refresh()
        return token

    # ------------------------------------------------------------ queries
    def query(self, query: str, variables: dict | None = None) -> dict:
        token = self._access_token()
        refreshed = False
        last = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self.http.post(API_URL, json={"query": query, "variables": variables or {}},
                                   headers={"Authorization": f"Bearer {token}",
                                            "X-JOBBER-GRAPHQL-VERSION": self.api_version},
                                   timeout=60)
            except requests.RequestException as e:
                self.sleep(min(60, 2 ** attempt))
                last = f"network error: {e}"
                continue
            if r.status_code == 401 and not refreshed:
                token, refreshed = self._refresh(), True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                self.sleep(min(60, 2 ** attempt))
                last = f"HTTP {r.status_code}"
                continue
            if r.status_code != 200:
                raise JobberError(f"Jobber API HTTP {r.status_code}: {r.text[:500]}")
            body = r.json()
            errors = body.get("errors") or []
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                self.sleep(self._throttle_wait(body))
                last = "throttled"
                continue
            if errors:
                raise JobberError("Jobber API error: " + json.dumps(errors)[:1500])
            return body["data"]
        raise JobberError(f"Jobber API kept failing after {self.max_attempts} tries ({last})")

    @staticmethod
    def _throttle_wait(body: dict) -> float:
        cost = (body.get("extensions") or {}).get("cost") or {}
        status = cost.get("throttleStatus") or {}
        need = float(cost.get("requestedQueryCost") or 1000)
        have = float(status.get("currentlyAvailable") or 0)
        rate = float(status.get("restoreRate") or 500)
        return max(1.0, (need - have) / rate + 1.0)
