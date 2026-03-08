# Deep Research Job Snapshot Design

## Problem

`/job research:<id>` now shows raw research metadata, but it still makes the user infer whether the job is healthy:

- `job_last_polled_at` moves
- `job_poll_count` rises
- `job_provider_status` remains `in_progress`

That is technically enough, but not clear enough. The shell should state directly whether the latest remote check succeeded and whether the job appears to be running normally.

## Approaches

### 1. Recommended: clearer one-shot snapshot

Keep `/job research:<id>` as a one-shot command, but add plain-language liveness fields:

- remote check result
- refresh age
- next poll due
- monitor health summary

This stays lightweight and avoids turning `/job` into a live dashboard.

### 2. Inline watch mode

Have `/job research:<id>` hold the terminal and print heartbeat updates for a short window.

This is more dynamic, but it complicates the shell and is heavier than needed for the current UX goal.

### 3. Full live research dashboard

Add a persistent live panel for research jobs.

This would be the richest UX, but it is too much complexity for the current shell.

## Recommended Design

Add a few explicit fields to the research job snapshot output:

- `job_live_status`
- `job_refresh_age`
- `job_next_poll_due_in`

These fields are derived from the latest refresh attempt and the configured poll interval.

### Truthfulness rules

- If the latest remote refresh succeeded:
  - show `job_live_status: remote reachable | running normally`
- If refresh failed:
  - show `job_live_status: last remote check failed`
  - include a short error summary
- If there has never been a successful poll:
  - show `job_live_status: waiting for first successful poll`

### Scope

- no new background loop
- no watch mode
- no fake provider phases

## Implementation Notes

- keep refresh-attempt result as transient runtime metadata on the returned research record
- do not persist transient refresh errors to disk
- compute `job_next_poll_due_in` from `last_polled_at` and `poll_interval_sec`
- keep output compact and terminal-friendly

