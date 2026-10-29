"""Public model failure categories, independent of adapters and the agent loop."""

MESSAGES = {
    "authentication": "The provider rejected the API key. Check the connection in Settings.",
    "quota": "The provider account or API key is out of credit. Check its credit and spending limits.",
    "not_found": "The provider does not know this model or endpoint. Check the model configuration.",
    "rate_limit": "The provider is rate-limiting requests. Try again shortly.",
    "bad_request": "The provider refused the request. Check the model and its settings.",
    "context_length": "The provider's context window is full. Condense the conversation before resuming.",
    "output_limit": "The model exhausted its output-token budget before completing the response. Increase max output tokens in Settings and resume.",
    "content_filter": "The provider blocked the response through its content policy.",
    "provider": "The model provider is temporarily unavailable or returned an error.",
    "timeout": "The provider did not answer in time. Increase its timeout in Settings if this persists.",
    "unreachable": "Could not reach the model provider. Check the endpoint and connection.",
    "redirect": "The provider tried to redirect the request; redirects are not followed.",
    "invalid": "The request could not be sent. Check the endpoint and API key format.",
    "protocol": "The provider returned an invalid or incomplete tool-call response.",
    "response_limit": "The provider response exceeded the safe response-size limit.",
    "empty_response": "The provider returned no answer or tool calls.",
    "stream_interrupted": "The provider response stream ended before completion.",
    "cancelled": "Model request cancelled.",
}
TRANSIENT = {"rate_limit", "provider", "timeout", "unreachable", "empty_response", "stream_interrupted"}
VALIDATION_REASONS = {
    "transport_shape": "The provider's streamed or JSON response had an invalid structure.",
    "envelope": "The response envelope was malformed.",
    "text_type": "The answer text had an unsupported type.",
    "text_size": "The answer exceeded the bounded text limit; use shorter responses.",
    "tool_list": "Tool calls must be a list.",
    "tool_count": "The model requested more than eight tools in one response.",
    "tool_identity": "Tool call identifiers were invalid or duplicated.",
    "tool_name": "A tool name was missing or invalid.",
    "arguments_type": "Tool arguments must be a JSON object.",
    "arguments_size": "Tool arguments exceeded 128 KiB; split the edit into smaller replacements.",
    "arguments_json": "Tool arguments contained incomplete or invalid JSON.",
    "reasoning_shape": "Reasoning continuation fields were malformed.",
}


class ProviderError(ValueError):
    """Never display arbitrary exception/provider text through the agent loop."""

    def __init__(self, kind: str, message: str | None = None, *, status: int | None = None,
                 retryable: bool | None = None, retry_after: float = 0, usage: dict | None = None,
                 validation: str | None = None):
        self.kind = kind if kind in MESSAGES else "provider"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        self.retryable = self.kind in TRANSIENT if retryable is None else retryable
        if retry_after > 5:
            self.retryable = False  # do not retry before a provider's longer cooldown
        self.retry_after = max(0, min(retry_after, 5))
        self.usage = usage or {}
        self.validation = validation if validation in VALIDATION_REASONS else None
        self.public_message = MESSAGES[self.kind] + (f" (HTTP {self.status})" if self.status else "")
        if self.validation:
            self.public_message += " " + VALIDATION_REASONS[self.validation]
        # Existing transport callers may use a host-authored, more specific message;
        # agent events always use public_message, never arbitrary exception text.
        super().__init__(message or self.public_message)

    def details(self) -> dict:
        return {"kind": self.kind, "retryable": self.retryable,
                **({"validation": self.validation} if self.validation else {}),
                **({"http_status": self.status} if self.status else {})}
