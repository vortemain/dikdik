"""Anthropic's native Messages API, for profiles whose provider is Anthropic.

Anthropic's OpenAI-compatible endpoint has no prompt caching, ignores reasoning effort and
never returns thinking. This transport speaks ``POST /v1/messages`` instead and hands the
rest of Dikdik the same chat-completions shaped reply as every other provider, so the agent
loop, retries and usage accounting stay provider-neutral.
"""

import http.client
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .model_config import ModelConfig
from .model_errors import ProviderError

API_VERSION = "2023-06-01"
BINDING_BETA = "thinking-binding-controls-2026-08-01"
CACHE = {"type": "ephemeral"}
THINKING_TYPES = {"anthropic.thinking": "thinking", "anthropic.redacted_thinking": "redacted_thinking"}
MAX_CONTINUATION_BYTES = 60000  # the agent keeps at most 64 KiB of continuation per reply
MAX_PAUSES = 3
STREAMED_OUTPUT_FLOOR, PLAIN_OUTPUT_FLOOR = 32000, 16000
FINISH = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
ERROR_KINDS = {
    "authentication_error": "authentication", "permission_error": "authentication", "not_found_error": "not_found",
    "rate_limit_error": "rate_limit", "overloaded_error": "provider", "api_error": "provider",
    "request_too_large": "context_length", "billing_error": "quota", "timeout_error": "timeout",
}


def is_native(config: ModelConfig) -> bool:
    public = config.public or {}
    return public.get("provider") == "anthropic" or (urlsplit(config.url).hostname or "").lower() == "api.anthropic.com"


def endpoint(config: ModelConfig) -> str:
    base = config.url.rstrip("/").removesuffix("/chat/completions").removesuffix("/messages")
    return base + "/messages"


def model_rules(model: str) -> dict:
    """What a model accepts. Adaptive thinking from 4.6 (and every Fable/Mythos), no sampling
    parameters from 4.7, and thinking bound to its conversation on the newest models."""
    name = model.lower().rsplit("/", 1)[-1]
    found = re.search(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d+))?", name)
    if not found:
        return {"adaptive": False, "sampling": True, "effort": False, "binding": False}
    family, major = found.group(1), int(found.group(2))
    minor = int(found.group(3)) if found.group(3) and len(found.group(3)) <= 2 else 0
    version = (major, minor)
    frontier = family in {"fable", "mythos"}
    adaptive = frontier or version >= (4, 6)
    return {"adaptive": adaptive, "sampling": not frontier and version < (4, 7),
            "effort": adaptive or (family == "opus" and version >= (4, 5)),
            "binding": (frontier and version >= (5, 1)) or (family in {"opus", "sonnet"} and version >= (5, 5))}


def _image(url: str) -> dict:
    found = re.match(r"data:(image/[\w.+-]+);base64,(.*)", url, re.S)
    if found:
        return {"type": "image", "source": {"type": "base64", "media_type": found.group(1), "data": found.group(2)}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str) and part["text"]:
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url")
            if isinstance(url, str) and url:
                blocks.append(_image(url))
    return blocks


def _thinking(message: dict) -> list[dict]:
    blocks = []
    for detail in message.get("reasoning_details") or []:
        kind = THINKING_TYPES.get(detail.get("type")) if isinstance(detail, dict) else None
        if kind == "thinking" and isinstance(detail.get("signature"), str):
            blocks.append({"type": "thinking", "thinking": detail.get("thinking") or "", "signature": detail["signature"]})
        elif kind == "redacted_thinking" and isinstance(detail.get("data"), str):
            blocks.append({"type": "redacted_thinking", "data": detail["data"]})
    return blocks


def _assistant(message: dict, replay: bool) -> list[dict]:
    blocks = _thinking(message) if replay else []
    blocks += _blocks(message.get("content"))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except ValueError:
            parsed = None
        blocks.append({"type": "tool_use", "id": call.get("id"), "name": function.get("name"),
                       "input": parsed if isinstance(parsed, dict) else {}})
    return blocks


