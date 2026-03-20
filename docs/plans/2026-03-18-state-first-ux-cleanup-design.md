# State-First UX Cleanup Design

**Date:** 2026-03-18
**Branch:** `lightweight-context-control`
**Status:** Approved for implementation

## Problem

Archon already has the core capabilities of a strong coding agent, but the operator-facing UX still makes users infer too much state.

The current confusion patterns are consistent:
- local commands can succeed while their response text still feels ambiguous or alarming
- approvals, blocked actions, and replay semantics are not always explicit enough
- CLI and Telegram mostly share capability, but not always the same wording or mental model
- tool activity logs are informative but still noisier than the default operator loop should be

The result is that Archon can be correct while still feeling uncertain. That is the wrong tradeoff for a Claude Code-style agent.

## Goals

- Make user-facing responses explain state change before they explain raw output.
- Keep CLI and Telegram aligned on command meaning, wording, and recovery paths.
- Make blocked and replayable actions feel deterministic.
- Keep the runtime lightweight and reuse the existing command, context, and approval infrastructure.
- Improve clarity without introducing a terminal-only UI layer or a new state machine.

## Non-Goals

- Building a full TUI or bottom status bar.
- Adding `rich`, curses, or other heavy rendering dependencies.
- Replacing the current activity feed architecture.
- Reworking the agent loop again beyond lightweight message shaping.
- Adding unsolicited warnings or Telegram spam.

## Chosen Approach

Use `state-first operator feedback`.

Every important operator-facing response should answer three questions in order:
- what happened
- what state changed
- what the user should do next, if anything

This should be implemented as a small shared wording/formatting layer used by local commands and approval flows, with the existing activity feed and context metrics reused underneath it.

## Architecture

### 1. Shared operator message builders

Archon currently builds stateful user-facing strings in several places:
- local shell/REPL command handlers
- interactive approval helpers
- Telegram command handling
- Telegram approval replay/status messaging

This slice should introduce one lightweight shared helper module for operator-facing state text. It should not own behavior; it should only turn known state into compact, consistent messages.

Target responsibilities:
- local command success text for `/compact`, `/new`, `/clear`, `/status`, `/context`
- blocked-action and approval status text
- short recommendations for elevated pressure
- shared labels such as `pending_compactions`, `approval_pending`, and `next_turn`

The key rule is that CLI and Telegram should not invent their own wording for the same state transition.

### 2. Normalize command responses around state transition

For the commands that change session state, the message should foreground the transition:

- `/compact`
  - say how many messages were compacted
  - show the artifact path
  - confirm that compacted context is queued for the next turn
  - avoid echoing summaries that can look like failures

- `/new` and `/clear`
  - say that chat context was cleared
  - clarify that the Archon session/process continues
  - reset visible pressure state so follow-up status commands feel truthful

- `/status`
  - stay compact
  - surface current pressure and whether Archon is waiting on something important

- `/context`
  - stay factual
  - show current history and pending compaction state
  - recommend `/compact` or `/new` only when pressure is elevated

This preserves the current command set while making the responses easier to trust.

### 3. Make blocked actions explicit and replayable

Approval UX is one of the most confusing current surfaces because several states can collapse into similar text.

The operator should always be able to tell:
- whether anything is pending approval
- what exact action is pending
- why it was blocked
- what `/approve` will replay
- whether `/approve_next` is currently armed

Telegram and CLI should share the same core approval vocabulary. Telegram can still add button affordances, but the underlying meaning should stay identical.

### 4. Tighten default activity feedback

The activity feed should show outcomes, not force the operator to parse transcripts.

Default presentation should favor:
- command or tool name
- explicit success/failure signal
- exit code when available
- short excerpt with omitted-count marker

This should build on the tool-history shaping already added in the lightweight context-control work. The operator-facing feed can stay plain text, but it should feel more intentional and less like raw passthrough.

### 5. Clarify the help and command taxonomy

`/help` should function as an operator recovery guide, not a flat command dump.

The top-level grouping should emphasize the main workflows:
- inspect state: `/status`, `/context`
- reduce pressure: `/compact`, `/new`
- resolve blocked actions: `/approvals`, `/approve`, `/approve_next`, `/deny`

Slash-command descriptions and Telegram command metadata should reinforce the same model.

### 6. Keep parity as a design constraint

Telegram is eventually a primary surface, so parity is not optional.

That does not mean every surface must render identically. It means:
- same commands when the capability is shared
- same state words
- same approval semantics
- same pressure guidance

Differences should only come from transport constraints, not from drift.

## Risks

### Risk 1: Over-formatting simple responses
Mitigation: keep the message schema compact and plain text; do not add decorative UI chrome.

### Risk 2: State wording drifts again
Mitigation: centralize wording helpers instead of scattering literals across CLI and Telegram.

### Risk 3: More words create more noise
Mitigation: make the responses structured but short, and only include next-step guidance when it is actionable.

### Risk 4: Approval UX becomes clearer in CLI but not Telegram
Mitigation: treat Telegram parity as part of the implementation scope and test it directly.

## Expected Outcome

After this slice:
- Archon should feel more deterministic even when nothing about the core agent logic changes
- users should spend less time inferring what state Archon is in
- `/compact`, `/new`, approval replay, and pressure handling should be easier to understand at a glance
- CLI and Telegram should feel like the same product, not adjacent interfaces
