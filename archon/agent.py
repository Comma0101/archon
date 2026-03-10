"""Core tool-use loop tying everything together."""

from __future__ import annotations

import logging
import os
import sys
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Generator, TypeVar, cast

from archon import memory as memory_store
from archon.control.hooks import HookBus
from archon.control.orchestrator import (
    orchestrate_response,
    orchestrate_stream_response,
)
from archon.control.policy import evaluate_mcp_policy, evaluate_tool_policy
from archon.execution.turn_executor import execute_turn, execute_turn_stream
from archon.llm import LLMClient, LLMResponse
from archon.tools import ToolRegistry
from archon.prompt import (
    build_runtime_capability_summary,
    build_skill_guidance,
    build_source_awareness_summary,
    build_system_prompt,
)
from archon.config import Config
from archon.security.redaction import redact_secret_like_text
from archon.research import store as research_store


logger = logging.getLogger(__name__)

ANSI_RESET = "\033[0m"
ANSI_TOOL_CALL = "\033[96m"       # bright cyan
ANSI_TOOL_RESULT = "\033[37m"     # readable light gray/white
ANSI_TOOL_RESULT_META = "\033[90m"  # dim for truncation summaries

_WORKER_TOOL_NAMES = {
    "delegate_code_task",
    "worker_start",
    "worker_send",
    "worker_status",
    "worker_poll",
    "worker_list",
}


def _detect_tool_loop(recent_calls: list[tuple[str, dict]], window: int = 6, min_repeats: int = 3) -> bool:
    """Detect repetitive tool call patterns in recent history."""
    if len(recent_calls) < min_repeats:
        return False
    # Check same exact call repeated
    last = recent_calls[-1]
    same_count = sum(1 for c in recent_calls[-window:] if c == last)
    if same_count >= min_repeats:
        return True
    # Check alternating A-B-A-B pattern
    if len(recent_calls) >= 6:
        tail = recent_calls[-6:]
        if tail[0] == tail[2] == tail[4] and tail[1] == tail[3] == tail[5]:
            return True
    return False


