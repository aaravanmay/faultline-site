"""faultline.detect — trial classification (the "catching" engine).

Given a baseline run (real, uncorrupted tool data) and a faulted run, decide:
  PASS   — handled the fault (recovered, abstained, or took no harmful action)
  SILENT — silently did the wrong thing on corrupted data (the dangerous kind)
  CRASH  — raised an unhandled exception

How we catch a SILENT failure without knowing the "right answer":
  The baseline run on REAL data is a free answer key — it's what a correct agent
  does when nothing is broken. We compare the faulted run against it and look for
  evidence the corruption flowed through to a real decision, in order of strength:

    1. invariant violated      — a rule you defined was broken (most precise)
    2. action divergence       — the agent took a consequential ACTION under
                                 corruption that it did NOT take on real data
                                 (e.g. placed an order it would otherwise refuse)
    3. poison parroting        — the corrupted value shows up in the agent's own
                                 output as if it were true (it swallowed the lie)
    4. derived-value tracking  — a NUMBER in the output moved baseline→faulted in
                                 lockstep with the injected corruption (same ratio,
                                 same delta, or a count of the truncated data) —
                                 catches corruption consumed through arithmetic

We deliberately do NOT flag "the answer text changed" on its own — harmless
wording changes are not failures, and treating them as failures cries wolf.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Small helpers over RUN dicts
# ---------------------------------------------------------------------------

def _called(run, name):
    """True if *name* appears at least once in run's events."""
    return any(ev["tool"] == name for ev in run["events"])


def _actions(run):
    """Signatures of every consequential ACTION the agent took (is_action tools).

    An action's signature is (tool, args, kwargs) so 'order 10' differs from
    'order 3'.
    """
    sigs = []
    for ev in run["events"]:
        if ev.get("is_action"):
            sigs.append((ev["tool"], repr(ev.get("args")), repr(ev.get("kwargs"))))
    return sigs


def _new_actions_under_fault(baseline_run, faulted_run):
    """Actions taken in the faulted run that were NOT taken on real data.

    A *new* consequential action under corruption is the strongest no-oracle
    signal of a silent failure: the broken tool changed what the agent DID.
    (Actions the agent stops doing under corruption are NOT flagged here — that
    can be a safe abstention; let an invariant judge those.)
    """
    base = set(_actions(baseline_run))
    return [a for a in _actions(faulted_run) if a not in base]


# Parameter-name tokens that mark an action argument as display-only (shown to a
# human, logged, or labeled — but not part of WHAT the action does). Kept tight
# on purpose: anything not on this list is treated as consequential.
_DISPLAY_ARG_TOKENS = (
    "display", "message", "msg", "label", "note", "comment",
    "description", "desc", "caption", "log", "banner",
)


def _is_display_param(param_name):
    """True when an action parameter's NAME marks it as display-only."""
    if not param_name:
        return False
    low = str(param_name).lower()
    for tok in _DISPLAY_ARG_TOKENS:
        if tok in low:
            return True
    return False


def _value_traceable_to_corruption(value, corrupted_leaves):
    """True when *value* IS an injected corrupted value (or a string embedding
    one) — i.e. the arg change is attributable to the fault we injected, not to
    some other computation the agent did."""
    for cv in corrupted_leaves:
        if cv is None or isinstance(cv, bool):
            continue
        s = str(cv)
        if len(s) < 2:
            continue
        try:
            if value == cv:
                return True
        except Exception:
            pass
        if isinstance(value, str) and s in value:
            return True
    return False


def _repr_eq(a, b):
    try:
        return repr(a) == repr(b)
    except Exception:
        return False


