# Deep Research Streaming-First Design

## Problem

Archon's current Deep Research integration is built around `interactions.create(... background=True)` followed by provider polling via `interactions.get(id)`. Live validation on March 8, 2026 showed that this path is not reliable with the current Google account/key combination:

- job creation succeeds
- provider polling returns `403 permission_denied`
- Google's streaming interaction path does emit live `thought_summary` progress

This means the provider capability exists, but Archon's chosen runtime architecture is wrong for the real behavior we observed.

## Decision

Archon will switch to a streaming-first Deep Research runtime.

Local persisted state becomes the source of truth for Deep Research jobs. Archon will no longer rely on provider polling as the primary or backup runtime path.

## Approaches Considered

### 1. Streaming-first, local state authoritative

Start Deep Research with `stream=True`, consume provider events in a background thread, persist progress locally, and serve `/job` and `/jobs` from local state.

Pros:
- Matches the provider behavior we verified live
- Gives real progress events instead of fake polling freshness
- Removes dependence on the failing `get()` path

Cons:
- Requires a more stateful local runtime
- Existing polling-oriented code must be simplified or removed

### 2. Stream plus polling fallback

Use streaming for progress, but retain `get()` for later refresh/recovery.

Pros:
- Keeps more provider integration surface available

Cons:
- Retains the broken path we just proved unreliable
- Adds more complexity for low practical value

### 3. Disable Deep Research

Turn Deep Research off until Google polling behavior is reliable.

Pros:
- Minimal engineering risk

Cons:
- Throws away a capability that does work through streaming

## Recommendation

Approach 1.

## Architecture

### Runtime model

`deep_research` will:
1. start a streamed interaction
2. persist the new research job record immediately
3. launch a background stream consumer thread
4. append progress/completion state to the local record as stream events arrive

The stream consumer becomes the authoritative runtime. `/job` and `/jobs` read the local record without provider polling.

### Client layer

`GoogleDeepResearchClient` will gain a streaming start API that exposes:
- interaction id as soon as available
- streamed event consumption
- normalized event/status extraction helpers

The old `get_research()` path will be removed from the live Deep Research workflow.

### Persistence model

`ResearchJobRecord` will be extended with fields needed for streaming state, likely including:
- `stream_status`
- `last_event_at`
- `event_count`
- `latest_thought_summary`

Only persisted local state determines whether a research job is active, completed, errored, or cancelled.

### UX model

Terminal and Telegram progress updates continue to flow through the existing UX event bus, but they are now emitted from streamed progress events instead of polling refreshes.

`/job research:<id>` should show:
- current local job status
n- stream status
- last event time
- elapsed time
- event count
- latest summary or output preview

### Cancellation

Cancellation remains local-first.

If provider cancellation is unavailable or unreliable, Archon marks the local job cancelled and stops surfacing it as active. Archon should not pretend the provider cancelled successfully unless that was explicitly confirmed.

## Error handling

### Startup failures

If streamed interaction creation fails, `deep_research` returns the real provider error and does not create a fake running job.

### Mid-stream failures

If the stream consumer fails after startup, Archon persists:
- terminal `error` status
- latest known summary
- the stream error text

### Interrupted Archon process

Because local state is authoritative, a process restart will not magically resume provider-side streaming. Existing jobs should be surfaced as interrupted or stale if they were left active without a live stream consumer.

This is acceptable for the first streaming-first cut. A later recovery design can be considered only if the provider offers a reliable reconnect path.

## Testing strategy

Use fake streaming clients and deterministic event fixtures.

Required coverage:
- streamed startup persists job id and starts consumer
- progress events update local state and emit UX events
- completion events finalize the record
- stream failure transitions to error
- cancellation stops local activity truthfully
- `/job` and `check_research_job` reflect local stream state without provider polling

## Success criteria

Deep Research is considered fixed when:
- a real live smoke test shows streamed progress events
- `/job` reflects stream-driven state changes
- no provider polling is required for normal operation
- Archon no longer reports fake `running normally` states from unreachable polling
