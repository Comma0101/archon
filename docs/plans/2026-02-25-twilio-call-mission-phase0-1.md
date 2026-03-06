# Twilio Call Mission (Phase 0 + Phase 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a lightweight Archon-controlled voice service lifecycle and a first outbound Twilio call mission path (scripted `<Say>` call) without embedding realtime call runtime complexity into Archon core.

**Architecture:** Build an Archon-native `archon/calls/*` control-plane subsystem plus `voice_service_*` and `call_mission_*` tools. Create a same-repo `services/archon_voice/` FastAPI service skeleton with `/health`, a TwiML endpoint, and a mission-start endpoint. Phase 1 uses Twilio REST + inline/scripted TwiML (`<Say>`) to prove call control and mission state flow before WebSocket conversational audio.

**Tech Stack:** Python stdlib (`urllib`, `json`, `pathlib`, `dataclasses`, `uuid`, `subprocess`), existing Archon tool/safety/config patterns, FastAPI/Uvicorn in optional `services/archon_voice` service (ported from Korami patterns), Twilio Voice REST API + TwiML.

---

### Task 1: Define config and state models for call missions (TDD RED)

**Files:**
- Create: `archon/calls/models.py`
- Create: `archon/calls/store.py`
- Modify: `archon/config.py`
- Test: `tests/test_calls_store.py`
- Test: `tests/test_config.py`

**Step 1: Write failing tests for call mission model serialization**

```python
def test_call_mission_roundtrip():
    mission = CallMission(
        call_session_id="call_123",
        goal="Call Comma and ask about their day",
        target_number="+15551234567",
        status="queued",
    )
    payload = mission.to_dict()
    restored = CallMission.from_dict(payload)
    assert restored.call_session_id == "call_123"
    assert restored.status == "queued"
```

**Step 2: Write failing tests for mission store paths and persistence**

```python
def test_call_store_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    mission = CallMission(call_session_id="call_1", goal="x", target_number="+1555", status="queued")
    save_call_mission(mission)
    loaded = load_call_mission("call_1")
    assert loaded is not None
    assert loaded.status == "queued"
```

**Step 3: Write failing tests for new calls config parsing**

```python
def test_load_config_calls_defaults():
    cfg = load_config()
    assert cfg.calls.enabled is False
    assert cfg.calls.voice_service.mode in {"systemd", "subprocess"}
```

**Step 4: Run targeted tests and confirm RED**

Run:
```bash
pytest tests/test_calls_store.py tests/test_config.py -q
```

**Step 5: Implement minimal models/store/config**
- Add dataclasses for:
  - `CallsConfig`
  - `CallVoiceServiceConfig`
  - `TwilioCallsConfig`
  - `CallMission`
- Add XDG state paths:
  - `~/.local/state/archon/calls/missions`
  - `~/.local/state/archon/calls/events`
- Add `save_call_mission`, `load_call_mission`, `append_call_event`

**Step 6: Run targeted tests and verify GREEN**

Run:
```bash
pytest tests/test_calls_store.py tests/test_config.py -q
```

**Step 7: Commit**

```bash
git add archon/config.py archon/calls/models.py archon/calls/store.py tests/test_calls_store.py tests/test_config.py
git commit -m "feat: add call mission models and store"
```

---

### Task 2: Add voice service control client + helpers (TDD RED)

**Files:**
- Create: `archon/calls/service_client.py`
- Create: `archon/calls/runner.py`
- Test: `tests/test_calls_service_client.py`

**Step 1: Write failing tests for local voice service health check**

```python
def test_voice_service_health_ok(monkeypatch):
    # monkeypatch urllib opener/response
    result = voice_service_health(base_url="http://127.0.0.1:8788")
    assert result["ok"] is True
```

**Step 2: Write failing tests for mission start request shape**

```python
def test_submit_call_mission_posts_json(monkeypatch):
    result = submit_call_mission(
        base_url="http://127.0.0.1:8788",
        mission_payload={"call_session_id": "call_1", "goal": "Call me"},
    )
    assert result["ok"] is True
```

**Step 3: Run tests and confirm RED**

Run:
```bash
pytest tests/test_calls_service_client.py -q
```

**Step 4: Implement minimal local HTTP client**
- `voice_service_health(base_url)`
- `voice_service_start_mission(base_url, payload)`
- stdlib `urllib.request`
- consistent JSON parsing and timeout handling

**Step 5: Add `runner.py` orchestration helpers (minimal)**
- wrappers that validate config and surface friendly errors

**Step 6: Run tests and verify GREEN**

Run:
```bash
pytest tests/test_calls_service_client.py -q
```

**Step 7: Commit**

```bash
git add archon/calls/service_client.py archon/calls/runner.py tests/test_calls_service_client.py
git commit -m "feat: add local voice service client for call missions"
```