def _diff_is_display_only(base_ev, new_ev, corrupted_leaves):
    """True iff *new_ev* differs from *base_ev* ONLY in display-only arguments.

    THE EXACT RULE (deliberately conservative — when in doubt, the divergence
    stays consequential and gets flagged):

      A faulted action is "display-only divergent" from a baseline action when
      ALL of the following hold:
        1. SAME tool, SAME number of positional args, SAME kwarg keys — the
           action and its target shape are identical.
        2. Every positional/keyword arg that differs satisfies BOTH:
           a. its parameter NAME (recorded from the tool's signature at wrap
              time, or the kwarg key) contains a display-only token
              (_DISPLAY_ARG_TOKENS: display/message/msg/label/note/comment/
              description/desc/caption/log/banner), AND
           b. its faulted VALUE is traceable to the injected corruption — it
              equals a corrupted value, or is a string embedding one. An arg
              that changed to something we did NOT inject means the agent
              computed a different action, and that is flagged.
        3. At least one arg differs (identical signatures never reach here).

      Everything else — a new tool call, a changed arg with a consequential
      name (qty, amount, address, target...), a changed display arg whose new
      value is NOT the injected corruption — is consequential and flags SILENT.
    """
    if base_ev["tool"] != new_ev["tool"]:
        return False
    bargs = base_ev.get("args") or []
    fargs = new_ev.get("args") or []
    bkw = base_ev.get("kwargs") or {}
    fkw = new_ev.get("kwargs") or {}
    if len(bargs) != len(fargs) or set(bkw) != set(fkw):
        return False
    names = new_ev.get("arg_names") or []
    saw_diff = False
    for i in range(len(fargs)):
        if _repr_eq(bargs[i], fargs[i]):
            continue
        saw_diff = True
        pname = names[i] if i < len(names) else None
        if not (_is_display_param(pname)
                and _value_traceable_to_corruption(fargs[i], corrupted_leaves)):
            return False
    for k in fkw:
        if _repr_eq(bkw[k], fkw[k]):
            continue
        saw_diff = True
        if not (_is_display_param(k)
                and _value_traceable_to_corruption(fkw[k], corrupted_leaves)):
            return False
    return saw_diff


def _consequential_new_actions(baseline_run, faulted_run):
    """New actions under fault, with clearly display-only divergence suppressed.

    A faulted action whose signature is new ONLY because a display-only arg
    carries the injected corruption (same action, same target, e.g. a log
    string or display_discount) is not evidence the agent DID anything
    different — see _diff_is_display_only for the exact suppression rule.
    A faulted action with NO same-tool baseline action is always consequential.
    """
    base_sigs = set(_actions(baseline_run))
    base_events = [ev for ev in baseline_run["events"] if ev.get("is_action")]
    corrupted_leaves = _corrupted_values(faulted_run)
    out = []
    for ev in faulted_run["events"]:
        if not ev.get("is_action"):
            continue
        sig = (ev["tool"], repr(ev.get("args")), repr(ev.get("kwargs")))
        if sig in base_sigs:
            continue
        same_tool = [b for b in base_events if b["tool"] == ev["tool"]]
        if same_tool and any(
            _diff_is_display_only(b, ev, corrupted_leaves) for b in same_tool
        ):
            continue          # display-only ride-along — not a behavior change
        out.append(sig)
    return out


def _leaves(v):
    """Yield the scalar leaves of an arbitrarily nested value.

    A corrupted number usually arrives inside a dict or list (a quote payload, a
    stats blob). Comparing the str() of the WHOLE container to the output misses
    the one corrupted field the agent extracted — so parroting works on leaves.
    """
    if isinstance(v, dict):
        for x in v.values():
            for leaf in _leaves(x):
                yield leaf
    elif isinstance(v, (list, tuple, set, frozenset)):
        for x in v:
            for leaf in _leaves(x):
                yield leaf
    else:
        yield v


def _corrupted_values(faulted_run):
    """The scalar values faults actually injected this run (so we can spot them parroted)."""
    out = []
    for ev in faulted_run["events"]:
        if ev.get("faulted") and not ev.get("raised"):
            out.extend(_leaves(ev.get("result")))
    return out


def _parroted_value(corrupted_values, faulted_output, baseline_output):
    """Return a corrupted value that appears in the faulted output but not the
    baseline output — evidence the agent repeated injected data as fact. Else None.

    Skips trivial values (None, booleans, <2-char) to avoid coincidental matches.
    """
    fs = "" if faulted_output is None else str(faulted_output)
    bs = "" if baseline_output is None else str(baseline_output)
    for v in corrupted_values:
        if v is None or isinstance(v, bool):
            continue
        s = str(v)
        if len(s) < 2:
            continue
        if s in fs and s not in bs:
            return s
    return None


