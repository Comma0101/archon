# Deep Research Live Workflow Plan

1. Add failing tests for persisted research refresh metadata and richer `/job` output.
2. Add failing tests for research progress UX event emission.
3. Extend `ResearchJobRecord` with polling metadata.
4. Update research store refresh logic to persist polling metadata and emit `ux.job_progress` / `ux.job_completed`.
5. Add a lightweight background monitor starter for new deep research jobs.
6. Wire the agent hook bus into research store events.
7. Extend terminal and Telegram UX event handling to include research progress notices.
8. Rework `/job research:<id>` formatting to show research-specific live workflow metadata.
9. Run focused tests, then the full suite, then re-check the user's job record.
