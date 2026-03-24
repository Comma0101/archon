"""Core tool-use loop tying everything together."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Generator

from archon import memory as memory_store
from archon.control.hooks import HookBus
from archon.control.orchestrator import (
    build_route_payload,
    orchestrate_response,
    orchestrate_stream_response,
)
from archon.control.policy import evaluate_mcp_policy, evaluate_tool_policy
from archon.control.session_controller import (
    extract_explicit_native_subagent_request,
    extract_explicit_job_status_ref,
    is_ai_news_request,
    split_job_ref,
    wants_news_force_refresh,
    wants_news_telegram_delivery,
)
from archon.execution.contracts import SuspensionRequest
from archon.execution.turn_executor import execute_turn, execute_turn_stream
from archon.llm import LLMClient, LLMResponse
from archon.tools import ToolRegistry
from archon.usage import UsageEvent, record_usage_event
from archon.activity import ActivitySummary, build_injection_text as _build_activity_injection_text
from archon.prompt import (
    build_runtime_capability_summary,
    build_skill_guidance,
    build_source_awareness_summary,
    build_system_prompt,
)
from archon.config import Config
from archon.context_metrics import estimate_tokens_from_chars
from archon.execution.history_shaping import (
    shape_read_file_result_for_history,
    shape_sampled_result_for_history,
    shape_shell_result_for_history,
    shape_tool_result_for_history,
    split_shell_exit_code as _split_shell_exit_code,
    truncate_text_for_history as _truncate_text_for_history,
)
from archon.execution.llm_runtime import _chat_with_retry
from archon.security.redaction import redact_secret_like_text
from archon.research import store as research_store
from archon.streaming import chat_once_with_timeout, stream_chat_with_retry


logger = logging.getLogger(__name__)

ANSI_RESET = "\033[0m"
ANSI_TOOL_CALL = "\033[96m"       # bright cyan
ANSI_TOOL_RESULT = "\033[37m"     # readable light gray/white
ANSI_TOOL_RESULT_META = "\033[90m"  # dim for truncation summaries

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
        self.prompt_pressure_max_input_tokens = _read_config_int(
            getattr(config, "agent", None),
            "prompt_pressure_max_input_tokens",
            20000,
            minimum=0,
        )
        self.prompt_pressure_max_history_tokens = _read_config_int(
            getattr(config, "agent", None),
            "prompt_pressure_max_history_tokens",
            0,
            minimum=0,
        )
        self.prompt_pressure_retain_messages = _read_config_int(
            getattr(config, "agent", None),
            "prompt_pressure_retain_messages",
            1,
            minimum=1,
        )
        self.llm_request_timeout_sec = float(getattr(config.agent, "llm_request_timeout_sec", 45))
        self.llm_retry_attempts = max(1, int(getattr(config.agent, "llm_retry_attempts", 3)))
        self.wall_clock_timeout_sec = max(
            1.0,
            float(getattr(config.agent, "wall_clock_timeout_sec", 600.0)),
        )
        self.max_consecutive_tool_errors = max(
            1,
            int(getattr(config.agent, "max_consecutive_tool_errors", 3)),
        )
        self.diagnostic_tool_error_threshold = max(
            1,
            int(getattr(config.agent, "diagnostic_tool_error_threshold", 2)),
        )
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
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.on_thinking: Callable[[], None] | None = None  # Called when LLM call starts
        self.on_tool_call: Callable[[str, dict], None] | None = None  # Called on tool use
        self.hooks = HookBus()
        self.policy_profile = "default"
        self.log_label: str = ""
        self._turn_counter = 0
        self.last_turn_id: str = ""
        self.last_suspension_request: SuspensionRequest | None = None
        self._pending_compactions: list[dict] = []
        self._activity_summary: ActivitySummary | None = None
        self.session_id = f"session-{time.time_ns()}"
        self.tools.set_session_id(self.session_id)
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

    def _system_prompt_for_visible_tools(self, tool_schemas: list[dict]) -> str:
        full_count = len(self.tools.get_schemas())
        visible_count = len(tool_schemas)
        if visible_count == full_count:
            return self.system_prompt
        return (
            self.system_prompt
            + "\n\n[Visible Tool Scope]\n"
            + f"This turn exposes {visible_count} tools after profile filtering."
        )

    def _visible_tool_schemas(self, active_profile: str) -> list[dict]:
        return self.tools.get_schemas_for_profile(
            self.config,
            profile_name=active_profile,
        )

    def run(self, user_message: str, policy_profile: str | None = None) -> str | SuspensionRequest:
        """Run a single user message through the agent loop."""
        turn_id = self._next_turn_id()
        self.last_turn_id = turn_id
        self.last_suspension_request = None
        active_profile = self._resolve_policy_profile(policy_profile)
        log_prefix = _make_log_prefix(self.log_label, turn_id)
        self._trim_history_if_needed()
        self._repair_history_tool_sequence()
        native_result = self._maybe_handle_native_capability_request(
            user_message,
            active_profile=active_profile,
            turn_id=turn_id,
        )
        if native_result is not None:
            return native_result
        self.history.append({"role": "user", "content": user_message})
        _maybe_capture_preference_memory(user_message)
        skill_guidance = build_skill_guidance(self.config, profile_name=active_profile)
        pending_compactions = self._consume_pending_compactions()
        visible_tool_schemas = self._visible_tool_schemas(active_profile)
        turn_system_prompt = _build_turn_system_prompt(
            self._system_prompt_for_visible_tools(visible_tool_schemas),
            user_message,
            self.config,
            profile_name=active_profile,
            skill_guidance=skill_guidance,
            compactions=pending_compactions,
            activity_summary=self._activity_summary,
        )
        result = orchestrate_response(
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
                    visible_tool_schemas,
                    max_attempts=self.llm_retry_attempts,
                    request_timeout_sec=self.llm_request_timeout_sec,
                    is_transient_error=_is_transient_llm_error,
                ),
                llm_step_no_tools=lambda iter_system_prompt: _chat_with_retry(
                    self.llm,
                    iter_system_prompt,
                    self.history,
                    [],
                    max_attempts=self.llm_retry_attempts,
                    request_timeout_sec=self.llm_request_timeout_sec,
                    is_transient_error=_is_transient_llm_error,
                ),
            ),
            emit_hook=self._emit_hook,
        )
        if self._activity_summary is not None:
            self._activity_summary = None
        return result

    def run_stream(self, user_message: str, policy_profile: str | None = None) -> Generator[str, None, None]:
        """Run a user message, streaming the final text response.

        Yields text deltas as they arrive. Tool-call iterations use non-streaming
        internally; only the final text response is streamed to the caller.
        """
        turn_id = self._next_turn_id()
        self.last_turn_id = turn_id
        self.last_suspension_request = None
        active_profile = self._resolve_policy_profile(policy_profile)
        log_prefix = _make_log_prefix(self.log_label, turn_id)
        self._trim_history_if_needed()
        self._repair_history_tool_sequence()
        native_result = self._maybe_handle_native_capability_request(
            user_message,
            active_profile=active_profile,
            turn_id=turn_id,
        )
        if native_result is not None:
            yield native_result
            return
        self.history.append({"role": "user", "content": user_message})
        _maybe_capture_preference_memory(user_message)
        skill_guidance = build_skill_guidance(self.config, profile_name=active_profile)
        pending_compactions = self._consume_pending_compactions()
        visible_tool_schemas = self._visible_tool_schemas(active_profile)
        turn_system_prompt = _build_turn_system_prompt(
            self._system_prompt_for_visible_tools(visible_tool_schemas),
            user_message,
            self.config,
            profile_name=active_profile,
            skill_guidance=skill_guidance,
            compactions=pending_compactions,
            activity_summary=self._activity_summary,
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
                llm_stream_step=lambda iter_system_prompt, on_text_delta: stream_chat_with_retry(
                    llm=self.llm,
                    system_prompt=iter_system_prompt,
                    history=self.history,
                    tools=visible_tool_schemas,
                    on_text_delta=on_text_delta,
                    on_fallback_chat=lambda: chat_once_with_timeout(
                        llm=self.llm,
                        system_prompt=iter_system_prompt,
                        history=self.history,
                        tools=visible_tool_schemas,
                        request_timeout_sec=self.llm_request_timeout_sec,
                    ),
                    is_transient_error=_is_transient_llm_error,
                    max_attempts=self.llm_retry_attempts,
                    request_timeout_sec=self.llm_request_timeout_sec,
                ).response,
                llm_step_no_tools=lambda iter_system_prompt: _chat_with_retry(
                    self.llm,
                    iter_system_prompt,
                    self.history,
                    [],
                    max_attempts=self.llm_retry_attempts,
                    request_timeout_sec=self.llm_request_timeout_sec,
                    is_transient_error=_is_transient_llm_error,
                ),
            ),
            emit_hook=self._emit_hook,
        )
        if self._activity_summary is not None:
            self._activity_summary = None

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
        self.last_input_tokens = 0
        self.last_output_tokens = 0

    def _record_llm_usage(self, *, turn_id: str, source: str, response: LLMResponse) -> bool:
        """Persist and emit normalized usage for an LLM response."""
        session_id = str(getattr(self, "session_id", "") or "").strip()
        provider = str(getattr(self.llm, "provider", "") or "").strip()
        model = str(getattr(self.llm, "model", "") or "").strip()
        input_tokens = getattr(response, "input_tokens", None)
        output_tokens = getattr(response, "output_tokens", None)
        self.last_input_tokens = max(0, int(input_tokens or 0))
        self.last_output_tokens = max(0, int(output_tokens or 0))

        payload = {
            "source": str(source or "").strip() or "chat",
            "session_id": session_id,
            "provider": provider,
            "model": model,
            "input_tokens": None if input_tokens is None else int(input_tokens),
            "output_tokens": None if output_tokens is None else int(output_tokens),
        }

        recorded = False
        if (
            session_id
            and provider
            and model
            and payload["input_tokens"] is not None
            and payload["output_tokens"] is not None
        ):
            try:
                event = UsageEvent(
                    event_id=f"{turn_id}:{payload['source']}:{time.time_ns()}",
                    session_id=session_id,
                    turn_id=turn_id,
                    source=payload["source"],
                    provider=provider,
                    model=model,
                    input_tokens=payload["input_tokens"],
                    output_tokens=payload["output_tokens"],
                    recorded_at=time.time(),
                )
                recorded = record_usage_event(event)
            except Exception:
                recorded = False

        hook_payload = dict(payload)
        hook_payload["recorded"] = recorded
        self._emit_hook("usage.recorded", hook_payload)
        return recorded

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
        if kind == "ux_event":
            event = hook_payload.get("event")
            if event is not None:
                self._emit_hook(
                    "ux.tool_event",
                {
                    "event": event,
                    "status": str(hook_payload.get("status", "") or ""),
                    "turn_id": self.last_turn_id,
                },
            )
            return
        if kind == "subagent_usage":
            source = str(hook_payload.get("source", "") or "").strip() or "subagent"
            provider = str(hook_payload.get("provider", "") or "").strip()
            model = str(hook_payload.get("model", "") or "").strip()
            input_tokens = max(0, int(hook_payload.get("input_tokens") or 0))
            output_tokens = max(0, int(hook_payload.get("output_tokens") or 0))
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.last_input_tokens = input_tokens
            self.last_output_tokens = output_tokens
            recorded = False
            if (
                self.session_id
                and provider
                and model
                and hook_payload.get("input_tokens") is not None
                and hook_payload.get("output_tokens") is not None
            ):
                try:
                    event = UsageEvent(
                        event_id=f"{self.last_turn_id}:{source}:{time.time_ns()}",
                        session_id=self.session_id,
                        turn_id=self.last_turn_id,
                        source=source,
                        provider=provider,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        recorded_at=time.time(),
                    )
                    recorded = record_usage_event(event)
                except Exception:
                    recorded = False
            self._emit_hook(
                "usage.recorded",
                {
                    "source": source,
                    "session_id": self.session_id,
                    "provider": provider,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "recorded": recorded,
                },
            )
            return
        if kind != "post_execute":
            return
        from archon.ux import events as ux_events
        from archon.ux.renderers import build_tool_summary

        name = str(hook_payload.get("name", "") or "").strip()
        status = str(hook_payload.get("status", "") or "").strip().lower()
        meta = hook_payload.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        result_preview = str(hook_payload.get("result_preview", "") or "")
        if status == "blocked":
            event = ux_events.tool_blocked(
                tool=name,
                session_id=self.session_id,
                command_preview=str(meta.get("command_preview", "") or ""),
                safety_level=str(meta.get("safety_level", "DANGEROUS") or "DANGEROUS"),
            )
            self._emit_hook("ux.tool_event", {"event": event, "turn_id": self.last_turn_id})
            return
        if status in {"ok", "error"}:
            summary = build_tool_summary(name, meta, result_preview)
            event = ux_events.tool_end(name, summary, session_id=self.session_id)
            self._emit_hook(
                "ux.tool_event",
                {
                    "event": event,
                    "status": "failed" if status == "error" else "completed",
                    "turn_id": self.last_turn_id,
                },
            )

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
        cfg_profile = ""
        if orchestrator_cfg is not None:
            cfg_profile = str(
                getattr(orchestrator_cfg, "default_profile", "default") or "default"
            ).strip()
        if cfg_profile:
            return cfg_profile
        return "default"

    def _maybe_handle_native_capability_request(
        self,
        user_message: str,
        *,
        active_profile: str,
        turn_id: str,
    ) -> str | None:
        if is_ai_news_request(user_message):
            return self._execute_native_tool_request(
                user_message,
                active_profile=active_profile,
                turn_id=turn_id,
                tool_name="news_brief",
                arguments={
                    "force_refresh": wants_news_force_refresh(user_message),
                    "send_to_telegram": wants_news_telegram_delivery(user_message),
                },
                route_path="native_news_direct",
                route_reason="native_news_request",
            )

        subagent_type, subagent_task = extract_explicit_native_subagent_request(user_message)
        if subagent_type and subagent_task:
            return self._execute_native_tool_request(
                user_message,
                active_profile=active_profile,
                turn_id=turn_id,
                tool_name="spawn_subagent",
                arguments={
                    "type": subagent_type,
                    "task": subagent_task,
                    "context": "",
                },
                route_path="native_subagent_direct",
                route_reason="native_subagent_request",
            )

        job_ref = extract_explicit_job_status_ref(user_message)
        if job_ref:
            kind, identifier = split_job_ref(job_ref)
            if kind == "research" and identifier:
                return self._execute_native_tool_request(
                    user_message,
                    active_profile=active_profile,
                    turn_id=turn_id,
                    tool_name="check_research_job",
                    arguments={"job_id": job_ref},
                    route_path="native_research_status_direct",
                    route_reason="native_research_status_request",
                )
            if kind == "worker" and identifier:
                return self._execute_native_tool_request(
                    user_message,
                    active_profile=active_profile,
                    turn_id=turn_id,
                    tool_name="worker_status",
                    arguments={"session_id": identifier},
                    route_path="native_worker_status_direct",
                    route_reason="native_worker_status_request",
                )
            if kind == "call" and identifier:
                return self._execute_native_tool_request(
                    user_message,
                    active_profile=active_profile,
                    turn_id=turn_id,
                    tool_name="call_mission_status",
                    arguments={"call_session_id": identifier},
                    route_path="native_call_status_direct",
                    route_reason="native_call_status_request",
                )
        return None

    def _execute_native_tool_request(
        self,
        user_message: str,
        *,
        active_profile: str,
        turn_id: str,
        tool_name: str,
        arguments: dict,
        route_path: str,
        route_reason: str,
    ) -> str:
        policy = evaluate_tool_policy(
            config=self.config,
            tool_name=tool_name,
            mode="review",
            profile_name=active_profile,
        )
        self._emit_hook(
            "policy.decision",
            {
                "tool_name": tool_name,
                "decision": policy.decision,
                "reason": policy.reason,
                "profile": policy.profile,
                "mode": policy.mode,
            },
        )
        self._emit_hook(
            "orchestrator.route",
            build_route_payload(
                turn_id=turn_id,
                mode=self._orchestrator_mode(),
                path=route_path,
                lane="operator",
                reason=route_reason,
            ),
        )

        self.history.append({"role": "user", "content": user_message})
        _maybe_capture_preference_memory(user_message)
        if policy.decision == "deny":
            response = (
                f"Tool '{tool_name}' is not allowed under profile '{policy.profile}' "
                f"({policy.reason})."
            )
        else:
            if self.on_tool_call is not None:
                try:
                    self.on_tool_call(tool_name, dict(arguments))
                except Exception:
                    pass
            response = self.tools.execute(tool_name, arguments)
        self.history.append({"role": "assistant", "content": response})
        return response

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
        return _truncate_text_for_history(
            result_text,
            self.tool_result_worker_max_chars
            if (tool_name or "").strip().lower() in {
                "delegate_code_task",
                "worker_start",
                "worker_send",
                "worker_status",
                "worker_poll",
                "worker_list",
            }
            else self.tool_result_max_chars,
        )

    def _shape_tool_result_for_history(
        self,
        tool_name: str,
        tool_args: dict,
        result_text: str,
    ) -> str:
        return shape_tool_result_for_history(
            tool_name,
            tool_args,
            result_text,
            tool_result_max_chars=self.tool_result_max_chars,
            tool_result_worker_max_chars=self.tool_result_worker_max_chars,
        )

    def _shape_shell_result_for_history(self, tool_args: dict, result_text: str) -> str:
        return shape_shell_result_for_history(
            tool_args,
            result_text,
            tool_result_max_chars=self.tool_result_max_chars,
        )

    def _shape_read_file_result_for_history(self, tool_args: dict, result_text: str) -> str:
        return shape_read_file_result_for_history(
            tool_args,
            result_text,
            tool_result_max_chars=self.tool_result_max_chars,
        )

    def _shape_sampled_result_for_history(self, tool_name: str, tool_args: dict, result_text: str) -> str:
        return shape_sampled_result_for_history(
            tool_name,
            tool_args,
            result_text,
            tool_result_max_chars=self.tool_result_max_chars,
        )

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
        current = _estimate_history_chars(self.history)
        if max_chars > 0 and trim_to > 0 and current > max_chars:
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

        if not self._should_compact_for_prompt_pressure(current):
            return

        retain_from = self._prompt_pressure_retain_from_index()
        if retain_from <= 0:
            return

        dropped_for_compaction = list(self.history[:retain_from])
        artifact = self._compact_history_segment(
            dropped_for_compaction,
            layer="task",
        )
        if artifact is None:
            return

        self.history = self.history[retain_from:]
        self._pending_compactions = list(self._pending_compactions) + [artifact]
        self._repair_history_tool_sequence()
        logger.info(
            "Prompt-pressure compacted %d older messages after %d input tokens",
            len(dropped_for_compaction),
            self.last_input_tokens,
        )

    def _should_compact_for_prompt_pressure(self, history_chars: int) -> bool:
        input_limit = max(0, int(getattr(self, "prompt_pressure_max_input_tokens", 0) or 0))
        history_limit = max(0, int(getattr(self, "prompt_pressure_max_history_tokens", 0) or 0))
        if input_limit <= 0 and history_limit <= 0:
            return False
        if input_limit > 0 and self.last_input_tokens >= input_limit:
            return True
        if history_limit > 0 and estimate_tokens_from_chars(history_chars) >= history_limit:
            return True
        return False

    def _prompt_pressure_retain_from_index(self) -> int:
        if not self.history:
            return 0

        retain_from = len(self.history) - 1
        if _is_user_tool_result_message(self.history[-1]):
            retain_from = len(self.history) - 1
            if len(self.history) >= 2 and _is_assistant_tool_use_message(self.history[-2]):
                retain_from = len(self.history) - 2
                if (
                    retain_from - 1 >= 0
                    and isinstance(self.history[retain_from - 1], dict)
                    and self.history[retain_from - 1].get("role") == "user"
                    and not _is_user_tool_result_message(self.history[retain_from - 1])
                ):
                    retain_from -= 1
        else:
            for index in range(len(self.history) - 1, -1, -1):
                message = self.history[index]
                if (
                    isinstance(message, dict)
                    and message.get("role") == "user"
                    and not _is_user_tool_result_message(message)
                ):
                    retain_from = index
                    break

        retain_messages = max(1, int(getattr(self, "prompt_pressure_retain_messages", 1) or 1))
        retain_floor = max(0, len(self.history) - retain_messages)
        return min(retain_from, retain_floor)

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

    def _consume_pending_compactions_into_prompt(self, prompt: str) -> str:
        artifacts = self._consume_pending_compactions()
        return _append_compactions_to_prompt(prompt, artifacts)


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


def _print_tool_result(tool_name: str, result: str, prefix: str = "", activity_feed=None):
    """Print compact tool result to stderr or the terminal activity feed."""
    lines = _format_tool_result_lines_for_display(tool_name, result)
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


def _format_tool_result_lines_for_display(tool_name: str, result_text: str) -> list[str]:
    name = str(tool_name or "").strip().lower()
    if name == "shell":
        return _format_shell_result_lines_for_display(result_text)
    return (result_text or "").splitlines()


def _format_shell_result_lines_for_display(result_text: str) -> list[str]:
    body, exit_code = _split_shell_exit_code(result_text)
    body_lines = body.splitlines()
    first_output = body_lines[0] if body_lines else "(no output)"
    lowered = str(result_text or "").lower()
    if "rejected by safety gate" in lowered or lowered.startswith("forbidden:"):
        state = "blocked"
    elif str(body or "").startswith("Error:") or (exit_code is not None and exit_code != 0):
        state = "error"
    else:
        state = "ok"
    summary = f"result={state}"
    if exit_code is not None:
        summary += f" | exit_code={exit_code}"
    summary += f" | output={first_output}"
    if len(body_lines) <= 1:
        return [summary]
    return [summary, f"... ({len(body_lines) - 1} more lines)"]


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




def _chat_stream_collect_with_retry(
    llm,
    system_prompt: str,
    history: list[dict],
    tools: list[dict],
    max_attempts: int = 3,
    request_timeout_sec: float | None = None,
) -> tuple[list[str], LLMResponse | None]:
    """Collect a streaming response into the legacy buffered tuple contract."""
    collected_text: list[str] = []
    result = stream_chat_with_retry(
        llm=llm,
        system_prompt=system_prompt,
        history=history,
        tools=tools,
        on_text_delta=collected_text.append,
        on_fallback_chat=lambda: chat_once_with_timeout(
            llm=llm,
            system_prompt=system_prompt,
            history=history,
            tools=tools,
            request_timeout_sec=request_timeout_sec,
        ),
        is_transient_error=_is_transient_llm_error,
        max_attempts=max_attempts,
        request_timeout_sec=request_timeout_sec,
    )
    return collected_text, result.response


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
    activity_summary: ActivitySummary | None = None,
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

    _append_compaction_lines(lines, compactions)
    activity_text = _build_activity_injection_text(
        summary=activity_summary,
        token_budget=getattr(getattr(config, "activity", None), "token_budget", 200),
    )
    if activity_text:
        lines.extend(["", activity_text])

    try:
        prefetched = memory_store.prefetch_for_query(user_message)
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


def _append_compactions_to_prompt(prompt: str, compactions: list[dict] | None) -> str:
    if not compactions:
        return prompt
    lines = [prompt]
    _append_compaction_lines(lines, compactions)
    return "\n".join(lines)


def _append_compaction_lines(lines: list[str], compactions: list[dict] | None) -> None:
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


def _read_config_int(cfg: object, name: str, default: int, *, minimum: int | None = None) -> int:
    if cfg is None:
        value = default
    else:
        raw = vars(cfg).get(name) if hasattr(cfg, "__dict__") and name in vars(cfg) else default
        if raw is default:
            try:
                raw = getattr(cfg, name)
            except Exception:
                raw = default
        value = default
        if isinstance(raw, (int, float, str)):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = default
    if minimum is not None:
        return max(minimum, value)
    return value
