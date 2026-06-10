"""faultline.fuzz — mode 3: auto-generate edge-case inputs and DISCOVER which ones break a rule.

`probe()` needs you to LIST the edge cases. `fuzz()` generates them: it applies a library of
generic input mutators (empty, oversize, inject quote/backslash/newline, None, NaN, inf, negative,
huge, duplicate, unicode, ...) to a baseline input — singly and in pairs — runs the function on
each, and reports which inputs silently break a property you stated.

Why this matters: it shifts the *finding* from you onto the tool. You state the rule; the fuzzer
discovers the breaking input. It's seeded/exhaustive, so every finding is reproducible — re-run and
get the same breaking input (unlike asking an LLM, which is different every time).
"""
from __future__ import annotations

import math

_STRING_MUTATORS = [
    ("empty", lambda s: ""),
    ("whitespace-only", lambda s: "   "),
    ("inject-quote", lambda s: s[: len(s) // 2] + '"' + s[len(s) // 2:]),
    ("inject-backslash", lambda s: s[: len(s) // 2] + "\\" + s[len(s) // 2:]),
    ("inject-newline", lambda s: s[: len(s) // 2] + "\n" + s[len(s) // 2:]),
    ("oversize-token", lambda s: s + " " + "z" * 4000),
    ("truncate-half", lambda s: s[: max(0, len(s) // 2)]),
    ("duplicate", lambda s: s + " " + s),
    ("unicode", lambda s: s + " 日本語 \U0001d4ca"),
]
_NUMBER_MUTATORS = [
    ("zero", lambda n: 0),
    ("one", lambda n: 1),
    ("negative", lambda n: -1),
    ("fractional-1.5", lambda n: 1.5),
    ("fractional-0.5", lambda n: 0.5),
    ("huge", lambda n: 10 ** 9),
    ("nan", lambda n: float("nan")),
    ("inf", lambda n: float("inf")),
]
_LIST_MUTATORS = [
    ("empty-list", lambda x: []),
    ("single", lambda x: list(x)[:1]),
    ("duplicate-all", lambda x: list(x) + list(x)),
    ("none-element", lambda x: list(x) + [None]),
    ("huge", lambda x: list(x) * 1000),
]


def _bend_dict_numbers(d, depth=0):
    """Multiply every numeric value by 1000 (0 -> 999), recursing ONE level into
    nested dicts. Deterministic; non-numeric values pass through untouched."""
    out = {}
    for k, v in d.items():
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = v * 1000 if v else 999
        elif isinstance(v, dict) and depth < 1:
            out[k] = _bend_dict_numbers(v, depth + 1)
        else:
            out[k] = v
    return out


def _drop_first_key(d):
    out = dict(d)
    for k in d:           # insertion order is deterministic in py3.7+
        del out[k]
        break
    return out


def _none_first_key(d):
    out = dict(d)
    for k in d:
        out[k] = None
        break
    return out


_DICT_MUTATORS = [
    ("empty-dict", lambda d: {}),
    ("drop-first-key", _drop_first_key),
    ("none-first-key", _none_first_key),
    ("bend-numerics", _bend_dict_numbers),
    ("extra-unexpected-key", lambda d: dict(d, _faultline_unexpected="??")),
]


def _default_mutators(base):
    if isinstance(base, bool):
        return [("true", lambda b: True), ("false", lambda b: False)]
    if isinstance(base, str):
        return _STRING_MUTATORS
    if isinstance(base, (int, float)):
        return _NUMBER_MUTATORS
    if isinstance(base, (list, tuple)):
        return _LIST_MUTATORS
    if isinstance(base, dict):
        return _DICT_MUTATORS
    return []


from ._result import LoudResult


class FuzzResult(LoudResult):
    def __init__(self, rows, label, tried):
        self.rows = rows
        self.label = label
        self.tried = tried

    def breakers(self):
        return [r for r in self.rows if r["status"] in ("SILENT-WRONG", "CRASH")]

    def report(self, write=True):
        b = self.breakers()
        silent = [r for r in b if r["status"] == "SILENT-WRONG"]
        crashed = [r for r in b if r["status"] == "CRASH"]
        lines = [
            "",
            "faultline · fuzz report  (mode 3: auto-generated edge inputs)",
            "=" * 62,
            "label: %s" % self.label,
            "tried %d generated inputs  ->  %d silently broke the rule, %d crashed" % (
                self.tried, len(silent), len(crashed)),
            "-" * 62,
        ]
        if silent:
            lines.append("⚠ SILENT failures the fuzzer DISCOVERED (valid input, wrong output, no error):")
            for r in silent:
                lines.append("    [%s]  %s" % (r["case"], r["detail"]))
        if crashed:
            lines.append("✗ inputs that crashed (loud, but still found by the fuzzer):")
            for r in crashed[:6]:
                lines.append("    [%s]  %s" % (r["case"], r["detail"]))
        if not b:
            lines.append("No generated input broke the rule. (Try more mutators or a tighter property.)")
        out = "\n".join(lines)
        if write:
            print(out)
        return out


def fuzz(fn, base, properties, mutators=None, include_pairs=True, label="fuzz", unpack=False):
    """Generate edge-case inputs from *base*, run *fn*, and report which silently break a property.

    fn         : the function under test (the real, unpatched library function).
    base       : a representative valid input. Mutators are chosen by its type (str/number/list).
    properties : list of ``prop(inp, out, err) -> Optional[str]`` (same as probe).
    mutators   : override the auto-chosen mutator list with your own [(name, fn(base)->input), ...].
    include_pairs : also try every PAIR of mutations composed (finds bugs that need two conditions).
    """
    muts = mutators or _default_mutators(base)
    cases = [("baseline", base)]
    for name, m in muts:
        try:
            cases.append((name, m(base)))
        except Exception:
            pass
    if include_pairs:
        for i, (n1, m1) in enumerate(muts):
            for (n2, m2) in muts[i + 1:]:
                try:
                    cases.append((n1 + "+" + n2, m2(m1(base))))
                except Exception:
                    pass
    rows = []
    for cname, inp in cases:
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
            status, detail = "SILENT-WRONG", "; ".join(violations)
        elif err is not None:
            status, detail = "CRASH", "raised %s: %s" % (type(err).__name__, str(err)[:60])
        else:
            status, detail = "PASS", "ok"
        rows.append({"case": cname, "status": status, "detail": detail})
    return FuzzResult(rows, label, tried=len(cases))
