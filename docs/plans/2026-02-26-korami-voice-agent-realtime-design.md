# Korami Voice Agent Realtime (Twilio + Deepgram) Design

**Date:** 2026-02-26  
**Status:** Proposed (recommended direction selected in chat; pending plan execution)

## Goal

Add a realtime conversational phone-call runtime for Archon/Korami-style call missions using WebSockets, while keeping `archon/` core lightweight.

The system must:
- keep telephony/realtime runtime in `services/`
- support outbound Twilio calls controlled by Archon tools
- support multi-turn conversations with low latency and barge-in
- preserve the current scripted `<Gather>` path as a fallback

## Non-Goals (Phase 2)

- Full generic "voice subagent framework" redesign inside `archon/`
- Replacing all current Twilio `<Gather>` flows
- Production-grade horizontal scaling / multi-instance coordination
- PCI/HIPAA compliance workflows, recording governance, or analytics dashboards
- Deepgram function/tool calling in the first realtime milestone (defer until bridge is stable)

## Current Baseline (Already Working)

- Archon can start scripted Twilio outbound calls via `call_mission_start`
- `services/archon_voice/` serves TwiML and mission endpoints
- Twilio `<Say>` + `<Gather input="speech">` one/few-turn flow works
- Cloudflare Tunnel public URL is wired and validated (`voice.kummalabs.com`)

This Phase 2 design adds a **realtime** path using Twilio Media Streams and a Deepgram voice agent WebSocket.

## Approaches Considered

### Approach A: Twilio Media Streams + Deepgram Voice Agent API (recommended)

Flow:
- Twilio `<Connect><Stream>` opens a bidirectional WebSocket to `services/archon_voice`
- `services/archon_voice` bridges Twilio audio/events to Deepgram Voice Agent API (`wss://agent.deepgram.com/v1/agent/converse`)
- Deepgram handles STT + LLM orchestration + TTS (configurable providers)
- Service returns generated audio back to Twilio as `mulaw/8000` base64 media messages

Pros:
- Best control over telephony behavior (marks/clear, barge-in, DTMF, custom event handling)
- Aligns with Korami-style realtime WS architecture
- Keeps Archon core clean (service runtime only)
- Deepgram docs provide a Twilio-specific integration path and compatible `mulaw` 8k settings

Cons:
- More bridge logic to implement/test than `<Gather>`
- Must handle Twilio and Deepgram WebSocket protocols correctly
- Requires robust tunnel/deployment uptime and WS reliability

Why recommended:
- This matches your stated direction ("use WebSocket and Deepgram handles it") and gives a real voice-agent experience without embedding heavy runtime logic into `archon/`.

### Approach B: Twilio ConversationRelay + your WebSocket app (alternative, faster but less control)

Flow:
- Twilio `<Connect><ConversationRelay>` handles STT/TTS/session voice loop
- Your app receives text/events over WebSocket and returns text tokens
- Twilio can use Deepgram as `transcriptionProvider`

Pros:
- Faster to ship a conversational prototype
- Twilio handles more realtime voice mechanics
- Simpler app protocol (text/event oriented)

Cons:
- Less raw audio control than Media Streams
- TTS provider is Twilio-managed (`Google`/`Amazon`/`ElevenLabs`), not a direct Deepgram TTS path
- Harder to reuse Korami raw-media patterns

Why not first:
- Good fallback/parallel option, but it does not match the "Deepgram handles WebSocket voice" direction as directly as Media Streams + Deepgram Voice Agent API.

### Approach C: Twilio Media Streams + custom Deepgram STT + separate LLM + separate TTS (defer)

Pros:
- Maximum control over every model/provider
- Easier to swap components independently

Cons:
- Highest engineering complexity (turn detection, barge-in, timing, streaming glue)
- Reinvents features the Deepgram Voice Agent API already bundles

Why deferred:
- Use the integrated Voice Agent API first to validate UX and runtime reliability.

## Recommended Architecture

### 1. Archon (`archon/`) = Control Plane

Archon owns:
- user intent + approvals (`call_mission_start`)
- call mission file-based state (`archon/calls/store.py`)
- tool UX (`call_mission_status`, `/calls on/off`)
- service lifecycle checks/start/stop

Archon should **not** own:
- public webhooks
- Twilio media WebSocket loop
- Deepgram WebSocket runtime

### 2. `services/archon_voice/` = Voice Runtime (Media Plane)

Extend the existing service with a realtime path:
- TwiML endpoint for mission mode `"realtime"`
- Twilio status callbacks (already present)
- Twilio media stream WebSocket endpoint
- Deepgram Voice Agent WebSocket client adapter
- Mission-scoped bridge runtime (Twilio <-> Deepgram)
- Transcript/event emission hooks

Suggested module layout:
- `services/archon_voice/realtime_models.py`
- `services/archon_voice/twilio_stream.py` (Twilio WS message parsing/serialization)
- `services/archon_voice/deepgram_agent.py` (Deepgram WS adapter)
- `services/archon_voice/realtime_bridge.py` (mission runtime orchestration)
- `services/archon_voice/security.py` (Twilio signature verification)

### 3. Twilio = PSTN + Media Transport

Twilio responsibilities:
- Outbound call creation via REST
- TwiML fetch for call instructions
- Bidirectional media streaming via `<Connect><Stream>`
- Call status callbacks

### 4. Deepgram Voice Agent API = Realtime Voice Intelligence

Deepgram responsibilities:
- STT/listening
- think/respond loop
- TTS/speech output
- turn-taking signals / speaking events

Phase 2a keeps Deepgram tools/function calling disabled or minimal until the bridge is stable.

