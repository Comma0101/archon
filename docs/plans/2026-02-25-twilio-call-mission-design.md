# Twilio Call Mission (WebSocket Voice Service) Design

**Date:** 2026-02-25  
**Status:** Proposed (design approved in chat, pending implementation)

## Goal

Enable Archon to start a conversational outbound phone call (for example, “call me and ask about my day”) by spawning a **call mission** that uses Twilio for telephony and a realtime voice runtime for conversation.

The system must:
- keep **Archon core lightweight**
- support **Telegram/terminal control UX**
- reuse proven patterns from `Documents/korami-voice-agent`
- allow Archon to **start/stop** the voice service

## Non-Goals (v1)

- Realtime voice embedded inside the main Archon REPL process
- MCP-first Twilio integration for the core calling path
- A generic “subagent framework” redesign
- Recording, transcription compliance workflows, or advanced call analytics
- Multi-call concurrency beyond a single active call mission

## Context

Archon already has:
- strong safety/approval patterns (terminal + Telegram)
- session-oriented long-running task UX (workers)
- Telegram voice in/out support
- file-based state, low dependency count, lightweight architecture guardrails

`korami-voice-agent` already implements the hardest part of conversational telephony:
- Twilio media stream WebSocket handling
- conversation manager pattern
- barge-in / clear-stream behavior
- FastAPI realtime voice runtime

The main gap is **outbound call control + generic mission orchestration**, not the media loop itself.

## Key Design Decision

Build a **same-repo, separate voice service** and let Archon control it.

- `archon/` remains the control plane (tools, approvals, mission management)
- `services/archon_voice/` becomes the realtime Twilio voice runtime (adapted from Korami)

This keeps Archon core lightweight while reusing the proven realtime pipeline.

## Approaches Considered

### Approach A: Embed Twilio/WebSocket voice runtime into Archon core (rejected)

Put FastAPI/Twilio/WebSocket/call conversation logic directly into `archon/`.

Pros:
- one process
- fewer integration boundaries

Cons:
- pollutes Archon core with realtime server complexity
- increases dependency/runtime weight in core
- mixes REPL/Telegram control plane with public webhook/media-plane logic
- harder reliability and debugging

Why rejected:
- Violates the lightweight guardrails we established.

### Approach B: Same repo, separate voice service (recommended)

Transplant/adapt reusable Korami voice runtime pieces into `services/archon_voice/` and let Archon control it via local HTTP APIs plus service lifecycle tools.

Pros:
- reuses existing realtime call pipeline patterns
- keeps core Archon lightweight
- clear separation of control plane vs media plane
- easy to evolve into systemd-managed service

Cons:
- second process to manage
- local service API contract required
- public URL/tunnel still required for Twilio callbacks/WebSocket

Why recommended:
- Best balance of speed, reuse, reliability, and architecture cleanliness.

### Approach C: MCP-first Twilio integration (defer)

Use a Twilio MCP server as the primary call integration layer.

Pros:
- broad Twilio API coverage
- standard tooling interface

Cons:
- does not solve realtime media stream runtime design
- adds surface area before core call UX/policy is stable
- weak fit for high-impact telephony safety policies

Why deferred:
- MCP can be useful later for long-tail Twilio APIs, but not as the core conversational calling architecture.

## Recommended Architecture

### 1. Archon (Control Plane)

Archon owns:
- user intent handling (terminal/Telegram)
- safety/approvals for calls (`DANGEROUS`)
- call mission lifecycle (start/status/cancel/list)
- voice service lifecycle (start/stop/status)
- mission summaries and memory capture

New subsystem:
- `archon/calls/`

Suggested modules:
- `archon/calls/models.py` — `CallMission`, `CallRun`, `CallEvent`, `CallPolicy`
- `archon/calls/store.py` — JSON/JSONL mission state and event logs
- `archon/calls/service_client.py` — local HTTP client to `archon_voice`
- `archon/calls/validate.py` — number normalization, policy checks
- `archon/calls/runner.py` — orchestration helpers for tools

### 2. `services/archon_voice` (Realtime Voice Service)

Derived from `korami-voice-agent` patterns but genericized.

Owns:
- FastAPI app
- Twilio webhook endpoints (TwiML + status callbacks)
- Twilio media stream WebSocket endpoint
- mission-scoped conversation runtime
- transcript and event logging (service-side, optionally mirrored)

### 3. Twilio (Telephony Transport)

