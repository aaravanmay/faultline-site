"""faultline.attest — a versioned, tamper-EVIDENT evidence report (Rung 3).

What this is, stated honestly so nobody over-claims it:

  * ``attest`` runs a suite (exactly like ``faultline run``) and writes a
    ``faultline.report.json`` v1 file: the per-fault verdicts plus a SHA-256
    content hash over a *canonical* (deterministic) serialization of the
    verdict body.
  * ``verify`` re-canonicalizes that body, recomputes the hash, and confirms
    it matches the stored one. Flip a verdict or edit a number in the file and
    the recomputed hash no longer matches -> verify fails and names the field.

What "signed / reproducible / tamper-evident" means here -- and what it is NOT:

  * The hash is a **SHA-256 content hash over a canonical form**. It is NOT a
    secret-key / asymmetric signature: faultline runs in the user's own CI with
    no server secret, so there is no private key to sign with. The honest claim
    is **tamper-evident + reproducible** -- anyone can re-canonicalize and
    re-hash to detect edits; ``verify`` re-derives the verdicts and confirms the
    hash. It is NOT "cryptographically signed by faultline", NOT "tamper-proof",
    NOT a certification / compliance pass. It is evidence an auditor can cite.

Determinism is the whole point. The hashed canonical body **excludes** every
non-deterministic field (timestamp, duration, git SHA/branch/ref, CI run URL).
Those stay in the report for humans, but live OUTSIDE the hashed body, so two
``attest`` runs on the same deterministic suite produce the SAME hash.

Report shape (v1)::

    {
      "report_version": 1,
      "kind": "faultline.report",
      "body": { ...the hashed, deterministic verdict payload... },
      "meta": { ...timestamp, duration_ms, git_*, ci_run_url... NOT hashed... },
      "attestation": {
        "algorithm": "sha256",
        "canonicalization": "json-sorted-keys-compact-utf8",
        "hashed_fields": ["report_version", "kind", "body"],
        "content_hash": "<hex>",
        "note": "tamper-evident content hash, not a secret-key signature"
      }
    }

Stdlib only (hashlib, json) -- no new dependencies, Python 3.9 floor.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os

from .report import to_payload

# Bump this if the report shape or the canonicalization changes. verify refuses
# a report whose report_version it does not understand (rather than silently
# "passing" a format it never validated).
REPORT_VERSION = 1
REPORT_KIND = "faultline.report"
HASH_ALGORITHM = "sha256"
CANONICALIZATION = "json-sorted-keys-compact-utf8"

# Keys produced by report.to_payload() that are NON-deterministic -> they go in
# meta and are NEVER part of the hashed body. (Two clean runs of the same
# deterministic suite differ only in these.)
_NONDETERMINISTIC_KEYS = (
    "duration_ms",
    "git_sha",
    "git_branch",
    "git_ref",
    "ci_run_url",
)

# Fields hashed by the content hash, in the order quoted in attestation.hashed_fields.
_HASHED_FIELDS = ("report_version", "kind", "body")


def _canonical_bytes(obj):
    """Deterministic UTF-8 serialization: sorted keys, compact separators.

    json.dumps already renders ints/floats/bools/None/str deterministically and
    cross-platform when keys are sorted and whitespace is fixed; that is exactly
    what a reproducible content hash needs.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def canonical_string(report):
    """The exact string that gets hashed, for a given report dict.

    Hashes report_version + kind + body ONLY -- never meta, never attestation.
    """
    hashed = {
        "report_version": report.get("report_version"),
        "kind": report.get("kind"),
        "body": report.get("body"),
    }
    return _canonical_bytes(hashed).decode("utf-8")


def compute_hash(report):
    """SHA-256 over the canonical form of (report_version, kind, body)."""
    h = hashlib.sha256()
    h.update(canonical_string(report).encode("utf-8"))
    return h.hexdigest()


