"""faultline.guard — the runtime seatbelt.

`faultline run`/`check` is the *test-time* product: it deliberately BREAKS your
tools and catches silent failures before you ship. The guard is the same idea
moved into PRODUCTION — a thin, in-process seatbelt that sits in front of an
agent's consequential ACTIONS (place_order, issue_refund, send_email,
delete_record, commit) and, the moment one is about to fire on data that breaks
a rule you set, BLOCKS it (or just alerts, in shadow mode).

It is NOT fault injection. At runtime the REAL action should execute — unless a
rule says stop. The rules are plain deterministic callables; no LLM judge.

Quickstart
----------
    import faultline as fl

    place_order = fl.wrap(_place_order, is_action=True)   # the real action

    def no_oversell(action):
        # action = {"tool", "args", "kwargs"}; return a string to block.
        if action["tool"] == "place_order" and action["args"][1] > IN_STOCK:
            return "ordering more than is in stock"

    # 1) Observe first — shadow mode lets the action fire, just records hits.
    with fl.guard([no_oversell], mode="shadow", on_violation=alert) as g:
        run_my_agent()
    g.report()

    # 2) Once you trust the rules, flip to enforce — a violation is BLOCKED.
    with fl.guard([no_oversell], mode="enforce"):
        run_my_agent()        # raises fl.GuardBlocked instead of placing the order

How it composes with the test-time harness
------------------------------------------
`fl.wrap(fn, is_action=True)` already records side-effects and STUBS them while a
test Recorder is active (so `fl.check` never fires a real order). The guard uses
its OWN active context, independent of that Recorder. Under a guard (and no test
Recorder) the REAL function actually runs unless a rule blocks it — which is the
whole point at runtime. A user already using faultline does not re-wrap anything.
"""
from __future__ import annotations

import contextvars

# The guard shares trace.py's wrapper. trace.py consults this ContextVar on every
# is_action call; we keep the var THERE (not here) so the wrapper can read it
# without importing guard (which would be circular). guard.py only reads/sets it.
from .trace import _active_guard


class GuardBlocked(Exception):
    """Raised in enforce mode when a rule blocks a consequential action.

    The real side-effect never ran. Catch this where you'd handle a refused
    action (log it, surface it to a human, take a safe fallback)."""

    def __init__(self, message, action=None):
        super().__init__(message)
        self.message = message
        self.action = action      # the {"tool","args","kwargs"} dict that was blocked


class Violation(object):
    """One rule firing on one action. Plain data; printable."""

    __slots__ = ("tool", "args", "kwargs", "message", "rule", "blocked")

    def __init__(self, tool, args, kwargs, message, rule, blocked):
        self.tool = tool
        self.args = args
        self.kwargs = kwargs
        self.message = message
        self.rule = rule          # the rule's name, for the report
        self.blocked = blocked    # True only in enforce mode

    def as_dict(self):
        return {
            "tool": self.tool,
            "args": self.args,
            "kwargs": self.kwargs,
            "message": self.message,
            "rule": self.rule,
            "blocked": self.blocked,
        }

    def __repr__(self):
        verb = "BLOCKED" if self.blocked else "shadow"
        return "<Violation %s %s(%r) :: %s>" % (verb, self.tool, self.args, self.message)


def _rule_name(rule):
    return getattr(rule, "__name__", None) or repr(rule)


