"""Bounded native subagent runner."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from archon.agent import _is_transient_llm_error
from archon.config import Config
from archon.control.policy import evaluate_tool_policy
from archon.execution.contracts import SuspensionRequest
from archon.execution.history_shaping import shape_tool_result_for_history
from archon.execution.llm_runtime import _chat_with_retry
from archon.llm import LLMClient, LLMResponse, ToolCall
from archon.security.redaction import redact_secret_like_text
from archon.tools import ToolRegistry


@dataclass(frozen=True)
class SubagentResult:
    status: str
    text: str
    input_tokens: int
    output_tokens: int
    iterations_used: int


@dataclass
class SubagentRunner:
    llm: LLMClient
    tools: ToolRegistry
    config: Config
    subagent_type: str = "explore"
    max_iterations: int | None = None
    wall_clock_timeout_sec: float | None = None
    llm_retry_attempts: int | None = None
    llm_request_timeout_sec: float | None = None
    tool_result_max_chars: int | None = None
    tool_result_worker_max_chars: int | None = None
    history: list[dict] = field(default_factory=list, init=False)
    total_input_tokens: int = field(default=0, init=False)
    total_output_tokens: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        agent_cfg = getattr(self.config, "agent", None)
        self.max_iterations = max(1, int(self.max_iterations or getattr(agent_cfg, "max_iterations", 15)))
        self.wall_clock_timeout_sec = max(
            1.0,
            float(self.wall_clock_timeout_sec or getattr(agent_cfg, "wall_clock_timeout_sec", 600.0)),
        )
        self.llm_retry_attempts = max(
            1,
            int(self.llm_retry_attempts or getattr(agent_cfg, "llm_retry_attempts", 3)),
        )
        self.llm_request_timeout_sec = float(
            self.llm_request_timeout_sec or getattr(agent_cfg, "llm_request_timeout_sec", 45.0)
        )
        self.tool_result_max_chars = max(
            200,
            int(self.tool_result_max_chars or getattr(agent_cfg, "tool_result_max_chars", 3000)),
        )
        self.tool_result_worker_max_chars = max(
            200,
            int(
                self.tool_result_worker_max_chars
                or getattr(agent_cfg, "tool_result_worker_max_chars", 1500)
            ),
        )

    def run(self, task: str, context: str = "") -> SubagentResult:
        self.history = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.history.append({"role": "user", "content": self._format_user_message(task, context)})
        started_at = time.monotonic()
        tool_error_streak = 0
        iterations_used = 0
        current_text = ""

        for iteration in range(self.max_iterations):
            if time.monotonic() - started_at > float(self.wall_clock_timeout_sec):
                return SubagentResult(
                    status="timeout",
                    text=current_text or "I stopped because the subagent exceeded its time budget.",
                    input_tokens=self.total_input_tokens,
                    output_tokens=self.total_output_tokens,
                    iterations_used=iterations_used,
                )

            if iteration == self.max_iterations - 1:
                return SubagentResult(
                    status="iteration_limit",
                    text=current_text or "[Iteration limit reached]",
                    input_tokens=self.total_input_tokens,
                    output_tokens=self.total_output_tokens,
                    iterations_used=iterations_used,
                )

            response = self._llm_step(task, context)
            iterations_used += 1
            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            self.history.append(self._make_assistant_message(response))
            current_text = response.text or ""

            if not response.tool_calls:
                return SubagentResult(
                    status="ok",
                    text=current_text,
                    input_tokens=self.total_input_tokens,
                    output_tokens=self.total_output_tokens,
                    iterations_used=iterations_used,
                )

            tool_results: list[dict] = []
            for call in response.tool_calls:
                policy = evaluate_tool_policy(
                    config=self.config,
                    tool_name=call.name,
                    mode="implement",
                    profile_name="default",
                )
                if policy.decision == "deny":
                    denied = (
                        f"Error: Policy denied tool '{call.name}' "
                        f"({policy.reason})"
                    )
                    tool_results.append(self._tool_result_block(call, denied))
                    self.history.append({"role": "user", "content": tool_results})
                    return SubagentResult(
                        status="failed",
                        text=denied,
                        input_tokens=self.total_input_tokens,
                        output_tokens=self.total_output_tokens,
                        iterations_used=iterations_used,
                    )

                result = self.tools.execute(call.name, call.arguments)
                if isinstance(result, SuspensionRequest):
                    failure = (
                        "Error: Subagents do not support suspension. "
                        f"{result.question or 'Human input required.'}"
                    )
                    tool_results.append(self._tool_result_block(call, failure))
                    self.history.append({"role": "user", "content": tool_results})
                    return SubagentResult(
                        status="failed",
                        text=failure,
                        input_tokens=self.total_input_tokens,
                        output_tokens=self.total_output_tokens,
                        iterations_used=iterations_used,
                    )

                result_text = redact_secret_like_text(str(result))
                shaped_result = shape_tool_result_for_history(
                    call.name,
                    call.arguments,
                    result_text,
                    tool_result_max_chars=self.tool_result_max_chars,
                    tool_result_worker_max_chars=self.tool_result_worker_max_chars,
                )
                tool_results.append(self._tool_result_block(call, shaped_result))
                if result_text.startswith("Error:"):
                    tool_error_streak += 1
                else:
                    tool_error_streak = 0

                if tool_error_streak >= int(
                    getattr(self.config.agent, "max_consecutive_tool_errors", 3)
                ):
                    self.history.append({"role": "user", "content": tool_results})
                    return SubagentResult(
                        status="failed",
                        text=result_text,
                        input_tokens=self.total_input_tokens,
                        output_tokens=self.total_output_tokens,
                        iterations_used=iterations_used,
                    )

            self.history.append({"role": "user", "content": tool_results})

        return SubagentResult(
            status="iteration_limit",
            text=current_text or "[Iteration limit reached]",
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
            iterations_used=iterations_used,
        )

    def _llm_step(self, task: str, context: str) -> LLMResponse:
        system_prompt = self._build_system_prompt(task, context)
        return _chat_with_retry(
            self.llm,
            system_prompt,
            self.history,
            self.tools.get_schemas(),
            max_attempts=int(self.llm_retry_attempts),
            request_timeout_sec=float(self.llm_request_timeout_sec),
            is_transient_error=_is_transient_llm_error,
        )

    def _build_system_prompt(self, task: str, context: str) -> str:
        lines = [
            "You are a bounded native Archon subagent.",
            f"Subagent type: {self.subagent_type}",
            "Complete the task concisely and stop when done.",
        ]
        if context.strip():
            lines.extend(["", "[Context]", context.strip()])
        lines.extend(["", "[Task]", task.strip()])
        return "\n".join(lines)

    def _format_user_message(self, task: str, context: str) -> str:
        task_text = str(task or "").strip()
        context_text = str(context or "").strip()
        if not context_text:
            return task_text
        return f"{task_text}\n\n[Context]\n{context_text}"

    def _make_assistant_message(self, response: LLMResponse) -> dict:
        msg = {"role": "assistant", "content": response.raw_content}
        if response.provider_message is not None:
            msg["_provider_message"] = response.provider_message
        return msg

    def _tool_result_block(self, call: ToolCall, content: str) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": call.id,
            "tool_name": call.name,
            "content": content,
        }