def build_report(result, agent="agent", duration_ms=None, trials=5, now=None):
    """Build a v1 faultline.report dict from a check() Result.

    Reuses report.to_payload() for the verdict shape, then splits it into a
    deterministic ``body`` (hashed) and a non-deterministic ``meta`` (not
    hashed), and stamps the content hash.
    """
    payload = to_payload(result, agent=agent, duration_ms=duration_ms, trials=trials)

    # meta = the non-deterministic fields, kept for humans but out of the hash.
    meta = {k: payload.get(k) for k in _NONDETERMINISTIC_KEYS}
    meta["faultline_version"] = _faultline_version()
    if now is None:
        now = datetime.datetime.utcnow()
    meta["created_at"] = now.replace(microsecond=0).isoformat() + "Z"

    # body = everything else from the payload (deterministic verdict content).
    body = {k: v for k, v in payload.items() if k not in _NONDETERMINISTIC_KEYS}

    report = {
        "report_version": REPORT_VERSION,
        "kind": REPORT_KIND,
        "body": body,
        "meta": meta,
    }
    report["attestation"] = {
        "algorithm": HASH_ALGORITHM,
        "canonicalization": CANONICALIZATION,
        "hashed_fields": list(_HASHED_FIELDS),
        "content_hash": compute_hash(report),
        "note": ("tamper-evident SHA-256 content hash over the canonical "
                 "verdict body; not a secret-key signature, not a certification"),
    }
    return report


def write_report(report, path):
    """Write the report as pretty JSON (human-diffable) with a trailing newline."""
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.write("\n")
    return path


def load_report(path):
    """Load a report dict from disk. Raises on bad JSON / missing file."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def verify_report(report):
    """Re-derive the content hash and confirm it matches the stored one.

    Returns (ok: bool, message: str). On mismatch the message names what is
    wrong (the recomputed vs stored hash, or the missing/mangled field) so a
    flipped verdict or edited number is caught and reported, not just rejected.
    """
    if not isinstance(report, dict):
        return False, "report is not a JSON object"

    version = report.get("report_version")
    if version != REPORT_VERSION:
        return False, ("unsupported report_version %r (this faultline understands v%d)"
                       % (version, REPORT_VERSION))

    if report.get("kind") != REPORT_KIND:
        return False, ("unexpected kind %r (expected %r)"
                       % (report.get("kind"), REPORT_KIND))

    att = report.get("attestation")
    if not isinstance(att, dict):
        return False, "missing attestation block"

    algo = att.get("algorithm")
    if algo != HASH_ALGORITHM:
        return False, ("unsupported hash algorithm %r (expected %r)"
                       % (algo, HASH_ALGORITHM))

    canon = att.get("canonicalization")
    if canon != CANONICALIZATION:
        return False, ("unexpected canonicalization %r (expected %r)"
                       % (canon, CANONICALIZATION))

    body = report.get("body")
    if not isinstance(body, dict):
        return False, "missing or malformed body"

    stored = att.get("content_hash")
    recomputed = compute_hash(report)
    if stored != recomputed:
        field = _name_mismatch(report)
        return False, ("hash mismatch: stored %s != recomputed %s%s -- the "
                       "report was edited after it was attested"
                       % (_short(stored), _short(recomputed), field))

    n = len(_results(body))
    return True, "verified: %d verdict(s), hash OK" % n


def _name_mismatch(report):
    """Best-effort: point at WHICH hashed field changed, for the error message.

    Pure heuristic for a friendlier message -- the hash mismatch alone already
    proves tampering; this just tries to name the culprit (a flipped verdict, an
    edited number) so the operator knows where to look.
    """
    body = report.get("body") or {}
    results = _results(body)
    bits = []
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            continue
        v = str(r.get("verdict", ""))
        if v and v not in ("pass", "fail", "crash", "inconclusive", ""):
            bits.append("body.results[%d].verdict=%r (not a known verdict)" % (i, v))
    if bits:
        return " (suspect: %s)" % "; ".join(bits[:3])
    return " (a field inside the hashed body -- report_version, kind, or body -- was changed)"


def _results(body):
    res = body.get("results") if isinstance(body, dict) else None
    return res if isinstance(res, list) else []


def _short(h):
    if not isinstance(h, str):
        return repr(h)
    return h[:12] + "..." if len(h) > 16 else h


def _faultline_version():
    try:
        from . import __version__
        return __version__
    except Exception:
        return None