class Agent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, config: Config):
        self.llm = llm
        self.tools = tools
        self.config = config
        try:
            self.max_iterations = max(1, int(getattr(config.agent, "max_iterations", 40)))
        except Exception:
            self.max_iterations = 40
        self.history: list[dict] = []
        # Lightweight context-window guard using message-count + approximate chars.
        self.history_max_messages = int(getattr(config.agent, "history_max_messages", 80))
        self.history_trim_to = int(getattr(config.agent, "history_trim_to_messages", 60))
        self.history_max_chars = int(getattr(config.agent, "history_max_chars", 48000))
        self.history_trim_to_chars = int(getattr(config.agent, "history_trim_to_chars", 36000))
        self.llm_request_timeout_sec = float(getattr(config.agent, "llm_request_timeout_sec", 45))
        self.llm_retry_attempts = max(1, int(getattr(config.agent, "llm_retry_attempts", 3)))
        self.tool_result_max_chars = max(
            200,
            int(getattr(config.agent, "tool_result_max_chars", 6000)),
        )
        self.tool_result_worker_max_chars = max(
            200,
            int(getattr(config.agent, "tool_result_worker_max_chars", 2500)),
        )
        self._system_prompt: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.on_thinking: Callable[[], None] | None = None  # Called when LLM call starts
        self.on_tool_call: Callable[[str, dict], None] | None = None  # Called on tool use
        self.hooks = HookBus()
        self.policy_profile = "default"
        self.log_label: str = ""
        self._turn_counter = 0
        self.last_turn_id: str = ""
        self._pending_compactions: list[dict] = []
        self.tools.hook_bus = self.hooks
        self.tools.set_execute_event_handler(self._on_tool_execute_event)
        try:
            research_store.ensure_research_recovery_started(cfg=self.config, hook_bus=self.hooks)
        except Exception:
            pass

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = build_system_prompt(
                tool_count=len(self.tools.get_schemas())
            )
        return self._system_prompt

    def run(self, user_message: str, policy_profile: str | None = None) -> str:
        """Run a single user message through the agent loop."""
        turn_id = self._next_turn_id()
        self.last_turn_id = turn_id
        active_profile = self._resolve_policy_profile(policy_profile)
        log_prefix = _make_log_prefix(self.log_label, turn_id)
        self._trim_history_if_needed()
        self._repair_history_tool_sequence()
        self.history.append({"role": "user", "content": user_message})
        _maybe_capture_preference_memory(user_message)
        skill_guidance = build_skill_guidance(self.config, profile_name=active_profile)
        pending_compactions = self._consume_pending_compactions()
        turn_system_prompt = _build_turn_system_prompt(
            self.system_prompt,
            user_message,
            self.config,
            profile_name=active_profile,
            skill_guidance=skill_guidance,
            compactions=pending_compactions,
        )
        return orchestrate_response(
            mode=self._orchestrator_mode(),
            turn_id=turn_id,
            user_message=user_message,
            run_legacy=lambda: execute_turn(
                self,
                turn_id=turn_id,
                user_message=user_message,
                active_profile=active_profile,
                log_prefix=log_prefix,
                turn_system_prompt=turn_system_prompt,
                llm_step=lambda iter_system_prompt: _chat_with_retry(
                    self.llm,
                    iter_system_prompt,
                    self.history,
                    self.tools.get_schemas(),
                    max_attempts=self.llm_retry_attempts,
                    request_timeout_sec=self.llm_request_timeout_sec,
                ),
            ),
            emit_hook=self._emit_hook,
        )

    def run_stream(self, user_message: str, policy_profile: str | None = None) -> Generator[str, None, None]:
        """Run a user message, streaming the final text response.

        Yields text deltas as they arrive. Tool-call iterations use non-streaming
        internally; only the final text response is streamed to the caller.
        """
        turn_id = self._next_turn_id()
        self.last_turn_id = turn_id
        active_profile = self._resolve_policy_profile(policy_profile)
        log_prefix = _make_log_prefix(self.log_label, turn_id)
        self._trim_history_if_needed()
        self._repair_history_tool_sequence()
        self.history.append({"role": "user", "content": user_message})
        _maybe_capture_preference_memory(user_message)
        skill_guidance = build_skill_guidance(self.config, profile_name=active_profile)
        pending_compactions = self._consume_pending_compactions()
        turn_system_prompt = _build_turn_system_prompt(
            self.system_prompt,
            user_message,
            self.config,
            profile_name=active_profile,
            skill_guidance=skill_guidance,
            compactions=pending_compactions,
        )

        yield from orchestrate_stream_response(
            mode=self._orchestrator_mode(),
            turn_id=turn_id,
            user_message=user_message,
            run_legacy_stream=lambda: execute_turn_stream(
                self,
                turn_id=turn_id,
                user_message=user_message,
                active_profile=active_profile,
                log_prefix=log_prefix,
                turn_system_prompt=turn_system_prompt,
                llm_stream_step=lambda iter_system_prompt: _chat_stream_collect_with_retry(
                    self.llm,
                    iter_system_prompt,
                    self.history,
                    self.tools.get_schemas(),
                    max_attempts=self.llm_retry_attempts,
                    request_timeout_sec=self.llm_request_timeout_sec,
                ),
            ),
            emit_hook=self._emit_hook,
        )

    @staticmethod
    def _make_assistant_msg(response) -> dict:
        """Build assistant history entry, preserving provider metadata."""
        msg = {"role": "assistant", "content": response.raw_content}
        if response.provider_message is not None:
            msg["_provider_message"] = response.provider_message
        return msg

    def reset(self):
        """Clear conversation history."""
        self.history.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def compact_context(self) -> dict:
        """Explicitly compact current conversation history into a session artifact."""
        messages = list(self.history)
        pending_compactions = list(self._pending_compactions)
        compacted_messages = len(messages)
        if not messages:
            return {
                "compacted_messages": 0,
                "path": "",
                "summary": "",
            }

        artifact = self._compact_history_segment(
            messages,
            layer="session",
        )
        if artifact is None:
            return {
                "compacted_messages": compacted_messages,
                "path": "",
                "summary": "",
            }

        self.history = []
        self._pending_compactions = pending_compactions + [artifact]
        return {
            "compacted_messages": compacted_messages,
            "path": str(artifact.get("path", "") or "").strip(),
            "summary": str(artifact.get("summary", "") or "").strip(),
        }

    def set_policy_profile(self, profile_name: str) -> None:
        value = (profile_name or "").strip()
        if value:
            self.policy_profile = value

    def _emit_hook(self, kind: str, payload: dict | None = None) -> None:
        try:
            self.hooks.emit_kind(
                kind,
                task_id=self.last_turn_id,
                payload=payload or {},
            )
        except Exception:
            # Hook infrastructure must never affect chat behavior.
            return

    def _on_tool_execute_event(self, kind: str, payload: dict) -> None:
        hook_payload = dict(payload or {})
        hook_payload.setdefault("turn_id", self.last_turn_id)
        self._emit_hook(f"tool_registry.{kind}", hook_payload)

    def _orchestrator_mode(self) -> str:
        orchestrator_cfg = getattr(self.config, "orchestrator", None)
        if not orchestrator_cfg:
            return "legacy"
        if bool(getattr(orchestrator_cfg, "enabled", False)):
            mode = str(getattr(orchestrator_cfg, "mode", "legacy") or "legacy").strip().lower()
            if mode == "hybrid":
                return "hybrid"
        return "legacy"

    def _resolve_policy_profile(self, turn_override: str | None) -> str:
        override = (turn_override or "").strip()
        if override:
            return override
        session_profile = (self.policy_profile or "").strip()
        if session_profile:
            return session_profile
        orchestrator_cfg = getattr(self.config, "orchestrator", None)
        if orchestrator_cfg is not None:
            cfg_profile = str(
                getattr(orchestrator_cfg, "default_profile", "default") or "default"
            ).strip()
            if cfg_profile:
                return cfg_profile
        return "default"

    def _create_google_deep_research_client(self):
        from archon.research.google_deep_research import GoogleDeepResearchClient

        deep_cfg = self.config.research.google_deep_research
        llm_cfg = getattr(self.config, "llm", None)
        api_key = ""
        if str(getattr(llm_cfg, "provider", "") or "").strip().lower() == "google":
            api_key = str(getattr(llm_cfg, "api_key", "") or "").strip()
        if not api_key and str(getattr(llm_cfg, "fallback_provider", "") or "").strip().lower() == "google":
            api_key = str(getattr(llm_cfg, "fallback_api_key", "") or "").strip()
        if not api_key:
            api_key = str(os.environ.get("GEMINI_API_KEY", "")).strip()
        return GoogleDeepResearchClient.from_api_key(
            api_key,
            agent=str(getattr(deep_cfg, "agent", "") or "").strip(),
            thinking_summaries=str(
                getattr(deep_cfg, "thinking_summaries", "auto") or "auto"
            ).strip().lower(),
        )

    def _next_turn_id(self) -> str:
        self._turn_counter += 1
        return f"t{self._turn_counter:03d}"

    def _truncate_tool_result_for_history(self, tool_name: str, result_text: str) -> str:
        limit = self.tool_result_max_chars
        name = (tool_name or "").strip().lower()
        if name in _WORKER_TOOL_NAMES:
            limit = min(limit, self.tool_result_worker_max_chars)
        if limit <= 0 or len(result_text) <= limit:
            return result_text
        omitted = len(result_text) - limit
        head_size = int(limit * 0.65)
        tail_size = int(limit * 0.25)
        middle = f"\n... [{omitted} chars omitted] ...\n"
        if head_size + tail_size + len(middle) >= len(result_text):
            return result_text
        return result_text[:head_size] + middle + result_text[-tail_size:]

    def _repair_history_tool_sequence(self) -> None:
        """Drop malformed tool-turn fragments before provider calls.

        Google strict function-calling rejects history when assistant tool calls are not
        immediately followed by a user tool_result turn.
        """
        if not self.history:
            return
        repaired: list[dict] = []
        i = 0
        changed = False
        while i < len(self.history):
            msg = self.history[i]
            if _is_assistant_tool_use_message(msg):
                prev_is_user_turn = bool(repaired) and repaired[-1].get("role") == "user"
                has_next_tool_result = (
                    i + 1 < len(self.history)
                    and _is_user_tool_result_message(self.history[i + 1])
                )
                if prev_is_user_turn and has_next_tool_result:
                    repaired.append(msg)
                    repaired.append(self.history[i + 1])
                    i += 2
                    continue
                changed = True
                if has_next_tool_result:
                    i += 2
                    continue
                i += 1
                continue
            if _is_user_tool_result_message(msg):
                changed = True
                i += 1
                continue
            repaired.append(msg)
            i += 1
        if changed:
            self.history = repaired

    def _enforce_iteration_budget(self) -> None:
        """Lightweight mid-turn trim: drop oldest messages if over char budget.

        Called after each tool-result append inside the iteration loop so that
        history cannot grow unbounded within a single turn.
        """
        max_chars = self.history_max_chars
        trim_to = self.history_trim_to_chars
        if max_chars <= 0 or trim_to <= 0:
            return
        current = _estimate_history_chars(self.history)
        if current <= max_chars:
            return
        original_chars = current
        dropped_count = 0
        while len(self.history) > 2 and current > trim_to:
            dropped = self.history.pop(0)
            current -= _estimate_message_chars(dropped)
            dropped_count += 1
        if dropped_count > 0:
            logger.info(
                "Auto-compact: dropped %d oldest messages (was %d chars, now %d)",
                dropped_count,
                original_chars,
                current,
            )

    def _trim_history_if_needed(self) -> None:
        """Trim old history with a lightweight dual budget to avoid unbounded growth.

        This runs at turn start only, so it trims complete prior conversation state
        and avoids interfering with in-flight tool-call loops.
        """
        try:
            max_msgs = int(self.history_max_messages)
            trim_to = int(self.history_trim_to)
            max_chars = int(self.history_max_chars)
            trim_to_chars = int(self.history_trim_to_chars)
        except Exception:
            return
        message_trim_enabled = max_msgs > 0 and trim_to > 0
        if message_trim_enabled and trim_to >= max_msgs:
            trim_to = max(1, max_msgs - 1)
        if max_chars > 0 and trim_to_chars >= max_chars:
            trim_to_chars = max(1, max_chars - 1)

        history_chars = _estimate_history_chars(self.history)
        pending_compactions: list[dict] = []
        over_message_budget = message_trim_enabled and len(self.history) > max_msgs
        over_char_budget = max_chars > 0 and trim_to_chars > 0 and history_chars > max_chars
        if not over_message_budget and not over_char_budget:
            self._pending_compactions = []
            return

        # First apply the message-count trim if needed.
        if over_message_budget:
            dropped_for_compaction = list(self.history[:-trim_to])
            self.history = self.history[-trim_to:]
            history_chars = _estimate_history_chars(self.history)
            artifact = self._compact_history_segment(
                dropped_for_compaction,
                layer="session",
            )
            if artifact is not None:
                pending_compactions.append(artifact)

        # Then enforce a lightweight char budget so giant messages/tool outputs trim earlier.
        dropped_for_task_compaction: list[dict] = []
        if max_chars > 0 and trim_to_chars > 0 and history_chars > max_chars:
            while len(self.history) > 1 and history_chars > trim_to_chars:
                dropped = self.history.pop(0)
                history_chars -= _estimate_message_chars(dropped)
                dropped_for_task_compaction.append(dropped)

        artifact = self._compact_history_segment(
            dropped_for_task_compaction,
            layer="task",
        )
        if artifact is not None:
            pending_compactions.append(artifact)
        self._pending_compactions = pending_compactions

    def _compact_history_segment(self, messages: list[dict], *, layer: str) -> dict | None:
        if not messages:
            return None
        try:
            artifact = memory_store.compact_history(
                messages,
                layer=layer,
                summary_id=f"history-{self.last_turn_id or 'latest'}",
            )
        except Exception:
            return None
        if not isinstance(artifact, dict):
            return None
        path = str(artifact.get("path", "")).strip()
        if not path:
            return None
        artifact["path"] = path
        artifact["layer"] = str(artifact.get("layer", layer)).strip() or layer
        artifact["summary"] = str(artifact.get("summary", "")).strip()
        return artifact

    def _consume_pending_compactions(self) -> list[dict]:
        artifacts = list(self._pending_compactions)
        self._pending_compactions = []
        return artifacts