def translate(messages: list[dict], replay: bool = True) -> tuple[list[dict], list[dict]]:
    """OpenAI-shaped messages to (system blocks, Messages-API turns). Leading system messages
    form the system prompt; a later one becomes a marked note in the next user turn."""
    system, turns = [], []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        system += _blocks(messages[index].get("content"))
        index += 1

    def add(role, blocks):
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += blocks
        else:
            turns.append({"role": role, "content": blocks})

    for message in messages[index:]:
        role = message.get("role")
        if role == "assistant":
            add("assistant", _assistant(message, replay))
        elif role == "tool":
            body = message.get("content")
            result = body if isinstance(body, str) else _blocks(body) or json.dumps(body)
            add("user", [{"type": "tool_result", "tool_use_id": message.get("tool_call_id"), "content": result or "(no output)"}])
        elif role == "system":
            notes = [{**block, "text": "[Dikdik runtime note]\n" + block["text"]} if block["type"] == "text" else block
                     for block in _blocks(message.get("content"))]
            add("user", notes)
        else:
            add("user", _blocks(message.get("content")))
    for turn in turns:
        if turn["role"] == "user":  # tool results must open the turn that answers the calls
            turn["content"].sort(key=lambda block: block.get("type") != "tool_result")
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]})
    return system, turns


def _mark(blocks: list[dict]) -> None:
    for block in reversed(blocks):
        if block.get("type") not in {"thinking", "redacted_thinking"}:
            block["cache_control"] = CACHE
            return


