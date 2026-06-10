"""Serialize a faultline Result and push it to a hosted faultline project.

Metadata ONLY — never the agent's prompts, data, tool I/O or code. The payload
is a direct, lossless serialization of Result(baseline, rows) and matches the
hosted platform's ``ingest_run(p_token, p_payload)`` Postgres RPC contract.

Auto-used by ``faultline run --push`` (or whenever FAULTLINE_TOKEN is set):
    FAULTLINE_TOKEN  the project token (flt_live_...) — keep in CI secrets.
                     This is the ONLY value you need for the hosted dashboard.
    FAULTLINE_URL    (optional) override the REST base — only if self-hosting.
    FAULTLINE_KEY    (optional) override the public anon key — only if self-hosting.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .detect import suggest_fix

# Hosted faultline dashboard — public endpoint + publishable anon key (the exact
# same pair the web app ships in the browser; safe to embed). A project TOKEN is
# what scopes a push to your project; URL/KEY only change if you self-host.
HOSTED_URL = "https://szzrnyxjwxfdalwoxtej.supabase.co"
HOSTED_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZ"
              "iI6InN6enJueXhqd3hmZGFsd294dGVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA3"
              "OTMxMzgsImV4cCI6MjA5NjM2OTEzOH0.biJabbevvVRdBbJ53R4_EBDqAdS31P60rb2P"
              "PQB0S2U")


def to_payload(result, agent="agent", duration_ms=None, trials=5):
    """Build the ingest payload dict from a Result. No agent I/O is included."""
    rows = []
    for r in result.rows:
        name = r.get("fault", "")
        try:
            fix = suggest_fix(name, r.get("verdict"))
        except Exception:
            fix = None
        rows.append({
            "fault": name,
            "verdict": str(r.get("verdict", "")).lower(),
            "detail": r.get("detail", ""),
            "suggested_fix": fix,
            "trials": r.get("trials", []),
        })
    baseline = getattr(result, "baseline", None) or {}
    return {
        "agent": agent,
        "trials": trials,
        "duration_ms": duration_ms,
        "baseline_ok": baseline.get("error") is None,
        "baseline_error": str(baseline.get("error")) if baseline.get("error") is not None else None,
        "git_sha": os.environ.get("GITHUB_SHA") or os.environ.get("FAULTLINE_SHA"),
        "git_branch": os.environ.get("GITHUB_REF_NAME") or os.environ.get("FAULTLINE_BRANCH"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "ci_run_url": _ci_url(),
        "results": rows,
    }


def _ci_url():
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return "%s/%s/actions/runs/%s" % (server, repo, run_id)
    return None


def _endpoint(url):
    e = (url or "").rstrip("/")
    if "/rpc/ingest_run" in e:
        return e
    if e.endswith("/rest/v1") or "/rest/v1" in e:
        return e + "/rpc/ingest_run"
    return e + "/rest/v1/rpc/ingest_run"


def push(payload, url, anon_key, token, timeout=20):
    """POST the payload to ingest_run. Returns (ok: bool, message: str)."""
    body = json.dumps({"p_token": token, "p_payload": payload}).encode("utf-8")
    req = urllib.request.Request(_endpoint(url), data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("apikey", anon_key)
    req.add_header("Authorization", "Bearer " + anon_key)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = ""
        return False, "%s %s %s" % (e.code, e.reason, detail[:300])
    except Exception as e:
        return False, str(e)


def push_from_env(result, agent="agent", trials=5, duration_ms=None):
    """Read FAULTLINE_URL/KEY/TOKEN from env and push.

    Returns (True/False, message) when attempted, or (None, reason) when skipped.
    """
    # URL + anon key default to the hosted dashboard; only the token is required.
    url = os.environ.get("FAULTLINE_URL") or HOSTED_URL
    key = os.environ.get("FAULTLINE_KEY") or HOSTED_KEY
    token = os.environ.get("FAULTLINE_TOKEN")
    if not token:
        return None, ("missing env: FAULTLINE_TOKEN — create a token in your "
                      "dashboard (Settings -> Tokens) and set it in CI secrets")
    payload = to_payload(result, agent=agent, duration_ms=duration_ms, trials=trials)
    return push(payload, url, key, token)
