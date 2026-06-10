"""faultline.mine — mode 5: invariant MINING. The tool writes its own rules.

Every other mode needs you to state the rule. `mine()` learns rules by watching the agent on a
handful of KNOWN-GOOD runs: which tools always get called, which always precede which, which
output keys are always present, which numbers stay finite. Those learned rules become enforceable
invariants — so when the agent later regresses (a model bump, a refactor, a broken tool), a rule
*nobody wrote* fires and catches it.

This is the Daikon-style "discover the spec from behavior" idea, aimed at silent agent failures.
It shifts the *finding* onto the tool (no human authoring checks) — and it compounds: more good runs
→ tighter mined spec → more regressions caught.
"""
from __future__ import annotations

from .trace import run_once


def _seq(run):
    return [ev["tool"] for ev in run.get("events", [])]


def _must_call(tool):
    def inv(run):
        if tool not in _seq(run):
            return "mined rule broken: tool '%s' was always called in good runs, but wasn't here" % tool
    inv.__doc__ = "tool '%s' is always called" % tool
    return inv


def _order_before(a, b):
    def inv(run):
        seq = _seq(run)
        if b in seq and a not in seq[: seq.index(b)]:
            return "mined rule broken: '%s' ran without '%s' before it" % (b, a)
    inv.__doc__ = "'%s' is always called before '%s'" % (a, b)
    return inv


def _has_key(key):
    def inv(run):
        out = run.get("output")
        if isinstance(out, dict) and key not in out:
            return "mined rule broken: output always had key '%s' in good runs, missing here" % key
    inv.__doc__ = "output always contains key '%s'" % key
    return inv


def _finite_field(key):
    import math
    def inv(run):
        out = run.get("output")
        if isinstance(out, dict) and isinstance(out.get(key), float) and not math.isfinite(out[key]):
            return "mined rule broken: output['%s'] was always a finite number, got %r" % (key, out[key])
    inv.__doc__ = "output['%s'] is always a finite number" % key
    return inv


class MinedSpec:
    def __init__(self, rules):
        self.rules = rules  # list of (description, invariant)

    def invariants(self):
        return [inv for _desc, inv in self.rules]

    def check(self, run):
        """Return the list of mined-rule violations for a single run."""
        msgs = []
        for _desc, inv in self.rules:
            try:
                m = inv(run)
            except Exception:
                m = None
            if m:
                msgs.append(m)
        return msgs

    def report(self, write=True):
        lines = ["", "faultline · mined spec  (mode 5: rules the tool learned by itself)",
                 "=" * 60, "learned %d rule(s) from the good runs:" % len(self.rules), "-" * 60]
        for desc, _inv in self.rules:
            lines.append("  • " + desc)
        if not self.rules:
            lines.append("  (no stable rule found — give it more/again varied good runs)")
        out = "\n".join(lines)
        if write:
            print(out)
        return out


def mine(agent, good_tasks, label="mined"):
    """Watch *agent* over several KNOWN-GOOD tasks and mine invariants that held across ALL of them."""
    runs = [run_once(agent, t) for t in good_tasks]
    runs = [r for r in runs if r.get("error") is None]
    rules = []
    if not runs:
        return MinedSpec(rules)

    seqs = [_seq(r) for r in runs]
    tool_sets = [set(s) for s in seqs]
    common_tools = set.intersection(*tool_sets) if tool_sets else set()

    # 1. tools always called
    for t in sorted(common_tools):
        rules.append((_must_call(t).__doc__, _must_call(t)))

    # 2. strict ordering: a always strictly before b (whenever both occur, in every run)
    for a in sorted(common_tools):
        for b in sorted(common_tools):
            if a != b and all(a in s and b in s and s.index(a) < s.index(b) for s in seqs):
                rules.append((_order_before(a, b).__doc__, _order_before(a, b)))

    # 3. output structure: dict keys always present
    outs = [r.get("output") for r in runs]
    if outs and all(isinstance(o, dict) for o in outs):
        common_keys = set.intersection(*[set(o) for o in outs])
        for k in sorted(common_keys):
            rules.append((_has_key(k).__doc__, _has_key(k)))
            if all(isinstance(o.get(k), float) for o in outs):
                rules.append((_finite_field(k).__doc__, _finite_field(k)))

    return MinedSpec(rules)