def _emit_tool_trace_line(text: str, *, activity_feed=None, ansi: str = "") -> None:
    emitter = getattr(activity_feed, "emit_text", None)
    if callable(emitter):
        emitter(text)
        return
    rendered = f"{ansi}{text}{ANSI_RESET}" if ansi else text
    print(rendered, file=sys.stderr)


def _print_tool_call(name: str, args: dict, prefix: str = "", activity_feed=None):
    """Print tool call info to stderr or the terminal activity feed."""
    pfx = f"{prefix} " if prefix else ""
    if name == "shell":
        command = redact_secret_like_text(str(args.get("command", "") or ""))
        _emit_tool_trace_line(f"{pfx}> {command}", activity_feed=activity_feed, ansi=ANSI_TOOL_CALL)
    elif name == "read_file":
        path = redact_secret_like_text(str(args.get("path", "") or ""))
        _emit_tool_trace_line(
            f"{pfx}> read_file: {path} (offset={args.get('offset', 0)} limit={args.get('limit', 2000)})",
            activity_feed=activity_feed,
            ansi=ANSI_TOOL_CALL,
        )
    elif name == "list_dir":
        path = redact_secret_like_text(str(args.get("path", "") or ""))
        _emit_tool_trace_line(f"{pfx}> {name}: {path}", activity_feed=activity_feed, ansi=ANSI_TOOL_CALL)
    elif name == "write_file":
        path = redact_secret_like_text(str(args.get("path", "") or ""))
        _emit_tool_trace_line(
            f"{pfx}> write_file: {path} ({len(args.get('content', ''))} chars)",
            activity_feed=activity_feed,
            ansi=ANSI_TOOL_CALL,
        )
    elif name == "edit_file":
        path = redact_secret_like_text(str(args.get("path", "") or ""))
        _emit_tool_trace_line(f"{pfx}> edit_file: {path}", activity_feed=activity_feed, ansi=ANSI_TOOL_CALL)
    elif name == "delegate_code_task":
        worker = str(args.get("worker", "auto") or "auto")
        mode = str(args.get("mode", "implement") or "implement")
        execution_mode = str(args.get("execution_mode", "auto") or "auto")
        _emit_tool_trace_line(
            f"{pfx}> delegate_code_task: worker={worker} mode={mode} execution={execution_mode}",
            activity_feed=activity_feed,
            ansi=ANSI_TOOL_CALL,
        )
    elif name == "worker_start":
        worker = str(args.get("worker", "auto") or "auto")
        mode = str(args.get("mode", "review") or "review")
        _emit_tool_trace_line(
            f"{pfx}> worker_start: worker={worker} mode={mode}",
            activity_feed=activity_feed,
            ansi=ANSI_TOOL_CALL,
        )
    elif name == "worker_send":
        session_id = str(args.get("session_id", "") or "")
        background = bool(args.get("background", False))
        _emit_tool_trace_line(
            f"{pfx}> worker_send: session={session_id} background={background}",
            activity_feed=activity_feed,
            ansi=ANSI_TOOL_CALL,
        )
    elif name.startswith("memory_"):
        path = redact_secret_like_text(str(args.get("path", "") or ""))
        _emit_tool_trace_line(f"{pfx}> {name}: {path}", activity_feed=activity_feed, ansi=ANSI_TOOL_CALL)
    else:
        _emit_tool_trace_line(f"{pfx}> {name}", activity_feed=activity_feed, ansi=ANSI_TOOL_CALL)


