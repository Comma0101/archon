"""Probe: what events does Google actually send for deep research streams?

Starts a stream, prints every raw event field, and exits as soon as the
stream stops yielding.  Cancels the job on exit so we don't waste quota.
Should finish in <30 seconds.
"""

from __future__ import annotations

import os
import sys
import time
import threading

from archon.config import load_config
from archon.research.google_deep_research import (
    DEFAULT_DEEP_RESEARCH_AGENT,
    GoogleDeepResearchClient,
    _field,
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _find_api_key() -> str:
    key = str(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if key:
        return key
    try:
        cfg = load_config()
        llm = getattr(cfg, "llm", None)
        if str(getattr(llm, "provider", "") or "").strip().lower() == "google":
            return str(getattr(llm, "api_key", "") or "").strip()
    except Exception:
        pass
    return ""


def main() -> int:
    api_key = _find_api_key()
    if not api_key:
        _log("No API key found. Set GEMINI_API_KEY.")
        return 1

    client = GoogleDeepResearchClient.from_api_key(api_key)

    _log("Creating stream...")
    t0 = time.monotonic()

    # Raw SDK access so we see uncoerced events
    raw_stream = client._interactions.create(
        agent=client.agent,
        input="What is Arch Linux?",
        background=True,
        store=True,
        stream=True,
        agent_config={"type": "deep-research", "thinking_summaries": "auto"},
    )

    _log(f"Stream object received in {time.monotonic() - t0:.1f}s: {type(raw_stream).__name__}")
    _log(f"  dir: {[a for a in dir(raw_stream) if not a.startswith('_')]}")

    interaction_id = None
    event_num = 0

    # Watchdog: force-kill if stuck blocking in iterator
    def _watchdog():
        time.sleep(30)
        _log("\n--- WATCHDOG: 30s timeout, force exit ---")
        os._exit(2)

    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()

    _log("--- Consuming raw stream events ---")
    try:
        for raw in raw_stream:
            event_num += 1
            elapsed = time.monotonic() - t0

            event_type = str(_field(raw, "event_type") or "")
            event_id = _field(raw, "event_id")
            interaction = _field(raw, "interaction")
            iid = str(_field(raw, "interaction_id") or _field(interaction, "id") or "")
            status = str(
                _field(raw, "status")
                or _field(interaction, "status")
                or ""
            )
            delta = _field(raw, "delta")
            delta_type = str(_field(delta, "type") or "")
            delta_text = str(_field(delta, "text") or _field(_field(delta, "content"), "text") or "")
            response = _field(raw, "response")
            response_text_len = len(str(_field(response, "output_text") or ""))

            if iid:
                interaction_id = iid

            _log(
                f"[event {event_num}] +{elapsed:.1f}s\n"
                f"  event_type    = {event_type!r}\n"
                f"  event_id      = {event_id!r}\n"
                f"  status        = {status!r}\n"
                f"  interaction_id= {iid!r}\n"
                f"  delta_type    = {delta_type!r}\n"
                f"  delta_text    = {delta_text[:120]!r} ({len(delta_text)} chars)\n"
                f"  response_text = {response_text_len} chars\n"
                f"  raw_type      = {type(raw).__name__}"
            )

    except Exception as e:
        _log(f"\n--- Stream ended with exception: {type(e).__name__}: {e} ---")

    elapsed = time.monotonic() - t0
    _log(f"\n--- Stream exhausted after {event_num} events in {elapsed:.1f}s ---")

    # Cancel the job so we don't waste quota
    if interaction_id:
        _log(f"Cancelling job {interaction_id}...")
        try:
            client.cancel_research(interaction_id)
            _log("Cancelled.")
        except Exception as e:
            _log(f"Cancel failed (may already be done): {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
