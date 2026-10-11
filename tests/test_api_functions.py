"""The two Vercel functions that act on a request: /api/refresh and /api/contact.

Each handler is loaded under Node with `fetch` replaced by a recorder, so the tests see
exactly which outbound calls a request would cause, and nothing leaves the machine. The
cases are the abuse paths found in the audit of 10 October 2026: an anonymous refresh that
could choose when the fixing's prices were read, and a contact form with no budget.

Skipped where Node is not installed. GitHub's ubuntu-24.04 image ships it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / "site" / "api"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

# Runs one request through a handler. `now` pins Date; `upstash` is the INCR results the
# fake Upstash returns ([hour, day]) or null for "not configured".
HARNESS = r"""
const [handlerPath, reqJson, envJson, now, upstashJson] = process.argv.slice(1);
const calls = [];
const RealDate = Date;
global.Date = class extends RealDate {
  constructor(...a) { super(...(a.length ? a : [now])); }
  static now() { return new RealDate(now).getTime(); }
};
Object.assign(process.env, JSON.parse(envJson));
const upstash = JSON.parse(upstashJson);
global.fetch = async (url, init) => {
  calls.push({ url: String(url), method: (init && init.method) || "GET" });
  if (String(url).includes("upstash")) {
    return { ok: true, json: async () => [
      { result: upstash[0] }, { result: 1 }, { result: upstash[1] }, { result: 1 }] };
  }
  if (String(url).endsWith("/runs?per_page=1")) {
    return { ok: true, json: async () => ({ workflow_runs: [] }) };
  }
  if (String(url).endsWith("/dispatches")) return { status: 204, text: async () => "" };
  return { ok: true, status: 200, json: async () => ({}), text: async () => "secret body" };
};
const handler = require(handlerPath);
const out = { status: 200, headers: {}, body: null };
const res = {
  status(c) { out.status = c; return this; },
  json(b) { out.body = b; return this; },
  setHeader(k, v) { out.headers[k] = v; },
  writeHead(c, h) { out.status = c; Object.assign(out.headers, h || {}); },
  end() {},
};
Promise.resolve(handler(JSON.parse(reqJson), res)).then(() => {
  process.stdout.write(JSON.stringify({ ...out, calls }));
});
"""


def _run(handler: str, req: dict, env: dict, now: str, upstash: list | None = None) -> dict:
    proc = subprocess.run(
        [NODE, "-e", HARNESS, str(API / handler), json.dumps(req), json.dumps(env), now,
         json.dumps(upstash)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(proc.stdout)


REFRESH_ENV = {"REFRESH_SECRET": "s3cret", "GITHUB_DISPATCH_TOKEN": "t", "GITHUB_REPO": "o/r"}
AFTER_FIXING = "2026-10-10T12:00:00Z"


def _refresh(req: dict, env: dict | None = None, now: str = AFTER_FIXING) -> dict:
    base = {"method": "POST", "query": {}, "headers": {"authorization": "Bearer s3cret"}}
    return _run("refresh.js", {**base, **req}, env if env is not None else REFRESH_ENV, now)


def _dispatched(out: dict) -> bool:
    return any(c["url"].endswith("/dispatches") for c in out["calls"])


def test_refresh_dispatches_for_the_administrator_after_the_fixing() -> None:
    out = _refresh({})
    assert out["status"] == 200 and out["body"]["dispatched"] is True
    assert _dispatched(out)


def test_refresh_refuses_an_anonymous_or_wrong_token() -> None:
    for headers in ({}, {"authorization": "Bearer nope"}, {"authorization": "s3cret"}):
        out = _refresh({"headers": headers})
        assert out["status"] == 401
        assert not out["calls"]


def test_refresh_is_closed_when_no_secret_is_configured() -> None:
    out = _refresh({}, env={"GITHUB_DISPATCH_TOKEN": "t"})
    assert out["status"] == 503
    assert not out["calls"]


def test_refresh_never_collects_before_the_fixing() -> None:
    # A collection before 11:00 would become the fixing (has_ok_run skips the 11:00 read).
    out = _refresh({}, now="2026-10-10T10:59:59Z")
    assert out["status"] == 409 and out["body"]["reason"] == "before_fixing"
    assert not _dispatched(out)


def test_refresh_refuses_get() -> None:
    # A GET that starts a collection can be fired by a prefetcher or a crawler.
    out = _refresh({"method": "GET"})
    assert out["status"] == 405
    assert not out["calls"]


CONTACT_ENV = {
    "RESEND_API_KEY": "k", "CONTACT_TO_EMAIL": "owner@example.org",
    "CONTACT_ACK_FROM": "TCI <c@example.org>",
    "UPSTASH_REDIS_REST_URL": "https://upstash.example", "UPSTASH_REDIS_REST_TOKEN": "x",
}
FORM = {"email": "a@b.example", "name": "Ann", "message": "Hello", "website": ""}


def _contact(form: dict, upstash: list | None, env: dict | None = None) -> dict:
    return _run("contact.js", {"method": "POST", "body": form},
                env if env is not None else CONTACT_ENV, AFTER_FIXING, upstash)


def _resend_sends(out: dict) -> int:
    return sum(1 for c in out["calls"] if "resend.com" in c["url"])


def test_contact_sends_within_budget() -> None:
    out = _contact(FORM, upstash=[1, 1])
    assert out["headers"]["Location"] == "/contact.html#sent"
    assert _resend_sends(out) == 2  # the owner's copy and the confirmation


def test_contact_stops_sending_once_the_hourly_or_daily_budget_is_spent() -> None:
    for counts in ([11, 11], [1, 41]):
        out = _contact(FORM, upstash=counts)
        assert out["headers"]["Location"] == "/contact.html#error"
        assert _resend_sends(out) == 0


def test_contact_still_works_without_the_counter() -> None:
    env = {k: v for k, v in CONTACT_ENV.items() if not k.startswith("UPSTASH")}
    out = _contact(FORM, upstash=None, env=env)
    assert out["headers"]["Location"] == "/contact.html#sent"


def test_contact_refuses_oversized_or_malformed_fields() -> None:
    for form in (
        {**FORM, "message": "x" * 10001},
        {**FORM, "name": "x" * 201},
        {**FORM, "email": "not-an-address"},
    ):
        out = _contact(form, upstash=[1, 1])
        assert out["headers"]["Location"] == "/contact.html#error"
        assert _resend_sends(out) == 0
