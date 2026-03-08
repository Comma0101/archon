# Deep Research Job Snapshot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `/job research:<id>` explicitly confirm whether the latest remote Deep Research check succeeded and whether the job appears healthy.

**Architecture:** Keep `/job research:<id>` as a one-shot command. Reuse the existing refresh call, attach transient refresh metadata to the returned record, and render compact liveness fields from that data plus the configured poll interval.

**Tech Stack:** Python, pytest, existing Archon research store and CLI handlers

---

### Task 1: Add failing `/job` liveness snapshot test

**Files:**
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add a test that expects `/job research:<id>` to include:
- `job_live_status`
- `job_refresh_age`
- `job_next_poll_due_in`

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -q -k research_workflow_details`

**Step 3: Write minimal implementation**

Update research refresh metadata and `/job research:<id>` formatting.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -q -k research_workflow_details`

**Step 5: Run broader verification**

Run:
- `python -m pytest tests/test_cli.py tests/test_research.py -q`
- `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`

