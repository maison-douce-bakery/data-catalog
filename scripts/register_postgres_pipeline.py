#!/usr/bin/env python3
"""Create or update the scheduled PostgreSQL ingestion pipeline in Marmot.

On a fresh Marmot it first sets the admin password to MARMOT_ADMIN_PASSWORD, only while the
account still has the seeded default login and a forced password change. It never triggers a
run, creates API keys or runs enrichment. Marmot requires a password of at least 8 characters.
Env: MARMOT_URL, MARMOT_ADMIN_USER (admin), MARMOT_ADMIN_PASSWORD, MARMOT_SOURCE_HOST,
MARMOT_SOURCE_PORT (5432), MARMOT_SOURCE_DATABASE, MARMOT_READER_USER, MARMOT_READER_PASSWORD,
MARMOT_SOURCE_SSL_MODE (require).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipelines", "postgres.json")
TIMEOUT = 30
WAIT_SECONDS = 600
POLL_SECONDS = 5
PAGE = 200
SECRET_KEYS = {"password", "api_key", "token", "access_token"}


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def env(name, default=None):
    value = os.environ.get(name, default)
    if not value:
        fail(f"{name} is not set")
    return value


def scrub(obj):
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in SECRET_KEYS}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


class HttpError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


def call(method, url, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode(errors="replace")) from None
    return json.loads(raw) if raw else {}


def describe(err):
    try:
        text = json.dumps(scrub(json.loads(err.body)))
    except ValueError:
        text = "(non-JSON body omitted)"
    return f"HTTP {err.status}: {text}"


def retry_until_up(label, fn, deadline):
    """Run fn until it succeeds; retry on connection errors and 5xx until the deadline."""
    n = 0
    while True:
        try:
            return fn()
        except HttpError as e:
            if e.status < 500:
                fail(f"{label} failed, {describe(e)}")
            reason = f"HTTP {e.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = type(e).__name__
        if time.monotonic() > deadline:
            fail(f"{label} did not succeed in time ({reason})")
        n += 1
        if n % 6 == 1:
            print(f"{label}: not ready ({reason}), retrying...")
        time.sleep(POLL_SECONDS)


def build_payload():
    with open(TEMPLATE) as f:
        payload = json.load(f)
    values = {
        "__SOURCE_HOST__": env("MARMOT_SOURCE_HOST"),
        "__SOURCE_PORT__": int(env("MARMOT_SOURCE_PORT", "5432")),
        "__SOURCE_DATABASE__": env("MARMOT_SOURCE_DATABASE"),
        "__READER_USER__": env("MARMOT_READER_USER"),
        "__READER_PASSWORD__": env("MARMOT_READER_PASSWORD"),
        "__SSL_MODE__": env("MARMOT_SOURCE_SSL_MODE", "require"),
    }
    cfg = payload["config"]
    for key, value in cfg.items():
        if isinstance(value, str) and value in values:
            cfg[key] = values[value]
    left = [k for k, v in cfg.items() if isinstance(v, str) and v.startswith("__")]
    if left:
        fail(f"unreplaced placeholders in template: {', '.join(left)}")
    return payload


def main():
    base = env("MARMOT_URL").rstrip("/")
    user = env("MARMOT_ADMIN_USER", "admin")
    password = env("MARMOT_ADMIN_PASSWORD")
    payload = build_payload()
    name = payload["name"]
    plugin_id = payload["plugin_id"]
    deadline = time.monotonic() + WAIT_SECONDS

    def login(username, pw):
        """Return the login response, or None when Marmot answers 401."""
        def attempt():
            try:
                return call("POST", f"{base}/api/v1/users/login", {"username": username, "password": pw})
            except HttpError as e:
                if e.status == 401:
                    return None
                raise
        return retry_until_up("login", attempt, deadline)

    session = login(user, password)
    if session is None:
        if user != "admin":
            fail(f"login failed for {user}: invalid credentials")
        default = login("admin", "admin")
        if default is None or not default.get("requires_password_change"):
            fail("login failed for admin and the account does not have the seeded default login "
                 "with a forced password change; nothing was changed")
        if len(password) < 8:
            fail("MARMOT_ADMIN_PASSWORD must be at least 8 characters")
        try:
            call("POST", f"{base}/api/v1/users/update-password", {"new_password": password},
                 default.get("access_token"))
        except HttpError as e:
            fail(f"setting the admin password failed, {describe(e)}")
        print("admin password set")
        session = login(user, password)
        if session is None:
            fail("login failed after setting the admin password")
    if session.get("requires_password_change"):
        fail("the admin account requires a password change that this password does not satisfy")
    token = session.get("access_token")
    if not token:
        fail("login response has no access_token")
    print(f"logged in as {user}")

    def api(method, path, body=None):
        try:
            return call(method, f"{base}{path}", body, token)
        except HttpError as e:
            fail(f"{method} {path} failed, {describe(e)}")

    print(f"waiting for plugin {plugin_id}")
    while True:
        info = retry_until_up("plugins", lambda: call("GET", f"{base}/api/v1/plugins", token=token), deadline)
        ids = [p.get("id") for p in info.get("plugins") or []]
        if info.get("loading") is False:
            if plugin_id in ids:
                break
            fail(f"plugins loaded but {plugin_id} is missing (found: {', '.join(ids) or 'none'})")
        if time.monotonic() > deadline:
            fail(f"timed out waiting for plugin {plugin_id}")
        print("plugins still loading...")
        time.sleep(POLL_SECONDS)

    matches, offset = [], 0
    while True:
        page = api("GET", f"/api/v1/ingestion/schedules?limit={PAGE}&offset={offset}")
        items = page.get("schedules") or []
        matches += [s for s in items if s.get("name") == name]
        offset += len(items)
        if not items or offset >= page.get("total", 0):
            break

    if len(matches) > 1:
        fail(f"{len(matches)} schedules are named {name}; remove the duplicates in Marmot")
    if matches:
        result = api("PUT", f"/api/v1/ingestion/schedules/{matches[0]['id']}", payload)
        action = "updated"
    else:
        result = api("POST", "/api/v1/ingestion/schedules", payload)
        action = "created"

    print(f"{action} pipeline {name} (cron: {payload['cron_expression']}, id: {result.get('id', 'unknown')})")
    if result.get("next_run_at"):
        print(f"next run: {result['next_run_at']}")


if __name__ == "__main__":
    main()