class guard(object):
    """A runtime seatbelt for an agent's consequential actions.

    Usable as a context manager OR a decorator::

        with fl.guard(rules, mode="enforce"): run_agent()

        @fl.guard(rules, mode="enforce")
        def run_agent(): ...

    Parameters
    ----------
    rules        : list of callables ``rule(action) -> Optional[str]`` where
                   ``action`` is ``{"tool", "args", "kwargs"}``. A returned
                   string is the violation message.
    mode         : "shadow" (default) — the action STILL fires; violations are
                   recorded and passed to ``on_violation``. The safe default:
                   observe before you block.
                   "enforce" — on a violation the action is BLOCKED: a
                   ``GuardBlocked`` is raised and the real side-effect never runs.
    on_violation : optional callable ``on_violation(violation)`` invoked for
                   every violation in BOTH modes (before the block in enforce).

    Attributes
    ----------
    violations   : list[Violation] collected while active.
    """

    def __init__(self, rules, mode="shadow", on_violation=None):
        if mode not in ("shadow", "enforce"):
            raise ValueError("mode must be 'shadow' or 'enforce', got %r" % (mode,))
        self.rules = list(rules or [])
        self.mode = mode
        self.on_violation = on_violation
        self.violations = []          # list[Violation]
        self.checked = 0              # how many actions passed THROUGH the guard
        self._token = None

    # -- activation -----------------------------------------------------------

    def __enter__(self):
        # Nest cleanly: stash whatever guard was active, restore on exit.
        self._token = _active_guard.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _active_guard.reset(self._token)
            self._token = None
        return False                  # never swallow exceptions (incl. GuardBlocked)

    def __call__(self, fn):
        """Decorator form: wrap *fn* so it runs inside this guard's scope.

        NOTE: a single guard instance accumulates violations across every call
        of the decorated fn (it is reused). Use the context-manager form when
        you want a fresh report per run.
        """
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with self:
                return fn(*args, **kwargs)

        return wrapper

    # -- the interception point (called by trace.py's wrapper) ----------------

    def _intercept_action(self, tool, args, kwargs, call_real):
        """Run every rule against this action, THEN decide whether to fire it.

        ``call_real`` is a zero-arg thunk that performs the real side-effect and
        returns its real value. We call it ONLY when nothing blocks (enforce) or
        always-after-recording (shadow). This is the inverse of the test-time
        stub: at runtime the real action is the default, not the exception.

        A rule that itself RAISES is surfaced loudly (re-raised), never swallowed
        into a false "all clear" — same no-false-green ethos as the detector.
        """
        self.checked += 1
        action = {"tool": tool, "args": list(args), "kwargs": dict(kwargs)}

        for rule in self.rules:
            try:
                msg = rule(action)
            except Exception as exc:
                # A broken rule must NOT read as "all clear". Wrap with context
                # so the operator can see which rule blew up on which action.
                raise GuardRuleError(rule, action, exc)
            if msg:
                blocked = self.mode == "enforce"
                v = Violation(tool, list(args), dict(kwargs), str(msg),
                              _rule_name(rule), blocked)
                self.violations.append(v)
                if self.on_violation is not None:
                    self.on_violation(v)
                if blocked:
                    # enforce: stop here, the real side-effect never runs.
                    raise GuardBlocked(str(msg), action=action)
                # shadow: keep checking remaining rules, then fall through and
                # let the real action fire (record-only). Don't double-record if
                # a second rule also fires — each firing is its own Violation,
                # which is what you want when triaging shadow logs.

        # No block: the REAL action executes and returns its real value.
        return call_real()

    # -- reporting ------------------------------------------------------------

    def report(self, print_it=True):
        """Human-readable summary of what the guard saw. Returns the text."""
        lines = []
        lines.append("")
        lines.append("faultline . guard report  (mode: %s)" % self.mode)
        lines.append("=" * 56)
        lines.append("actions checked: %d" % self.checked)
        if not self.violations:
            lines.append("violations: 0 - clean")
        else:
            blocked = sum(1 for v in self.violations if v.blocked)
            lines.append("violations: %d  (blocked: %d, shadow-only: %d)"
                         % (len(self.violations), blocked, len(self.violations) - blocked))
            lines.append("-" * 56)
            for v in self.violations:
                verb = "BLOCKED" if v.blocked else "ALLOWED (shadow)"
                lines.append("  %-16s %s%r" % (verb, v.tool, tuple(v.args)))
                lines.append("      rule %s :: %s" % (v.rule, v.message))
        if self.mode == "shadow" and self.violations:
            lines.append("-" * 56)
            lines.append("These fired but were ALLOWED (shadow mode). Flip to "
                         "mode='enforce' to block them.")
        lines.append("")
        text = "\n".join(lines)
        if print_it:
            print(text)
        return text


class GuardRuleError(Exception):
    """A guard rule raised while evaluating an action.

    Surfaced loudly on purpose: a rule that crashes must never be mistaken for a
    rule that passed. Mirrors the detector's no-false-green ethos."""

    def __init__(self, rule, action, cause):
        self.rule = rule
        self.action = action
        self.cause = cause
        super().__init__(
            "guard rule %s raised on action %s: %s: %s"
            % (_rule_name(rule), action.get("tool"), type(cause).__name__, cause)
        )
