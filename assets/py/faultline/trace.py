"""faultline.trace — recording harness.

Wraps tool calls so a Recorder can observe (and optionally corrupt) them
without touching real side-effects for action tools.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect

_active: contextvars.ContextVar = contextvars.ContextVar("faultline_active", default=None)

# The RUNTIME guard's active context — independent of the test Recorder above.
# A test Recorder stubs is_action tools (so `fl.check` never fires a real order);
# a guard does the opposite: it lets the REAL action run unless a rule blocks it.
# guard.py reads/sets this var; the wrapper below consults it for action calls
# when no Recorder is active. Kept here (not in guard.py) so the wrapper can read
# it without importing guard — that would be a circular import.
_active_guard: contextvars.ContextVar = contextvars.ContextVar(
    "faultline_active_guard", default=None
)


class Recorder:
    """Accumulates EVENT dicts for a single agent run."""

    def __init__(self) -> None:
        self.events: list = []
        self.fault = None


def wrap(fn, is_action: bool = False, name=None):
    """Return a wrapper around *fn* that participates in fault injection.

    If no Recorder is active the wrapper is transparent — it just calls fn.
    *name* overrides the tool name faults target by (used by framework adapters so a
    LangChain/LlamaIndex tool is targeted by its framework name, not the raw fn name).
    """
    # Positional-parameter names, captured once at wrap time. They let the
    # detector reason about WHICH argument of an action diverged (e.g. a
    # display-only `message` arg vs a consequential `qty` arg). Builtins and
    # C-callables without an inspectable signature degrade to [] gracefully.
    try:
        _arg_names = [
            p.name for p in inspect.signature(fn).parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):
        _arg_names = []

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        recorder = _active.get()
        if recorder is None:
            # No test Recorder active. If a RUNTIME guard is active and this is a
            # consequential action, route it through the guard's rules: the guard
            # runs them, then fires the REAL action (shadow / no-violation) or
            # blocks it (enforce + violation). The guard only governs actions;
            # plain tools pass through untouched.
            g = _active_guard.get()
            if g is not None and is_action:
                nm = name or getattr(fn, "__name__", "tool")
                return g._intercept_action(
                    nm, args, kwargs, lambda: fn(*args, **kwargs)
                )
            # no active session — pass-through
            return fn(*args, **kwargs)

        nm = name or getattr(fn, "__name__", "tool")

        # For action tools we never invoke the real function; produce a stub.
        if is_action:
            result = {"_faultline_stub": "ok"}
        else:
            result = fn(*args, **kwargs)

        faulted = False
        fault = recorder.fault
        pre_fault_result = None

        if fault is not None and fault.applies_to(nm):
            try:
                original = result
                corrupted = fault.hit(nm, list(args), dict(kwargs), result)
                # Only count the call as faulted if the fault actually CHANGED the
                # value. StaleData's first call and WrongNumber on number-free data
                # return the real value — blaming the agent for "handling" (or
                # "parroting") a corruption that never happened produces false
                # verdicts in both directions.
                changed = corrupted is not original
                if changed:
                    try:
                        changed = bool(corrupted != original)
                    except Exception:          # ambiguous comparisons (DataFrames etc.)
                        changed = True
                result = corrupted
                faulted = changed
                if changed:
                    # Keep the REAL (pre-corruption) value alongside the corrupted
                    # one — the detector uses the (real, corrupted) pair to spot
                    # outputs DERIVED from the corruption (sums, counts, products)
                    # even when the injected value never appears verbatim.
                    pre_fault_result = original
            except Exception:
                # The fault raised — record it and re-raise.
                event = {
                    "tool": nm,
                    "args": list(args),
                    "kwargs": dict(kwargs),
                    "arg_names": _arg_names[:len(args)],
                    "faulted": True,
                    "raised": True,
                    "result": None,
                    "pre_fault_result": None,
                    "is_action": is_action,
                }
                recorder.events.append(event)
                raise

        event = {
            "tool": nm,
            "args": list(args),
            "kwargs": dict(kwargs),
            "arg_names": _arg_names[:len(args)],
            "faulted": faulted,
            "raised": False,
            "result": result,
            "pre_fault_result": pre_fault_result,
            "is_action": is_action,
        }
        recorder.events.append(event)
        return result

    wrapper._faultline_tool = True
    return wrapper


def tool(fn):
    """Decorator: mark fn as an observable, injectable tool (not an action)."""
    return wrap(fn, is_action=False)


@contextlib.contextmanager
def session(recorder: Recorder, fault=None):
    """Context manager that arms *recorder* (and optionally *fault*) for the
    duration of the block, then resets on exit regardless of exceptions."""
    recorder.fault = fault
    token = _active.set(recorder)
    try:
        yield recorder
    finally:
        _active.reset(token)
        recorder.fault = None


def run_once(agent, task, fault=None) -> dict:
    """Run *agent(task)* once under the given fault (or no fault).

    Returns a RUN dict: {"events": [...], "output": <return value or None>,
    "error": <exception or None>}.
    """
    recorder = Recorder()
    output = None
    error = None
    with session(recorder, fault):
        try:
            output = agent(task)
        except Exception as exc:  # noqa: BLE001
            error = exc
    return {"events": recorder.events, "output": output, "error": error}
