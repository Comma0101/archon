# Deep Research Live Workflow Design

## Problem

Archon's native Google Deep Research support can start a real background research interaction, but the user-facing workflow is still too thin:

- `/job research:<id>` often shows only `in_progress` and `Research job started`
- there is no background polling after a job starts
- there are no live progress notices in terminal or Telegram
- the shared job event path exists, but research jobs do not use it for non-terminal state changes

This makes Deep Research feel inert even when the provider interaction is still running correctly.

## Constraints

- Google Deep Research currently exposes coarse interaction states, not rich internal phases
- Archon should not invent fake provider phases
- terminal and Telegram should both see the same truthful job lifecycle
- keep runtime lightweight: no permanent daemon, no heavy polling loops

## Recommended Approach

Add an Archon-managed research monitor around the provider interaction:

1. start a lightweight background poller when a deep research job is created
2. refresh the stored record on the configured interval
3. persist refresh metadata that explains what Archon last observed
4. emit cross-surface UX events when the job changes state or when Archon records a meaningful heartbeat
5. make `/job research:<id>` render richer research-specific status instead of only the generic 5-line job summary

## Data Model

Extend the persisted research job record with Archon-owned tracking metadata:

- `last_polled_at`
- `poll_count`
- `provider_status`

Notes:

- `status` remains Archon's normalized effective status
- `provider_status` stores the latest raw provider status Archon saw
- `updated_at` continues to represent the last meaningful state change or output change
- `last_polled_at` represents the last refresh attempt that reached the provider successfully

## UX

### `/job research:<id>`

Show:

- `job_id`
- `job_kind`
- `job_status`
- `job_summary`
- `job_last_update_at`
- `provider_status`
- `job_last_polled_at`
- `job_elapsed`
- `job_poll_count`

If there is final output, include a short preview block after the metadata.

### Terminal / Telegram notices

Emit compact truthful notices only:

- research job started
- research still running
- research requires action
- research completed
- research failed

No raw tool spam and no invented provider phases.

## Hook/Event Model

Use the existing hook bus and UX event system.

Add a new typed UX event:

- `job_progress`

The terminal feed and Telegram adapter should subscribe to both:

- `ux.job_progress`
- `ux.job_completed`

## Implementation Notes

- wire the agent hook bus into research store event emission explicitly
- background monitor threads should stop once the job reaches a terminal state
- if refresh fails, keep the local record unchanged and do not emit misleading progress
- polling interval should come from `research.google_deep_research.poll_interval_sec`

## Testing

- store refresh persists `last_polled_at`, `poll_count`, and `provider_status`
- monitor emits progress event on non-terminal change and completed event on terminal change
- `/job research:<id>` shows rich research metadata
- terminal feed renders progress notices cleanly

