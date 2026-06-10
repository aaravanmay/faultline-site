"""faultline.scenarios — Method A: test the AGENT itself on honest, hard inputs. No lying to it.

The chaos/`check` mode corrupts a tool's output and watches the agent act on the lie — which is
prone to "garbage in, garbage out" (feed an agent a wrong number and of course it does the wrong
thing). `scenarios` is the honest alternative: you give the agent a battery of *real, legal*
situations and one behavioral rule, and faultline flags any honest situation where the agent breaks
the rule. Nothing is faked — every tool returns the truth. A failure here is a genuine agent bug
(the agent ignored data it actually had), not an artifact of corrupted input.

Example rule: "never order more units than the stock the agent actually read."
The agent reads true stock from its tool; if it still orders more than it saw, that's a real defect.
"""
from __future__ import annotations

from .trace import run_once


from ._result import LoudResult


class ScenariosResult(LoudResult):
    def __init__(self, rows, label):
        self.rows = rows
        self.label = label

    def violations(self):
        return [r for r in self.rows if r["status"] == "UNSAFE"]

    def breakers(self):
        return self.violations() + self.crashes()

    def crashes(self):
        return [r for r in self.rows if r["status"] == "CRASH"]

    def safe(self):
        """True only if the agent handled every honest scenario without breaking a rule or crashing."""
        return not self.violations() and not self.crashes()

    def report(self, write=True):
        lines = [
            "",
            "faultline · scenarios report  (Method A: honest hard cases, no faults injected)",
            "=" * 66,
            "label: %s" % self.label,
            "every tool returned the TRUTH — any failure below is a real agent bug.",
            "-" * 66,
        ]
        for r in self.rows:
            mark = {"OK": "  ok  ", "UNSAFE": "UNSAFE", "CRASH": "CRASH "}.get(r["status"], r["status"])
            lines.append("%s  %-22s %s" % (mark, r["name"], r["detail"]))
        lines.append("-" * 66)
        v = self.violations()
        if v:
            lines.append("⚠ %d honest scenario(s) broke the rule — a real bug, not garbage-in." % len(v))
        else:
            lines.append("✓ agent held the rule on every honest scenario.")
        out = "\n".join(lines)
        if write:
            print(out)
        return out


def _name_task(case):
    if isinstance(case, (tuple, list)) and len(case) == 2:
        return case[0], case[1]
    if isinstance(case, dict) and "task" in case:
        return case.get("name", repr(case["task"])), case["task"]
    return repr(case), case


def scenarios(agent, cases, invariants, label="scenarios"):
    """Run *agent* on each honest case and flag any that break an invariant. No faults injected.

    cases       : list of ``(name, task)`` tuples (or ``{"name":..., "task":...}`` dicts).
    invariants  : list of ``inv(run) -> Optional[str]`` — return a message if the rule was broken.
                  The run dict exposes the real tool calls (``run["events"]``) and ``run["output"]``,
                  so a rule can compare what the agent *saw* against what it *did*.
    """
    rows = []
    for case in cases:
        name, task = _name_task(case)
        run = run_once(agent, task)
        msgs = []
        if run.get("error") is None:
            for inv in invariants:
                try:
                    m = inv(run)
                except Exception:
                    m = None
                if m:
                    msgs.append(m)
        if run.get("error") is not None:
            status, detail = "CRASH", "raised %s" % run["error"]
        elif msgs:
            status, detail = "UNSAFE", "; ".join(msgs)
        else:
            status, detail = "OK", "handled correctly"
        rows.append({"name": name, "status": status, "detail": detail})
    return ScenariosResult(rows, label)
