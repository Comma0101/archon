"""Minimal MCP stdio server for Claude Code permission prompt callbacks."""

import json
import os
import sys
import time

from archon.workers.session_store import (
    add_worker_approval_request,
    decide_worker_approval,
    list_worker_approvals,
    load_worker_session,
)

SERVER_NAME = "archon_approval"
TOOL_NAME = "permission_prompt"
FULL_TOOL_NAME = f"mcp__{SERVER_NAME}__{TOOL_NAME}"
DEFAULT_TIMEOUT_SEC = 1800.0
DEFAULT_POLL_SEC = 0.5


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(msg, list):
            responses = [resp for item in msg if (resp := _handle_message(item)) is not None]
            if responses:
                _write_json(responses)
            continue

        response = _handle_message(msg)
        if response is not None:
            _write_json(response)
    return 0


def _handle_message(msg: dict) -> dict | None:
    if not isinstance(msg, dict):
        return None
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        if msg_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "archon-approval-mcp", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        if msg_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "description": "Ask Archon whether Claude Code should allow a pending tool/action. Returns a JSON string with behavior allow/deny.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "tool_name": {"type": "string"},
                                "toolName": {"type": "string"},
                                "input": {},
                                "tool_input": {},
                                "context": {},
                            },
                            "additionalProperties": True,
                        },
                    }
                ]
            },
        }

    if method == "tools/call":
        if msg_id is None:
            return None
        params = msg.get("params", {}) or {}
        tool_name = str(params.get("name", ""))
        arguments = params.get("arguments", {}) or {}
        if tool_name != TOOL_NAME:
            return _error(msg_id, -32601, f"Unknown tool: {tool_name}")
        result_text = _handle_permission_call(arguments)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": result_text}],
                "isError": False,
            },
        }

    if msg_id is not None:
        return _error(msg_id, -32601, f"Method not found: {method}")
    return None


def _handle_permission_call(arguments: dict) -> str:
    session_id = os.environ.get("ARCHON_WORKER_SESSION_ID", "").strip()
    timeout_sec = _env_float("ARCHON_WORKER_APPROVAL_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC)
    poll_sec = _env_float("ARCHON_WORKER_APPROVAL_POLL_SEC", DEFAULT_POLL_SEC)

    tool_name = _extract_tool_name(arguments)
    tool_input = _extract_tool_input(arguments)
    details = _build_details(tool_name, tool_input, arguments)

    if not session_id:
        return json.dumps(
            {"behavior": "deny", "message": "Archon approval broker missing ARCHON_WORKER_SESSION_ID"}
        )

    request = add_worker_approval_request(session_id, action=tool_name or "tool", details=details)
    if request is None:
        return json.dumps(
            {"behavior": "deny", "message": f"Archon approval broker could not record request for session {session_id}"}
        )

    deadline = time.monotonic() + max(1.0, timeout_sec)
    while time.monotonic() < deadline:
        session = load_worker_session(session_id)
        if session is None:
            _auto_deny(session_id, request.request_id, "Session missing while waiting for approval")
            return json.dumps({"behavior": "deny", "message": "Archon worker session not found"})
        if session.status == "cancelled":
            _auto_deny(session_id, request.request_id, "Session cancelled")
            return json.dumps({"behavior": "deny", "message": "Archon worker session cancelled"})

        approvals = list_worker_approvals(session_id, pending_only=False)
        current = next((a for a in approvals if a.request_id == request.request_id), None)
        if current is not None and current.status != "pending":
            if current.status == "approved":
                payload = {
                    "behavior": "allow",
                    "updatedInput": tool_input,
                    "message": current.note or "Approved by Archon",
                }
            else:
                payload = {
                    "behavior": "deny",
                    "message": current.note or "Denied by Archon",
                }
            return json.dumps(payload)

        time.sleep(max(0.05, poll_sec))

    _auto_deny(session_id, request.request_id, "Timed out waiting for Archon approval")
    return json.dumps({"behavior": "deny", "message": "Timed out waiting for Archon approval"})


def _extract_tool_name(arguments: dict) -> str:
    for key in ("tool_name", "toolName", "name"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "tool"


def _extract_tool_input(arguments: dict):
    for key in ("input", "tool_input", "arguments", "toolInput"):
        if key in arguments:
            return arguments.get(key)
    # Fall back to passing the raw arguments; Claude may accept updatedInput omitted, but this is safer.
    return arguments


def _build_details(tool_name: str, tool_input, raw_args: dict) -> str:
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "raw_args": raw_args,
    }
    text = json.dumps(payload, sort_keys=True)
    if len(text) > 1000:
        return text[:1000] + "...(truncated)"
    return text


def _auto_deny(session_id: str, request_id: str, note: str):
    try:
        decide_worker_approval(session_id, request_id, "deny", note=note)
    except Exception:
        pass


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _error(msg_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _write_json(payload):
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
