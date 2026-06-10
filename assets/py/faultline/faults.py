"""The fault library — the ways reality goes wrong for an AI agent.

Each Fault either CORRUPTS a tool's return value (silent failures — the
dangerous kind) or makes the tool FAIL outright (timeouts, server errors).
You point a fault at specific tools with `targets=[...]`, or leave it None to
hit every tool the agent calls.
"""
from __future__ import annotations


class Fault:
    """Base class. Override `hit` to transform a tool's result or raise."""
    name = "fault"

    def __init__(self, targets=None):
        # None = applies to every tool; otherwise only the named ones
        self._targets = set(targets) if targets else None

    def applies_to(self, tool_name):
        return self._targets is None or tool_name in self._targets

    def hit(self, tool_name, args, kwargs, result):
        return result

    def reset(self):
        """Reset any per-run state. The runner calls this before each trial so faults
        with memory (e.g. StaleData) don't leak state across trials of one check()."""
        pass


# ---- SILENT failures: tool says "200 OK" but the data is wrong ----

class WrongNumber(Fault):
    """Return a plausible-but-wrong number (e.g. stale inventory of 10 when
    it's really 2). The agent has no way to know — this is the silent killer."""
    name = "wrong-number"

    def __init__(self, factor=5.0, targets=None):
        super().__init__(targets)
        self.factor = factor

    def _bend(self, v):
        # bend a number to a plausible-but-wrong value; never a no-op (0 * factor == 0 would be)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        wrong = v * self.factor
        if wrong == v:                 # 0 (and any other fixed point) → use a clearly-wrong value
            wrong = self.factor
        return type(v)(wrong)

    def hit(self, tool_name, args, kwargs, result):
        return self._corrupt(result)

    def _corrupt(self, v):
        # Recurse so a wrong number is injected wherever it lives — a bare value,
        # a list of values, or numbers nested inside dicts/lists. Anything
        # non-numeric passes through untouched (so it's never a silent no-op on
        # the common list-of-numbers / nested-dict return shapes).
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return self._bend(v)
        # pandas DataFrame/Series — detected by module name so pandas stays an
        # OPTIONAL dependency: zero imports, zero cost when pandas is absent.
        if (getattr(type(v), "__module__", "") or "").startswith("pandas"):
            return self._corrupt_pandas(v)
        if isinstance(v, dict):
            return {k: self._corrupt(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self._corrupt(x) for x in v]
        if isinstance(v, tuple):
            return tuple(self._corrupt(x) for x in v)
        return v

    def _bend_cell(self, x):
        # numpy scalars (e.g. int64) are not Python ints — unbox via .item() so
        # _bend's isinstance check sees a plain int/float.
        if hasattr(x, "item"):
            try:
                x = x.item()
            except (ValueError, TypeError):
                return x
        return self._bend(x)

    def _corrupt_pandas(self, v):
        """Bend the numeric columns of a pandas DataFrame (or a numeric Series).

        - NEVER mutates the caller's object — always returns a corrupted copy.
        - Only int/uint/float dtypes are bent (dtype.kind in 'iuf'); bool,
          datetime, object and string columns pass through untouched.
        - Anything that isn't a DataFrame/Series (Index, Timestamp, ...) is
          returned unchanged.
        """
        kind = getattr(getattr(v, "dtype", None), "kind", None)
        if kind is not None:                       # Series-like
            if kind in "iuf":
                return v.map(self._bend_cell)
            return v.copy()
        if hasattr(v, "columns"):                  # DataFrame-like
            out = v.copy()
            for col in out.columns:
                if getattr(out[col].dtype, "kind", "") in "iuf":
                    out[col] = out[col].map(self._bend_cell)
            return out
        return v


class StaleData(Fault):
    """Return the FIRST value this tool ever produced on every later call —
    simulates a cache that never refreshed."""
    name = "stale-data"

    def __init__(self, targets=None):
        super().__init__(targets)
        self._seen = {}

    def hit(self, tool_name, args, kwargs, result):
        if tool_name not in self._seen:
            self._seen[tool_name] = result
            return result
        return self._seen[tool_name]

    def reset(self):
        self._seen = {}


class Truncate(Fault):
    """Return only half the data — partial/cut-off response."""
    name = "truncate"

    def hit(self, tool_name, args, kwargs, result):
        if isinstance(result, (str, list, tuple)):
            return result[:max(0, len(result) // 2)]
        if isinstance(result, dict):
            keys = list(result)[:max(0, len(result) // 2)]
            return {k: result[k] for k in keys}
        return result


class NullResponse(Fault):
    """Return None where the agent expects real data."""
    name = "null-response"

    def hit(self, tool_name, args, kwargs, result):
        return None


# ---- HARD failures: the tool blows up ----

class Timeout(Fault):
    """The tool hangs / times out."""
    name = "timeout"

    def hit(self, tool_name, args, kwargs, result):
        raise TimeoutError("%s timed out (injected by faultline)" % tool_name)


class ServerError(Fault):
    """The tool returns an HTTP error."""
    name = "server-error"

    def __init__(self, code=500, targets=None):
        super().__init__(targets)
        self.code = code

    def hit(self, tool_name, args, kwargs, result):
        raise RuntimeError("%s returned HTTP %d (injected by faultline)" % (tool_name, self.code))
