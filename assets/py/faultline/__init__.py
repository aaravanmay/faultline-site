"""faultline — chaos engineering for AI agents.

Everyone tests whether an agent works when everything goes RIGHT.
faultline tests what happens when reality goes WRONG: a tool returns stale or
wrong data, an API times out, a server 500s. It runs your agent under each
failure and tells you where it breaks — especially where it produces a
confidently WRONG answer with no error at all (the dangerous, SILENT kind).

Quickstart
----------
    import faultline as fl

    @fl.tool                              # wrap each tool faultline may break
    def get_inventory(item): ...

    place_order = fl.wrap(_place_order, is_action=True)   # side-effects are captured, never fire

    def my_agent(task): ...               # your agent, which calls those tools

    def must_not_oversell(run):           # an invariant: return a string if violated
        out = run["output"]
        if out and out.get("decision") == "BUY":
            return "ordered out-of-stock goods"

    result = fl.check(my_agent, task, faults=[
        fl.WrongNumber(targets=["get_inventory"]),
        fl.NullResponse(targets=["get_inventory"]),
        fl.Timeout(),
    ], invariants=[must_not_oversell], trials=5)

    result.report()                       # PASS / FAIL(silent) / CRASH per fault

No oracle, no LLM-judge: a fault is FAIL only when the agent confidently changes
its answer on corrupted data with no retry and no uncertainty (a silent failure).
The legacy v0.1 oracle API lives in ``faultline.legacy``.
"""
from __future__ import annotations

# --- canonical v1 API ---
from .trace import tool, wrap, run_once       # tool/wrap record events + inject faults
from .runner import check, Result             # the harness + aggregated result
from .faults import (
    Fault, WrongNumber, StaleData, Truncate, NullResponse, Timeout, ServerError,
)
from . import legacy                          # back-compat: faultline.legacy.chaos(...)
from . import invariants                       # reusable invariants distilled from real bugs
from .probe import probe, mutations, ProbeResult
from .fuzz import fuzz, FuzzResult
from .replay import record, replay, ReplayResult, save_trace, load_trace
from .mine import mine, MinedSpec
from .scenarios import scenarios, ScenariosResult
from .fix import propose_fix
from .guard import guard, GuardBlocked, GuardRuleError   # runtime seatbelt (production counterpart to check)
from .attest import (                                     # Rung 3: tamper-evident evidence report
    build_report, write_report, load_report, verify_report, compute_hash,
)
from .adapters import instrument, instrument_langchain, instrument_llamaindex
from .invariants import (
    numeric_answer_finite, abstain_when_context_empty,
    no_poison_parroting, no_silent_shrink,
)

__version__ = "0.4.1"
__all__ = [
    "check", "run_once", "tool", "wrap", "Result",
    "Fault", "WrongNumber", "StaleData", "Truncate", "NullResponse", "Timeout", "ServerError",
    "legacy", "invariants",
    "numeric_answer_finite", "abstain_when_context_empty",
    "no_poison_parroting", "no_silent_shrink",
    "probe", "mutations", "ProbeResult",
    "fuzz", "FuzzResult",
    "record", "replay", "ReplayResult", "save_trace", "load_trace",
    "mine", "MinedSpec",
    "scenarios", "ScenariosResult",
    "propose_fix",
    "guard", "GuardBlocked", "GuardRuleError",
    "build_report", "write_report", "load_report", "verify_report", "compute_hash",
    "instrument", "instrument_langchain", "instrument_llamaindex",
]
