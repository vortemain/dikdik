"""Original DIKDIK adapter for the public chat-completions tool-call protocol."""

import json
import uuid
from typing import Callable

from .agent import ModelResponse, ToolCall
from .model_config import ModelConfig, from_environment
from .providers import chat_completion, usage_breakdown
from .model_errors import ProviderError
from .model_limits import MAX_MODEL_TEXT_BYTES, MAX_TOOL_ARGUMENT_BYTES

DEFAULT_AGENT_OUTPUT_TOKENS = 8192
ADAPTIVE_OUTPUT_CEILING = 32768


def reasoning_context(message: dict) -> dict:
    """Return only provider continuation fields, never arbitrary response metadata."""
    details = message.get("reasoning_details")
    if details:
        if not isinstance(details, list) or not all(isinstance(d, dict) for d in details):
            raise ProviderError("protocol", validation="reasoning_shape")
        context = {"reasoning_details": details}
    else:
        context = next(({key: message[key]} for key in ("reasoning_content", "reasoning")
                        if isinstance(message.get(key), str) and message[key]), {})
    encoded = json.dumps(context, allow_nan=False)
    if len(encoded.encode()) > 65536:
        raise ProviderError("response_limit")
    return json.loads(encoded)


class CompatibleAgentModel:
    def __init__(self, approve_external: bool, resolve: Callable[[], ModelConfig] = from_environment,
                 on_text: Callable[[str], None] | None = None, cancel=None):
        if approve_external is not True:
            raise ValueError("Explicit external-code disclosure approval required")
        self._resolve, self._config, self._on_text = resolve, None, on_text
        self._cancel = cancel
        self._output_tokens = None
        self._repair_hint = ""
        self._plain_response = False

    def respond(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        # Resolved once per task, on first use, so the key is decrypted only when needed.
        self._config = self._config or self._resolve()
        configured = (self._config.options or {}).get("max_output_tokens")
        if self._output_tokens is None:
            self._output_tokens = configured or DEFAULT_AGENT_OUTPUT_TOKENS
        try:
            request_messages = messages + ([{"role": "user", "content": self._repair_hint}] if self._repair_hint else [])
            data = chat_completion(request_messages, tools, max_tokens=self._output_tokens, config=self._config,
                                   on_text=None if self._plain_response else self._on_text,
                                   **({"cancel": self._cancel} if self._cancel is not None else {}))
        except ProviderError as exc:
            if exc.kind == "output_limit":
                raise self._truncated(configured, exc.usage) from None
            if exc.kind == "protocol":
                # A rejected transport response has executed no tools. Regenerate
                # without SSE; the agent's existing three-attempt budget applies.
                self._plain_response = True
                raise self._malformed(exc.validation or "transport_shape", {"usage": exc.usage}) from None
            raise
        finally:
            if self._on_text:
                self._on_text("")  # the persisted message (or tool call) replaces the live text
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") in {"length", "max_tokens"}:
                raise self._truncated(configured, usage_breakdown(data))
            if choice.get("finish_reason") == "content_filter":
                raise ProviderError("content_filter", usage=usage_breakdown(data))
            if choice.get("finish_reason") == "error":
                raise ProviderError("provider", usage=usage_breakdown(data))
            message = choice["message"]
            content = message.get("content")
            content = "" if content is None else content
            if not isinstance(content, str):
                raise self._malformed("text_type", data)
            if len(json.dumps(content, ensure_ascii=True).encode()) > MAX_MODEL_TEXT_BYTES:
                raise self._malformed("text_size", data)
            raw_calls = message.get("tool_calls")
            raw_calls = [] if raw_calls is None else raw_calls
            if not isinstance(raw_calls, list):
                raise self._malformed("tool_list", data)
            if len(raw_calls) > 8:
                raise self._malformed("tool_count", data)
            calls = []
            ids = set()
            for call in raw_calls:
                identity = call.get("id") or "call_" + uuid.uuid4().hex
                function = call["function"]
                name, arguments = function["name"], function["arguments"]
                if call.get("type", "function") != "function" or not isinstance(identity, str) or not 1 <= len(identity) <= 128 or identity in ids:
                    raise self._malformed("tool_identity", data)
                if not isinstance(name, str) or not 1 <= len(name) <= 64:
                    raise self._malformed("tool_name", data)
                if not isinstance(arguments, (str, dict)):
                    raise self._malformed("arguments_type", data)
                try:
                    if len(json.dumps(arguments).encode()) > 2 * MAX_TOOL_ARGUMENT_BYTES:
                        raise self._malformed("arguments_size", data)
                    parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (ValueError, TypeError) as exc:
                    if isinstance(exc, ProviderError):
                        raise
                    raise self._malformed("arguments_json", data) from None
                if not isinstance(parsed, dict):
                    raise self._malformed("arguments_type", data)
                if len(json.dumps(parsed, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()) > MAX_TOOL_ARGUMENT_BYTES:
                    raise self._malformed("arguments_size", data)
                ids.add(identity)
                calls.append(ToolCall(identity, name, parsed))
            continuation = reasoning_context(message)
            if not content.strip() and not calls:
                raise ProviderError("empty_response", usage=usage_breakdown(data))
        except ProviderError as exc:
            if exc.kind == "protocol" and not exc.retryable:
                raise self._malformed(exc.validation or "envelope", data) from None
            raise
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._malformed("envelope", data) from None
        usage = usage_breakdown(data)
        reported = usage.get("total_tokens", 0)
        thinking = message.get("reasoning_content") or message.get("reasoning") or ""
        self._repair_hint = ""
        self._plain_response = False
        return ModelResponse(content=content, tool_calls=calls, usage_tokens=reported,
                             reasoning=thinking[:16000] if isinstance(thinking, str) else "", usage=usage,
                             reasoning_context=continuation)

    def _malformed(self, validation, data):
        failure = ProviderError("protocol", validation=validation, retryable=True,
                                usage=usage_breakdown(data) if isinstance(data, dict) else {})
        self._repair_hint = (
            "Runtime response validation: " + failure.public_message +
            " The discarded response executed no tools. Return valid JSON object arguments matching the supplied tool schemas, "
            "with at most eight calls. When editing a large file, prefer small unique edit_file replacements over a full rewrite. "
            "Use the existing task plan and retained findings; do not restart investigation just to repair response formatting."
        )
        return failure

    def _truncated(self, configured, usage):
        # An explicit profile cap is a spending/response constraint, not a suggestion.
        retryable = configured is None and self._output_tokens < ADAPTIVE_OUTPUT_CEILING
        if retryable:
            self._output_tokens = min(self._output_tokens * 2, ADAPTIVE_OUTPUT_CEILING)
        return ProviderError("output_limit", "Incomplete model response: output-token limit reached",
                             retryable=retryable, usage=usage)
