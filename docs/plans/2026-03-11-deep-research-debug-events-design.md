# Deep Research Debug Events Design

**Problem**

The new live smoke tool reproduces a real integration gap quickly, but it still does not tell us where the gap lives.

Observed live behavior:

- the job starts successfully
- Archon persists only two early events
- `last_event_id` stays empty
- `latest_thought_summary` stays empty
- `stream_status` remains `interaction.status_update`

That leaves two plausible root causes:

1. Google is only sending startup/status events for this run and never sending resumable ids or thought summaries.
2. Google is sending richer payloads, but Archon's stream coercion in `archon/research/google_deep_research.py` is still missing fields.

We need targeted instrumentation at the provider-event parsing boundary.

**Goals**

- Show the raw event shape and Archon's normalized interpretation side by side.
- Keep the instrumentation off by default.
- Reuse the normal Deep Research runtime path, including the new smoke script.
- Make the output compact enough to inspect manually.

**Non-Goals**

- No permanent UI or `/job` changes.
- No new persistent debug state.
- No broad logging framework changes.
- No smoke-script-specific provider fork.

## Decision

Add env-gated debug output inside `archon/research/google_deep_research.py`, not in the smoke script.

Use a simple flag such as `ARCHON_DEEP_RESEARCH_DEBUG=1`. When set, the client prints a compact debug line for the first few streamed provider events to `stderr`.

## Approaches Considered

### 1. Recommended: debug at the client coercion boundary

Pros:

- Logs the exact place where raw provider events become Archon events
- Works for the smoke script and any normal Deep Research run
- Avoids duplicate parsing logic

Cons:

- Slightly more invasive than script-local logging

### 2. Smoke-script-only raw provider dump

Pros:

- Lower blast radius

Cons:

- Forks the code path under investigation
- Easier to debug the wrong thing

### 3. Always-on debug logging

Pros:

- Zero activation friction

Cons:

- Too noisy
- Bad default for normal runs

## Design

### Activation

Use one environment variable:

- `ARCHON_DEEP_RESEARCH_DEBUG=1`

If unset, runtime behavior remains unchanged.

### Instrumentation point

Add the debug hook in `archon/research/google_deep_research.py` around stream-event coercion, where raw provider event objects are converted into `DeepResearchStreamEvent`.

This is the narrowest place where we can inspect:

- raw event fields
- fallback extraction paths
- final normalized values

### Output format

Print one compact line per raw event to `stderr` for the first `N` events, where `N` is small and fixed, such as `10`.

Each line should include:

- raw event type
- whether `event_id` is present
- interaction id
- status
- whether `delta.type` exists
- whether text exists at:
  - `delta.text`
  - `delta.content.text`
  - `response.output_text`
  - `interaction.outputs`
- the normalized `event_type`
- the normalized `event_id`
- the normalized `delta_type`
- whether normalized text is non-empty

Example shape:

```text
[deep-research-debug] type=content.delta raw_event_id=no raw_status=in_progress raw_delta_type=thought_summary text_paths=delta.text:no,delta.content.text:yes,response.output_text:no,interaction.outputs:no normalized_event_id=evt-1 normalized_delta_type=thought_summary normalized_text=yes
```

The output should avoid dumping full research text bodies. Presence/absence and short summaries are enough.

### Safety constraints

- Cap the number of logged events.
- Print to `stderr`, not the persisted job state.
- Do not log API keys or full payload dumps.
- Do not change runtime control flow.

## Testing Strategy

Add focused unit tests in `tests/test_research.py` that:

1. set `ARCHON_DEEP_RESEARCH_DEBUG=1`
2. feed a fake stream event fixture through the existing coercion path
3. assert that debug output mentions the expected field-presence markers and normalized values
4. assert that with the flag unset, no debug output is produced

The live verification is a rerun of:

```bash
XDG_STATE_HOME=/tmp/archon-state ARCHON_DEEP_RESEARCH_DEBUG=1 python scripts/deep_research_smoke.py --prompt "..." --timeout 90
```

## Success Criteria

- We can tell from one smoke run whether Google omitted resumable ids/thought summaries or Archon failed to parse them.
- The debug output stays small, bounded, and opt-in.
- Normal Deep Research runs are unchanged when the env flag is absent.
