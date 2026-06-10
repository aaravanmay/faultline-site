"""faultline.runner — the top-level check() harness.

Runs the agent under every fault N times, aggregates trial statuses into
per-fault verdicts, and returns a Result with a human-readable report.
"""
from __future__ import annotations

import os

from .trace import run_once
from .detect import classify_trial, suggest_fix
from ._result import LoudResult


def check(agent, task, faults, invariants=None, trials: int = 5, action_tools=None):
    """Chaos-test *agent* against every fault in *faults*.

    Parameters
    ----------
    agent         : callable — agent(task) -> output
    task          : any — passed to agent unchanged
    faults        : list of Fault objects
    invariants    : list of inv(run) -> Optional[str] callables
    trials        : how many faulted runs per fault (default 5)
    action_tools  : ignored (reserved for future adapter use)

    Returns
    -------
    Result
    """
    invariants = invariants or []

    # Baseline: no fault.
    baseline = run_once(agent, task)

    rows = []
    for fault in faults:
        trial_statuses = []
        trial_details = []

        for _ in range(trials):
            fault.reset()                 # clear any per-run fault state (e.g. StaleData) between trials
            faulted_run = run_once(agent, task, fault)
            status, detail = classify_trial(baseline, faulted_run, fault, invariants)
            trial_statuses.append(status)
            trial_details.append(detail)

        # Aggregate verdict.
        silent_count = trial_statuses.count("SILENT")
        crash_count = trial_statuses.count("CRASH")
        pass_count = trial_statuses.count("PASS")

        if silent_count >= trials // 2 + 1:        # a true majority (works for even trial counts too)
            verdict = "FAIL"
        elif crash_count == trials:
            verdict = "CRASH"
        elif pass_count == trials:
            verdict = "PASS"
        else:
            verdict = "INCONCLUSIVE"

        # Keep the first notable detail (SILENT > CRASH > anything else).
        detail = ""
        for s, d in zip(trial_statuses, trial_details):
            if s in ("SILENT", "CRASH"):
                detail = d
                break
        if not detail and trial_details:
            detail = trial_details[0]

        rows.append({
            "fault": fault.name,
            "verdict": verdict,
            "detail": detail,
            "trials": trial_statuses,
        })

    return Result(baseline, rows)


class Result(LoudResult):
    """Aggregated outcome of a check() run."""

    def breakers(self):
        return [r for r in self.rows if r["verdict"] in ("FAIL", "CRASH")]

    def __init__(self, baseline: dict, rows: list) -> None:
        self.baseline = baseline
        self.rows = rows

    @property
    def silent(self) -> list:
        """Rows whose verdict is FAIL (majority-SILENT)."""
        return [r for r in self.rows if r["verdict"] == "FAIL"]

    def report(self, write_md: bool = True) -> str:
        """Build a terminal-ready report, print it, and optionally write
        tripwire-report.md in the current working directory."""
        lines: list = []
        lines.append("")
        lines.append("faultline · check report")
        lines.append("=" * 62)

        # Baseline note.
        if self.baseline["error"] is not None:
            lines.append(
                "baseline: AGENT CRASHED BEFORE ANY FAULT — %s"
                % self.baseline["error"]
            )
        else:
            lines.append("baseline: agent ran OK (no fault)")
        lines.append("-" * 62)

        total = len(self.rows)
        handled = 0

        for row in self.rows:
            verdict = row["verdict"]
            if verdict == "PASS":
                icon = "✓"   # ✓
                handled += 1
            elif verdict == "FAIL":
                icon = "⚠"   # ⚠
            elif verdict == "CRASH":
                icon = "✗"   # ✗
            else:
                icon = "?"

            tally = "[%s]" % ", ".join(row["trials"])
            lines.append(
                "%s  %-20s  %-13s  %s  %s"
                % (icon, row["fault"], verdict, tally, row["detail"])
            )

        lines.append("-" * 62)

        # Resilience summary.
        lines.append("Resilience: %d/%d faults handled" % (handled, total))

        # SILENT warning.
        silent_rows = self.silent
        if silent_rows:
            lines.append(
                "⚠ %d SILENT failure(s) — the dangerous kind:"
                % len(silent_rows)
            )
            for r in silent_rows:
                lines.append("    %s: %s" % (r["fault"], r["detail"]))

        # Suggested fixes for everything that didn't pass (find -> SUGGEST -> verify).
        bad = [r for r in self.rows if r["verdict"] in ("FAIL", "CRASH")]
        if bad:
            lines.append("")
            lines.append("Suggested fixes (then re-run to verify):")
            for r in bad:
                lines.append("  - %s: %s" % (r["fault"], suggest_fix(r["fault"], r["verdict"])))

        lines.append("")
        text = "\n".join(lines)
        print(text)

        if write_md:
            try:
                path = os.path.join(os.getcwd(), "faultline-report.md")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("```\n")
                    fh.write(text)
                    fh.write("\n```\n")
            except OSError:
                pass  # non-fatal

        return text
