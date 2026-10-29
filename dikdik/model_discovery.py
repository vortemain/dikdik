"""Bounded, credential-safe model discovery with provider pagination.

Only model metadata is requested. Pagination tokens are query values on a fixed
endpoint; provider-supplied URLs never receive a credential.
"""
import datetime
import json
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

MAX_BYTES = 16 * 1024 * 1024
MAX_MODELS = 20_000
MAX_PAGES = 100


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a key to an unexpected host or downgrade.


def _text(value, limit=512):
    return value[:limit] if isinstance(value, str) and not any(ord(c) < 32 for c in value) else ""


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None


def _price(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return str(value) if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def normalize_model(row: dict, provider: str) -> dict | None:
    identifier = _text(row.get("id") or row.get("name") or row.get("model"))
    if not identifier:
        return None
    if provider == "google" and identifier.startswith("models/"):
        identifier = identifier[7:]
    name = _text(row.get("display_name") or row.get("displayName") or row.get("name")) or identifier
    if name == "models/" + identifier:
        name = identifier
    context = next((_number(row.get(k)) for k in ("context_length", "context_window", "max_context_length", "max_model_len", "max_input_tokens", "inputTokenLimit", "contextWindow", "contextLength") if _number(row.get(k))), None)
    if context is None and isinstance(row.get("limits"), dict):
        context = _number(row["limits"].get("max_context_length"))
    capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
    chat = capabilities.get("completion_chat")
    methods = row.get("supportedGenerationMethods")
    if isinstance(methods, list):
        chat = "generateContent" in methods
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
    created = _number(row.get("created"))
    if created is None and isinstance(row.get("created_at") or row.get("createTime"), str):
        try:
            created = datetime.datetime.fromisoformat(row.get("created_at") or row["createTime"]).timestamp()
        except ValueError:
            pass
    return {"id": identifier, "name": name, "context": context,
            "prompt_price": _price(pricing.get("prompt")), "completion_price": _price(pricing.get("completion")),
            "created": created, "chat_supported": chat if isinstance(chat, bool) else None}


def discover_models(info: dict, key: str | None, timeout: float = 30) -> dict:
    provider, base = info["id"], info.get("base", "").rstrip("/")
    if not base:
        raise ValueError("Add your resource endpoint to list its models")
    if info.get("models_unavailable"):
        raise ValueError(info["models_unavailable"])
    headers = {"Accept": "application/json", "User-Agent": "Dikdik/0.1 model-discovery"}
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
    elif key:
        headers["api-key" if info.get("kind") == "azure" else "Authorization"] = key if info.get("kind") == "azure" else "Bearer " + key
    endpoint = base + "/models"
    query = {"limit": 1000} if provider == "anthropic" else {}
    if provider == "google" and urlsplit(base).hostname == "generativelanguage.googleapis.com":
        # Native Gemini discovery exposes page tokens, context and generation methods.
        endpoint = base.removesuffix("/openai") + "/models"
        query = {"pageSize": 1000}
        headers.pop("Authorization", None)
        if key:
            headers["x-goog-api-key"] = key
    if provider == "cerebras" and not key and urlsplit(base).hostname == "api.cerebras.ai":
        endpoint = "https://api.cerebras.ai/public/v1/models"
    # Fireworks' native catalog uses an account resource, not the inference base.
    if provider == "fireworks" and urlsplit(base).hostname == "api.fireworks.ai":
        endpoint = "https://api.fireworks.ai/v1/accounts/fireworks/models"
        query = {"pageSize": 200}
    opener = urllib.request.build_opener(_NoRedirect())
    deadline, consumed, pages, models, cursors = time.monotonic() + timeout, 0, 0, {}, set()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or pages >= MAX_PAGES:
            raise ValueError("The provider model list did not finish; refresh to try again")
        url = endpoint + ("?" + urlencode(query) if query else "")
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with opener.open(request, timeout=min(15, remaining)) as answer:
                raw = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise ValueError("The provider model list timed out. Try again shortly.")
                    chunk = answer.read1(min(65536, MAX_BYTES - consumed + 1))
                    if not chunk:
                        break
                    consumed += len(chunk)
                    if consumed > MAX_BYTES:
                        raise ValueError("The provider model directory exceeds the 16 MB metadata limit")
                    raw.extend(chunk)
            data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            error = {401: "The provider rejected the key. Update it and try again.",
                     403: "This key does not have permission to list models.",
                     404: "This endpoint does not offer a model list. You can still enter a model ID.",
                     429: "The provider is rate limiting model lists. Try again shortly."}.get(exc.code)
            raise ValueError(error or f"The provider returned HTTP {exc.code} while listing models") from None
        except (urllib.error.URLError, OSError):
            raise ValueError("Could not reach the provider" + (". Check that your local model server is running." if info.get("kind") == "local" else ". Try again shortly.")) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("The provider returned an invalid model list") from None
        rows = data if isinstance(data, list) else data.get("data", data.get("models")) if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("The provider returned an unrecognized model list")
        for row in rows:
            model = normalize_model(row, provider) if isinstance(row, dict) else None
            if model:
                models[model["id"]] = model
        if len(models) > MAX_MODELS:
            raise ValueError("The model directory exceeds 20,000 entries; narrow the provider endpoint")
        pages += 1
        cursor = ""
        if isinstance(data, dict):
            if provider == "anthropic" and data.get("has_more") is True:
                cursor = _text(data.get("last_id"))
                if not cursor:
                    raise ValueError("The provider omitted its next-page cursor")
                query["after_id"] = cursor
            elif data.get("nextPageToken") or data.get("next_page_token"):
                cursor = _text(data.get("nextPageToken") or data.get("next_page_token"), 4096)
                if not cursor:
                    raise ValueError("The provider returned an invalid next-page cursor")
                query["pageToken" if "nextPageToken" in data else "page_token"] = cursor
            elif data.get("has_more") is True:
                cursor = _text(data.get("last_id")) or (models and next(reversed(models)))
                if not cursor:
                    raise ValueError("The provider omitted its next-page cursor")
                query["after"] = cursor
            elif data.get("next") or data.get("next_url") or data.get("next_page"):
                raise ValueError("This provider uses an unsupported pagination format; the full list could not be loaded")
        if not cursor:
            break
        if cursor in cursors or not rows:
            raise ValueError("The provider repeated a page; the full model list could not be loaded")
        cursors.add(cursor)
    return {"models": sorted(models.values(), key=lambda m: (-(m["created"] or 0), m["id"].lower())),
            "source": "provider", "complete": True, "pages": pages, "fetched_at": time.time()}
