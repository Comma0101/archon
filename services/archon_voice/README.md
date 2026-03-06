# Archon Voice Service (Phase 0 Skeleton)

Local runtime service for Archon voice/call features. This lives outside `archon/` core so the core package stays lightweight.

## Phase 0 scope

- `GET /health`
- Placeholder mission endpoints:
  - `POST /missions`
  - `GET /missions/{mission_id}`
- Minimal TwiML helper builders (`<Say>`, `<Hangup>`)
- No Twilio REST calls yet

## Local setup (service-only venv)

```bash
python3 -m venv services/archon_voice/.venv
services/archon_voice/.venv/bin/pip install fastapi uvicorn pytest httpx
```

## Run

```bash
services/archon_voice/.venv/bin/uvicorn services.archon_voice.app:app --reload --port 8788
```

## Test

```bash
services/archon_voice/.venv/bin/pytest services/archon_voice/tests -q
```

## Realtime mode ops hardening (manual verification checklist)

This section documents a manual smoke-test path for `mode="realtime_media_stream"` using Twilio + Deepgram.

### Realtime env vars

Set these before starting `uvicorn`:

- `DEEPGRAM_API_KEY` (required for Deepgram Voice Agent realtime backend; if unset, the service keeps the no-Deepgram fallback path)
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`
- `ARCHON_VOICE_PUBLIC_BASE_URL` (public `https://...` base that Twilio can reach; path prefixes are supported)
- `ARCHON_VOICE_STRICT_TWILIO_SIGNATURE` (optional; default `false`)

Strict signature note:

- `ARCHON_VOICE_STRICT_TWILIO_SIGNATURE=1` currently enforces Twilio signature validation for `POST /twilio/status/{mission_id}` and returns `403 invalid_twilio_signature` on mismatch.
- Enable it only when `ARCHON_VOICE_PUBLIC_BASE_URL` matches the exact public URL Twilio is using (stable tunnel/domain). Quick tunnels that change URLs can cause callback signature failures if the env var is stale.

Example shell exports:

```bash
export DEEPGRAM_API_KEY=dg_...
export TWILIO_ACCOUNT_SID=AC...
export TWILIO_AUTH_TOKEN=...
export TWILIO_FROM_NUMBER=+15551234567
export ARCHON_VOICE_PUBLIC_BASE_URL=https://voice.example.com
# Optional (recommended after the public URL is stable)
export ARCHON_VOICE_STRICT_TWILIO_SIGNATURE=1
```

### Startup (manual)

Terminal 1 (`uvicorn`):

```bash
services/archon_voice/.venv/bin/uvicorn services.archon_voice.app:app --host 127.0.0.1 --port 8788
```

Terminal 2 (`cloudflared`, quick tunnel example):

```bash
cloudflared tunnel --url http://127.0.0.1:8788
```

Use the public HTTPS URL from `cloudflared` as `ARCHON_VOICE_PUBLIC_BASE_URL` and restart `uvicorn` if you changed exported env vars after launch.

### Startup (`systemd --user` examples)

Example unit files are included in `services/archon_voice/deploy/`:

- `archon-voice.service.example`
- `cloudflared-archon-voice.service.example`

Typical setup flow:

```bash
mkdir -p ~/.config/systemd/user
cp services/archon_voice/deploy/archon-voice.service.example ~/.config/systemd/user/archon-voice.service
cp services/archon_voice/deploy/cloudflared-archon-voice.service.example ~/.config/systemd/user/cloudflared-archon-voice.service
# Edit the copied units (paths, tunnel name/config, env file paths) before enabling
systemctl --user daemon-reload
systemctl --user enable --now archon-voice.service
systemctl --user enable --now cloudflared-archon-voice.service
```

Useful logs while testing:

```bash
journalctl --user -u archon-voice.service -f
journalctl --user -u cloudflared-archon-voice.service -f
```

### Manual realtime verification checklist

1. Confirm local and public health checks.

```bash
curl -fsS http://127.0.0.1:8788/health
curl -fsS "$ARCHON_VOICE_PUBLIC_BASE_URL/health"
```

Expected result (both): JSON with `{"ok": true, "status": "healthy", "service": "archon_voice"}`.

2. Create a realtime mission (this can trigger a live Twilio call if Twilio creds are configured).

```bash
curl -sS http://127.0.0.1:8788/missions \
  -H 'content-type: application/json' \
  -d '{
    "call_session_id": "manual_rt_001",
    "goal": "Ask the user how their trading is going today.",
    "target_number": "+15550001111",
    "mode": "realtime_media_stream"
  }'
```

Expected checks:

- Response includes `ok: true`
- `mission.mode == "realtime_media_stream"`
- `mission.twiml_url` points at `ARCHON_VOICE_PUBLIC_BASE_URL/twilio/missions/<id>/twiml`

3. Public TwiML verification (preflight).

```bash
curl -sS -X POST "$ARCHON_VOICE_PUBLIC_BASE_URL/twilio/missions/manual_rt_001/twiml"
```

Expected checks:

- Response is XML (`<Response>...</Response>`)
- Contains `<Connect>` and `<Stream ...>`
- Stream URL is `wss://.../twilio/missions/manual_rt_001/stream` (preserves any base-path prefix from `ARCHON_VOICE_PUBLIC_BASE_URL`)

4. Live realtime call smoke test (answer the call and speak a few short turns).

Expected runtime checks:

- `uvicorn`/journal logs show Twilio hitting:
  - `POST /twilio/missions/<id>/twiml`
  - WebSocket handshake/connection for `/twilio/missions/<id>/stream`
  - `POST /twilio/status/<id>` callbacks
- `GET /missions/<id>` shows realtime fields filling in during/after the call:
  - `status` moves to `in_progress` during streaming, then usually `completed` on normal stop (`stream_disconnected` indicates an abnormal stream close)
  - `provider_call_sid` is set
  - `twilio_stream_sid` is set
  - `realtime_session_started_at` is non-zero
  - `realtime_session_ended_at` is non-zero after hangup/stop
- With `DEEPGRAM_API_KEY` configured, expect:
  - `voice_backend == "deepgram_voice_agent_v1"`
  - `transcript` contains user/assistant entries after conversation starts

Status polling example:

```bash
curl -sS http://127.0.0.1:8788/missions/manual_rt_001
```
