# Reliability and Coherence Design

## Goal
Raise Archon's quality by tightening runtime truthfulness, decision discipline, codebase hygiene, and trustable UX before adding new capability.

## Current Gap
Archon is already broad and extensible, but the weak points are no longer about missing features. The main issues are:

- runtime surfaces occasionally disagree with actual execution paths
- native capabilities can still fall through to generic model/tool behavior
- legacy or half-migrated control-plane paths still create ambiguity
- terminal and Telegram UX are better than before but still not fully predictable
- the repo does not currently have a current `CODEBASE_CONTEXT.json` artifact at the root

These gaps reduce trust more than the lack of any specific new feature.

## Recommended Approach
Run a bounded reliability-first milestone with four workstreams:

1. Runtime truthfulness
2. Decision discipline
3. Codebase hygiene and context sync
4. Trustable UX

This is the right order because correctness problems poison UX and architecture decisions. Fixing reliability first also creates a cleaner base for any later work on smaller-model routing, deeper multi-agent behavior, or broader plugin expansion.

## Architecture
### 1. Runtime Truthfulness
Make every route, job, and execution status surface reflect what the system is actually doing.

This workstream should cover:
- worker and research job lifecycle consistency
- truthful route labels across terminal and Telegram
- stale runtime-path removal where current source and live behavior can diverge
- stronger regression coverage for high-risk flows such as news, deep research, and voice reply

The design constraint is simple: a status surface should never imply more certainty than the underlying runtime can prove.

### 2. Decision Discipline
Reduce wrong fallthroughs and wasted iterations by making intent routing stricter and more explicit.

This workstream should cover:
- native capability preference when a dedicated path exists
- fewer accidental web-search fallthroughs
- clearer separation between command handling, bounded operator tasks, research requests, and generic chat
- guardrails that keep the main model from exploring Archon's own state through indirect shell work when a first-class command or tool already exists

This does not require a full multi-model redesign. The immediate need is better routing discipline, not more model complexity.

### 3. Codebase Hygiene and Context Sync
Treat maintainability as part of reliability.

This workstream should cover:
- dead or duplicate path removal where behavior has already been replaced
- clearer ownership between control-plane, execution, adapters, and UX modules
- targeted cleanup of tests that still encode stale runtime assumptions
- regeneration of `CODEBASE_CONTEXT.json` so the repository has a current machine-readable architecture artifact again

The codebase should end this milestone simpler than it started.

### 4. Trustable UX
Polish only the UX surfaces that directly affect confidence and predictability.

This workstream should cover:
- terminal redraw and async event rendering
- slash-command predictability and discoverability
- `/jobs` and `/job` clarity
- Telegram parity where parity is intended
- voice reply consistency for long responses

This is intentionally not a visual redesign pass. The goal is trust, not flair.

## Delivery Order
### Phase 1: Reliability Baseline
Lock down the highest-risk runtime inconsistencies first, then add regression coverage.

### Phase 2: Routing and Guardrails
Tighten capability selection and reduce ambiguous turn handling.

### Phase 3: Cleanup and Context
Remove stale paths and regenerate `CODEBASE_CONTEXT.json`.

### Phase 4: UX Trust Pass
Polish prompt redraw, command predictability, and cross-surface output consistency.
In execution, this phase narrowed to the remaining trust-breaking job-command confusion because
the terminal redraw and long TTS reply fixes were already present in the base branch before this
worktree started.

## Testing Strategy
The milestone should rely on focused regression coverage rather than broad speculative refactors.

Required coverage areas:
- terminal activity feed and prompt redraw
- Telegram text and voice routing
- deep research and worker lifecycle surfaces
- slash-command and job inspection behavior
- any new context-generation logic for `CODEBASE_CONTEXT.json`

## Definition of Done
- No known stale legacy execution path remains active in the main terminal or Telegram runtime.
- Native capabilities win over generic paths in the high-frequency cases this milestone targets.
- Job, route, and voice/news flows have focused regression tests.
- `CODEBASE_CONTEXT.json` exists at repo root, validates, and reflects the current architecture.
- The touched code is simpler and more explicit than before the milestone started.

## Non-Goals
- broad new MCP or plugin expansion
- full multi-model orchestration
- architectural rewrites that are not justified by current reliability issues
- purely cosmetic UX redesign
