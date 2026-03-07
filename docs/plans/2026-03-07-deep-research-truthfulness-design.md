# Deep Research Truthfulness Design

## Goal
Make Archon truthful when a request is classified as native Google Deep Research. If native Deep Research is disabled or startup fails, Archon must say so explicitly instead of silently falling back to ordinary web/tool research while still labeling the turn as a deep-research job.

## Problem
Today `Agent._maybe_start_deep_research_job()` classifies broad research requests correctly, but if native startup throws an exception it returns `None` and the main agent loop continues as a normal tool-using turn. This produces misleading UX:
- the route can appear as `job (deep research request)`
- the assistant can verbally claim it can do deep research
- the actual execution is just iterative `web_search`/`web_read`

This breaks trust.

## Recommended Approach
Use strict truthfulness.

Behavior:
- If native Deep Research is disabled, return a direct user-facing message that native Deep Research is unavailable because it is disabled in config.
- If native Deep Research startup fails, return a direct user-facing message with the startup failure type/reason.
- Do not silently continue into the normal LLM/tool loop for requests that were explicitly routed as native deep research.
- Only emit the `hybrid_deep_research_job_v0` route payload after a real research job is started.
- Keep the existing explicit background-job path unchanged when startup succeeds.

## Scope
In scope:
- `Agent.run()` and `Agent.run_stream()` deep-research truthfulness behavior
- route/hook correctness for successful native startup
- tests covering disabled and startup-failure paths
- compact operator-facing wording for failure messages

Out of scope:
- adding automatic fallback to standard web research
- adding new `/doctor` or `/status` display fields
- changing the Google Deep Research client itself

## Expected UX
Examples:
- Disabled:
  - `Native Deep Research unavailable: disabled in config. Enable [research.google_deep_research].enabled to use research jobs.`
- Startup failure:
  - `Native Deep Research failed to start: ValueError: Missing Google API key for Deep Research`
- Success:
  - `Research job started: research:<id>`
  - `Use /jobs or /job research:<id> to inspect progress.`

## Files
- Modify: `archon/agent.py`
- Test: `tests/test_agent.py`
- Context sync: `AGENT_CONTEXT.json`

## Verification
- Focused: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests/test_agent.py -q -k "deep_research"`
- Full: `XDG_STATE_HOME=/tmp/archon-state python -m pytest tests -q`
