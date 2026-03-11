# Deep Research Live Thoughts Design

**Problem**

Archon already persists Deep Research stream metadata, including `latest_thought_summary` and `last_event_id`, but the current runtime still has two user-visible gaps:

- `consume_research_stream(...)` in `archon/research/store.py` saves thought summaries without emitting live UX progress events, so terminal and Telegram users do not see those updates as they arrive.
- Local stream liveness reconciliation is process-local. A second Archon process can see a nonterminal stream-backed job without an in-memory monitor and incorrectly rewrite it to terminal `error` with `Research stream inactive`, even when the provider interaction is still running.

**Goals**

- Surface live Deep Research thought summaries through the existing transient terminal/Telegram activity feed.
- Keep nonterminal research jobs truthful across multiple Archon processes.
- Preserve the current streaming-first architecture, timeout behavior, and resume path.

**Non-Goals**

- No persistent multi-line research activity pane.
- No new UX event type unless the existing `ux.job_progress` shape proves insufficient.
- No provider polling redesign.
- No new persisted fields unless the existing model cannot express the fix.

## Decision

Use the existing transient `ux.job_progress` feed for live thought summaries, and replace false terminal `stream inactive` transitions with a truthful detached stream state.

## Approaches Considered

### 1. Reuse `ux.job_progress` and add detached stream semantics

Emit progress directly from `consume_research_stream(...)` when a new `thought_summary` arrives. If a nonterminal stream-backed job loses its local monitor, keep it nonterminal and mark it detached instead of forcing an error.

Pros:

- Smallest runtime change
- Matches the current terminal activity model
- Fixes the misleading cross-process failure mode

Cons:

- Research thoughts remain transient one-line notices
- Detached state relies on formatter text rather than a new data model

### 2. Add a dedicated `research_thought` UX event kind

Pros:

- Cleaner event semantics for future UX surfaces
- Easier to extend with metadata later

Cons:

- More event surface area than the current terminal feed needs
- Still does not solve liveness truthfulness by itself

### 3. Build a persistent multi-line research activity pane

Pros:

- Best long-running observability

Cons:

- Requires a new stateful terminal renderer
- Wrong scope for the current bugfix

## Architecture

### Live thought summaries

`archon/research/store.py` will emit `ux.job_progress` during `consume_research_stream(...)` when:

- `delta_type == "thought_summary"`
- the incoming text is non-empty
- the summary text changed from the currently persisted `latest_thought_summary`

The event payload stays in the existing `job_progress(...)` shape:

- `job_kind="research"`
- `job_id=f"research:{interaction_id}"`
- `status=<latest nonterminal status>`
- `summary=<thought summary text>`

This keeps terminal and Telegram rendering unchanged because both already subscribe to `ux.job_progress`.

### Detached stream semantics

`archon/research/store.py` currently assumes that missing in-memory monitor ownership means the stream is dead. That is only true inside a single process. The reconciliation path should instead:

- preserve the existing nonterminal `status`
- preserve `latest_thought_summary`, `summary`, and `last_event_id`
- rewrite `stream_status` to `stream.detached`
- avoid forcing terminal `error` unless a real terminal failure occurred

This turns the local record into a truthful statement: Archon is not currently consuming live progress for this job, but the job itself is not proven failed.

### Recovery behavior

Existing startup recovery remains the preferred way to resume a stream-backed job:

- if `last_event_id` exists, attempt resume with the existing Google client path
- if `last_event_id` is missing, do not manufacture terminal `error`
- instead persist the job as detached and let real terminal conditions drive the final state

Real terminal conditions remain:

- provider completion
- explicit stream/runtime failure
- timeout
- explicit user cancellation

### `/job` formatting

`archon/research/formatting.py` should special-case `stream_status == "stream.detached"` so `/job research:<id>` no longer claims `stream active`.

Recommended text:

- `job_stream_status: stream.detached`
- `job_live_status: stream detached | no live consumer`

If a detached job already has a `latest_thought_summary`, that value should still be rendered via the existing `job_latest_thought_summary` line.

## Error Handling

- Normal streamed completion remains unchanged.
- Stream EOF without terminal completion remains a real error.
- Cross-process absence of a monitor is not an error by itself.
- Missing `last_event_id` is not an error by itself; it only means the job cannot be resumed from a saved event cursor.

## Testing Strategy

Add focused tests that prove the behavior change rather than just the data mutation:

1. `consume_research_stream(...)` emits a live `ux.job_progress` event for a new `thought_summary`.
2. Repeated identical summaries are deduplicated so the feed does not spam.
3. Loading or reconciling a nonterminal stream-backed job with no local monitor leaves it nonterminal and marks it detached.
4. Startup recovery leaves a missing-`last_event_id` job detached instead of terminal `error`.
5. `/job research:<id>` renders detached state truthfully and still shows the latest thought summary when present.

## Risks

- Detached jobs may remain nonterminal until timeout if they cannot be resumed and the provider offers no reliable refresh path.
- Emitting every changed thought summary could still be noisy if the provider sends frequent small rewrites.

## Mitigations

- Deduplicate by exact summary text for the first cut.
- Keep timeout enforcement as the backstop for unrecoverable detached jobs.

## Success Criteria

- Terminal and Telegram users see live thought-summary notices while Deep Research runs.
- A second Archon process does not falsely rewrite an active research job to `Research stream inactive`.
- `/job research:<id>` distinguishes detached streams from active streams.
- Existing streamed completion, resume, and timeout flows still behave as before.
