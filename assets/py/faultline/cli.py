"""faultline command-line interface.

    faultline demo                 run the built-in offline demo (no API key)
    faultline run <file.py>        chaos-test the suite in <file.py>  (alias: check)
    faultline attest <file.py>     run + write a tamper-evident faultline.report.json
    faultline verify <report.json> re-derive the hash, confirm the report is untampered
    faultline probe <file.py>      run honest edge cases    -> needs faultline_probe()
    faultline fuzz <file.py>       auto-generate edge inputs -> needs faultline_fuzz()
    faultline scenarios <file.py>  honest hard situations    -> needs faultline_scenarios()
    faultline replay <file.py>     re-run a recorded trace   -> needs faultline_replay()
    faultline mine <file.py>       learn rules from good runs -> needs faultline_mine()
    faultline version

Every testing subcommand exits non-zero when a silent failure (or crash) is
found, so any of them can gate CI. In GitHub Actions, a markdown verdict table
is written to $GITHUB_STEP_SUMMARY and counts to $GITHUB_OUTPUT automatically.

A *suite file* defines a function ``faultline_suite()`` returning a dict:

    def faultline_suite():
        return {
            "agent": my_agent,                # callable: agent(task) -> output
            "task":  {...},                   # passed to the agent unchanged
            "faults": [fl.WrongNumber(...), fl.Timeout(), ...],
            "invariants": [my_invariant],     # optional
            "trials": 5,                      # optional
        }

Exit code is non-zero if any SILENT failure (FAIL) or CRASH is found, so
``faultline run`` gates CI / a GitHub Action out of the box.
"""
from __future__ import annotations

import importlib.util
import os
import sys

from . import __version__
from .runner import check

USAGE = __doc__


def _run_suite(suite, _push=False) -> int:
    agent = suite["agent"]
    task = suite.get("task")
    faults = suite["faults"]
    invariants = suite.get("invariants")
    trials = suite.get("trials", 5)
    import time as _time
    _t0 = _time.perf_counter()
    result = check(agent, task, faults, invariants=invariants, trials=trials)
    duration_ms = int((_time.perf_counter() - _t0) * 1000)
    result.report()
    if _push or os.environ.get("FAULTLINE_TOKEN"):
        from . import report as _report
        agent_name = suite.get("name") or getattr(agent, "__name__", "agent")
        ok, msg = _report.push_from_env(result, agent=agent_name, trials=trials, duration_ms=duration_ms)
        if ok is None:
            print("\nfaultline: results not pushed (%s)." % msg)
        elif ok:
            print("\nfaultline: results pushed to your dashboard ✓")
        else:
            print("\nfaultline: push failed -> %s" % msg)
    bad = [r for r in result.rows if r["verdict"] in ("FAIL", "CRASH")]
    _ci_emit("chaos-check", len(bad), len(result.rows))
    if bad:
        print("\nfaultline: %d fault(s) not handled -> exit 1" % len(bad))
        return 1
    print("\nfaultline: all faults handled -> exit 0")
    return 0


def _run_attest(suite, out_path) -> int:
    """Run the suite (same gate semantics as run) AND write a tamper-evident
    faultline.report.json. Exit code follows the gate (non-zero on FAIL/CRASH),
    so `attest` still gates CI -- it is `run` plus a signed evidence file.
    """
    from . import attest as _attest

    agent = suite["agent"]
    task = suite.get("task")
    faults = suite["faults"]
    invariants = suite.get("invariants")
    trials = suite.get("trials", 5)
    import time as _time
    _t0 = _time.perf_counter()
    result = check(agent, task, faults, invariants=invariants, trials=trials)
    duration_ms = int((_time.perf_counter() - _t0) * 1000)
    result.report()

    agent_name = suite.get("name") or getattr(agent, "__name__", "agent")
    report = _attest.build_report(result, agent=agent_name,
                                  duration_ms=duration_ms, trials=trials)
    _attest.write_report(report, out_path)
    chash = report["attestation"]["content_hash"]
    n = len(report["body"].get("results", []))
    print("\nfaultline: wrote %s" % out_path)
    print("faultline: attested %d verdict(s) -- sha256 %s (tamper-evident, not a secret-key signature)"
          % (n, chash))

    bad = [r for r in result.rows if r["verdict"] in ("FAIL", "CRASH")]
    _ci_emit("attest", len(bad), len(result.rows), extra="content-hash `%s`" % chash)
    if bad:
        print("\nfaultline: %d fault(s) not handled -> exit 1 (report still written)" % len(bad))
        return 1
    print("\nfaultline: all faults handled -> exit 0")
    return 0


