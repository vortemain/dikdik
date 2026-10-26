"""Which model a task talks to: the default profile, else environment variables.

The default profile (Settings → Models) wins. Its API key is decrypted from the
secrets store only when a model call is about to be made, lives in memory for
that task, and is never persisted, logged or returned. When no profile exists,
DIKDIK_MODEL_URL / DIKDIK_MODEL / DIKDIK_API_KEY are used as before.
"""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .profiles import LOOPBACK_HOSTS, SCHEMA as PROFILE_SCHEMA
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
    if not (endpoint and model and key):
        raise ValueError("No model is set up. Add a model profile in Settings → Models, or set DIKDIK_MODEL_URL, DIKDIK_MODEL and DIKDIK_API_KEY on the server")
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
            db.execute(PROFILE_SCHEMA)
            if profile_id:
                row = db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
                if row is None:
                    raise ValueError("The model profile this agent profile uses was deleted. Pick another in Settings → Agents")
                return dict(row)
            row = db.execute("SELECT * FROM profiles WHERE is_default=1 ORDER BY created LIMIT 1").fetchone()
        return dict(row) if row else None

    @staticmethod
    def _public(profile: dict, base: str) -> dict:
        return {"source": "profile", "profile": profile["name"], "provider": profile["provider"],
                "provider_name": PROVIDER_NAMES.get(profile["provider"], profile["provider"]),
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
            if ref and ref not in {item["ref"] for item in self.secrets.list()}:
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
            try:
                key = self.secrets.reveal(ref).decode("utf-8")
            except KeyError:
                raise ValueError(f"API key “{ref}” for model profile “{profile['name']}” is not stored. Add it under Settings → API keys") from None
            except UnicodeDecodeError:
                raise ValueError(f"API key “{ref}” is not valid text") from None
            auth = "api-key" if profile["provider"] == "azure" else "bearer"
        elif profile["provider"] == "local":
            key, auth = "", "none"
        else:
            raise ValueError(f"Model profile “{profile['name']}” has no API key. Add one under Settings → API keys and select it on the profile")
        timeout = profile["timeout_seconds"] or PROFILE_TIMEOUT
        return ModelConfig(chat_url(base), profile["model"], key, auth, timeout, self._public(profile, base))
