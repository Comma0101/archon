"""Built-in tools for setup and human handoff flows."""

from __future__ import annotations

from archon.execution.contracts import SuspensionRequest


def register_setup_tools(registry) -> None:
    def ask_human(
        question: str,
        context: str = "",
        project: str = "",
        resume_hint: str = "",
    ) -> SuspensionRequest:
        return SuspensionRequest(
            reason="needs_human_input",
            question=str(question or "").strip(),
            context=str(context or "").strip(),
            project=str(project or "").strip(),
            resume_hint=str(resume_hint or "").strip(),
        )

    registry.register(
        "ask_human",
        "Ask the user for information or an action the agent cannot complete alone.",
        {
            "properties": {
                "question": {"type": "string", "description": "What the human needs to provide or do."},
                "context": {"type": "string", "description": "Why this input is needed and any helpful context."},
                "project": {"type": "string", "description": "Related project name, if any."},
                "resume_hint": {"type": "string", "description": "How work will resume after the human responds."},
            },
            "required": ["question"],
        },
        ask_human,
    )