---

### Task 3: Add voice service lifecycle tools to Archon (Phase 0) (TDD RED)

**Files:**
- Create: `archon/tooling/call_service_tools.py`
- Modify: `archon/tools.py` (registration wiring only)
- Test: `tests/test_tools_calls_service.py`

**Step 1: Write failing tool tests for service status/start/stop**

```python
def test_voice_service_status_reports_health(monkeypatch):
    registry = ToolRegistry()
    result = registry.run_tool("voice_service_status", {})
    assert "status" in result
```

```python
def test_voice_service_start_requires_confirmation(monkeypatch):
    registry = ToolRegistry(confirmer=lambda cmd, lvl: True)
    out = registry.run_tool("voice_service_start", {})
    assert "mode" in out
```

**Step 2: Run targeted tests and confirm RED**

Run:
```bash
pytest tests/test_tools_calls_service.py -q
```

**Step 3: Implement `voice_service_status`**
- SAFE
- local health probe via `archon/calls/service_client.py`
- returns `running/healthy/unreachable` style payload

**Step 4: Implement `voice_service_start` / `voice_service_stop`**
- classify as `DANGEROUS`
- support config modes:
  - `systemd` (shell out `systemctl --user start/stop`)
  - `subprocess` (stub or minimal dev support)
- surface clear messages if service mode unsupported/not configured

**Step 5: Register tools**
- Add to tool registry via new `archon/tooling/call_service_tools.py`
- Keep `archon/tools.py` as thin public wrapper

**Step 6: Run tests and verify GREEN**

Run:
```bash
pytest tests/test_tools_calls_service.py -q
```

**Step 7: Commit**

```bash
git add archon/tooling/call_service_tools.py archon/tools.py tests/test_tools_calls_service.py
git commit -m "feat: add voice service lifecycle tools"
```

---

### Task 4: Create `services/archon_voice` FastAPI skeleton (Phase 0 service) (TDD RED)

**Files:**
- Create: `services/archon_voice/app.py`
- Create: `services/archon_voice/models.py`
- Create: `services/archon_voice/twiml.py`
- Create: `services/archon_voice/README.md`
- Test: `services/archon_voice/tests/test_app.py`
- Test: `services/archon_voice/tests/test_twiml.py`

**Step 1: Write failing tests for `/health` and TwiML builder**

```python
def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
```

```python
def test_build_say_twiml():
    xml = build_say_twiml("Hello", voice="alice")
    assert "<Say" in xml and "Hello" in xml and "<Response>" in xml
```

**Step 2: Run service tests and confirm RED**

Run:
```bash
pytest services/archon_voice/tests -q
```

**Step 3: Implement minimal service skeleton**
- FastAPI app with:
  - `GET /health`
  - placeholder `POST /missions`
  - placeholder `GET /missions/{id}`
- TwiML helpers:
  - `<Say>`
  - `<Hangup>`
- no Twilio calls yet

**Step 4: Run tests and verify GREEN**

Run:
```bash
pytest services/archon_voice/tests -q
```

**Step 5: Commit**

```bash
git add services/archon_voice
git commit -m "feat: add archon voice service skeleton"
```

---

### Task 5: Add Phase 1 outbound scripted call path (Twilio REST + TwiML) (TDD RED)

**Files:**
- Modify: `services/archon_voice/app.py`
- Create: `services/archon_voice/twilio_client.py`
- Modify: `services/archon_voice/models.py`
- Modify: `services/archon_voice/twiml.py`
- Test: `services/archon_voice/tests/test_twilio_client.py`
- Test: `services/archon_voice/tests/test_app.py`

**Step 1: Write failing tests for Twilio outbound call request**

```python
def test_create_outbound_call_posts_form(monkeypatch):
    result = create_outbound_call(
        account_sid="AC123",
        auth_token="secret",
        from_number="+15550000000",
        to_number="+15551112222",
        twiml_url="https://example.com/twilio/missions/call_1/twiml",
    )
    assert result["sid"].startswith("CA")
```

**Step 2: Write failing tests for mission start endpoint and TwiML endpoint**

```python
def test_post_missions_creates_queued_mission(client):
    resp = client.post("/missions", json={...})
    assert resp.status_code == 200
    assert resp.json()["status"] in {"queued", "initiated"}
```

```python
def test_twiml_endpoint_returns_say_response(client):
    resp = client.post("/twilio/missions/call_1/twiml")
    assert resp.status_code == 200
    assert "text/xml" in resp.headers["content-type"]
    assert "<Say" in resp.text
```

**Step 3: Run tests and confirm RED**

Run:
```bash
pytest services/archon_voice/tests/test_twilio_client.py services/archon_voice/tests/test_app.py -q
```

