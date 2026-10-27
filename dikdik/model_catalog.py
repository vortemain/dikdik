"""A small local catalog of model names per provider, for the profile editor.

These are suggestions, not a promise that a provider still serves them: any
model name can be typed. Nothing here is fetched from the network.
"""

CATALOG = {
    "anthropic": {
        "name": "Anthropic",
        "key_help": "https://console.anthropic.com/settings/keys",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"],
    },
    "openai": {
        "name": "OpenAI",
        "key_help": "https://platform.openai.com/api-keys",
        "models": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o"],
    },
    "azure": {
        "name": "Azure OpenAI",
        "key_help": "https://learn.microsoft.com/azure/ai-services/openai/quickstart",
        "models": [],
    },
    "local": {
        "name": "Local (OpenAI-compatible)",
        "key_help": "",
        "models": [],
    },
}
DEFAULT_PROVIDER = "anthropic"


def catalog() -> dict:
    return {"default_provider": DEFAULT_PROVIDER, "providers": CATALOG}
