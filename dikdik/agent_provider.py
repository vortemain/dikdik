"""Original DIKDIK adapter for the public chat-completions tool-call protocol."""

import json
from typing import Callable

from .agent import ModelResponse, ToolCall
from .model_config import ModelConfig, from_environment
from .providers import chat_completion, usage_breakdown


class CompatibleAgentModel:
    def __init__(self, approve_external: bool, resolve: Callable[[], ModelConfig] = from_environment,
                 on_text: Callable[[str], None] | None = None):
        if approve_external is not True:
            raise ValueError("Explicit external-code disclosure approval required")
        self._resolve, self._config, self._on_text = resolve, None, on_text

    def respond(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        # Resolved once per task, on first use, so the key is decrypted only when needed.
        self._config = self._config or self._resolve()
        try:
            data = chat_completion(messages, tools, max_tokens=2048, config=self._config, on_text=self._on_text)
        finally:
            if self._on_text:
                self._on_text("")  # the persisted message (or tool call) replaces the live text
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise ValueError("Incomplete model response")
            message = choice["message"]
            content = message.get("content") or ""
            if not isinstance(content, str) or len(content.encode()) > 8192:
                raise ValueError("Invalid model text")
            raw_calls = message.get("tool_calls", [])
            if not isinstance(raw_calls, list) or len(raw_calls) > 8:
                raise ValueError("Too many model tool calls")
            calls = []
            ids = set()
            for call in raw_calls:
                identity = call["id"]
                function = call["function"]
                name, arguments = function["name"], function["arguments"]
                if call.get("type") != "function" or not isinstance(identity, str) or not 1 <= len(identity) <= 128 or identity in ids:
                    raise ValueError("Invalid tool identity")
                if not isinstance(name, str) or not 1 <= len(name) <= 64 or not isinstance(arguments, str) or len(arguments.encode()) > 32768:
                    raise ValueError("Invalid tool arguments")
                parsed = json.loads(arguments)
                if not isinstance(parsed, dict):
                    raise ValueError("Tool arguments must be an object")
                ids.add(identity)
                calls.append(ToolCall(identity, name, parsed))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ValueError("Provider returned an invalid or incomplete tool-call response") from None
        usage = usage_breakdown(data)
        reported = usage.get("total_tokens", 0)
        thinking = message.get("reasoning_content") or message.get("reasoning") or ""
        return ModelResponse(content=content, tool_calls=calls, usage_tokens=reported,
                             reasoning=thinking[:16000] if isinstance(thinking, str) else "", usage=usage)