**Step 4: Implement Twilio REST client (stdlib)**
- POST to Twilio Calls API (`application/x-www-form-urlencoded`)
- basic auth
- parse JSON response
- small timeout

**Step 5: Implement mission start + TwiML**
- `POST /missions`
  - validates payload
  - stores mission in memory/file (service-side minimal)
  - creates outbound call using Twilio client
- `POST /twilio/missions/{mission_id}/twiml`
  - returns scripted `<Say> ... </Say><Hangup/>`
- optional `StatusCallback` endpoint stub for future state updates

**Step 6: Run tests and verify GREEN**

Run:
```bash
pytest services/archon_voice/tests -q
```

**Step 7: Commit**

```bash
git add services/archon_voice
git commit -m "feat: add scripted outbound Twilio call mission path"
```

---

### Task 6: Add Archon `call_mission_*` tools for Phase 1 (TDD RED)

**Files:**
- Create: `archon/tooling/call_mission_tools.py`
- Modify: `archon/tools.py` (registration wiring only)
- Modify: `archon/calls/runner.py`
- Test: `tests/test_tools_calls_missions.py`

**Step 1: Write failing tests for mission tools**

```python
def test_call_mission_start_requires_approval(monkeypatch):
    registry = ToolRegistry(confirmer=lambda cmd, lvl: True)
    result = registry.run_tool("call_mission_start", {
        "target_number": "+15551112222",
        "goal": "Call me and ask about my day",
    })
    assert "call_session_id" in result
```

```python
def test_call_mission_status_reads_store(monkeypatch):
    registry = ToolRegistry()
    out = registry.run_tool("call_mission_status", {"call_session_id": "call_1"})
    assert "status" in out
```

**Step 2: Run tests and confirm RED**

Run:
```bash
pytest tests/test_tools_calls_missions.py -q
```

**Step 3: Implement `call_mission_start`**
- `DANGEROUS`
- validates number/policy (minimal checks in Phase 1)
- ensures voice service running (or surfaces explicit status error)
- stores mission in Archon store
- submits mission to local voice service
- returns `call_session_id` + status

**Step 4: Implement status/list/cancel tools**
- `call_mission_status` (SAFE)
- `call_mission_list` (SAFE)
- `call_mission_cancel` (DANGEROUS, may be stubbed if service cancel endpoint not ready)

**Step 5: Register tools**
- Add via `archon/tooling/call_mission_tools.py`

**Step 6: Run tests and verify GREEN**

Run:
```bash
pytest tests/test_tools_calls_missions.py -q
```

**Step 7: Commit**

```bash
git add archon/tooling/call_mission_tools.py archon/tools.py archon/calls/runner.py tests/test_tools_calls_missions.py
git commit -m "feat: add call mission tools and phase1 orchestration"
```

---

### Task 7: Safety, docs, and regression verification

**Files:**
- Modify: `archon/safety.py` (if call tools need explicit classification notes/rules)
- Modify: `AGENT_CONTEXT.json`
- Modify: `docs/plans/2026-02-25-twilio-call-mission-design.md` (if implementation notes clarified)

**Step 1: Verify safety behavior**
- ensure `call_mission_start` and `call_mission_cancel` route through confirmation
- ensure `voice_service_start/stop` are treated as dangerous

**Step 2: Run focused tests**

Run:
```bash
pytest tests/test_tools_calls_service.py tests/test_tools_calls_missions.py -q
```

**Step 3: Run full Archon suite**

Run:
```bash
HOME=/tmp PYTHONPATH=/home/comma/.local/lib/python3.14/site-packages pytest tests/ -q
```

**Step 4: Update `AGENT_CONTEXT.json`**
- add `archon/calls/*`
- add `services/archon_voice/*`
- add new test files
- update test count and changelog

**Step 5: Validate JSON**

Run:
```bash
python -m json.tool AGENT_CONTEXT.json >/dev/null
```

**Step 6: Commit**

```bash
git add AGENT_CONTEXT.json
git commit -m "docs: record twilio call mission phase0-1 architecture and tests"
```

---

### Notes / Intentional Non-Goals (Phase 0 + Phase 1)

- No realtime conversational WebSocket media stream in Phase 1 (that is Phase 2)
- No Twilio SDK requirement (use stdlib HTTP first)
- No recordings
- No broad contacts/book support beyond minimal allowlist/policy checks
- No MCP integration

### Phase 2 Preview (not in this plan)

Phase 2 will replace scripted `<Say>` behavior with a Korami-derived realtime media stream conversation loop:
- TwiML `<Connect><Stream>`
- WebSocket media endpoint
- mission-scoped conversational runtime
- transcript/status event propagation back to Archon

