# Archon Lightweight Architecture Guardrails

## Purpose

Keep Archon lightweight in the way that matters:
- low runtime/dependency weight
- clear module boundaries
- file-based, inspectable state
- easy debugging/recovery

This document is a decision filter for new features and refactors. It is not a rewrite plan.

## What "Lightweight" Means (Archon-Specific)

Archon is no longer "small". It is still lightweight if it preserves:

1. **Runtime simplicity**
- Prefer stdlib and existing SDKs
- Avoid adding frameworks for orchestration, persistence, or UI

2. **Operational transparency**
- State is inspectable on disk (`.md`, `.json`, `.jsonl`)
- Errors can be debugged from logs/traces without hidden services

3. **Small explicit modules**
- Split by domain/responsibility, not by generic abstraction
- Prefer simple helper modules over inheritance hierarchies

4. **Behavior-preserving refactors first**
- Structural cleanup before new feature depth
- Tests remain the safety net

## Non-Goals

- Becoming a plugin framework first
- Adding a database/vector store by default
- Building a TUI/web dashboard before core reliability is solid
- Abstracting every provider/worker/tool behind heavy generic interfaces

## Decision Rules (Use Before Adding Features)

For any proposed change, prefer the first option that works:

1. **Can this be done in an existing module cleanly?**
- If yes, do that.

2. **If not, can it be extracted into a small domain helper module?**
- Prefer extraction over new framework abstractions.

3. **Does it require a new runtime dependency?**
- Only add if it removes significant complexity or materially improves reliability.

4. **Does it create hidden state/background services?**
- Default to file-based state + explicit commands.

5. **Does it weaken safety/observability?**
- If yes, stop and redesign.

## Architecture Guardrails

## 1. Dependencies

- **Default:** stdlib + existing provider SDKs only
- Add new runtime dependency only when all are true:
  - meaningfully reduces code complexity
  - stable/maintained
  - hard to replace with stdlib cleanly
  - not introducing a second execution model

Examples:
- Good: a small parser/helper that replaces brittle custom parsing
- Bad: adding a workflow framework for simple orchestration

## 2. State and Persistence

- **Canonical state remains file-based** under XDG paths
- Prefer:
  - `.md` for human-maintained memory
  - `.json` for structured state snapshots
  - `.jsonl` for logs/events/history

- Do not add a DB unless file-based state becomes a proven bottleneck (not just "might")

## 3. Module Boundaries

- Split by domain, not by technical pattern
- Keep public entrypoints stable (`archon/tools.py` wrapper pattern is good)
- Prefer shared helper modules before base classes

Current approved pattern examples:
- `archon/tooling/*` for tool registration domains
- `archon/workers/common.py` for shared adapter helpers
- `archon/adapters/telegram_client.py` for shared Telegram Bot API operations

## 4. Worker/Delegation System

- Keep **Archon** as policy owner (safety/approvals/session model)
- Worker adapters are execution backends, not autonomous orchestration layers
- Prefer turn-based continuation + explicit polling over live TUI complexity
- Add observability before adding deeper interactivity

## 5. Memory System

- Keep the current layered design:
  - `MEMORY.md` (startup index)
  - canonical markdown files (`projects/*`, `profiles/*`, etc.)
  - machine index (`memory_index.json`)
  - review queue (`memory_inbox.jsonl`)
- Retrieval quality matters more than memory volume
- New memory automation should default to inbox/review, not direct writes

## 6. CLI / Terminal UX

- Improve readability with simple ANSI formatting and formatting helpers
- Avoid TUI frameworks unless a clear UX problem cannot be solved simply
- Slash commands are for explicit control (`/help`, `/reset`, `/model`)
- Prefer native terminal behaviors (e.g., bracketed paste) over custom modes where possible

## Refactor Guardrails (Behavior-Preserving Mode)

When doing cleanup/refactor work:

- No behavior changes unless explicitly scoped
- Preserve stable import surfaces when possible
- Refactor in small phases with green tests after each phase
- Favor "extract + delegate" over "rewrite"
- Keep monkeypatch/test seams stable if they are already heavily used in tests

## Reliability Before Feature Depth

Prioritize in this order:

1. Correctness bugs
2. Safety classification/approval behavior
3. Retry/recovery/observability
4. Context/memory reliability
5. Maintainability refactors
6. Performance improvements
7. Feature expansion

This order keeps Archon useful while it grows.

## Soft Limits (Signals, Not Hard Rules)

These are prompts to refactor, not blockers:

- Module > ~500-700 lines -> review for domain split
- Test file > ~400-600 lines -> review for domain split
- Repeated helper logic in 3+ places -> extract shared helper
- New feature requiring multiple hidden background loops -> redesign first

## Review Checklist for New Work

Before merging a significant change, ask:

- Does this keep runtime dependencies minimal?
- Does it preserve or improve observability?
- Is state still inspectable/recoverable?
- Did we add abstraction only where duplication justified it?
- Is the public interface stable (or intentionally versioned)?
- Are tests targeted and fast for the changed area?

## Current Near-Term Priorities (Aligned with These Guardrails)

1. Finish remaining worker-tool maintainability cleanup (small splits, no behavior changes)
2. Improve context budgeting reliability (smarter than raw message count, still lightweight)
3. Unify transient retry behavior across streaming/non-streaming paths
4. Improve memory inbox apply quality (section-aware merge for canonical files)

## Decision Heuristic (Short Version)

When in doubt:
- **Extract, don't framework**
- **Persist plainly, not opaquely**
- **Observe before optimizing**
- **Stabilize before expanding**

