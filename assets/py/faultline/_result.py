"""Shared, hard-to-misuse verdict surface for every faultline result.

faultline exists to catch *silent* failures — so a result object must never let
you quietly read a false "all clear". Every result type mixes this in and gets
the same loud API, so any reasonable thing you reach for tells the truth:

    r.ok / r.passed     -> True ONLY if nothing broke
    r.failed            -> True if anything broke
    r.breaks / r.failures -> the list of breaks (the names people guess first)
    len(r)              -> number of breaks
    bool(r)             -> True if anything broke  (so `if r: ...` means trouble)
    r.assert_ok()       -> raise AssertionError on any break (pytest / CI style)

Each result class only has to implement ``breakers()`` returning the list of
rows that broke (silent-wrong or crash).
"""
from __future__ import annotations


class LoudResult:
    def breakers(self):  # pragma: no cover - overridden by every subclass
        raise NotImplementedError("result must implement breakers()")

    @property
    def ok(self) -> bool:
        """True only if nothing broke. The blessed positive check."""
        return not self.breakers()

    @property
    def passed(self) -> bool:
        return self.ok

    @property
    def failed(self) -> bool:
        return not self.ok

    @property
    def breaks(self):
        """The breaks — alias so a first guess (`r.breaks`) returns the truth."""
        return self.breakers()

    @property
    def failures(self):
        return self.breakers()

    def __len__(self) -> int:
        return len(self.breakers())

    def __bool__(self) -> bool:
        return bool(self.breakers())

    def assert_ok(self):
        """Raise AssertionError if anything broke — for pytest / CI gating."""
        b = self.breakers()
        if b:
            head = "; ".join(self._one_line(r) for r in b[:5])
            more = "" if len(b) <= 5 else " (+%d more)" % (len(b) - 5)
            raise AssertionError(
                "faultline: %d silent/crash failure(s) — the code did the wrong "
                "thing with no error. %s%s\nCall .report() for the full breakdown."
                % (len(b), head, more)
            )
        return self

    @staticmethod
    def _one_line(row):
        if isinstance(row, dict):
            return str(row.get("detail") or row.get("case") or row.get("fault") or row)
        if isinstance(row, (list, tuple)):
            return " ".join(str(x) for x in row)
        return str(row)

    def __repr__(self) -> str:
        n = len(self.breakers())
        return "<%s %s | %d break(s)>" % (type(self).__name__, "OK" if n == 0 else "FAIL", n)