def _print_tool_result(result: str, prefix: str = "", activity_feed=None):
    """Print compact tool result to stderr or the terminal activity feed."""
    lines = result.splitlines()
    pfx = f"{prefix} " if prefix else ""
    if not lines:
        return
    first = lines[0][:200]
    _emit_tool_trace_line(f"{pfx}  {first}", activity_feed=activity_feed, ansi=ANSI_TOOL_RESULT)
    if len(lines) > 1:
        _emit_tool_trace_line(
            f"{pfx}  ... ({len(lines) - 1} more lines)",
            activity_feed=activity_feed,
            ansi=ANSI_TOOL_RESULT_META,
        )


def _is_assistant_tool_use_message(message: object) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_use"
        for block in content
    )


def _is_user_tool_result_message(message: object) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def _make_log_prefix(log_label: str, turn_id: str) -> str:
    label = (log_label or "").strip()
    if label:
        return f"[{label} turn={turn_id}]"
    return f"[turn={turn_id}]"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _estimate_history_chars(history: list[dict]) -> int:
    return sum(_estimate_message_chars(msg) for msg in history)


def _estimate_message_chars(message: object) -> int:
    """Approximate serialized payload size of a history message using JSON-like recursion.

    This is intentionally lightweight (no tokenizer dependency) and only used as a
    coarse trimming heuristic.
    """
    if message is None:
        return 0
    if isinstance(message, str):
        return len(message)
    if isinstance(message, (int, float, bool)):
        return 8
    if isinstance(message, list):
        return sum(_estimate_message_chars(item) for item in message)
    if isinstance(message, dict):
        total = 0
        for key, value in message.items():
            # `_provider_message` often duplicates content in another shape; counting it
            # lightly avoids over-penalizing Google-provider preserved payloads.
            if key == "_provider_message":
                total += 16
                continue
            total += len(str(key))
            total += _estimate_message_chars(value)
        return total
    return len(str(message))


