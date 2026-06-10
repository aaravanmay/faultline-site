"""faultline.replay — mode 4: catch a SILENT regression after a model/prompt/version change.

The other modes test one version of an agent. `replay` tests two: you **record** what the agent
does today, then after you change something (upgrade the model, tweak the prompt, bump a dep) you
**replay** the recorded task and faultline flags where a *consequential* output silently changed —
the "it passed last month, the model got bumped, now it quietly does the wrong thing" failure.

This is the test↔production loop: record real runs (or known-good runs), replay them on every
change, gate CI on silent behavior drift. It builds on the interception/recording primitive
(flightlog) — the part a competitor can't bolt on without the plumbing.
"""
from __future__ import annotations

from .trace import run_once


def record(agent, task, label="recorded-run"):
    """Run agent(task) once, capturing its tool calls + final output as a replayable trace."""
    run = run_once(agent, task)
    return {
        "label": label,
        "task": task,
        "events": run["events"],
        "output": run["output"],
        "error": run.get("error"),
    }


def _actions(events):
    # consequential actions = is_action tools, by (tool, args, kwargs) signature
    sigs = []
    for ev in events:
        if ev.get("is_action"):
            sigs.append((ev.get("tool"), repr(ev.get("args")), repr(ev.get("kwargs"))))
    return sigs


from ._result import LoudResult


class ReplayResult(LoudResult):
    def __init__(self, findings, recorded, new, label):
        self.findings = findings
        self.recorded = recorded
        self.new = new
        self.label = label

    def regressed(self):
        return bool(self.findings)

    def breakers(self):
        return list(self.findings)

    def report(self, write=True):
        lines = [
            "",
            "faultline · replay report  (mode 4: regression after a change)",
            "=" * 62,
            "label: %s" % self.label,
            "-" * 62,
        ]
        if self.findings:
            lines.append("⚠ SILENT REGRESSION(S) — behavior changed with no error:")
            for f in self.findings:
                lines.append("    %s" % f)
        else:
            lines.append("✓ no silent regression — consequential behavior is unchanged.")
        out = "\n".join(lines)
        if write:
            print(out)
        return out


def replay(agent, recorded, watch=None, invariants=None, label="replay"):
    """Re-run *agent* on the recorded task and flag silent regressions vs the recording.

    watch       : ``output -> dict`` of the consequential fields to compare (e.g. the decision,
                  the amount, the chosen action). If any watched field changed, that's a regression.
                  If None, the whole output is compared.
    invariants  : optional extra checks ``inv(old_run, new_run) -> Optional[str]``.
    """
    new = run_once(agent, recorded["task"])
    findings = []

    old_out, new_out = recorded["output"], new["output"]
    if watch is not None:
        try:
            ow, nw = watch(old_out), watch(new_out)
            for k in ow:
                if ow.get(k) != nw.get(k):
                    findings.append("decision '%s' silently changed after the update: %r -> %r" % (k, ow.get(k), nw.get(k)))
        except Exception:
            pass
    elif old_out != new_out:
        findings.append("the output silently changed after the update: %r -> %r" % (str(old_out)[:60], str(new_out)[:60]))

    old_actions, new_actions = _actions(recorded["events"]), _actions(new["events"])
    if set(new_actions) - set(old_actions):
        findings.append("the agent now takes a consequential action it did NOT take before: %s" % (sorted(set(new_actions) - set(old_actions))[:3]))

    for inv in (invariants or []):
        try:
            msg = inv(recorded, new)
        except Exception:
            msg = None
        if msg:
            findings.append(msg)

    return ReplayResult(findings, recorded, new, label)


# --- flightlog loop: persist a real run to disk so it becomes a regression test ---
import json as _json


def _jsonable(v):
    # Recurse into containers so a single non-serializable leaf (e.g. a datetime
    # buried in kwargs) is repr()'d on its own — without collapsing the whole
    # list/dict into one string and losing the structure on load.
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    try:
        _json.dumps(v)
        return v
    except (TypeError, ValueError):
        return repr(v)


def save_trace(recorded, path):
    """Save a recorded run to disk (a captured 'production' trace) — it becomes a replayable test."""
    safe = {
        "label": recorded.get("label"),
        "task": _jsonable(recorded.get("task")),
        "events": [{"tool": e.get("tool"), "is_action": e.get("is_action", False),
                    "args": _jsonable(e.get("args")), "kwargs": _jsonable(e.get("kwargs")),
                    "result": _jsonable(e.get("result"))}
                   for e in recorded.get("events", [])],
        "output": _jsonable(recorded.get("output")),
    }
    with open(path, "w") as f:
        _json.dump(safe, f, indent=2)
    return path


def load_trace(path):
    """Load a saved trace; pass it to replay(agent, trace, ...) after a model/prompt/version change."""
    with open(path) as f:
        return _json.load(f)
