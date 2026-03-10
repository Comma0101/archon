# Deep Research Reliability Design

## Goal
Make streaming Deep Research jobs survive an Archon process restart by resuming persisted non-terminal research streams from local state, without reintroducing polling-first behavior or adding a separate daemon.

## Current Gap
Deep Research is already streaming-first and can resume inside an active worker thread after a stream interruption, but Archon loses the stream worker on process exit. After restart, persisted jobs remain on disk but no code restarts their stream consumers. The result is stale `in_progress` jobs that no longer advance.

## Recommended Approach
Add process-start recovery that scans persisted research jobs, identifies resumable non-terminal stream-backed jobs, and starts background resume workers for them using the existing Google streaming client and saved `last_event_id`.

This keeps the architecture simple:
- no external supervisor
- no polling fallback
- no second runtime path
- no per-command recovery hacks

## Architecture
### 1. Store-Level Recovery Entry Point
Add a recovery function in `archon/research/store.py` that:
- runs once per process
- builds the existing config-backed Google client
- scans persisted research jobs
- selects jobs that are:
  - non-terminal
  - stream-backed
  - have an `interaction_id`
  - have a resumable `last_event_id`
  - do not already have a live in-process monitor
- starts background resume workers for those jobs

### 2. Shared Resume Worker Path
Reuse the existing `consume_research_stream(...)` and resume loop semantics.

Implementation shape:
- keep `start_research_stream_job(...)` as the fresh-job entry point
- extract a helper for the resume-thread path, or add a dedicated `resume_research_stream_job(...)`
- the resume worker should:
  - call `client.resume_research_stream(interaction_id, last_event_id=...)`
  - continue the existing consume/resume loop until terminal state
  - mark the record terminal on clean failure

### 3. Agent Startup Bootstrap
Trigger recovery from `Agent.__init__`, guarded so it only starts once per process.

Why this is the right place:
- it works for the normal CLI process without special shell code
- it reuses the agent’s real hook bus, so progress/completion events can still flow to terminal/Telegram after wiring
- a once-only guard prevents duplicate recovery scans when multiple Agents are instantiated in one process

### 4. Failure Semantics
If recovery cannot resume a job, Archon should stop pretending it is alive.

Cases:
- no Google client available: leave jobs untouched and skip recovery
- missing `last_event_id`: mark as terminal error with truthful summary
- resume call raises provider error: mark as terminal error with that reason
- resumed stream ends without completion: keep the existing `stream ended before completion` terminal behavior

## Testing
1. Recovery resumes a persisted non-terminal stream job and completes it.
2. Recovery marks a non-terminal stream job with no `last_event_id` as terminal error.
3. Recovery only runs once per process even if multiple `Agent` instances are created.
4. Existing Deep Research start path continues to work.

## Non-Goals
- persistent external daemon
- provider polling fallback
- cross-process leader election
- retroactive recovery of already-corrupted old completed jobs