def _run_verify(report_path) -> int:
    """Load a faultline.report.json, recompute its content hash, and confirm it
    matches. Any edit (a flipped verdict, an altered number) changes the hash ->
    exit non-zero and name the mismatch. Clean report -> exit 0.
    """
    from . import attest as _attest

    if not os.path.exists(report_path):
        print("faultline: no such file: %s" % report_path, file=sys.stderr)
        return 2
    try:
        report = _attest.load_report(report_path)
    except Exception as exc:
        print("faultline: could not read %s as a report -- %s: %s"
              % (report_path, type(exc).__name__, exc), file=sys.stderr)
        return 2

    ok, msg = _attest.verify_report(report)
    if ok:
        print("faultline: %s" % msg)
        return 0
    print("faultline: VERIFY FAILED -- %s" % msg, file=sys.stderr)
    return 1


def _load_suite(path):
    if not os.path.exists(path):
        print("faultline: no such file: %s" % path, file=sys.stderr)
        return None
    spec = importlib.util.spec_from_file_location("_faultline_suite_mod", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as exc:
        print("faultline: error loading %s — %s: %s" % (path, type(exc).__name__, exc),
              file=sys.stderr)
        return None
    fn = getattr(mod, "faultline_suite", None)
    if fn is None:
        print("faultline: %s defines no faultline_suite() function" % path, file=sys.stderr)
        return None
    try:
        return fn()
    except Exception as exc:
        print("faultline: error in faultline_suite() of %s — %s: %s" % (path, type(exc).__name__, exc),
              file=sys.stderr)
        return None


def _load_fn(path, fn_name):
    """Load *fn_name*() from a suite file (same loader semantics as _load_suite)."""
    if not os.path.exists(path):
        print("faultline: no such file: %s" % path, file=sys.stderr)
        return None
    spec = importlib.util.spec_from_file_location("_faultline_mode_mod", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as exc:
        print("faultline: error loading %s — %s: %s" % (path, type(exc).__name__, exc),
              file=sys.stderr)
        return None
    fn = getattr(mod, fn_name, None)
    if fn is None:
        print("faultline: %s defines no %s() function" % (path, fn_name), file=sys.stderr)
        return None
    try:
        return fn()
    except Exception as exc:
        print("faultline: error in %s() of %s — %s: %s" % (fn_name, path, type(exc).__name__, exc),
              file=sys.stderr)
        return None


def _ci_emit(mode, breaks, total_checked, extra=None):
    """Write CI-friendly artifacts when running inside GitHub Actions.

    - $GITHUB_STEP_SUMMARY gets a small markdown verdict table.
    - $GITHUB_OUTPUT gets machine-readable counts for downstream steps.
    Silently does nothing outside CI.
    """
    verdict = "PASS" if breaks == 0 else "FAIL"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a") as f:
                f.write("\n### faultline · %s\n\n" % mode)
                f.write("| verdict | silent/broken | checked |\n|---|---|---|\n")
                f.write("| %s %s | %d | %d |\n" % ("✅" if breaks == 0 else "⚠️", verdict, breaks, total_checked))
                if extra:
                    f.write("\n%s\n" % extra)
        except Exception:
            pass
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        try:
            with open(output_path, "a") as f:
                f.write("verdict=%s\nbreaks=%d\nchecked=%d\nmode=%s\n" % (verdict, breaks, total_checked, mode))
        except Exception:
            pass


def _run_mode(mode, path):
    """Run one of the non-chaos testing modes from a suite file. Returns exit code."""
    from . import probe, fuzz, scenarios, replay, mine, load_trace

    cfg = _load_fn(path, "faultline_%s" % mode)
    if cfg is None:
        return 2
    if not isinstance(cfg, dict):
        print("faultline: faultline_%s() must return a dict of arguments" % mode, file=sys.stderr)
        return 2

    try:
        if mode == "probe":
            res = probe(cfg["fn"], cfg["cases"], cfg["properties"],
                        label=cfg.get("label", "probe"), unpack=cfg.get("unpack", True))
        elif mode == "fuzz":
            res = fuzz(cfg["fn"], cfg["base"], cfg["properties"],
                       mutators=cfg.get("mutators"), include_pairs=cfg.get("include_pairs", True),
                       label=cfg.get("label", "fuzz"), unpack=cfg.get("unpack", False))
        elif mode == "scenarios":
            res = scenarios(cfg["agent"], cfg["cases"], cfg["invariants"],
                            label=cfg.get("label", "scenarios"))
        elif mode == "replay":
            trace = cfg.get("trace")
            if isinstance(trace, str):
                trace = load_trace(trace)
            res = replay(cfg["agent"], trace, watch=cfg.get("watch"),
                         invariants=cfg.get("invariants"), label=cfg.get("label", "replay"))
        elif mode == "mine":
            spec = mine(cfg["agent"], cfg["good_tasks"], label=cfg.get("label", "mined"))
            spec.report()
            print("\nfaultline: mined %d rule(s) -> exit 0 (informational)" % len(spec.rules))
            _ci_emit("mine", 0, len(spec.rules))
            return 0
        else:
            print("faultline: unknown mode %r" % mode, file=sys.stderr)
            return 2
    except KeyError as exc:
        print("faultline: faultline_%s() is missing required key %s" % (mode, exc), file=sys.stderr)
        return 2

    res.report()
    breaks = len(res.breakers())
    checked = len(getattr(res, "rows", getattr(res, "findings", []))) or breaks
    _ci_emit(mode, breaks, checked)
    if breaks:
        print("\nfaultline: %d silent/broken finding(s) -> exit 1" % breaks)
        return 1
    print("\nfaultline: all checks held -> exit 0")
    return 0


MODE_COMMANDS = ("probe", "fuzz", "scenarios", "replay", "mine")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "help"
    if cmd == "check":          # alias: check == run
        cmd = "run"
        argv = ["run"] + argv[1:]

    if cmd in ("version", "--version", "-V"):
        print("faultline %s" % __version__)
        return 0

    if cmd == "demo":
        from .examples.quickstart import faultline_suite
        print("faultline demo -- a tiny offline agent, no API key needed\n")
        return _run_suite(faultline_suite(), _push=("--push" in argv))

    if cmd == "run":
        _push = "--push" in argv
        args = [a for a in argv[1:] if a != "--push"]
        if not args:
            print("faultline: 'run' needs a file, e.g. faultline run suite.py", file=sys.stderr)
            return 2
        suite = _load_suite(args[0])
        if suite is None:
            return 2
        return _run_suite(suite, _push=_push)

    if cmd == "attest":
        args = argv[1:]
        out_path = "faultline.report.json"
        positional = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("--out", "-o"):
                if i + 1 >= len(args):
                    print("faultline: --out needs a path", file=sys.stderr)
                    return 2
                out_path = args[i + 1]
                i += 2
                continue
            if a.startswith("--out="):
                out_path = a.split("=", 1)[1]
                i += 1
                continue
            positional.append(a)
            i += 1
        if not positional:
            print("faultline: 'attest' needs a file, e.g. faultline attest suite.py", file=sys.stderr)
            return 2
        suite = _load_suite(positional[0])
        if suite is None:
            return 2
        return _run_attest(suite, out_path)

    if cmd == "verify":
        args = argv[1:]
        if not args:
            print("faultline: 'verify' needs a report, e.g. faultline verify faultline.report.json",
                  file=sys.stderr)
            return 2
        return _run_verify(args[0])

    if cmd in MODE_COMMANDS:
        if len(argv) < 2:
            print("faultline: '%s' needs a file, e.g. faultline %s suite.py" % (cmd, cmd), file=sys.stderr)
            return 2
        return _run_mode(cmd, argv[1])

    if cmd in ("help", "-h", "--help"):
        print(USAGE)
        return 0

    print("faultline: unknown command %r\n" % cmd, file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
