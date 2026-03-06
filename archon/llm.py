"""Thin wrapper normalizing Anthropic, OpenAI, and Google GenAI responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    raw_content: list[dict]  # For appending to history as-is
    input_tokens: int
    output_tokens: int
    provider_message: object = None  # Raw provider response (preserves thought_signature etc.)


class LLMClient:
    def __init__(self, provider: str, model: str, api_key: str,
                 temperature: float = 0.3, base_url: str = ""):
        self.provider = provider
        self.model = model
        self.temperature = temperature

        if provider == "anthropic":
            import anthropic
            kwargs = {"api_key": api_key or None}
            if base_url:
                kwargs["base_url"] = base_url
            self.client = anthropic.Anthropic(**kwargs)
        elif provider == "openai":
            import openai
            kwargs = {"api_key": api_key or None}
            if base_url:
                kwargs["base_url"] = base_url
            self.client = openai.OpenAI(**kwargs)
        elif provider == "google":
            if not api_key:
                raise ValueError(
                    "Missing Google API key. Set GEMINI_API_KEY (or [llm].api_key in ~/.config/archon/config.toml)."
                )
            from google import genai
            self.client = genai.Client(api_key=api_key or None)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def chat(self, system_prompt: str, messages: list[dict],
             tools: list[dict] | None = None) -> LLMResponse:
        if self.provider == "anthropic":
            return self._chat_anthropic(system_prompt, messages, tools)
        elif self.provider == "google":
            return self._chat_google(system_prompt, messages, tools)
        else:
            return self._chat_openai(system_prompt, messages, tools)

    def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> Generator[str | LLMResponse, None, None]:
        """Stream text deltas, yielding str chunks for text and a final LLMResponse.

        Yields:
            str: Incremental text chunk.
            LLMResponse: Final response object (always the last yield).

        Falls back to non-streaming if the provider doesn't support it.
        """
        if self.provider == "anthropic":
            yield from self._stream_anthropic(system_prompt, messages, tools)
        elif self.provider == "google":
            yield from self._stream_google(system_prompt, messages, tools)
        else:
            # OpenAI streaming
            yield from self._stream_openai(system_prompt, messages, tools)

    def _chat_anthropic(self, system_prompt: str, messages: list[dict],
                        tools: list[dict] | None) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": self.temperature,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        text = None
        tool_calls = []
        raw_content = []

        for block in response.content:
            if block.type == "text":
                text = block.text
                raw_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))
                raw_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_content=raw_content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def _stream_anthropic(
        self, system_prompt: str, messages: list[dict], tools: list[dict] | None,
    ) -> Generator[str | LLMResponse, None, None]:
        """Stream Anthropic responses, yielding text deltas then final LLMResponse."""
        kwargs = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": self.temperature,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content: list[dict] = []
        input_tokens = 0
        output_tokens = 0

        with self.client.messages.stream(**kwargs) as stream:
            current_block_type = None
            current_tool_name = ""
            current_tool_id = ""
            current_tool_json = ""

            for event in stream:
                if event.type == "message_start":
                    if hasattr(event, "message") and hasattr(event.message, "usage"):
                        input_tokens = event.message.usage.input_tokens
                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "text":
                        current_block_type = "text"
                    elif block.type == "tool_use":
                        current_block_type = "tool_use"
                        current_tool_name = block.name
                        current_tool_id = block.id
                        current_tool_json = ""
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if current_block_type == "text" and hasattr(delta, "text"):
                        text_parts.append(delta.text)
                        yield delta.text
                    elif current_block_type == "tool_use" and hasattr(delta, "partial_json"):
                        current_tool_json += delta.partial_json
                elif event.type == "content_block_stop":
                    if current_block_type == "text":
                        raw_content.append({"type": "text", "text": "".join(text_parts)})
                    elif current_block_type == "tool_use":
                        import json as _json
                        args = _json.loads(current_tool_json) if current_tool_json else {}
                        tool_calls.append(ToolCall(
                            id=current_tool_id,
                            name=current_tool_name,
                            arguments=args,
                        ))
                        raw_content.append({
                            "type": "tool_use",
                            "id": current_tool_id,
                            "name": current_tool_name,
                            "input": args,
                        })
                    current_block_type = None
                elif event.type == "message_delta":
                    if hasattr(event, "usage") and event.usage:
                        output_tokens = event.usage.output_tokens

        final_text = "".join(text_parts) if text_parts else None
        yield LLMResponse(
            text=final_text,
            tool_calls=tool_calls,
            raw_content=raw_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _stream_google(
        self, system_prompt: str, messages: list[dict], tools: list[dict] | None,
    ) -> Generator[str | LLMResponse, None, None]:
        """Stream Google GenAI responses. Falls back to non-streaming since
        Google streaming with tool calls and thought_signature is complex."""
        # Use non-streaming for now — Google's streaming API doesn't
        # reliably preserve thought_signature with tool calls.
        response = self._chat_google(system_prompt, messages, tools)
        if response.text is not None and response.text != "":
            yield response.text
        yield response

    def _stream_openai(
        self, system_prompt: str, messages: list[dict], tools: list[dict] | None,
    ) -> Generator[str | LLMResponse, None, None]:
        """Stream OpenAI responses, yielding text deltas then final LLMResponse."""
        import json as _json

        oai_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            converted = self._convert_message_to_openai(msg)
            if isinstance(converted, list):
                oai_messages.extend(converted)
            else:
                oai_messages.append(converted)

        kwargs = {
            "model": self.model,
            "messages": oai_messages,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = self._convert_tools_to_openai(tools)

        text_parts: list[str] = []
        tool_calls_map: dict[int, dict] = {}
        input_tokens = 0
        output_tokens = 0

        stream = self.client.chat.completions.create(**kwargs)
        for chunk in stream:
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                text_parts.append(delta.content)
                yield delta.content

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name if tc_delta.function and tc_delta.function.name else "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_calls_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_map[idx]["arguments"] += tc_delta.function.arguments

        final_text = "".join(text_parts) if text_parts else None
        tool_calls: list[ToolCall] = []
        raw_content: list[dict] = []

        if final_text:
            raw_content.append({"type": "text", "text": final_text})

        for idx in sorted(tool_calls_map):
            tc = tool_calls_map[idx]
            args = _json.loads(tc["arguments"]) if tc["arguments"] else {}
            tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))
            raw_content.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": args,
            })

        yield LLMResponse(
            text=final_text,
            tool_calls=tool_calls,
            raw_content=raw_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _chat_google(self, system_prompt: str, messages: list[dict],
                     tools: list[dict] | None) -> LLMResponse:
        from google.genai import types

        # Convert messages to Google format
        contents = []
        for msg in messages:
            converted = self._convert_message_to_google(msg)
            if converted:
                contents.append(converted)

        # Convert tool schemas
        google_tools = None
        if tools:
            google_tools = [self._convert_tools_to_google(tools)]

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
                tools=google_tools,
            ),
        )

        text = None
        tool_calls = []
        raw_content = []

        candidate = response.candidates[0]
        for i, part in enumerate(candidate.content.parts):
            if part.text is not None:
                text = (text or "") + part.text
                raw_content.append({"type": "text", "text": part.text})
            if part.function_call is not None:
                fc = part.function_call
                call_id = f"call_{fc.name}_{i}"
                tool_calls.append(ToolCall(
                    id=call_id,
                    name=fc.name,
                    arguments=dict(fc.args) if fc.args else {},
                ))
                raw_content.append({
                    "type": "tool_use",
                    "id": call_id,
                    "name": fc.name,
                    "input": dict(fc.args) if fc.args else {},
                })

        # Preserve the raw Content object (includes thought_signature)
        provider_message = candidate.content

        usage = response.usage_metadata
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_content=raw_content,
            input_tokens=usage.prompt_token_count if usage else 0,
            output_tokens=usage.candidates_token_count if usage else 0,
            provider_message=provider_message,
        )

    def _convert_message_to_google(self, msg: dict):
        """Convert Anthropic-format message to Google Content."""
        from google.genai import types

        # If we have the raw provider Content, use it directly
        # (preserves thought_signature and other metadata)
        provider_msg = msg.get("_provider_message")
        if provider_msg is not None:
            return provider_msg

        role = msg["role"]
        content = msg.get("content")

        if role == "user":
            if isinstance(content, str):
                return types.Content(
                    role="user",
                    parts=[types.Part(text=content)],
                )
            elif isinstance(content, list):
                # Tool results
                parts = []
                for block in content:
                    if block.get("type") == "tool_result":
                        parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name=block.get("tool_name", "unknown"),
                                response={"result": block.get("content", "")},
                            )
                        ))
                if parts:
                    return types.Content(role="user", parts=parts)
                return None

        elif role == "assistant":
            if isinstance(content, str):
                return types.Content(
                    role="model",
                    parts=[types.Part(text=content)],
                )
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if block.get("type") == "text" and block.get("text"):
                        parts.append(types.Part(text=block["text"]))
                    elif block.get("type") == "tool_use":
                        parts.append(types.Part(
                            function_call=types.FunctionCall(
                                name=block["name"],
                                args=block.get("input", {}),
                            )
                        ))
                if parts:
                    return types.Content(role="model", parts=parts)
                return None

        return None

    def _convert_tools_to_google(self, tools: list[dict]):
        """Convert Anthropic tool schemas to Google Tool format."""
        from google.genai import types

        declarations = []
        for tool in tools:
            schema = tool.get("input_schema", {})
            declarations.append(types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=schema if schema.get("properties") else None,
            ))
        return types.Tool(function_declarations=declarations)

    def _chat_openai(self, system_prompt: str, messages: list[dict],
                     tools: list[dict] | None) -> LLMResponse:
        # Convert Anthropic-style messages to OpenAI format
        oai_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            converted = self._convert_message_to_openai(msg)
            if isinstance(converted, list):
                oai_messages.extend(converted)
            else:
                oai_messages.append(converted)

        kwargs = {
            "model": self.model,
            "messages": oai_messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = self._convert_tools_to_openai(tools)

        import json
        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        text = choice.message.content
        tool_calls = []
        raw_content = []

        if text:
            raw_content.append({"type": "text", "text": text})

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))
                raw_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments),
                })

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_content=raw_content,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )

    def _convert_message_to_openai(self, msg: dict) -> dict:
        """Convert Anthropic-format message to OpenAI format."""
        role = msg["role"]
        content = msg.get("content")

        if role == "user" and isinstance(content, list):
            # Tool results
            results = []
            for block in content:
                if block.get("type") == "tool_result":
                    results.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block.get("content", ""),
                    })
            return results[0] if len(results) == 1 else results

        if role == "assistant" and isinstance(content, list):
            import json
            text_parts = []
            tool_calls = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"]),
                        }
                    })
            result = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                result["tool_calls"] = tool_calls
            return result

        return {"role": role, "content": content if isinstance(content, str) else ""}

    def _convert_tools_to_openai(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic tool schemas to OpenAI function calling format."""
        oai_tools = []
        for tool in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                }
            })
        return oai_tools
