"""Which model a task talks to: the default profile, else environment variables.

The default profile (Settings → Models) wins. Its API key is decrypted from the
secrets store only when a model call is about to be made, lives in memory for
that task, and is never persisted, logged or returned. When no profile exists,
DIKDIK_MODEL_URL / DIKDIK_MODEL / DIKDIK_API_KEY are used as before.
"""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .connections import SCHEMA as CONNECTION_SCHEMA, secret_ref as connection_ref
from .model_catalog import NVIDIA_DEFAULT_MODEL, PROVIDERS as CATALOG_PROVIDERS
from .profiles import LOOPBACK_HOSTS, migrate as migrate_profiles
from .secrets_store import SecretsError

# Providers whose public API speaks the chat-completions protocol at a known base URL.
DEFAULT_ENDPOINTS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1"}
PROVIDER_NAMES = {"openai": "OpenAI", "anthropic": "Anthropic", "azure": "Azure OpenAI", "local": "Local"}
ENV_TIMEOUT, PROFILE_TIMEOUT = 30, 60


@dataclass(frozen=True)
class ModelConfig:
    url: str
    model: str
    key: str = field(repr=False)
    auth: str  # "bearer", "api-key" (Azure) or "none" (keyless local server)
    timeout: float
    public: dict  # safe to persist and show: never contains the key
    options: dict = field(default_factory=dict)  # temperature, top_p, max_output_tokens

    def __post_init__(self):
        if self.auth != "none" and (not self.key or any(ord(char) < 33 or ord(char) > 126 for char in self.key)):
            raise ValueError("API key must contain only printable ASCII without whitespace")
        if not self.model:
            raise ValueError("Model name is missing")
        check_url(self.url)


def check_url(url: str) -> None:
    """HTTPS everywhere; plain HTTP only to a server on this machine."""
    parts = urlsplit(url)
    loopback = (parts.hostname or "").lower() in LOOPBACK_HOSTS
    if parts.scheme not in {"https", "http"} or (parts.scheme == "http" and not loopback):
        raise ValueError("Model endpoints must use HTTPS (plain HTTP is allowed only for localhost)")
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Model endpoint must be a plain URL without credentials, query or fragment")