Twilio is used for:
- outbound call creation (REST Calls API)
- fetching TwiML from the voice service
- streaming call audio via WebSocket (`<Connect><Stream>`)

## Call Mission Model (“Subagent” without framework sprawl)

The user wants Archon to “spawn a subagent” that can call and converse. The right implementation is a **call mission runner**, not a generic subagent framework rewrite.

### Call Mission (concept)

A call mission is a constrained conversational agent job:
- `goal`: “Call Comma and ask about their day”
- `target_number`: E.164 destination
- `persona`: friendly/personal assistant
- `constraints`: max duration, topic limits, safety rules
- `status`: queued/ringing/answered/active/completed/failed/cancelled

### Why not reuse coding worker adapters?

Coding workers are optimized for:
- subprocess CLI tools
- turn-based text tasks
- file/code workflows

Phone calls need:
- realtime audio loop
- telephony state transitions
- low-latency turn-taking
- public webhooks

The UX pattern (session/status/cancel) is reusable, but the runtime implementation should be separate.

## System Boundaries (Important)

### What gets reused from Korami

Reuse/adapt:
- Twilio media stream event parsing
- WebSocket handler structure
- conversation manager/barge-in pattern
- FastAPI app layout for websocket+webhook endpoints

### What does NOT get copied into Archon voice service

Remove/replace:
- restaurant/order/menu business logic
- domain-specific services/schemas
- order-related tool prompts and handlers

Add:
- generic personal-call mission prompts
- outbound call creation path
- mission-scoped config/policy and status reporting

## End-to-End Flow (Conversational Outbound Call)

1. User asks Archon:
   - “Call me and ask about my day”
2. Archon parses intent and prepares a `CallMission`
3. Archon requests approval (DANGEROUS action)
4. Archon ensures `archon_voice` service is running (`voice_service_start` or auto-start)
5. Archon submits mission to local voice service API
6. Voice service creates Twilio outbound call via REST
7. Twilio hits voice service TwiML endpoint for that mission
8. Voice service returns TwiML containing `<Connect><Stream ...>`
9. Twilio opens WebSocket to voice service media endpoint
10. Voice service runs mission conversation runtime (Korami-derived loop)
11. Voice service emits status/transcript events
12. Archon reports mission status in terminal/Telegram and stores summaries

## Twilio + WebSocket Requirements

For conversational calls, **WebSocket is required**, but not sufficient alone.

The voice service must expose:
- HTTP endpoint for TwiML (Twilio fetches instructions)
- HTTP endpoint for status callbacks (optional but recommended)
- WebSocket endpoint for media stream audio

### Public reachability

Twilio must reach the voice service over the internet, so v1 still needs one of:
- deployed public service
- tunnel (Cloudflare Tunnel, ngrok, etc.)
- public reverse proxy with TLS

This requirement exists regardless of whether Archon or the service creates the call.

## Service Lifecycle Control (Archon can start/stop it)

User requirement: Archon should have power to start and stop the voice service.

### Recommended modes

#### Mode A: `systemd --user` (default)
- `voice_service_start` calls `systemctl --user start archon-voice.service`
- `voice_service_stop` calls `systemctl --user stop archon-voice.service`
- `voice_service_status` calls `systemctl --user status/is-active`

Pros:
- reliable restarts
- logs via journald
- best for Twilio callbacks and long-running operation

#### Mode B: local subprocess (dev mode)
- Archon spawns/stops the service process directly

Pros:
- quick local iteration

Cons:
- weaker lifecycle management than systemd

### Config shape (proposed)

```toml
[calls]
enabled = true

[calls.voice_service]
mode = "systemd" # or "subprocess"
base_url = "http://127.0.0.1:8788"
auto_start = true
health_timeout_sec = 3

[calls.twilio]
account_sid = "..."
auth_token = "..."
from_number = "+1..."
public_base_url = "https://your-public-endpoint.example" # TwiML + WS endpoints
```

## Safety / Policy Design (Must Be First-Class)

Phone calls are high-impact actions. v1 policy should be strict.

### Call actions classification
- `call_mission_start` -> `DANGEROUS`
- `call_mission_cancel` -> `DANGEROUS`
- `voice_service_stop` -> likely `DANGEROUS`
- `voice_service_status`, `call_mission_status`, `call_mission_list` -> `SAFE`

### v1 guardrails
- contacts/allowlist only (initially)
- international disabled by default
- emergency/premium numbers denied
- quiet hours policy
- max call duration default (e.g. 10 minutes)
- max concurrent calls = 1
- recording off by default
- optional AI disclosure line configurable

