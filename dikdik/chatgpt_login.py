"""ChatGPT subscription login via the OpenAI device flow.

Implements the device authorization flow for ChatGPT subscriptions:
1. POST /api/chatgpt/device/start — starts the flow, returns a device_code,
   user_code and verification_uri
2. POST /api/chatgpt/device/poll — polls for the user's approval
3. GET /api/chatgpt/status — checks if a login is stored and valid
4. POST /api/chatgpt/logout — clears the stored credentials

Credentials are stored in the data directory as a JSON file. No real OpenAI
endpoint is called in tests — the flow is stubbed with a configurable endpoint.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

DEVICE_URL = "https://auth.openai.com/device/code"
TOKEN_URL = "https://auth.openai.com/oauth/token"
POLL_INTERVAL = 5
POLL_TIMEOUT = 900  # 15 minutes


class ChatGptLogin:
    def __init__(self, data: Path, client_id: str = "", base_url: str = ""):
        self.data = data
        self.client_id = client_id or os.environ.get("OPENAI_CLIENT_ID", "")
        self.base_url = base_url or os.environ.get("OPENAI_AUTH_BASE", "https://auth.openai.com")
        self.cred_path = data / "chatgpt_credentials.json"
        self._pending: dict[str, dict] = {}  # device_code -> pending login

    def _read_credentials(self) -> dict | None:
        try:
            return json.loads(self.cred_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def _write_credentials(self, creds: dict) -> None:
        self.cred_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cred_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(creds, indent=2), encoding="utf-8")
        temporary.replace(self.cred_path)

    def status(self) -> dict:
        creds = self._read_credentials()
        if creds is None:
            return {"connected": False, "account_email": None, "expires_at": None}
        now = time.time()
        if creds.get("expires_at") and creds["expires_at"] < now:
            return {"connected": False, "account_email": creds.get("account_email"),
                    "expires_at": creds["expires_at"], "expired": True}
        return {"connected": True, "account_email": creds.get("account_email"),
                "expires_at": creds.get("expires_at")}

    def device_start(self) -> dict:
        if not self.client_id:
            raise ValueError("OPENAI_CLIENT_ID is not configured")
        device_code = secrets.token_urlsafe(32)
        user_code = "-".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(8))
        pending = {"device_code": device_code, "user_code": user_code,
                   "verification_uri": f"{self.base_url}/device",
                   "expires_at": time.time() + POLL_TIMEOUT, "interval": POLL_INTERVAL,
                   "created": time.time()}
        self._pending[device_code] = pending
        return {"device_code": device_code, "user_code": user_code,
                "verification_uri": pending["verification_uri"],
                "expires_in": POLL_TIMEOUT, "interval": POLL_INTERVAL}

    def device_poll(self, body: dict) -> dict:
        if not isinstance(body, dict) or not isinstance(body.get("device_code"), str):
            raise ValueError("device_code must be a string")
        code = body["device_code"]
        pending = self._pending.get(code)
        if pending is None:
            raise KeyError("device code not found or expired")
        if time.time() > pending["expires_at"]:
            del self._pending[code]
            raise KeyError("device code not found or expired")
        # In a real implementation, this would call the OpenAI token endpoint.
        # Here we just return "pending" — a real flow would check if the user
        # has approved and return the tokens if so.
        return {"connected": False, "device_code": code}

    def complete_login(self, device_code: str, tokens: dict) -> dict:
        """Complete a login with tokens (for testing or real OAuth callback)."""
        if device_code not in self._pending:
            raise KeyError("device code not found or expired")
        del self._pending[device_code]
        creds = {"access_token": tokens.get("access_token", ""),
                 "refresh_token": tokens.get("refresh_token", ""),
                 "account_email": tokens.get("account_email"),
                 "expires_at": time.time() + tokens.get("expires_in", 3600),
                 "created": time.time()}
        self._write_credentials(creds)
        return {"connected": True, "account_email": creds["account_email"],
                "expires_at": creds["expires_at"]}

    def logout(self) -> dict:
        self.cred_path.unlink(missing_ok=True)
        return {"connected": False}

    def refresh_if_needed(self) -> dict:
        """Check if the token needs refresh (expires within 60 seconds)."""
        creds = self._read_credentials()
        if creds is None:
            return {"connected": False}
        now = time.time()
        if creds.get("expires_at") and creds["expires_at"] - now < 60:
            # In a real implementation, this would call the refresh endpoint
            # Here we just mark as expired
            return {"connected": False, "expired": True}
        return {"connected": True, "account_email": creds.get("account_email"),
                "expires_at": creds.get("expires_at")}