def request_body(messages: list[dict], tools: list[dict] | None, max_tokens: int, config: ModelConfig,
                 stream: bool, replay: bool = True) -> tuple[dict, dict]:
    """The request and its extra headers. Cache breakpoints sit on the tool list, the system
    prompt and the two newest turns, so each call re-reads the previous call's prefix."""
    options = config.options or {}
    rules = model_rules(config.model)
    system, turns = translate(messages, replay)
    body = {"model": config.model, "max_tokens": max_tokens, "messages": turns}
    if options.get("max_output_tokens") is None and rules["adaptive"]:
        body["max_tokens"] = max(max_tokens, STREAMED_OUTPUT_FLOOR if stream else PLAIN_OUTPUT_FLOOR)
    if system:
        _mark(system)
        body["system"] = system
    if tools:
        body["tools"] = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                          "input_schema": tool["function"].get("parameters") or {"type": "object", "properties": {}}}
                         for tool in tools]
        body["tools"][-1]["cache_control"] = CACHE
    for turn in turns[-2:]:
        _mark(turn["content"])
    headers = {}
    if rules["adaptive"]:
        body["thinking"] = {"type": "adaptive", "display": "summarized"}
        if rules["binding"]:
            # Compaction rewrites older turns; drop the thinking it invalidates instead of failing.
            body["thinking"]["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
            headers["anthropic-beta"] = BINDING_BETA
    effort = options.get("reasoning_effort")
    if effort and rules["effort"]:
        body["output_config"] = {"effort": "low" if effort == "minimal" else effort}
    if rules["sampling"]:
        sampled = next(((key, options[key]) for key in ("temperature", "top_p") if options.get(key) is not None), None)
        if sampled:
            body[sampled[0]] = sampled[1]
    if stream:
        body["stream"] = True
    return body, headers


def failure(exc: urllib.error.HTTPError) -> tuple[ProviderError, bool]:
    """The public error, and whether the API refused a replayed thinking signature."""
    from .providers import out_of_credit, provider_failure
    try:
        detail = json.loads(exc.read(8192) or b"{}")
    except (ValueError, OSError):
        detail = {}
    error = detail.get("error") if isinstance(detail, dict) and isinstance(detail.get("error"), dict) else {}
    kind, message = error.get("type"), str(error.get("message") or "")
    if exc.code == 413 or kind == "request_too_large" or "prompt is too long" in message:
        return ProviderError("context_length", "The conversation is longer than this model's context window", status=exc.code), False
    if exc.code == 529 or kind == "overloaded_error":
        return ProviderError("provider", "Anthropic is overloaded right now. Try again shortly", status=exc.code), False
    if kind == "billing_error" or out_of_credit(message):
        return ProviderError("quota", status=exc.code), False
    signature = exc.code == 400 and "signature" in message and "thinking" in message
    return provider_failure(exc), signature


def stream_failure(event: dict) -> ProviderError:
    error = event.get("error") if isinstance(event.get("error"), dict) else {}
    return ProviderError(ERROR_KINDS.get(error.get("type"), "provider"))


def assemble(lines, on_text, prefix: str = "") -> dict:
    """Rebuild one Messages response from its server-sent events."""
    from .providers import RESPONSE_LIMIT, STREAM_WIRE_LIMIT
    message, blocks, partial, size, text, finished = {}, {}, {}, 0, prefix, False
    for raw in lines:
        size += len(raw)
        if size > STREAM_WIRE_LIMIT or len(raw) > RESPONSE_LIMIT:
            raise ProviderError("response_limit", "Provider response exceeds limit")
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "error":
            raise stream_failure(event)
        if kind == "message_start" and isinstance(event.get("message"), dict):
            message = {**event["message"], "content": []}
        elif kind == "content_block_start" and isinstance(event.get("content_block"), dict):
            blocks[event.get("index")] = dict(event["content_block"])
        elif kind == "content_block_delta" and isinstance(event.get("delta"), dict):
            block, delta = blocks.get(event.get("index")), event["delta"]
            if block is None:
                raise ProviderError("protocol", validation="envelope")
            change = delta.get("type")
            if change == "text_delta":
                block["text"] = block.get("text", "") + str(delta.get("text", ""))
                text += str(delta.get("text", ""))
                if on_text and delta.get("text"):
                    on_text(text)
            elif change == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + str(delta.get("thinking", ""))
            elif change == "signature_delta":
                block["signature"] = block.get("signature", "") + str(delta.get("signature", ""))
            elif change == "input_json_delta":
                partial[event.get("index")] = partial.get(event.get("index"), "") + str(delta.get("partial_json", ""))
        elif kind == "content_block_stop":
            index = event.get("index")
            if index in partial and index in blocks:
                try:
                    blocks[index]["input"] = json.loads(partial.pop(index) or "{}")
                except ValueError:
                    raise ProviderError("protocol", validation="arguments_json") from None
        elif kind == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            message.update({key: value for key, value in delta.items() if key in {"stop_reason", "stop_details"}})
            if isinstance(event.get("usage"), dict):
                message["usage"] = {**(message.get("usage") or {}), **{k: v for k, v in event["usage"].items() if v is not None}}
        elif kind == "message_stop":
            finished = True
            break
    if not finished:
        raise ProviderError("stream_interrupted", usage=_usage(message.get("usage")))
    message["content"] = [blocks[index] for index in sorted(key for key in blocks if type(key) is int)]
    return message


def _usage(usage) -> dict:
    """Anthropic counts cached input apart from ``input_tokens``; prompt_tokens is the whole prompt."""
    if not isinstance(usage, dict):
        return {}
    count = {key: usage[key] for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
             if type(usage.get(key)) is int and usage[key] >= 0}
    prompt = count.get("input_tokens", 0) + count.get("cache_read_input_tokens", 0) + count.get("cache_creation_input_tokens", 0)
    result = {"prompt_tokens": prompt, "completion_tokens": count.get("output_tokens", 0),
              "total_tokens": prompt + count.get("output_tokens", 0)}
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if key in count:
            result[key] = count[key]
    return result


def _add_usage(total: dict, usage: dict) -> dict:
    return {key: total.get(key, 0) + usage.get(key, 0) for key in {*total, *usage}}


def to_completion(content: list[dict], stop_reason, usage: dict) -> dict:
    """A Messages reply as the chat-completions reply the agent loop reads."""
    usage_data = {"usage": usage}
    if stop_reason == "refusal":
        raise ProviderError("content_filter", "The model declined this request", usage=usage)
    if stop_reason == "model_context_window_exceeded":
        raise ProviderError("context_length", usage=usage)
    text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
    calls = [{"id": block.get("id"), "type": "function",
              "function": {"name": block.get("name"), "arguments": json.dumps(block.get("input") or {})}}
             for block in content if block.get("type") == "tool_use"]
    details = []
    for block in content:
        if block.get("type") == "thinking" and isinstance(block.get("signature"), str):
            details.append({"type": "anthropic.thinking", "thinking": block.get("thinking") or "", "signature": block["signature"]})
        elif block.get("type") == "redacted_thinking" and isinstance(block.get("data"), str):
            details.append({"type": "anthropic.redacted_thinking", "data": block["data"]})
    if len(json.dumps(details).encode()) > MAX_CONTINUATION_BYTES:
        details = []  # dropping every block is always valid; a partial set is not
    summary = "\n\n".join(block["thinking"] for block in content if block.get("type") == "thinking" and block.get("thinking"))
    message = {"content": text or None, **({"tool_calls": calls} if calls else {}),
               **({"reasoning_content": summary} if summary else {}), **({"reasoning_details": details} if details else {})}
    return {"choices": [{"message": message, "finish_reason": FINISH.get(stop_reason, "stop")}], **usage_data}


def complete(messages, tools, max_tokens, config: ModelConfig, on_text, opener, on_response=None, check=None) -> dict:
    """One model turn. A ``pause_turn`` is resumed (at most three times) with the partial reply
    appended; a thinking signature the API no longer accepts is stripped once and retried."""
    from .providers import RESPONSE_LIMIT
    headers = {"Content-Type": "application/json", "x-api-key": config.key, "anthropic-version": API_VERSION}
    conversation, collected, usage, replay = list(messages), [], {}, True
    pauses = 0
    while True:
        body, extra = request_body(conversation, tools, max_tokens, config, on_text is not None, replay)
        if collected:
            body["messages"].append({"role": "assistant", "content": [dict(block) for block in collected]})
        try:
            if check:
                check()
            req = urllib.request.Request(endpoint(config), data=json.dumps(body).encode(), headers={**headers, **extra}, method="POST")
            with opener.open(req, timeout=config.timeout) as response:
                if on_response:
                    on_response(response)
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    prefix = "".join(block.get("text", "") for block in collected if block.get("type") == "text")
                    reply = assemble(response, on_text, prefix)
                else:
                    raw = response.read(RESPONSE_LIMIT + 1)
                    if len(raw) > RESPONSE_LIMIT:
                        raise ProviderError("response_limit", "Provider response exceeds limit")
                    try:
                        reply = json.loads(raw)
                    except ValueError:
                        raise ProviderError("protocol", "Provider did not return JSON", validation="envelope") from None
                    if not isinstance(reply, dict) or not isinstance(reply.get("content"), list):
                        if isinstance(reply, dict) and reply.get("type") == "error":
                            raise stream_failure(reply)
                        raise ProviderError("protocol", validation="envelope")
        except urllib.error.HTTPError as exc:
            problem, signature = failure(exc)
            if signature and replay:
                replay = False
                continue
            raise problem from None
        except http.client.HTTPException:
            raise ProviderError("stream_interrupted") from None
        except ProviderError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            from .providers import provider_failure
            raise provider_failure(exc) from None
        usage = _add_usage(usage, _usage(reply.get("usage")))
        collected += [block for block in reply["content"] if isinstance(block, dict)]
        if reply.get("stop_reason") == "pause_turn" and pauses < MAX_PAUSES:
            pauses += 1
            continue
        stop = "end_turn" if reply.get("stop_reason") == "pause_turn" else reply.get("stop_reason")
        return to_completion(collected, stop, usage)