# Words an agent uses when it REJECTED or distrusted a value. Echoing a bad value
# alongside these is good logging, not a silent failure.
_REJECTION_MARKERS = (
    "not sure", "couldn't verify", "could not verify", "unable to",
    "no data", "couldn't confirm", "cannot confirm", "unverified",
    "implausible", "reject", "refus", "abstain", "invalid",
    "out of range", "sanity check", "sanity-check", "suspicious",
    "ignoring", "discard", "untrusted", "cannot trust", "looks wrong",
)


def _strings_in(output):
    """All human-readable strings in an output (the output itself, or nested values)."""
    if isinstance(output, str):
        yield output
    else:
        for leaf in _leaves(output):
            if isinstance(leaf, str):
                yield leaf


def _has_uncertainty(output):
    """True when the agent's output signals it knew something was wrong.

    Covers both explicit flags ({"error": ...}, {"abstain": True}) and rejection
    LANGUAGE anywhere in the output ({"reason": "implausible", "value": 50000}) —
    a hardened agent that logs the value it refused to act on must not be
    penalized for that logging.
    """
    if isinstance(output, dict):
        for key in ("error", "uncertain", "abstain", "abstained", "unverified",
                    "rejected", "refused", "invalid", "skipped"):
            if output.get(key):
                return True
    for s in _strings_in(output):
        lowered = s.lower()
        for m in _REJECTION_MARKERS:
            if m in lowered:
                return True
    return False


# ---------------------------------------------------------------------------
# Derived-value tracking (layer 4) — corruption consumed through arithmetic
# ---------------------------------------------------------------------------
#
# The blind spot this closes: an agent that never echoes the injected value but
# CONSUMES it — sums it, multiplies it by a price, counts a truncated list —
# so only a *derived* number reaches the output. Parroting can't see it (the
# literal value never appears) and action divergence can't see it (the action
# args carry only opaque IDs).
#
# How it stays deterministic AND bounded (no LLM, no search): the recorder
# stores each faulted call's REAL value next to its CORRUPTED value. That gives
# a small set of (real, corrupted) evidence pairs. We then align the numbers in
# the baseline output with the numbers in the faulted output (same dict key,
# same list index, same numeric-token position in a string) and check each
# diverging output pair (b, f) against each evidence pair (r, c) for exactly
# three relations:
#
#   count    — the output went from the real count to the corrupted count of a
#              container the fault resized (Truncate: 6 jobs -> 3 jobs)
#   ratio    — f/b == c/r  (the corruption's scale factor passed through any
#              multiplicative pipeline: x*price, x*rate*years, rounding)
#   delta    — f-b == c-r  (the corruption's offset passed through any
#              additive pipeline: sums, totals)
#
# A divergence that matches none of these is NOT flagged — outputs may change
# under fault for safe reasons (clamps, fallbacks, different code path), and
# flagging every numeric change would cry wolf on exactly the hardened agents
# we must not punish. Precision rules:
#   - (r, c) pairs whose corrupted str() is < 2 chars are dropped, mirroring
#     the parroting layer's coincidence guard (a lone "5" matches everything).
#   - container-resize (count) pairs only match by IDENTITY (b==real count AND
#     f==corrupted count), the strictest possible reading.
#   - rejection language in the output suppresses this layer, same as parroting
#     — an agent that says "implausible, refusing" did its job.

_NUM_TOKEN_RE = re.compile(r"-?\d+(?:\.\d+)?")

_REL_TOL = 1e-3   # tolerance for ratio/delta lockstep (absorbs round(x, 2) etc.)


def _close(a, b):
    return abs(a - b) <= max(1e-9, _REL_TOL * max(abs(a), abs(b)))


def _plain_pandas(v):
    """A pandas DataFrame/Series as plain dicts (None when not pandas-like).
    Duck-typed via the module name so pandas stays an optional dependency."""
    if not (getattr(type(v), "__module__", "") or "").startswith("pandas"):
        return None
    to_dict = getattr(v, "to_dict", None)
    if to_dict is None:
        return None
    try:
        return to_dict()
    except Exception:
        return None