def chat_url(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def from_environment() -> ModelConfig:
    endpoint = os.environ.get("DIKDIK_MODEL_URL", "")
    model = os.environ.get("DIKDIK_MODEL", "")
    key = os.environ.get("DIKDIK_API_KEY", "")
    nvidia_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY")
    if not any((endpoint, model, key)) and nvidia_key:
        return ModelConfig("https://integrate.api.nvidia.com/v1/chat/completions", NVIDIA_DEFAULT_MODEL, nvidia_key, "bearer", PROFILE_TIMEOUT,
                           {"source": "environment", "model": NVIDIA_DEFAULT_MODEL, "host": "integrate.api.nvidia.com", "provider_name": "NVIDIA"})
    if not (endpoint and model and key):
        raise ValueError("No model is set up. Connect NVIDIA or another provider in Settings → Models, set NVIDIA_API_KEY, or set DIKDIK_MODEL_URL, DIKDIK_MODEL and DIKDIK_API_KEY on the server")
    if urlsplit(endpoint).scheme != "https":
        raise ValueError("DIKDIK_MODEL_URL must use HTTPS")
    host = urlsplit(endpoint).hostname or ""
    return ModelConfig(endpoint, model, key, "bearer", ENV_TIMEOUT, {"source": "environment", "model": model, "host": host})


class ModelResolver:
    """Reads the default profile from SQLite and its key from the encrypted store."""

    def __init__(self, store, secrets):
        self.store, self.secrets = store, secrets

    def _default_profile(self, profile_id: str | None = None) -> dict | None:
        """The chosen model profile (from an agent profile), else the default one."""
        with self.store.connect() as db:
            migrate_profiles(db)
            db.execute(CONNECTION_SCHEMA)
            if profile_id:
                row = db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
                if row is None:
                    raise ValueError("The model profile this agent profile uses was deleted. Pick another in Settings → Agents")
                return self._linked(db, dict(row))
            row = db.execute("SELECT * FROM profiles WHERE is_default=1 ORDER BY created LIMIT 1").fetchone()
            return self._linked(db, dict(row)) if row else None

    @staticmethod
    def _linked(db, profile: dict) -> dict:
        """A profile linked to a provider connection takes its provider, endpoint and key from it."""
        if not profile.get("connection_id"):
            return profile
        connection = db.execute("SELECT * FROM connections WHERE id=?", (profile["connection_id"],)).fetchone()
        if connection is None:
            raise ValueError(f"The provider connection for model profile “{profile['name']}” was deleted. Edit the profile to link another")
        endpoint = profile["endpoint"] or connection["endpoint"]
        return {**profile, "provider": connection["provider"], "endpoint": endpoint,
                "credential_secret_ref": connection_ref(connection["id"]), "connection_name": connection["name"],
                "_key_optional": connection["provider"] == "local" and
                (urlsplit(endpoint or "").hostname or "").lower() in LOOPBACK_HOSTS}

    @staticmethod
    def _public(profile: dict, base: str) -> dict:
        normalized = base.rstrip("/").removesuffix("/chat/completions")
        provider_name = next((entry["name"] for entry in CATALOG_PROVIDERS.values() if entry["base"] and entry["base"] == normalized), None)
        return {"source": "profile", "profile": profile["name"], "profile_id": profile["id"], "provider": profile["provider"],
                **({"connection": profile["connection_name"]} if profile.get("connection_name") else {}),
                "provider_name": provider_name or profile.get("connection_name") or PROVIDER_NAMES.get(profile["provider"], profile["provider"]),
                "model": profile["model"], "host": urlsplit(base).hostname or ""}

    @staticmethod
    def _base(profile: dict) -> str:
        base = profile["endpoint"] or DEFAULT_ENDPOINTS.get(profile["provider"], "")
        if not base:
            raise ValueError(f"Model profile “{profile['name']}” needs an endpoint for {PROVIDER_NAMES.get(profile['provider'], profile['provider'])}")
        return base

    def describe(self, profile_id: str | None = None) -> dict:
        """What the next task will use, without decrypting anything."""
        try:
            profile = self._default_profile(profile_id)
            if profile is None:
                try:
                    return {"configured": True, **from_environment().public}
                except ValueError as exc:
                    return {"configured": False, "source": "none", "problem": str(exc)}
            info = self._public(profile, self._base(profile))
            ref = profile["credential_secret_ref"]
            if ref and ref not in {item["ref"] for item in self.secrets.list()} and not profile.get("_key_optional"):
                return {"configured": False, **info, "problem": f"API key “{ref}” is not stored. Add it under Settings → API keys"}
            if not ref and profile["provider"] != "local":
                return {"configured": False, **info, "problem": f"Model profile “{profile['name']}” has no API key. Add one under Settings → API keys and select it on the profile"}
            return {"configured": True, **info}
        except (ValueError, SecretsError) as exc:
            return {"configured": False, "source": "profile", "problem": str(exc)}

    def resolve(self, profile_id: str | None = None) -> ModelConfig:
        profile = self._default_profile(profile_id)
        if profile is None:
            return from_environment()
        base = self._base(profile)
        ref = profile["credential_secret_ref"]
        if ref:
            auth = "api-key" if profile["provider"] == "azure" else "bearer"
            try:
                key = self.secrets.reveal(ref).decode("utf-8")
            except KeyError:
                if profile.get("_key_optional"):
                    key, auth = "", "none"
                else:
                    raise ValueError(f"API key “{ref}” for model profile “{profile['name']}” is not stored. Add it under Settings → API keys") from None
            except UnicodeDecodeError:
                raise ValueError(f"API key “{ref}” is not valid text") from None
        elif profile["provider"] == "local":
            key, auth = "", "none"
        else:
            raise ValueError(f"Model profile “{profile['name']}” has no API key. Add one under Settings → API keys and select it on the profile")
        timeout = profile["timeout_seconds"] or PROFILE_TIMEOUT
        options = {k: profile.get(k) for k in ("temperature", "top_p", "max_output_tokens") if profile.get(k) is not None}
        return ModelConfig(chat_url(base), profile["model"], key, auth, timeout, self._public(profile, base), options)
