"""Shared turn-execution helpers for executor cutover."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Generator

from archon.control.policy import evaluate_mcp_policy, evaluate_tool_policy
from archon.llm import LLMResponse
from archon.security.redaction import redact_secret_like_text

if TYPE_CHECKING:
    from archon.agent import Agent


def execute_turn(
    agent: "Agent",
    *,
    turn_id: str,
    user_message: str,
    active_profile: str,
    log_prefix: str,
    turn_system_prompt: str,
    llm_step: Callable[[str], LLMResponse],
) -> str:
    """Execute a single non-streaming assistant turn.

    The caller is responsible for turn preparation (history repair, user message
    append, prompt construction) and for providing the per-iteration LLM step.
    """
    from archon.agent import _detect_tool_loop, _print_tool_call, _print_tool_result

    recent_tool_calls: list[tuple[str, dict]] = []

    for iteration in range(agent.max_iterations):
        if iteration > 0:
            iteration_hint = (
                f"\n\n[Iteration {iteration + 1}/{agent.max_iterations}. "
                "Be targeted — don't repeat previous approaches.]"
            )
            iter_system_prompt = turn_system_prompt + iteration_hint
        else:
            iter_system_prompt = turn_system_prompt

        if agent.on_thinking:
            agent.on_thinking()

        response = llm_step(iter_system_prompt)
        agent.total_input_tokens += response.input_tokens
        agent.total_output_tokens += response.output_tokens

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
                        continue

                agent._emit_hook(
                    "pre_tool",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
                _print_tool_call(call.name, call.arguments, prefix=log_prefix)
                if agent.on_tool_call:
                    agent.on_tool_call(call.name, call.arguments)
                result = agent.tools.execute(call.name, call.arguments)
                result_text = redact_secret_like_text(str(result))
                _print_tool_result(result_text, prefix=log_prefix)
                history_result = agent._truncate_tool_result_for_history(
                    call.name,
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
        except Exception:
            if agent.history:
                from archon.agent import _is_assistant_tool_use_message

                if _is_assistant_tool_use_message(agent.history[-1]):
                    agent.history.pop()
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
) -> Generator[str, None, None]:
    """Execute a single streaming assistant turn.

    The caller is responsible for turn preparation (history repair, user message
    append, prompt construction) and for providing the per-iteration streaming step.
    """
    from archon.agent import _detect_tool_loop, _print_tool_call, _print_tool_result

    recent_tool_calls: list[tuple[str, dict]] = []

    for iteration in range(agent.max_iterations):
        if iteration > 0:
            iteration_hint = (
                f"\n\n[Iteration {iteration + 1}/{agent.max_iterations}. "
                "Be targeted — don't repeat previous approaches.]"
            )
            iter_system_prompt = turn_system_prompt + iteration_hint
        else:
            iter_system_prompt = turn_system_prompt

        if agent.on_thinking:
            agent.on_thinking()

        collected_text, response = llm_stream_step(iter_system_prompt)
        if response is None:
            yield "[Stream ended without response]"
            return

        agent.total_input_tokens += response.input_tokens
        agent.total_output_tokens += response.output_tokens

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
                        continue

                agent._emit_hook(
                    "pre_tool",
                    {
                        "turn_id": turn_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
                _print_tool_call(call.name, call.arguments, prefix=log_prefix)
                if agent.on_tool_call:
                    agent.on_tool_call(call.name, call.arguments)
                result = agent.tools.execute(call.name, call.arguments)
                result_text = redact_secret_like_text(str(result))
                _print_tool_result(result_text, prefix=log_prefix)
                history_result = agent._truncate_tool_result_for_history(
                    call.name,
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
        except Exception:
            if agent.history:
                from archon.agent import _is_assistant_tool_use_message

                if _is_assistant_tool_use_message(agent.history[-1]):
                    agent.history.pop()
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
