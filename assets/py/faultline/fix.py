"""faultline.fix — propose a CONCRETE code fix for a caught silent failure (uses an LLM).

The detector tells you *what* broke and the *kind* of fix (a deterministic, no-LLM hint). This goes
one step further: hand it the failure + the agent's source and it asks a model for the actual patch —
the minimal corrected code, specific to your code, not generic advice. You still review it (LLM
patches can be wrong) and re-run faultline to confirm the failure is gone.

    import faultline as fl
    result = fl.check(agent, task, faults=[...], invariants=[...])
    if result.silent:
        print(fl.propose_fix(result, agent))   # concrete patch for THIS code
"""
from __future__ import annotations

import inspect

from . import llm

_SYSTEM = (
    "You are a senior engineer fixing an AI agent that has a SILENT FAILURE — it confidently does the "
    "wrong thing with no error raised. Propose the MINIMAL, concrete code change that fixes it. Show "
    "the exact corrected code (or a small diff), specific to the code given — not vague advice. One "
    "short line of why. Do not restate the problem."
)


def _as_detail(failure):
    if isinstance(failure, str):
        return failure
    rows = getattr(failure, "silent", None)          # check() Result
    if rows:
        return "\n".join("- %s: %s" % (r.get("fault"), r.get("detail")) for r in rows)
    viol = getattr(failure, "violations", None)       # ScenariosResult
    if callable(viol):
        return "\n".join("- %s: %s" % (r.get("name"), r.get("detail")) for r in viol())
    return str(failure)


def _source_of(agent, tools):
    parts = []
    for obj in [agent] + list(tools or []):
        if obj is None:
            continue
        try:
            parts.append(inspect.getsource(obj))
        except (OSError, TypeError):
            pass
    return "\n\n".join(parts) if parts else None


def propose_fix(failure, agent=None, tools=None, code=None, model=None, max_tokens=700):
    """Ask an LLM for a concrete fix to a caught silent failure.

    failure : a description string, or a faultline ``Result`` / ``ScenariosResult`` (we extract it).
    agent   : the agent callable — we read its source via ``inspect``. Add ``tools=[...]`` to include
              the tool functions' source too. Or pass ``code=`` directly (a string of the relevant code).
    Returns the model's proposed fix as text. Needs an API key (see faultline.llm).
    """
    detail = _as_detail(failure)
    src = code or _source_of(agent, tools)
    prompt = "A faultline check caught a silent failure:\n\n%s\n" % detail
    if src:
        prompt += "\nThe agent code:\n```python\n%s\n```\n" % src
    prompt += "\nPropose the minimal concrete fix: show the corrected code and one line of why."
    kwargs = {"system": _SYSTEM, "max_tokens": max_tokens}
    if model:
        kwargs["model"] = model
    return llm.claude(prompt, **kwargs)
