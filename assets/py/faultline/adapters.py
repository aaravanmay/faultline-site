"""faultline.adapters — auto-instrument framework agents so their tools are faultline-injectable
WITHOUT manually adding ``@fl.tool`` to each one (the way observability tools auto-instrument).

    import faultline as fl

    fl.instrument_langchain(my_tools)          # wraps each LangChain tool, in place
    # now fl.check / fl.run_once can inject faults into them by their tool name:
    fl.check(agent, task, faults=[fl.WrongNumber(targets=["search"])], invariants=[...])

Each tool's underlying callable is wrapped with faultline's tracer. Outside a faultline run the tools
behave 100% normally (the wrapper is transparent when no run is active). Tool names are the FRAMEWORK's
names, so a fault can target "search", "get_weather", etc. Pass ``actions=[...]`` to mark tools that
take real-world side-effects (their real function won't fire under test — a stub is used instead).
"""
from __future__ import annotations

from .trace import wrap


def _as_tool_list(x):
    """Accept a list of tools, a single tool, or an agent/executor exposing `.tools`."""
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    tools = getattr(x, "tools", None)
    if tools is not None:
        try:
            return list(tools)
        except TypeError:
            pass
    return [x]


def _tool_name(t, fallback):
    v = getattr(t, "name", None)
    if isinstance(v, str) and v:
        return v
    meta = getattr(t, "metadata", None)            # LlamaIndex: tool.metadata.name
    if meta is not None:
        v = getattr(meta, "name", None)
        if isinstance(v, str) and v:
            return v
    return fallback


def _wrap_attr(obj, attr, name, is_action):
    """Replace obj.attr with a faultline-wrapped version, if it's a plain callable. Returns True if done."""
    fn = getattr(obj, attr, None)
    if callable(fn) and not getattr(fn, "_faultline_tool", False):
        try:
            setattr(obj, attr, wrap(fn, is_action=is_action, name=name))
            return True
        except Exception:
            return False
    return False


def instrument_langchain(tools_or_agent, actions=None):
    """Auto-wrap a LangChain agent's tools (a list of tools, or an AgentExecutor with ``.tools``).

    Returns what you passed in (mutated in place). ``actions`` = tool names whose real function must
    NOT fire under test (orders, writes, deletes) — faultline stubs them instead.
    """
    actions = set(actions or [])
    wrapped = []
    for i, t in enumerate(_as_tool_list(tools_or_agent)):
        name = _tool_name(t, "tool_%d" % i)
        is_action = name in actions
        # LangChain Tool/StructuredTool → `.func` (sync) and/or `.coroutine`; BaseTool → `._run`
        ok = _wrap_attr(t, "func", name, is_action)
        ok = _wrap_attr(t, "coroutine", name, is_action) or ok
        if not ok:
            ok = _wrap_attr(t, "_run", name, is_action)
        if ok:
            wrapped.append(name)
    return tools_or_agent


def instrument_llamaindex(tools_or_agent, actions=None):
    """Auto-wrap LlamaIndex FunctionTools (a list, or an agent/engine exposing ``.tools``)."""
    actions = set(actions or [])
    wrapped = []
    for i, t in enumerate(_as_tool_list(tools_or_agent)):
        name = _tool_name(t, "tool_%d" % i)
        is_action = name in actions
        # LlamaIndex FunctionTool stores the callable as `.fn` (older) / `._fn`
        ok = _wrap_attr(t, "fn", name, is_action)
        if not ok:
            ok = _wrap_attr(t, "_fn", name, is_action)
        if ok:
            wrapped.append(name)
    return tools_or_agent


def instrument(tools_or_agent, actions=None):
    """Best-effort auto-instrument — tries LangChain tool shapes, then LlamaIndex. Safe to call once."""
    instrument_langchain(tools_or_agent, actions=actions)
    instrument_llamaindex(tools_or_agent, actions=actions)
    return tools_or_agent
