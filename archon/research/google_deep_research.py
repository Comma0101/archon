"""Thin wrapper around Google's Deep Research Interactions API."""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from typing import Any
import warnings


DEFAULT_DEEP_RESEARCH_AGENT = "deep-research-pro-preview-12-2025"
_DEBUG_EVENT_LIMIT = 10


@dataclass(frozen=True)
class DeepResearchInteraction:
    interaction_id: str
    status: str
    output_text: str = ""


@dataclass(frozen=True)
class DeepResearchStreamEvent:
    event_type: str
    event_id: str = ""
    interaction_id: str = ""
    status: str = ""
    text: str = ""
    delta_type: str = ""


@dataclass(frozen=True)
class DeepResearchStream:
    interaction_id: str
    status: str
    events: object
    last_event_id: str = ""


class GoogleDeepResearchClient:
    """Start and inspect Deep Research interactions.

    Official constraints encoded here:
    - Deep Research runs through the Interactions API, not generate_content.
    - Long-running research must run with background=True.
    - Background interactions must also set store=True.
    - Remote MCP / custom tools are not supported here; only built-in web
      research and optional file_search style tools are allowed.
    """

    def __init__(
        self,
        interactions_client: object,
        *,
        agent: str = DEFAULT_DEEP_RESEARCH_AGENT,
        thinking_summaries: str = "auto",
    ) -> None:
        self._client_ref = interactions_client  # Keep strong reference to prevent httpx client GC
        self._interactions = _resolve_interactions_client(interactions_client)
        self.agent = str(agent or DEFAULT_DEEP_RESEARCH_AGENT).strip() or DEFAULT_DEEP_RESEARCH_AGENT
        self.thinking_summaries = str(thinking_summaries or "auto").strip().lower() or "auto"

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        agent: str = DEFAULT_DEEP_RESEARCH_AGENT,
        thinking_summaries: str = "auto",
    ) -> "GoogleDeepResearchClient":
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("Missing Google API key for Deep Research")
        from google import genai

        client = genai.Client(api_key=key)
        return cls(client, agent=agent, thinking_summaries=thinking_summaries)

    def start_research(
        self,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> DeepResearchInteraction:
        interaction = self._interactions.create(
            agent=self.agent,
            input=str(prompt or ""),
            background=True,
            store=True,
            agent_config=self._agent_config(),
            tools=_validate_supported_tools(tools),
        )
        return _coerce_interaction(interaction)

    def start_research_stream(
        self,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> DeepResearchStream:
        stream = self._interactions.create(
            agent=self.agent,
            input=str(prompt or ""),
            background=True,
            store=True,
            stream=True,
            agent_config=self._agent_config(),
            tools=_validate_supported_tools(tools),
        )
        events = _coerce_stream_events(stream)
        first = next(events, None)
        if first is None:
            return DeepResearchStream(
                interaction_id="",
                status="unknown",
                last_event_id="",
                events=iter(()),
            )
        return DeepResearchStream(
            interaction_id=first.interaction_id,
            status=first.status or "unknown",
            last_event_id=first.event_id,
            events=_prepend_event(first, events),
        )

    def resume_research_stream(
        self,
        interaction_id: str,
        *,
        last_event_id: str,
    ) -> DeepResearchStream:
        stream = self._interactions.get(
            str(interaction_id or "").strip(),
            stream=True,
            last_event_id=str(last_event_id or "").strip(),
        )
        events = _coerce_stream_events(stream)
        first = next(events, None)
        if first is None:
            return DeepResearchStream(
                interaction_id=str(interaction_id or "").strip(),
                status="unknown",
                last_event_id="",
                events=iter(()),
            )
        return DeepResearchStream(
            interaction_id=first.interaction_id or str(interaction_id or "").strip(),
            status=first.status or "unknown",
            last_event_id=first.event_id,
            events=_prepend_event(first, events),
        )

    def get_research(self, interaction_id: str) -> DeepResearchInteraction:
        interaction = self._interactions.get(str(interaction_id or "").strip())
        return _coerce_interaction(interaction)

    def cancel_research(self, interaction_id: str) -> DeepResearchInteraction:
        interaction = self._interactions.cancel(str(interaction_id or "").strip())
        return _coerce_interaction(interaction)

    def _agent_config(self) -> dict[str, str]:
        return {
            "type": "deep-research",
            "thinking_summaries": self.thinking_summaries,
        }


def _resolve_interactions_client(client: object) -> object:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Interactions usage is experimental and may change in future versions.",
            category=UserWarning,
        )
        interactions = getattr(client, "interactions", None)
    return interactions if interactions is not None else client


def _validate_supported_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError(
                "Google Deep Research only supports built-in web research and optional file_search tools"
            )
        tool_type = str(tool.get("type", "") or "").strip().lower()
        if tool_type not in {"file_search"}:
            raise ValueError(
                "Google Deep Research only supports built-in web research and optional file_search tools"
            )
        normalized.append(tool)
    return normalized


def _coerce_interaction(interaction: object) -> DeepResearchInteraction:
    interaction_id = _field(interaction, "id") or _field(interaction, "name")
    status = _field(interaction, "status") or _field(interaction, "state") or "unknown"
    response = _field(interaction, "response")
    output_text = ""
    if response is not None:
        output_text = (
            _field(response, "output_text")
            or _field(response, "text")
            or ""
        )
    if not output_text:
        output_text = _extract_output_text_from_outputs(_field(interaction, "outputs"))
    return DeepResearchInteraction(
        interaction_id=str(interaction_id or "").strip(),
        status=str(status or "unknown").strip().lower() or "unknown",
        output_text=str(output_text or ""),
    )


def _field(obj: object, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _extract_output_text_from_outputs(outputs: object) -> str:
    if not isinstance(outputs, list):
        return ""
    for item in reversed(outputs):
        text = _field(item, "text")
        if text is None:
            continue
        normalized = str(text or "").strip()
        if normalized:
            return normalized
    return ""


def _coerce_stream_events(stream: object):
    debug_enabled = _deep_research_debug_enabled()
    remaining_debug_events = _DEBUG_EVENT_LIMIT
    for raw in stream:
        event = _coerce_stream_event(raw)
        if debug_enabled and remaining_debug_events > 0:
            _emit_deep_research_debug_event(raw, event)
            remaining_debug_events -= 1
        yield event


def _coerce_stream_event(event: object) -> DeepResearchStreamEvent:
    event_type = str(_field(event, "event_type") or "").strip()
    event_id = str(_field(event, "event_id") or "").strip()
    interaction = _field(event, "interaction")
    delta = _field(event, "delta")
    interaction_id = str(
        _field(event, "interaction_id")
        or _field(interaction, "id")
        or _field(interaction, "name")
        or ""
    ).strip()
    status = str(
        _field(event, "status")
        or _field(interaction, "status")
        or _field(interaction, "state")
        or ""
    ).strip().lower()
    delta_content = _field(delta, "content")
    response = _field(event, "response")
    text = str(
        _field(event, "text")
        or _field(delta, "text")
        or _field(delta_content, "text")
        or _field(response, "output_text")
        or _field(response, "text")
        or _extract_output_text_from_outputs(_field(interaction, "outputs"))
        or ""
    ).strip()
    delta_type = str(_field(delta, "type") or "").strip().lower()
    return DeepResearchStreamEvent(
        event_type=event_type,
        event_id=event_id,
        interaction_id=interaction_id,
        status=status,
        text=text,
        delta_type=delta_type,
    )


def _deep_research_debug_enabled() -> bool:
    return str(os.environ.get("ARCHON_DEEP_RESEARCH_DEBUG", "") or "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def _emit_deep_research_debug_event(raw: object, event: DeepResearchStreamEvent) -> None:
    interaction = _field(raw, "interaction")
    delta = _field(raw, "delta")
    delta_content = _field(delta, "content")
    response = _field(raw, "response")
    interaction_outputs = _extract_output_text_from_outputs(_field(interaction, "outputs"))
    raw_status = str(
        _field(raw, "status") or _field(interaction, "status") or _field(interaction, "state") or ""
    ).strip().lower() or "-"
    raw_delta_type = str(_field(delta, "type") or "").strip().lower() or "-"
    normalized_event_id = event.event_id or "-"
    normalized_delta_type = event.delta_type or "-"
    normalized_interaction_id = event.interaction_id or "-"
    print(
        "[deep-research-debug] "
        f"type={event.event_type or '-'} "
        f"raw_event_id={'yes' if str(_field(raw, 'event_id') or '').strip() else 'no'} "
        f"raw_interaction_id={'yes' if str(_field(raw, 'interaction_id') or '').strip() else 'no'} "
        f"interaction_id={normalized_interaction_id} "
        f"raw_status={raw_status} "
        f"raw_delta_type={raw_delta_type} "
        f"delta.text={'yes' if str(_field(delta, 'text') or '').strip() else 'no'} "
        f"delta.content.text={'yes' if str(_field(delta_content, 'text') or '').strip() else 'no'} "
        f"response.output_text={'yes' if str(_field(response, 'output_text') or '').strip() else 'no'} "
        f"interaction.outputs={'yes' if interaction_outputs else 'no'} "
        f"normalized_event_id={normalized_event_id} "
        f"normalized_delta_type={normalized_delta_type} "
        f"normalized_text={'yes' if event.text else 'no'}",
        file=sys.stderr,
    )


def _prepend_event(first_event: DeepResearchStreamEvent, rest):
    yield first_event
    yield from rest
