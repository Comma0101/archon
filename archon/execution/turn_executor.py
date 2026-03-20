"""Shared turn-execution helpers for executor cutover."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Generator

from archon.control.policy import evaluate_mcp_policy, evaluate_tool_policy
from archon.execution.contracts import SuspensionRequest
from archon.llm import LLMResponse
from archon.security.redaction import redact_secret_like_text

if TYPE_CHECKING:
    from archon.agent import Agent


def _drop_last_assistant_tool_use(agent: "Agent") -> None:
    if not getattr(agent, "history", None):
        return
    from archon.agent import _is_assistant_tool_use_message

    if _is_assistant_tool_use_message(agent.history[-1]):
        agent.history.pop()


def _maybe_add_diagnostic_hint(agent: "Agent", prompt: str, consecutive_tool_errors: int) -> str:
    if consecutive_tool_errors < getattr(agent, "diagnostic_tool_error_threshold", 2):
        return prompt
    return (
        prompt
        + "\n\n[DIAGNOSTIC]\n"
        + "You've hit repeated tool errors. Before acting again, identify the likely cause, "
        + "avoid repeating the same failed action, and stop if user input or an environment fix is required."
    )


def _finalize_without_tools(
    agent: "Agent",
    turn_system_prompt: str,
    instruction: str,
    llm_step_no_tools: Callable[[str], LLMResponse] | None,
    *,
    fallback_text: str,
) -> str:
    if llm_step_no_tools is None:
        return fallback_text
    response = llm_step_no_tools(f"{turn_system_prompt}\n\n{instruction}")
    agent.total_input_tokens += response.input_tokens
    agent.total_output_tokens += response.output_tokens
    try:
        agent._record_llm_usage(
            turn_id=getattr(agent, "last_turn_id", "") or "turn",
            source="chat",
            response=response,
        )
    except Exception:
        pass
    text = response.text or fallback_text
    agent.history.append(agent._make_assistant_msg(response))
    return text


def execute_turn(
    agent: "Agent",
    *,
    turn_id: str,
    user_message: str,
    active_profile: str,
    log_prefix: str,
    turn_system_prompt: str,
    llm_step: Callable[[str], LLMResponse],
    llm_step_no_tools: Callable[[str], LLMResponse] | None = None,
) -> str | SuspensionRequest:
    """Execute a single non-streaming assistant turn.

    The caller is responsible for turn preparation (history repair, user message
    append, prompt construction) and for providing the per-iteration LLM step.
    """
    from archon.agent import _detect_tool_loop, _print_tool_call, _print_tool_result

    recent_tool_calls: list[tuple[str, dict]] = []
    consecutive_tool_errors = 0
    started_at = time.monotonic()

    for iteration in range(agent.max_iterations):
        turn_system_prompt = agent._consume_pending_compactions_into_prompt(turn_system_prompt)

        if time.monotonic() - started_at > agent.wall_clock_timeout_sec:
            return _finalize_without_tools(
                agent,
                turn_system_prompt,
                "The turn exceeded its wall-clock time budget. Summarize what happened, what failed, and what the user should do next. Do not call tools.",
                llm_step_no_tools,
                fallback_text="I stopped because the turn exceeded its time budget.",
            )

        if iteration == agent.max_iterations - 1:
            return _finalize_without_tools(
                agent,
                turn_system_prompt,
                "You reached the step limit for this turn. Summarize what you accomplished, what failed, and what the user should do next. Do not call tools.",
                llm_step_no_tools,
                fallback_text="[Iteration limit reached]",
            )

        if iteration > 0:
            iteration_hint = (
                f"\n\n[Iteration {iteration + 1}/{agent.max_iterations}. "
                "Be targeted — don't repeat previous approaches.]"
            )
            iter_system_prompt = turn_system_prompt + iteration_hint
        else:
            iter_system_prompt = turn_system_prompt

        iter_system_prompt = _maybe_add_diagnostic_hint(
            agent,
            iter_system_prompt,
            consecutive_tool_errors,
        )

        if agent.on_thinking:
            agent.on_thinking()

        response = llm_step(iter_system_prompt)
        agent.total_input_tokens += response.input_tokens
        agent.total_output_tokens += response.output_tokens
        try:
            agent._record_llm_usage(
                turn_id=turn_id,
                source="chat",
                response=response,
            )
        except Exception:
            pass

        if not response.tool_calls:
            text = response.text or ""
            agent.history.append(agent._make_assistant_msg(response))
            return text

        agent.history.append(agent._make_assistant_msg(response))

        tool_results = []
        try:
            for call in response.tool_calls:
                policy_decision = evaluate_tool_policy(
                    config=agent.config,
                    tool_name=call.name,
                    mode="implement",
                    profile_name=active_profile,
                )
                agent._emit_hook(
                    "policy.decision",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "decision": policy_decision.decision,
                        "reason": policy_decision.reason,
                        "profile": policy_decision.profile,
                        "mode": policy_decision.mode,
                    },
                )
                if policy_decision.decision == "deny":
                    denied_result = (
                        f"Error: Policy denied tool '{call.name}' "
                        f"({policy_decision.reason})"
                    )
                    consecutive_tool_errors += 1
                    agent._emit_hook(
                        "post_tool",
                        {
                            "turn_id": turn_id,
                            "name": call.name,
                            "result_is_error": True,
                            "result_length": len(denied_result),
                            "policy_decision": policy_decision.decision,
                        },
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "tool_name": call.name,
                            "content": denied_result,
                        }
                    )
                    if consecutive_tool_errors >= agent.max_consecutive_tool_errors:
                        _drop_last_assistant_tool_use(agent)
                        return _finalize_without_tools(
                            agent,
                            turn_system_prompt,
                            "You hit repeated tool errors. Summarize what failed, what you tried, and what the user should do next. Do not call tools.",
                            llm_step_no_tools,
                            fallback_text="I stopped after repeated tool failures.",
                        )
                    continue

                if call.name == "mcp_call":
                    server_name = str(call.arguments.get("server", "") or "").strip()
                    mcp_policy_decision = evaluate_mcp_policy(
                        config=agent.config,
                        server_name=server_name,
                        profile_name=active_profile,
                    )
                    agent._emit_hook(
                        "policy.decision",
                        {
                            "turn_id": turn_id,
                            "name": f"mcp:{server_name}" if server_name else "mcp",
                            "decision": mcp_policy_decision.decision,
                            "reason": mcp_policy_decision.reason,
                            "profile": mcp_policy_decision.profile,
                            "mode": mcp_policy_decision.mode,
                        },
                    )
                    if mcp_policy_decision.decision == "deny":
                        denied_result = (
                            f"Error: Policy denied MCP server "
                            f"'{server_name or 'unknown'}' "
                            f"({mcp_policy_decision.reason})"
                        )
                        consecutive_tool_errors += 1
                        agent._emit_hook(
                            "post_tool",
                            {
                                "turn_id": turn_id,
                                "name": call.name,
                                "result_is_error": True,
                                "result_length": len(denied_result),
                                "policy_decision": mcp_policy_decision.decision,
                            },
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call.id,
                                "tool_name": call.name,
                                "content": denied_result,
                            }
                        )
                        if consecutive_tool_errors >= agent.max_consecutive_tool_errors:
                            _drop_last_assistant_tool_use(agent)
                            return _finalize_without_tools(
                                agent,
                                turn_system_prompt,
                                "You hit repeated tool errors. Summarize what failed, what you tried, and what the user should do next. Do not call tools.",
                                llm_step_no_tools,
                                fallback_text="I stopped after repeated tool failures.",
                            )
                        continue

                agent._emit_hook(
                    "pre_tool",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
                _print_tool_call(
                    call.name,
                    call.arguments,
                    prefix=log_prefix,
                    activity_feed=getattr(agent, "terminal_activity_feed", None),
                )
                if agent.on_tool_call:
                    agent.on_tool_call(call.name, call.arguments)
                result = agent.tools.execute(call.name, call.arguments)
                if isinstance(result, SuspensionRequest):
                    agent.last_suspension_request = result
                    agent._emit_hook(
                        "post_tool",
                        {
                            "turn_id": turn_id,
                            "name": call.name,
                            "result_is_error": False,
                            "result_length": 0,
                            "result_kind": "suspension",
                            "policy_decision": policy_decision.decision,
                            "job_id": result.job_id,
                        },
                    )
                    return result
                result_text = redact_secret_like_text(str(result))
                _print_tool_result(
                    result_text,
                    prefix=log_prefix,
                    activity_feed=getattr(agent, "terminal_activity_feed", None),
                )
                if result_text.startswith("Error:"):
                    consecutive_tool_errors += 1
                else:
                    consecutive_tool_errors = 0
                history_result = agent._shape_tool_result_for_history(
                    call.name,
                    call.arguments,
                    result_text,
                )
                agent._emit_hook(
                    "post_tool",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "result_is_error": result_text.startswith("Error:"),
                        "result_length": len(result_text),
                        "policy_decision": policy_decision.decision,
                    },
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "tool_name": call.name,
                        "content": history_result,
                    }
                )
                if consecutive_tool_errors >= agent.max_consecutive_tool_errors:
                    _drop_last_assistant_tool_use(agent)
                    return _finalize_without_tools(
                        agent,
                        turn_system_prompt,
                        "You hit repeated tool errors. Summarize what failed, what you tried, and what the user should do next. Do not call tools.",
                        llm_step_no_tools,
                        fallback_text="I stopped after repeated tool failures.",
                    )
        except Exception:
            _drop_last_assistant_tool_use(agent)
            raise

        for call in response.tool_calls:
            recent_tool_calls.append((call.name, call.arguments))
        if len(recent_tool_calls) > 10:
            recent_tool_calls = recent_tool_calls[-10:]
        if _detect_tool_loop(recent_tool_calls):
            stuck_msg = "I notice I'm repeating the same actions. Let me stop and reassess."
            agent.history.append({"role": "assistant", "content": stuck_msg})
            return stuck_msg

        agent.history.append(
            {
                "role": "user",
                "content": tool_results,
            }
        )
        agent._enforce_iteration_budget()

    return "[Iteration limit reached]"


def execute_turn_stream(
    agent: "Agent",
    *,
    turn_id: str,
    user_message: str,
    active_profile: str,
    log_prefix: str,
    turn_system_prompt: str,
    llm_stream_step: Callable[[str], tuple[list[str], LLMResponse | None]],
    llm_step_no_tools: Callable[[str], LLMResponse] | None = None,
) -> Generator[str, None, None]:
    """Execute a single streaming assistant turn.

    The caller is responsible for turn preparation (history repair, user message
    append, prompt construction) and for providing the per-iteration streaming step.
    """
    from archon.agent import _detect_tool_loop, _print_tool_call, _print_tool_result

    recent_tool_calls: list[tuple[str, dict]] = []
    consecutive_tool_errors = 0
    started_at = time.monotonic()

    for iteration in range(agent.max_iterations):
        turn_system_prompt = agent._consume_pending_compactions_into_prompt(turn_system_prompt)

        if time.monotonic() - started_at > agent.wall_clock_timeout_sec:
            yield _finalize_without_tools(
                agent,
                turn_system_prompt,
                "The turn exceeded its wall-clock time budget. Summarize what happened, what failed, and what the user should do next. Do not call tools.",
                llm_step_no_tools,
                fallback_text="I stopped because the turn exceeded its time budget.",
            )
            return

        if iteration == agent.max_iterations - 1:
            yield _finalize_without_tools(
                agent,
                turn_system_prompt,
                "You reached the step limit for this turn. Summarize what you accomplished, what failed, and what the user should do next. Do not call tools.",
                llm_step_no_tools,
                fallback_text="[Iteration limit reached]",
            )
            return

        if iteration > 0:
            iteration_hint = (
                f"\n\n[Iteration {iteration + 1}/{agent.max_iterations}. "
                "Be targeted — don't repeat previous approaches.]"
            )
            iter_system_prompt = turn_system_prompt + iteration_hint
        else:
            iter_system_prompt = turn_system_prompt

        iter_system_prompt = _maybe_add_diagnostic_hint(
            agent,
            iter_system_prompt,
            consecutive_tool_errors,
        )

        if agent.on_thinking:
            agent.on_thinking()

        collected_text, response = llm_stream_step(iter_system_prompt)
        if response is None:
            yield "[Stream ended without response]"
            return

        agent.total_input_tokens += response.input_tokens
        agent.total_output_tokens += response.output_tokens
        try:
            agent._record_llm_usage(
                turn_id=turn_id,
                source="chat",
                response=response,
            )
        except Exception:
            pass

        if not response.tool_calls:
            agent.history.append(agent._make_assistant_msg(response))
            if collected_text:
                yield from collected_text
            elif response.text is not None:
                yield response.text
            else:
                yield "(empty response)"
            return

        agent.history.append(agent._make_assistant_msg(response))

        tool_results = []
        try:
            for call in response.tool_calls:
                policy_decision = evaluate_tool_policy(
                    config=agent.config,
                    tool_name=call.name,
                    mode="implement",
                    profile_name=active_profile,
                )
                agent._emit_hook(
                    "policy.decision",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "decision": policy_decision.decision,
                        "reason": policy_decision.reason,
                        "profile": policy_decision.profile,
                        "mode": policy_decision.mode,
                    },
                )
                if policy_decision.decision == "deny":
                    denied_result = (
                        f"Error: Policy denied tool '{call.name}' "
                        f"({policy_decision.reason})"
                    )
                    consecutive_tool_errors += 1
                    agent._emit_hook(
                        "post_tool",
                        {
                            "turn_id": turn_id,
                            "name": call.name,
                            "result_is_error": True,
                            "result_length": len(denied_result),
                            "policy_decision": policy_decision.decision,
                        },
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "tool_name": call.name,
                            "content": denied_result,
                        }
                    )
                    if consecutive_tool_errors >= agent.max_consecutive_tool_errors:
                        _drop_last_assistant_tool_use(agent)
                        yield _finalize_without_tools(
                            agent,
                            turn_system_prompt,
                            "You hit repeated tool errors. Summarize what failed, what you tried, and what the user should do next. Do not call tools.",
                            llm_step_no_tools,
                            fallback_text="I stopped after repeated tool failures.",
                        )
                        return
                    continue

                if call.name == "mcp_call":
                    server_name = str(call.arguments.get("server", "") or "").strip()
                    mcp_policy_decision = evaluate_mcp_policy(
                        config=agent.config,
                        server_name=server_name,
                        profile_name=active_profile,
                    )
                    agent._emit_hook(
                        "policy.decision",
                        {
                            "turn_id": turn_id,
                            "name": f"mcp:{server_name}" if server_name else "mcp",
                            "decision": mcp_policy_decision.decision,
                            "reason": mcp_policy_decision.reason,
                            "profile": mcp_policy_decision.profile,
                            "mode": mcp_policy_decision.mode,
                        },
                    )
                    if mcp_policy_decision.decision == "deny":
                        denied_result = (
                            f"Error: Policy denied MCP server "
                            f"'{server_name or 'unknown'}' "
                            f"({mcp_policy_decision.reason})"
                        )
                        consecutive_tool_errors += 1
                        agent._emit_hook(
                            "post_tool",
                            {
                                "turn_id": turn_id,
                                "name": call.name,
                                "result_is_error": True,
                                "result_length": len(denied_result),
                                "policy_decision": mcp_policy_decision.decision,
                            },
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call.id,
                                "tool_name": call.name,
                                "content": denied_result,
                            }
                        )
                        if consecutive_tool_errors >= agent.max_consecutive_tool_errors:
                            _drop_last_assistant_tool_use(agent)
                            yield _finalize_without_tools(
                                agent,
                                turn_system_prompt,
                                "You hit repeated tool errors. Summarize what failed, what you tried, and what the user should do next. Do not call tools.",
                                llm_step_no_tools,
                                fallback_text="I stopped after repeated tool failures.",
                            )
                            return
                        continue

                agent._emit_hook(
                    "pre_tool",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
                _print_tool_call(
                    call.name,
                    call.arguments,
                    prefix=log_prefix,
                    activity_feed=getattr(agent, "terminal_activity_feed", None),
                )
                if agent.on_tool_call:
                    agent.on_tool_call(call.name, call.arguments)
                result = agent.tools.execute(call.name, call.arguments)
                if isinstance(result, SuspensionRequest):
                    agent.last_suspension_request = result
                    agent._emit_hook(
                        "post_tool",
                        {
                            "turn_id": turn_id,
                            "name": call.name,
                            "result_is_error": False,
                            "result_length": 0,
                            "result_kind": "suspension",
                            "policy_decision": policy_decision.decision,
                            "job_id": result.job_id,
                        },
                    )
                    return
                result_text = redact_secret_like_text(str(result))
                _print_tool_result(
                    result_text,
                    prefix=log_prefix,
                    activity_feed=getattr(agent, "terminal_activity_feed", None),
                )
                if result_text.startswith("Error:"):
                    consecutive_tool_errors += 1
                else:
                    consecutive_tool_errors = 0
                history_result = agent._shape_tool_result_for_history(
                    call.name,
                    call.arguments,
                    result_text,
                )
                agent._emit_hook(
                    "post_tool",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "result_is_error": result_text.startswith("Error:"),
                        "result_length": len(result_text),
                        "policy_decision": policy_decision.decision,
                    },
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "tool_name": call.name,
                        "content": history_result,
                    }
                )
                if consecutive_tool_errors >= agent.max_consecutive_tool_errors:
                    _drop_last_assistant_tool_use(agent)
                    yield _finalize_without_tools(
                        agent,
                        turn_system_prompt,
                        "You hit repeated tool errors. Summarize what failed, what you tried, and what the user should do next. Do not call tools.",
                        llm_step_no_tools,
                        fallback_text="I stopped after repeated tool failures.",
                    )
                    return
        except Exception:
            _drop_last_assistant_tool_use(agent)
            raise

        for call in response.tool_calls:
            recent_tool_calls.append((call.name, call.arguments))
        if len(recent_tool_calls) > 10:
            recent_tool_calls = recent_tool_calls[-10:]
        if _detect_tool_loop(recent_tool_calls):
            stuck_msg = "I notice I'm repeating the same actions. Let me stop and reassess."
            agent.history.append({"role": "assistant", "content": stuck_msg})
            yield stuck_msg
            return

        agent.history.append(
            {
                "role": "user",
                "content": tool_results,
            }
        )
        agent._enforce_iteration_budget()

    yield "[Iteration limit reached]"
