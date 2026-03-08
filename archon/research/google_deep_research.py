"""Thin wrapper around Google's Deep Research Interactions API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import warnings


DEFAULT_DEEP_RESEARCH_AGENT = "deep-research-pro-preview-12-2025"


@dataclass(frozen=True)
class DeepResearchInteraction:
    interaction_id: str
    status: str
    output_text: str = ""


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
    ) -> None:
        self._client_ref = interactions_client  # Keep strong reference to prevent httpx client GC
        self._interactions = _resolve_interactions_client(interactions_client)
        self.agent = str(agent or DEFAULT_DEEP_RESEARCH_AGENT).strip() or DEFAULT_DEEP_RESEARCH_AGENT

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        agent: str = DEFAULT_DEEP_RESEARCH_AGENT,
    ) -> "GoogleDeepResearchClient":
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("Missing Google API key for Deep Research")
        from google import genai

        client = genai.Client(api_key=key)
        return cls(client, agent=agent)

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
            tools=_validate_supported_tools(tools),
        )
        return _coerce_interaction(interaction)

    def get_research(self, interaction_id: str) -> DeepResearchInteraction:
        interaction = self._interactions.get(str(interaction_id or "").strip())
        return _coerce_interaction(interaction)

    def cancel_research(self, interaction_id: str) -> DeepResearchInteraction:
        interaction = self._interactions.cancel(str(interaction_id or "").strip())
        return _coerce_interaction(interaction)


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
