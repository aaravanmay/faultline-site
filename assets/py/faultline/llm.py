"""faultline.llm — optional LLM adapter so faultline can drive REAL model-backed agents.

faultline's *detection* is LLM-free and deterministic. But to TEST a real agent — one whose
behavior actually depends on a live model — you need the model. This wires in Anthropic so you can
build a real Claude-backed agent, wrap its tools with faultline, break them, and let faultline catch
the real silent failures the live model produces.

Key loading mirrors the hunt scripts: read .env if the env var is missing/empty, and drop an
inherited ANTHROPIC_BASE_URL when a real sk-ant key is present (Claude Code seeds an empty key + a
base url that would otherwise hijack the call).
"""
from __future__ import annotations

import os

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_client = None
_calls = 0  # how many real API calls faultline has made (visible cost meter)


def load_key(path=".env"):
    loaded = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    loaded.add(k)
                    if k and not os.environ.get(k):
                        os.environ[k] = v.strip().strip("'\"")
    except OSError:
        pass
    if "ANTHROPIC_BASE_URL" not in loaded and os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-ant-"):
        os.environ.pop("ANTHROPIC_BASE_URL", None)
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _get_client():
    global _client
    if _client is None:
        key = load_key()
        if not key:
            raise RuntimeError(
                "faultline's LLM features (propose_fix and the live-agent demos) need an Anthropic API "
                "key. Set ANTHROPIC_API_KEY in your environment — faultline uses YOUR key and you pay "
                "your own usage. The core testing modes (probe, fuzz, scenarios, check, replay, mine, "
                "and the auto-instrument adapters) need no key at all."
            )
        import anthropic  # imported lazily so faultline has no hard LLM dependency
        _client = anthropic.Anthropic()
    return _client


def claude(prompt, system=None, model=DEFAULT_MODEL, max_tokens=512, temperature=0.0):
    """One real Claude call. Returns the text. Counts toward faultline.llm.call_count()."""
    global _calls
    _calls += 1
    msg = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "You are a helpful assistant.",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def call_count():
    return _calls
