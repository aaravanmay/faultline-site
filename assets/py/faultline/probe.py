"""faultline.probe — the SECOND testing mode: property + metamorphic checks.

`check()` (mode 1, chaos) catches: *a tool returned BROKEN data and the agent silently went
wrong.* But many silent failures trigger on perfectly **valid, edge-case input** — an escaped
quote, an unterminated code fence, an oversized token, a degenerate config — with no broken
tool at all. `probe()` (mode 2) catches those.

Same philosophy, mirror-image mechanism:

    check():  faults   (corrupt a tool's RETURN)  + invariants  ->  silent-wrong on bad data
    probe():  mutators (transform a valid INPUT)  + properties  ->  silent-wrong on edge input

You give a baseline input, a few mutators that turn it into edge cases, and properties that must
hold no matter what. probe runs them all and flags any case where the function silently violates a
property (no exception). Deterministic, no LLM-judge — just like check().
"""
from __future__ import annotations


def mutations(base, *mutators):
    """Build probe cases: the baseline input plus one mutated input per mutator.

    Each mutator is a ``(name, fn)`` pair where ``fn(base) -> mutated_input``. Mutators are the
    edge-case analog of faults: general transforms ("inject a quote", "unterminate a fence",
    "oversize a token", "degenerate the config") that turn valid input into a boundary case.
    """
    cases = [("baseline", base)]
    for name, fn in mutators:
        cases.append((name, fn(base)))
    return cases


from ._result import LoudResult


class ProbeResult(LoudResult):
    def __init__(self, rows, label):
        self.rows = rows
        self.label = label

    def silent(self):
        return [r for r in self.rows if r["status"] == "SILENT-WRONG"]

    def breakers(self):
        return [r for r in self.rows if r["status"] in ("SILENT-WRONG", "CRASH")]

    def report(self, write=True):
        lines = [
            "",
            "faultline · probe report  (mode 2: property / metamorphic)",
            "=" * 62,
            "label: " + self.label,
            "-" * 62,
        ]
        mark = {"PASS": "✓", "SILENT-WRONG": "⚠", "CRASH": "✗"}
        for r in self.rows:
            lines.append("%s  %-26s %-13s %s" % (
                mark.get(r["status"], "?"), r["case"][:26], r["status"], r["detail"]))
        lines.append("-" * 62)
        sil = self.silent()
        if sil:
            lines.append("⚠ %d SILENT property violation(s) — valid input, wrong output, no error:" % len(sil))
            for r in sil:
                lines.append("    %s: %s" % (r["case"], r["detail"]))
        else:
            lines.append("All properties held across every probed input.")
        out = "\n".join(lines)
        if write:
            print(out)
        return out


def probe(fn, cases, properties, label="probe", unpack=True):
    """Run *fn* over each input in *cases*; flag cases that silently violate a property.

    fn         : the function under test (the real, unpatched library function).
    cases      : list of (name, input). See `mutations()` to build them from a baseline + mutators.
    properties : list of ``prop(inp, out, err) -> Optional[str]`` — return a message if violated.
                 A property that expects the fixed code to *raise* should return None when err is
                 set; on the buggy code there is no err and the output violates -> flagged.
    unpack     : if True, a tuple input is splatted as *args and a dict as **kwargs.
    """
    rows = []
    for name, inp in cases:
        err = None
        out = None
        try:
            if unpack and isinstance(inp, tuple):
                out = fn(*inp)
            elif unpack and isinstance(inp, dict):
                out = fn(**inp)
            else:
                out = fn(inp)
        except Exception as e:  # noqa: BLE001
            err = e
        violations = []
        for p in properties:
            try:
                msg = p(inp, out, err)
            except Exception as pe:  # a property that crashes is NOT a pass — surface it loudly
                violations.append("PROPERTY ERROR: %s raised %s: %s — fix your property (signature is prop(inp, out, err))"
                                  % (getattr(p, "__name__", "property"), type(pe).__name__, str(pe)[:60]))
                continue
            if msg:
                violations.append(msg)
        if violations:
            status = "SILENT-WRONG"
            detail = "; ".join(violations)
        elif err is not None:
            status = "CRASH"
            detail = "raised %s: %s" % (type(err).__name__, str(err)[:80])
        else:
            status = "PASS"
            detail = "ok"
        rows.append({"case": name, "status": status, "detail": detail})
    return ProbeResult(rows, label)
