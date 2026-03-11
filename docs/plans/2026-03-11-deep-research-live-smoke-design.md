# Deep Research Live Smoke Design

**Problem**

Deep Research regressions are expensive to discover because the current feedback loop is either:

- unit tests, which are fast but do not exercise the real Google provider path
- a normal live research run, which can take 20 minutes before a bug becomes obvious

We need a minimal live smoke path that exercises the real runtime quickly and shows the exact stream/job state Archon persisted.

**Goals**

- Exercise the real Google Deep Research integration with a real API key.
- Go through Archon's existing stream-backed job path, not a separate provider-only probe.
- Finish within a short bounded window and surface partial success if live stream activity is observed.
- Print a compact final snapshot that makes stream bugs obvious immediately.

**Non-Goals**

- No replay harness.
- No offline fixture runner.
- No new long-lived daemon or background service.
- No CI requirement for live smoke.
- No broad Deep Research UX redesign.

## Decision

Add one minimal script: `scripts/deep_research_smoke.py`.

The script runs a real Deep Research request through Archon's existing runtime, subscribes to live progress notices, waits for a short timeout, and prints the final persisted job snapshot.

## Approach Options

### 1. Recommended: single live smoke script

Pros:

- Smallest scope
- Tests the real path that broke
- Useful immediately during local development

Cons:

- Requires a live API key
- Results can still vary by provider behavior

### 2. Direct provider probe

Pros:

- Even smaller script

Cons:

- Skips Archon's job store, hooks, and stream reconciliation
- Would miss exactly the bugs we care about

### 3. Live plus replay harness

Pros:

- Better long-term regression coverage

Cons:

- Too much scope for the immediate problem

## Script Behavior

CLI shape:

```bash
python scripts/deep_research_smoke.py --prompt "how to build an ai agent in 2026 based on the trend of openclaw" --timeout 90
```

Behavior:

1. Read `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
2. Build the Google Deep Research client using current Archon config defaults where available.
3. Start a real research stream using `start_research_stream_job(...)`.
4. Subscribe to `ux.job_progress` and print live one-line notices as they arrive.
5. Poll the persisted job record for a short bounded window.
6. Print a compact final snapshot:
   - `job_id`
   - `status`
   - `provider_status`
   - `stream_status`
   - `last_event_id`
   - `latest_thought_summary`
   - `event_count`
   - `poll_count`
   - `error`
7. Exit successfully if the job starts and shows real stream evidence within the timeout, even if the provider is still `in_progress`.

Real stream evidence means at least one of:

- `event_count > 0`
- non-empty `last_event_id`
- non-empty `latest_thought_summary`
- terminal job state

## Error Handling

- Missing API key: fail fast with a clear message.
- Startup failure: fail fast with the real exception text.
- Timeout without any stream evidence: fail.
- Timeout with stream evidence but still nonterminal: succeed and print the partial snapshot.

## Output Style

Keep output plain and compact:

- one line when the job starts
- one line per live `ux.job_progress` event
- one final block with the persisted snapshot

This is a developer smoke tool, not a user-facing product surface.

## Testing Strategy

Do not depend on live Google calls for automated tests.

Automated tests should cover:

- missing API key behavior
- success semantics when the job remains `in_progress` but emits live stream evidence
- failure semantics when the timeout expires with no stream evidence

The real live verification is a manual smoke run after implementation.

## Success Criteria

- A developer can run one command and know within about a minute whether Deep Research streaming is alive.
- The script surfaces `last_event_id`, `latest_thought_summary`, and stream status clearly enough to catch regressions quickly.
- The script uses the same runtime path Archon uses in production for Deep Research jobs.
