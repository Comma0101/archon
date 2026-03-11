# Token Usage Ledger Design

**Date:** 2026-03-11
**Branch:** `token-usage-ledger`
**Status:** Approved for implementation

## Problem

Archon currently exposes token usage through `Agent.total_input_tokens` and `Agent.total_output_tokens`, then surfaces those counters through `/status`, `/cost`, per-turn stats, and exit summaries. Those counters are accurate only for the main chat agent loop. They do not represent total workflow usage across side paths such as:

- news summarization in `archon/news/summarize.py`
- Deep Research interactions in `archon/research/google_deep_research.py`
- future worker-side LLM activity
- any future auxiliary model paths

This creates a truth gap: token counts shown to the user look authoritative, but they only reflect a subset of the system.

## Goals

- Create one truthful source of token-accounting data for Archon.
- Preserve the existing lightweight session counters for compatibility and UI continuity.
- Track usage across the main chat loop and non-chat LLM paths where usage metadata is available.
- Persist usage to Archon state so restart does not erase the accounting record.
- Make token reporting explicitly distinguish `chat-session` usage from `workflow-total` usage.

## Non-Goals

- No price estimation in this slice.
- No billing dashboard or historical analytics UI.
- No attempt to infer token counts when a provider path does not expose usage metadata.
- No full worker-ecosystem accounting unless those workers already return structured usage cleanly.

## Chosen Approach

Add a small persistent usage ledger and derive truthful summaries from it.

The existing `Agent.total_input_tokens` and `Agent.total_output_tokens` counters will remain as the fast in-memory counters for the active chat agent session. The new ledger will be the durable source of truth for all tracked usage events. Reporting surfaces can then choose the right view:

- session chat counters for lightweight turn/status display
- workflow totals from the ledger for accurate accounting

This keeps the runtime simple while removing ambiguity.

## Architecture

### 1. Usage event model

Create a small usage event record with fields like:

- `event_id`
- `session_id`
- `turn_id`
- `source` (`chat`, `news`, `deep_research`, later `worker`)
- `provider`
- `model`
- `input_tokens`
- `output_tokens`
- `recorded_at`
- `meta` (optional dict for provider-specific extras)

The model should stay intentionally narrow. It is an accounting record, not a telemetry firehose.

### 2. Persistent usage store

Add a store under Archon state, likely alongside the existing state layout in `~/.local/state/archon/`. The store should support:

- append-only event recording
- summarizing totals by session
- summarizing totals by source
- lightweight filtering by date/session

JSONL is sufficient for this slice. It matches the project’s existing pragmatic state style and avoids introducing a heavier database just for token accounting.

### 3. Recorder API

Expose a small API such as:

- `record_usage_event(...)`
- `summarize_usage_for_session(...)`
- `summarize_usage_for_date(...)`

This layer should be usable from both agent execution and side subsystems like news.

### 4. Main chat path integration

The main chat loop in `archon/execution/turn_executor.py` already receives normalized `LLMResponse.input_tokens` and `LLMResponse.output_tokens`. That path should:

- keep incrementing the in-memory session counters
- also emit one ledger event per LLM response

Streaming and non-streaming paths should both use the same recorder helper.

### 5. News integration

`archon/news/summarize.py` already gets an `LLMResponse` from `llm.chat(...)`. That path should write usage events with source `news` whenever the LLM path succeeds and returns usage metadata.

This closes one of the biggest existing accounting holes without invasive architecture changes.

### 6. Deep Research integration

Deep Research is different: it uses the Google Interactions API and may or may not expose token usage in a stable way. This slice should do one of two things explicitly:

- record structured usage if the provider exposes it reliably
- otherwise record no token event and keep reporting honest

The important point is not to fake counts. If Deep Research usage is unavailable, the reporting layer should reflect that it is excluded from token totals.

### 7. Reporting surfaces

Keep `/status` compact.

Update reporting so Archon can truthfully distinguish:

- `chat_session_tokens`: current in-memory chat session only
- `workflow_session_tokens`: sum from the persisted ledger for the current session

This can be done by improving `/cost` now and deferring any command rename. The wording should explicitly say `session_chat` vs `workflow_total` so the number is not misleading.

## Data Integrity Rules

- Never estimate tokens when the provider did not return them.
- Never silently merge incompatible sources into one opaque total.
- Always preserve provider/model/source on each event.
- If a subsystem is not yet accounted for, reporting should stay explicit about what is included.

## Testing Strategy

Add focused tests for:

- usage event recording and JSONL persistence
- session/date summarization
- main agent execution recording one event per LLM response
- news summarizer usage recording
- reporting surfaces distinguishing chat-session vs workflow-total
- no bogus counts when usage metadata is unavailable

## Expected Outcome

After this slice:

- Archon will have a truthful token-accounting foundation.
- `/status` and `/cost` can stop implying that one in-memory counter pair covers the whole system.
- future cost estimation will have a reliable substrate instead of being layered onto misleading counters.
