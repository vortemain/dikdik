"""Model providers, OpenRouter-style: who they are, where their API lives, a few models.

Every provider here speaks the OpenAI chat-completions protocol at its base URL
(Azure with its own key header), so connecting one is: paste a key, pick a model.
The model lists are starting suggestions; "Load all models" asks the provider
itself (`GET <base>/models`) with the user's stored key, on request only.
Users can add their own OpenAI-compatible provider; registering it is what makes
its URL acceptable for a profile, so arbitrary endpoints never slip in unannounced.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

# kind: the stored profile/connection provider type (how requests authenticate).
PROVIDERS = {
    "openrouter": {"name": "OpenRouter", "kind": "openai", "base": "https://openrouter.ai/api/v1", "key_help": "https://openrouter.ai/keys",
                   "blurb": "One key for hundreds of models from every lab", "public_models": True,
                   "models": ["anthropic/claude-sonnet-5.5", "openai/gpt-5", "google/gemini-2.5-pro", "z-ai/glm-5.2", "deepseek/deepseek-chat-v3.1",
                              "qwen/qwen3-coder", "moonshotai/kimi-k2", "x-ai/grok-4"]},
    "anthropic": {"name": "Anthropic", "kind": "anthropic", "base": "https://api.anthropic.com/v1", "key_help": "https://console.anthropic.com/settings/keys",
                  "blurb": "Claude models", "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]},
    "openai": {"name": "OpenAI", "kind": "openai", "base": "https://api.openai.com/v1", "key_help": "https://platform.openai.com/api-keys",
               "blurb": "GPT models", "models": ["gpt-5", "gpt-5-mini", "gpt-5-codex", "gpt-4.1", "o4-mini"]},
    "google": {"name": "Google Gemini", "kind": "openai", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_help": "https://aistudio.google.com/apikey", "blurb": "Gemini models", "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]},
    "deepseek": {"name": "DeepSeek", "kind": "openai", "base": "https://api.deepseek.com/v1", "key_help": "https://platform.deepseek.com/api_keys",
                 "blurb": "DeepSeek chat and reasoning", "models": ["deepseek-chat", "deepseek-reasoner"]},
    "xai": {"name": "xAI", "kind": "openai", "base": "https://api.x.ai/v1", "key_help": "https://console.x.ai", "blurb": "Grok models",
            "models": ["grok-4", "grok-code-fast-1", "grok-3-mini"]},
    "mistral": {"name": "Mistral", "kind": "openai", "base": "https://api.mistral.ai/v1", "key_help": "https://console.mistral.ai/api-keys",
                "blurb": "Mistral and Codestral", "models": ["mistral-large-latest", "codestral-latest", "devstral-medium-latest", "mistral-small-latest"]},
    "groq": {"name": "Groq", "kind": "openai", "base": "https://api.groq.com/openai/v1", "key_help": "https://console.groq.com/keys",
             "blurb": "Very fast open models", "models": ["moonshotai/kimi-k2-instruct", "openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b"]},
    "cerebras": {"name": "Cerebras", "kind": "openai", "base": "https://api.cerebras.ai/v1", "key_help": "https://cloud.cerebras.ai",
                 "blurb": "Very fast open models", "models": ["qwen-3-coder-480b", "gpt-oss-120b", "llama-3.3-70b"]},
    "together": {"name": "Together AI", "kind": "openai", "base": "https://api.together.xyz/v1", "key_help": "https://api.together.xyz/settings/api-keys",
                 "blurb": "Open models in the cloud", "models": ["Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8", "deepseek-ai/DeepSeek-V3.1", "meta-llama/Llama-3.3-70B-Instruct-Turbo"]},
    "fireworks": {"name": "Fireworks", "kind": "openai", "base": "https://api.fireworks.ai/inference/v1", "key_help": "https://fireworks.ai/account/api-keys",
                  "blurb": "Open models in the cloud", "models": ["accounts/fireworks/models/qwen3-coder-480b-a35b-instruct", "accounts/fireworks/models/deepseek-v3p1",
                                                                  "accounts/fireworks/models/kimi-k2-instruct"]},
    "moonshot": {"name": "Moonshot (Kimi)", "kind": "openai", "base": "https://api.moonshot.ai/v1", "key_help": "https://platform.moonshot.ai/console/api-keys",
                 "blurb": "Kimi models", "models": ["kimi-k2-0905-preview", "kimi-k2-turbo-preview"]},
    "zai": {"name": "Z.ai (GLM)", "kind": "openai", "base": "https://api.z.ai/api/paas/v4", "key_help": "https://z.ai/manage-apikey/apikey-list",
            "blurb": "GLM models", "models": ["glm-5.2", "glm-4.6", "glm-4.5-air"]},
    "qwen": {"name": "Qwen (Alibaba Cloud)", "kind": "openai", "base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "key_help": "https://modelstudio.console.alibabacloud.com", "blurb": "Qwen models", "models": ["qwen3-coder-plus", "qwen-max", "qwen-plus"]},
    "perplexity": {"name": "Perplexity", "kind": "openai", "base": "https://api.perplexity.ai", "key_help": "https://www.perplexity.ai/settings/api",
                   "blurb": "Models with web search", "models": ["sonar-pro", "sonar-reasoning-pro", "sonar"]},
    "ollama": {"name": "Ollama", "kind": "local", "base": "http://127.0.0.1:11434/v1", "key_help": "https://ollama.com/download",
               "blurb": "Models running on this computer", "no_key": True, "models": ["qwen3-coder:30b", "gpt-oss:20b", "llama3.2"]},
    "lmstudio": {"name": "LM Studio", "kind": "local", "base": "http://127.0.0.1:1234/v1", "key_help": "https://lmstudio.ai",
                 "blurb": "Models running on this computer", "no_key": True, "models": []},
    "azure": {"name": "Azure OpenAI", "kind": "azure", "base": "", "key_help": "https://portal.azure.com",
              "blurb": "Your Azure OpenAI deployment (needs its endpoint)", "models": []},
}
DEFAULT_PROVIDER = "openrouter"
CUSTOM_SCHEMA = "CREATE TABLE IF NOT EXISTS custom_providers (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, base TEXT NOT NULL, created REAL NOT NULL)"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def known_bases() -> set[str]:
    return {p["base"] for p in PROVIDERS.values() if p["base"]}


def custom_providers(store) -> list[dict]:
    with store.connect() as db:
        db.execute(CUSTOM_SCHEMA)
        return [dict(r) for r in db.execute("SELECT * FROM custom_providers ORDER BY created")]


def approved_endpoints(store) -> set[str]:
    """Base URLs a profile or connection may use: the catalog plus providers the user added."""
    return known_bases() | {p["base"] for p in custom_providers(store)}


def check_base(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 400:
        raise ValueError("Enter the provider's base URL, e.g. https://api.example.com/v1")
    base = value.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    parts = urlsplit(base)
    loopback = (parts.hostname or "").lower() in LOOPBACK
    if parts.scheme not in {"https", "http"} or (parts.scheme == "http" and not loopback) or not parts.hostname:
        raise ValueError("The base URL must use https:// (http:// only for this computer)")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Leave keys and query strings out of the URL; the key has its own field")
    return base


def add_custom(store, body) -> dict:
    if not isinstance(body, dict):
        raise ValueError("Send the provider's name and base URL")
    name = body.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[\w .()-]{1,60}", name.strip() or "-"):
        raise ValueError("Name the provider with 1-60 letters, digits, spaces or . - ( )")
    base = check_base(body.get("base_url"))
    provider_id = "custom-" + uuid.uuid4().hex[:10]
    with store.connect() as db:
        db.execute(CUSTOM_SCHEMA)
        if db.execute("SELECT 1 FROM custom_providers WHERE name=?", (name.strip(),)).fetchone():
            raise ValueError(f"A provider called {name.strip()} already exists")
        db.execute("INSERT INTO custom_providers VALUES(?,?,?,?)", (provider_id, name.strip(), base, time.time()))
    return {"id": provider_id, "name": name.strip(), "base": base}


def provider_info(store, provider_id: str) -> dict:
    if provider_id in PROVIDERS:
        return {"id": provider_id, **PROVIDERS[provider_id]}
    for custom in custom_providers(store):
        if custom["id"] == provider_id:
            local = (urlsplit(custom["base"]).hostname or "") in LOOPBACK
            return {"id": custom["id"], "name": custom["name"], "kind": "local" if local else "openai", "base": custom["base"],
                    "key_help": "", "blurb": "Your provider", "models": [], "custom": True, "no_key": local}
    raise KeyError(provider_id)


def catalog(store=None, connections: list[dict] | None = None) -> dict:
    """Every provider with whether it is connected (a connection whose endpoint is its base)."""
    connected = {}
    for connection in connections or []:
        connected.setdefault(connection.get("endpoint") or "", connection)
    providers = []
    entries = [{"id": key, **value} for key, value in PROVIDERS.items()]
    if store is not None:
        entries += [provider_info(store, c["id"]) for c in custom_providers(store)]
    for entry in entries:
        match = connected.get(entry["base"]) if entry["base"] else None
        if match is None and entry["id"] in {"openai", "anthropic"}:
            match = next((c for c in connections or [] if c["provider"] == entry["id"] and not c.get("endpoint")), None)
        providers.append({**{k: v for k, v in entry.items() if k != "public_models"}, "connection": match and match["id"],
                          "connection_name": match and match["name"], "key_set": bool(match and match.get("key_set"))})
    return {"default_provider": DEFAULT_PROVIDER, "providers": {p["id"]: p for p in providers}, "order": [p["id"] for p in providers]}


def live_models(info: dict, key: str | None, timeout: float = 15) -> list[dict]:
    """The provider's own model list (`GET <base>/models`), newest information first."""
    if not info.get("base"):
        raise ValueError("This provider needs its endpoint first")
    headers = {"Accept": "application/json", "User-Agent": "dikdik"}
    if key:
        headers["api-key" if info.get("kind") == "azure" else "Authorization"] = key if info.get("kind") == "azure" else f"Bearer {key}"
    if info["id"] == "anthropic":
        headers.update({"x-api-key": key or "", "anthropic-version": "2023-06-01"})
    request = urllib.request.Request(info["base"].rstrip("/") + "/models", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            data = json.loads(answer.read(8_000_000))
    except urllib.error.HTTPError as exc:
        raise ValueError({401: "The provider rejected the key", 403: "The key is not allowed to list models",
                          404: "This provider does not offer a model list; type the model name instead"}.get(exc.code, f"The provider answered HTTP {exc.code}")) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("Could not reach the provider" + (" — is it running on this computer?" if info.get("kind") == "local" else "")) from None
    rows = data.get("data") if isinstance(data, dict) else data
    models = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
        models.append({"id": row["id"][:200], "name": str(row.get("name") or row.get("display_name") or row["id"])[:200],
                       "context": row.get("context_length") or row.get("context_window") or None,
                       "prompt_price": pricing.get("prompt"), "completion_price": pricing.get("completion"),
                       "created": row.get("created") if isinstance(row.get("created"), (int, float)) else None})
    return sorted(models, key=lambda m: -(m["created"] or 0))[:2000]