## Realtime Call Flow (Recommended)

1. User asks Archon to make a conversational call
2. Archon approval + mission creation (`call_session_id`)
3. Archon submits mission to `services/archon_voice` with mission mode `"realtime"`
4. Service creates Twilio outbound call; Twilio requests mission TwiML
5. Service returns TwiML:
   - `<Connect><Stream url="wss://voice.../twilio/missions/{id}/stream"/>`
6. Twilio opens bidirectional WebSocket to service
7. Service opens Deepgram Voice Agent WebSocket
8. Service sends Deepgram `Settings` with Twilio-compatible audio config (`mulaw`, 8kHz)
9. Service forwards Twilio inbound audio (`media`) to Deepgram (binary audio)
10. Service forwards Deepgram output audio to Twilio `media` messages
11. Service uses Twilio `mark`/`clear` + Deepgram speaking events for barge-in/interrupt handling
12. Service stores transcript/status and mirrors mission updates to Archon-readable state
13. Call ends; mission status finalizes (`completed` / `failed`)

## Protocol / Integration Requirements (Latest-Docs Aligned)

### Twilio Media Streams (bidirectional)

As of 2026-02-26:
- Bidirectional streams use `<Connect><Stream>`
- Twilio blocks subsequent TwiML while connected
- Only **one bidirectional stream per call**
- Twilio expects outbound audio sent back as `audio/x-mulaw`, `8000 Hz`, base64
- Twilio supports `media`, `mark`, and `clear` messages from your server

### Twilio security

Twilio docs require validating `X-Twilio-Signature` for Media Streams traffic/authenticity. This should be included in Phase 2, not deferred to "later".

### Deepgram Voice Agent API

As of 2026-02-26:
- Deepgram Voice Agent API V1 uses WebSocket `wss://agent.deepgram.com/v1/agent/converse`
- Send a `Settings` message immediately after connect
- Audio format can be configured for Twilio-compatible `mulaw` 8k input/output
- Deepgram emits conversation and speaking lifecycle events (`ConversationText`, `UserStartedSpeaking`, `AgentStartedSpeaking`, etc.)
- KeepAlive messages are recommended for long-lived sessions

## Mission State and Persistence

Archon’s existing file-based mission store should remain the source of truth for control-plane UX.

Recommended additions to mission state:
- `mode` (`scripted_gather` | `realtime_media_stream`)
- `provider` (`twilio`)
- `voice_backend` (`deepgram_voice_agent_v1`)
- `call_sid`, `twilio_stream_sid`
- `realtime_session_started_at`, `realtime_session_ended_at`
- transcript turns (speaker, text, timestamp)
- error fields (`provider_error_code`, `provider_error_message`)

Persistence strategy:
- Service writes local mission transcript snapshots (fast path)
- Archon mirrors/reads summarized status through existing mission endpoints or polling
- Keep file-based JSON/JSONL approach (no DB required in first milestone)

## Safety and Guardrails (Phase 2)

- Preserve `DANGEROUS` approval for outbound calls
- Reuse number normalization / explicit target display
- Add per-mission max duration and max idle timeout
- Add optional allowlist (user-configurable)
- Keep fallback to scripted `<Gather>` if realtime bridge init fails

## Ops and Deployment Notes

- Continue using a dedicated subdomain (`voice.kummalabs.com`), not `www`
- Run `uvicorn` and `cloudflared` as `systemd --user` services for reliability
- Add structured service logs for Twilio/Deepgram event IDs and mission IDs
- Avoid storing provider secrets in repo; keep in local env/systemd environment files

## Testing Strategy

### Unit tests (fast, deterministic)
- Twilio WS message parse/serialize helpers
- Deepgram WS adapter message handling (mock sockets)
- TwiML generation for realtime mode (`<Connect><Stream>`)
- Mission mode routing + config validation

### Integration tests (service-local, no live providers)
- FastAPI WebSocket endpoint with fake Twilio client
- Fake Deepgram server / adapter mocks to test bridge orchestration
- Barge-in behavior (`clear` emission) and transcript capture

### Manual smoke tests (live)
- Outbound call connects to realtime path
- Natural two-way turn-taking
- Interrupt while agent is speaking
- Call end + mission status finalization

## Recommended Phase Breakdown

### Phase 2A (Bridge skeleton, no full intelligence)
- Realtime TwiML + Twilio WS endpoint
- Fake audio loop / echo or canned response
- Twilio mark/clear handling scaffolding

### Phase 2B (Deepgram Voice Agent integration)
- Deepgram WS adapter
- Twilio <-> Deepgram audio bridge
- Transcript + events + retries/timeouts

### Phase 2C (Hardening + UX)
- Signature validation
- Better mission status reporting
- systemd service files/docs
- Fallback and error recovery paths

## References (Checked 2026-02-26)

- Twilio Media Streams Overview: https://www.twilio.com/docs/voice/media-streams
- Twilio Media Streams WebSocket Messages: https://www.twilio.com/docs/voice/media-streams/websocket-messages
- Twilio ConversationRelay overview: https://www.twilio.com/docs/voice/conversationrelay
- TwiML `<ConversationRelay>`: https://www.twilio.com/docs/voice/twiml/connect/conversationrelay
- Deepgram Twilio + Voice Agent guide: https://developers.deepgram.com/docs/twilio-and-deepgram-voice-agent
- Deepgram Voice Agent API reference (V1 WebSocket): https://developers.deepgram.com/reference/voice-agent/voice-agent
- Deepgram Voice Agent config guide: https://developers.deepgram.com/docs/configure-voice-agent