### Approval UX

Telegram can reuse the improved request-scoped inline approval system:
- `Approve Request`
- `Allow 15m`
- `Deny`

This is especially important once calls and voice are involved.

## Archon UX Design (Terminal + Telegram)

### Terminal UX
- `call_mission_start(...)` returns immediately with:
  - `call_session_id`
  - target number (redacted or partially shown)
  - status = queued
- `call_mission_status(...)`
- `call_mission_list(...)`
- `call_mission_cancel(...)`

### Telegram UX
- start mission -> inline approval prompt (DANGEROUS)
- after approval, brief status messages:
  - `queued`
  - `ringing`
  - `answered`
  - `completed/failed`
- later enhancement: inline `Hang Up` / `Status` buttons

## Data Persistence (Lightweight)

Keep file-based state consistent with Archon’s architecture.

Suggested paths:
- `~/.local/state/archon/calls/missions/<call_session_id>.json`
- `~/.local/state/archon/calls/events/<call_session_id>.jsonl`

Archon-side records:
- mission metadata (goal, number hash/redacted, status)
- service submission result
- summarized transcript/outcome (not raw audio)

Voice service may also maintain its own local logs for realtime debugging.

## Realtime Conversation Runtime (Korami-Derived)

### v1 conversational mission runtime goals
- low-latency STT/TTS loop
- natural pause handling / barge-in
- mission prompt/persona support
- transcript accumulation
- terminal/Telegram observable status

### Call subagent constraints (important)

The conversational subagent should be constrained, unlike the main Archon agent.

Allowed initially:
- mission prompt + ephemeral conversation state
- optional memory lookup/read (future)

Disallowed initially:
- shell
- filesystem writes
- arbitrary worker delegation
- dangerous tools unrelated to the phone call

This improves safety and reduces latency surprises.

## Phased Rollout Plan (High-Level)

### Phase 0: Voice Service Lifecycle + Health
- Add Archon service control tools:
  - `voice_service_start`
  - `voice_service_stop`
  - `voice_service_status`
- Add local voice service `/health`
- No Twilio calls yet

### Phase 1: Outbound Call (Scripted, non-conversational)
- `call_mission_start` creates Twilio outbound call
- voice service returns simple TwiML (`<Say>` + `<Hangup>`) proof-of-path
- mission status tracking via callbacks or Twilio polling

This proves:
- approvals
- service lifecycle
- Twilio auth/config
- outbound call path

### Phase 2: Conversational Outbound Mission (WebSocket)
- Add `<Connect><Stream>`
- Adapt Korami media stream + conversation manager for generic missions
- “Call me and ask about my day”

### Phase 3: UX / Policy polish
- Telegram call status improvements
- better transcript summaries + memory capture
- optional hangup buttons
- stronger policy controls (contacts, schedules)

## Risks and Mitigations

### Risk: Realtime service complexity leaks into Archon core
Mitigation:
- keep voice runtime in `services/archon_voice/`
- Archon only talks to it via narrow local API

### Risk: Twilio public URL/webhook setup friction
Mitigation:
- Phase 0/1 docs and tooling include explicit health/status checks
- support local dev with tunnel and clear config

### Risk: Telephony safety/cost mistakes
Mitigation:
- strict `DANGEROUS` approval
- duration and concurrency caps
- allowlist-first rollout

### Risk: Korami code transplant drags in domain baggage
Mitigation:
- copy only media/runtime layers
- replace domain logic with generic mission interfaces
- keep business-specific code out of `services/archon_voice/`

## Testing Strategy (Design-Level)

### Archon control plane tests
- unit tests for number validation/policy
- unit tests for service lifecycle tools (mock systemd/subprocess)
- unit tests for call mission store and status transitions

### Voice service tests
- unit tests for TwiML generation
- Twilio webhook/TwiML endpoint tests
- WebSocket event parsing tests using recorded Twilio media events

### Integration tests (later)
- local voice service + mocked Twilio API
- mission start -> status updates -> completion path

## Recommendation Summary

Proceed with:
- **same-repo, separate `services/archon_voice/` service**
- Archon-native `call_mission_*` + `voice_service_*` tools
- Korami-derived realtime WebSocket conversation runtime
- phased rollout starting with service lifecycle and scripted outbound calls

This achieves the subagent-calling-user goal while preserving Archon’s lightweight core architecture.