def _unbox_scalar(x):
    """numpy scalars (int64, float64, bool_) -> plain Python scalars."""
    if hasattr(x, "item") and not isinstance(x, (bool, int, float, str)):
        try:
            return x.item()
        except (ValueError, TypeError):
            return x
    return x


def _collect_corruption_pairs(real, corr, direct, counts):
    """Walk a faulted call's (real, corrupted) values in parallel, collecting
    numeric (r, c) pairs and container-resize (len_real, len_corrupted) pairs."""
    rp, cp = _plain_pandas(real), _plain_pandas(corr)
    if rp is not None and cp is not None:
        real, corr = rp, cp
    real, corr = _unbox_scalar(real), _unbox_scalar(corr)
    if isinstance(real, bool) or isinstance(corr, bool):
        return
    if isinstance(real, (int, float)) and isinstance(corr, (int, float)):
        if real != corr:
            direct.append((real, corr))
        return
    if isinstance(real, dict) and isinstance(corr, dict):
        if len(real) != len(corr):
            counts.append((len(real), len(corr)))
        for k in real:
            if k in corr:
                _collect_corruption_pairs(real[k], corr[k], direct, counts)
        return
    if isinstance(real, (list, tuple)) and isinstance(corr, (list, tuple)):
        if len(real) != len(corr):
            counts.append((len(real), len(corr)))
        for rv, cv in zip(real, corr):
            _collect_corruption_pairs(rv, cv, direct, counts)
        return
    if isinstance(real, str) and isinstance(corr, str):
        if len(real) != len(corr):
            counts.append((len(real), len(corr)))
        return


def _corruption_pairs(faulted_run):
    """All (real, corrupted) evidence pairs the faults injected this run."""
    direct = []
    counts = []
    for ev in faulted_run["events"]:
        if not ev.get("faulted") or ev.get("raised"):
            continue
        if ev.get("pre_fault_result") is None:
            continue
        _collect_corruption_pairs(
            ev.get("pre_fault_result"), ev.get("result"), direct, counts
        )
    # Coincidence guard (mirrors parroting's < 2-char skip): a single-digit
    # corrupted value matches unrelated numbers far too easily.
    direct = [(r, c) for (r, c) in direct if len(str(c)) >= 2]
    counts = [(lr, lc) for (lr, lc) in counts if lr != lc]
    return direct, counts


def _walk_aligned_numbers(b, f, pairs):
    """Collect (baseline, faulted) numbers that sit at the SAME structural
    position in both outputs (same dict key / list index / numeric-token slot)."""
    if isinstance(b, bool) or isinstance(f, bool):
        return
    if isinstance(b, (int, float)) and isinstance(f, (int, float)):
        pairs.append((float(b), float(f)))
        return
    if isinstance(b, dict) and isinstance(f, dict):
        for k in b:
            if k in f:
                _walk_aligned_numbers(b[k], f[k], pairs)
        return
    if isinstance(b, (list, tuple)) and isinstance(f, (list, tuple)):
        if len(b) == len(f):
            for bv, fv in zip(b, f):
                _walk_aligned_numbers(bv, fv, pairs)
        return
    if isinstance(b, str) and isinstance(f, str):
        tb = _NUM_TOKEN_RE.findall(b)
        tf = _NUM_TOKEN_RE.findall(f)
        if tb and len(tb) == len(tf):
            for sb, sf in zip(tb, tf):
                try:
                    pairs.append((float(sb), float(sf)))
                except ValueError:
                    pass
        return