def _chat_with_retry(
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
    max_attempts: int = 3,
    request_timeout_sec: float | None = None,
) -> LLMResponse:
    """Best-effort retry for transient provider errors (kept intentionally simple)."""
    delays = (0.35, 1.0)

    def _attempt_chat(active_llm) -> LLMResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return _call_with_timeout(
                    lambda: active_llm.chat(system_prompt, history, tools=tools),
                    request_timeout_sec,
                )
            except Exception as e:
                if attempt >= max_attempts or not _is_transient_llm_error(e):
                    raise
                time.sleep(delays[min(attempt - 1, len(delays) - 1)])

    return _attempt_chat(llm)


def _chat_stream_collect_with_retry(
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
    max_attempts: int = 3,
    request_timeout_sec: float | None = None,
) -> tuple[list[str], LLMResponse | None]:
    """Collect a streaming response with best-effort transient retry.

    This retries only around internal stream collection before any chunks are yielded
    to the caller, so retries do not duplicate user-visible output.
    """
    delays = (0.35, 1.0)
    def _attempt_stream(active_llm) -> tuple[list[str], LLMResponse | None]:
        attempt = 0
        while True:
            attempt += 1
            try:
                return _call_with_timeout(
                    lambda: _collect_stream_response(active_llm, system_prompt, history, tools),
                    request_timeout_sec,
                )
            except Exception as e:
                if attempt >= max_attempts or not _is_transient_llm_error(e):
                    raise
                time.sleep(delays[min(attempt - 1, len(delays) - 1)])

    return _attempt_stream(llm)


