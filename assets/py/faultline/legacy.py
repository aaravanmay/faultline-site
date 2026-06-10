"""faultline.legacy — the original v0.1 oracle-based API.

Kept for backward compatibility and the original smoke test. New code should use
the canonical v1 API: ``faultline.check`` + ``faultline.tool``/``wrap`` (no oracle
needed, multi-trial, captures side-effecting tools). See faultline/__init__.py.
"""
from __future__ import annotations

import contextvars
import functools

from .faults import (  # re-exported for convenience
    Fault, WrongNumber, StaleData, Truncate, NullResponse, Timeout, ServerError,
)

__all__ = [
    "tool", "chaos", "Result", "PASS", "SILENT", "CRASH",
    "Fault", "WrongNumber", "StaleData", "Truncate", "NullResponse", "Timeout", "ServerError",
]

# the fault currently armed (so decorated tools know whether to misbehave)
_active_fault = contextvars.ContextVar("faultline_active_fault", default=None)

PASS = "PASS"            # agent still got the right answer (resilient)
SILENT = "SILENT-WRONG"  # agent gave a WRONG answer with no error (dangerous)
CRASH = "CRASH"          # agent threw an unhandled exception


def tool(fn):
    """Decorator: mark a function as a tool faultline is allowed to break.

    (Legacy oracle-path decorator. For ``faultline.check`` use ``faultline.tool``.)
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        f = _active_fault.get()
        if f is not None and f.applies_to(fn.__name__):
            return f.hit(fn.__name__, args, kwargs, result)  # may corrupt or raise
        return result
    wrapper._faultline_tool = True
    return wrapper


def _run(agent, task):
    try:
        return agent(task), None
    except Exception as e:  # noqa: BLE001 — we want to catch anything the agent throws
        return None, e


def chaos(agent, task, oracle, faults):
    """Run `agent(task)` once per fault and classify how it copes.

    Returns a Result with a resilience score and a per-fault breakdown.
    """
    base_out, base_err = _run(agent, task)
    baseline_ok = base_err is None and bool(oracle(base_out))

    rows = []
    for f in faults:
        token = _active_fault.set(f)
        try:
            out, err = _run(agent, task)
        finally:
            _active_fault.reset(token)

        if err is not None:
            status = CRASH
            detail = "agent crashed — %s: %s" % (type(err).__name__, err)
        elif bool(oracle(out)):
            status = PASS
            detail = "handled it — answer still correct"
        else:
            status = SILENT
            detail = "WRONG answer, no error raised → %r" % (out,)
        rows.append((f.name, status, detail))

    return Result(baseline_ok, base_out, rows)


class Result:
    def __init__(self, baseline_ok, baseline_out, rows):
        self.baseline_ok = baseline_ok
        self.baseline_out = baseline_out
        self.rows = rows  # list of (fault_name, status, detail)

    @property
    def resilience(self):
        if not self.rows:
            return 0.0
        passes = sum(1 for _, s, _ in self.rows if s == PASS)
        return passes / len(self.rows)

    @property
    def silent_failures(self):
        return [r for r in self.rows if r[1] == SILENT]

    def report(self):
        lines = []
        lines.append("")
        lines.append("faultline · resilience report")
        lines.append("=" * 60)
        if self.baseline_ok:
            lines.append("baseline (no faults): ✓ agent is correct")
        else:
            lines.append("baseline (no faults): ✗ agent is ALREADY wrong — fix that first")
        lines.append("-" * 60)
        icon = {PASS: "✓", SILENT: "⚠", CRASH: "✗"}
        for name, status, detail in self.rows:
            lines.append("%s  %-14s %-13s %s" % (icon.get(status, "?"), name, status, detail))
        lines.append("-" * 60)
        pct = int(round(self.resilience * 100))
        passes = sum(1 for _, s, _ in self.rows if s == PASS)
        lines.append("Resilience score: %d%%  (%d/%d faults handled)" % (pct, passes, len(self.rows)))
        sf = self.silent_failures
        if sf:
            lines.append("⚠ %d SILENT failure(s): agent gave a WRONG answer with NO error." % len(sf))
            lines.append("  These are the dangerous ones — they pass every normal test.")
        out = "\n".join(lines)
        print(out)
        return out