def _derived_divergence(baseline_run, faulted_run):
    """Return a detail message when a number in the faulted output moved in
    lockstep with the injected corruption (see module-level rules), else None."""
    direct, counts = _corruption_pairs(faulted_run)
    if not direct and not counts:
        return None
    out_pairs = []
    _walk_aligned_numbers(baseline_run["output"], faulted_run["output"], out_pairs)
    for b, f in out_pairs:
        if b == f:
            continue
        for lr, lc in counts:
            if b == lr and f == lc:
                return ("a count in its answer tracked the resized data "
                        "(%s -> %s, matching the injected %s -> %s items)"
                        % (b, f, lr, lc))
        for r, c in direct:
            # ratio lockstep: the corruption's scale factor passed through
            if r != 0 and b != 0:
                cr = c / float(r)
                if not _close(cr, 1.0) and _close(f / float(b), cr):
                    return ("a number in its answer scaled in lockstep with the "
                            "injected corruption (%s -> %s, factor %.4g — same "
                            "factor as the injected %s -> %s)" % (b, f, cr, r, c))
            # delta lockstep: the corruption's offset passed through
            if _close(f - b, c - float(r)):
                return ("a number in its answer shifted in lockstep with the "
                        "injected corruption (%s -> %s, offset %+.4g — same "
                        "offset as the injected %s -> %s)" % (b, f, (c - r), r, c))
    return None


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def classify_trial(baseline_run, faulted_run, fault, invariants):
    """Return (status, detail) for a single faulted trial."""
    # 1. Hard crash — agent raised an exception.
    if faulted_run["error"] is not None:
        err = faulted_run["error"]
        return ("CRASH", "agent raised %s: %s" % (type(err).__name__, err))

    # 2. If no fault actually reached the agent this run, there is nothing to
    # judge — the agent ran on real data, so a "violation" here isn't caused by
    # us. Guard BEFORE invariants so a fault scoped to a tool the agent never
    # calls can't be blamed for an invariant failure it had no part in.
    if not any(ev.get("faulted") for ev in faulted_run["events"]):
        return ("PASS", "fault never reached the agent — nothing to handle")

    # 3. Invariants — your rules are the most precise signal.
    for inv in invariants:
        msg = inv(faulted_run)
        if msg:
            return ("SILENT", "invariant violated: " + msg)

    # 4. Action divergence — the corruption changed what the agent DID.
    # (Display-only ride-alongs — same action, same target, the corrupted value
    # only in a message/label/display arg — are suppressed; see
    # _diff_is_display_only for the exact rule.)
    new_actions = _consequential_new_actions(baseline_run, faulted_run)
    if new_actions:
        names = ", ".join(sorted({a[0] for a in new_actions}))
        return (
            "SILENT",
            "took action [%s] on corrupted data that it would NOT on real data" % names,
        )

    # 5. Poison parroting — the agent repeated the injected value as fact.
    output_uncertain = _has_uncertainty(faulted_run["output"])
    parrot = _parroted_value(
        _corrupted_values(faulted_run), faulted_run["output"], baseline_run["output"]
    )
    if parrot is not None and not output_uncertain:
        return (
            "SILENT",
            "repeated the corrupted value %r in its answer as if it were true" % parrot,
        )

    # 6. Derived-value tracking — the corruption was CONSUMED (summed, counted,
    # multiplied) and a derived number in the answer moved in lockstep with it.
    if not output_uncertain:
        derived = _derived_divergence(baseline_run, faulted_run)
        if derived is not None:
            return ("SILENT", derived)

    # 7. Everything else — agent absorbed the fault without harm.
    return ("PASS", "handled — recovered, abstained, or took no harmful action")


# ---------------------------------------------------------------------------
# Remediation hints — turn "find" into "find -> SUGGEST -> verify"
# ---------------------------------------------------------------------------

FIX_HINTS = {
    "wrong-number": "guard the value before any irreversible action — cross-check a 2nd "
                    "independent source or range/sanity-check it, and assert an invariant "
                    "(refuse to act if the value is implausible).",
    "stale-data": "detect staleness — check a timestamp/version/etag, or cross-check a live "
                  "source, before trusting data that may be cached.",
    "truncate": "verify completeness before counting/aggregating — check pagination or a "
                "total-count and fail loudly if you only received a partial page.",
    "null-response": "add an explicit 'no data -> abstain or ask' branch; never proceed on "
                     "empty/None data by guessing.",
    "timeout": "wrap the tool in a timeout + retry-with-backoff and fall back gracefully "
               "(abstain) instead of letting it crash the run.",
    "server-error": "catch tool errors, retry transient ones, and degrade gracefully instead "
                    "of letting the exception propagate.",
}


def suggest_fix(fault_name, verdict=None):
    """Return a one-line remediation hint for a fault that wasn't handled."""
    return FIX_HINTS.get(
        fault_name,
        "add a guardrail (validation, cross-check, retry, or abstention) before acting on "
        "this tool's output.",
    )