def _collect_stream_response(
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
) -> tuple[list[str], LLMResponse | None]:
    collected_text: list[str] = []
    response: LLMResponse | None = None
    for chunk in llm.chat_stream(system_prompt, history, tools=tools):
        if isinstance(chunk, str):
            collected_text.append(chunk)
        elif isinstance(chunk, LLMResponse):
            response = chunk
    return collected_text, response


T = TypeVar("T")


def _call_with_timeout(fn: Callable[[], T], timeout_sec: float | None) -> T:
    if timeout_sec is None or float(timeout_sec) <= 0:
        return fn()

    mailbox: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def _runner() -> None:
        try:
            mailbox.put((True, fn()))
        except Exception as e:
            mailbox.put((False, e))

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    try:
        ok, payload = mailbox.get(timeout=float(timeout_sec))
    except queue.Empty as e:
        raise TimeoutError(f"LLM request TIMEOUT after {timeout_sec}s") from e
    if ok:
        return cast(T, payload)
    raise cast(Exception, payload)


def _is_transient_llm_error(exc: Exception) -> bool:
    text = str(exc).upper()
    transient_markers = (
        "503",
        "500",
        "502",
        "504",
        "429",
        "UNAVAILABLE",
        "RATE LIMIT",
        "TIMEOUT",
        "TEMPORAR",
        "TRY AGAIN",
    )
    return any(marker in text for marker in transient_markers)


def _maybe_capture_preference_memory(user_message: str) -> None:
    """Best-effort capture of explicit user preference statements into the memory inbox."""
    try:
        memory_store.capture_preference_to_inbox(user_message, source="user_message")
    except Exception:
        # Never let memory capture interfere with the main chat loop.
        return


def _build_turn_system_prompt(
    base_prompt: str,
    user_message: str,
    config: Config,
    profile_name: str = "default",
    skill_guidance: str = "",
    compactions: list[dict] | None = None,
) -> str:
    """Augment the system prompt with best-effort indexed memory snippets for this turn."""
    lines = [base_prompt]
    capability_summary = build_runtime_capability_summary(config, profile_name=profile_name)
    if capability_summary:
        lines.extend(["", capability_summary])
    source_awareness = build_source_awareness_summary()
    if source_awareness:
        lines.extend(["", source_awareness])
    if skill_guidance:
        lines.extend(["", skill_guidance])

    for artifact in compactions or []:
        path = str(artifact.get("path", "")).strip()
        if not path:
            continue
        lines.extend(["", "[Compacted Context]"])
        lines.append(f"path: {path}")
        layer = str(artifact.get("layer", "")).strip()
        if layer:
            lines.append(f"layer: {layer}")
        summary = str(artifact.get("summary", "")).strip()
        if summary:
            lines.append(f"summary: {summary}")

    try:
        prefetched = memory_store.prefetch_for_query(user_message, limit=2)
    except Exception:
        prefetched = []
    if not prefetched:
        return "\n".join(lines)

    lines.extend(["", "[Retrieved Memory]"])
    for item in prefetched:
        last_modified = str(item.get("last_modified", "")).strip()
        if last_modified:
            last_modified = last_modified.replace("+00:00", "Z")
        lines.append(
            f"- path={item.get('path','')} kind={item.get('kind','')} "
            f"scope={item.get('scope','')} stability={item.get('stability','')} "
            f"confidence={item.get('confidence','')} "
            f"last_modified={last_modified or 'unknown'} "
            f"score={item.get('score', 0)}"
        )
        excerpt = str(item.get("excerpt", "")).strip()
        if excerpt:
            lines.append(excerpt)
    return "\n".join(lines)
