"""Opt-in outgoing webhooks: fire an HTTP POST when a task reaches a terminal state.

Never telemetry: webhooks are user-configured, never automatic, and no webhook
fires without an explicit configuration the user saved. The signing secret is a
reference into the encrypted key store; the secret itself never leaves the
machine. Each delivery carries `X-Dikdik-Signature: sha256=<hex>`, an HMAC of the
exact body, which the receiver recomputes with its copy of the secret.
"""

import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request
from .store import Store


def signature(secret: bytes | str, payload: bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret  # the key store reveals bytes
    return "sha256=" + hmac.new(key, payload, hashlib.sha256).hexdigest()


class WebhookStore:
    def __init__(self, store: Store):
        self.store = store
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS webhooks (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, secret_ref TEXT,
                    events TEXT NOT NULL DEFAULT 'terminal', enabled INTEGER NOT NULL DEFAULT 1,
                    created REAL NOT NULL
                );
            """)

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM webhooks ORDER BY created").fetchall()]

    def get(self, webhook_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM webhooks WHERE id=?", (webhook_id,)).fetchone()
        if row is None:
            raise KeyError(webhook_id)
        return dict(row)

    def create(self, body: dict, now: float) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Webhook must be a JSON object")
        url = body.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or len(url) > 2000:
            raise ValueError("url must be an http(s) URL")
        events = body.get("events", "terminal")
        if not isinstance(events, str) or not events.strip() or len(events) > 100:
            raise ValueError("events must be a non-empty string")
        import secrets as _s
        webhook_id = _s.token_hex(12)
        with self.store.connect() as db:
            db.execute("INSERT INTO webhooks(id,url,secret_ref,events,enabled,created) VALUES(?,?,?,?,?,?)",
                       (webhook_id, url, body.get("secret_ref"), events, 1, now))
        return self.get(webhook_id)

    def update(self, webhook_id: str, body: dict) -> dict:
        self.get(webhook_id)
        if not isinstance(body, dict) or set(body) - {"url", "secret_ref", "events", "enabled"}:
            raise ValueError("Webhook accepts url, secret_ref, events and enabled")
        fields, values = [], []
        if "url" in body:
            if not isinstance(body["url"], str) or not body["url"].startswith(("http://", "https://")):
                raise ValueError("url must be an http(s) URL")
            fields.append("url=?"); values.append(body["url"])
        if "secret_ref" in body:
            fields.append("secret_ref=?"); values.append(body["secret_ref"])
        if "events" in body:
            if not isinstance(body["events"], str) or not body["events"].strip():
                raise ValueError("events must be a non-empty string")
            fields.append("events=?"); values.append(body["events"])
        if "enabled" in body:
            fields.append("enabled=?"); values.append(1 if body["enabled"] else 0)
        if not fields:
            raise ValueError("Nothing to update")
        values.append(webhook_id)
        with self.store.connect() as db:
            db.execute(f"UPDATE webhooks SET {', '.join(fields)} WHERE id=?", values)
        return self.get(webhook_id)

    def delete(self, webhook_id: str) -> None:
        self.get(webhook_id)
        with self.store.connect() as db:
            db.execute("DELETE FROM webhooks WHERE id=?", (webhook_id,))

    def fire(self, task_id: str, state: str, now: float, reveal=None) -> None:
        """POST to every enabled webhook once a task reaches a terminal state. The
        database and key store are read here, on the caller's thread; only the
        network calls run in the background, so a slow or unreachable receiver never
        holds up the task, and nothing touches the store after the engine closes."""
        if state not in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
            return
        with self.store.connect() as db:
            rows = db.execute("SELECT url, secret_ref FROM webhooks WHERE enabled=1").fetchall()
        if not rows:
            return
        payload = json.dumps({"task": task_id, "state": state, "time": now}).encode()
        deliveries = []
        for row in rows:
            headers = {"Content-Type": "application/json", "X-Dikdik-Event": state}
            if row["secret_ref"] and reveal:
                try:
                    secret = reveal(row["secret_ref"])
                    if secret:
                        headers["X-Dikdik-Signature"] = signature(secret, payload)
                except Exception:
                    pass  # a bad secret ref should never break the task
            deliveries.append((row["url"], headers))
        threading.Thread(target=deliver, args=(deliveries, payload), daemon=True).start()


def deliver(deliveries: list[tuple[str, dict]], payload: bytes) -> None:
    for url, headers in deliveries:
        try:
            request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            urllib.request.urlopen(request, timeout=10).close()
        except (urllib.error.URLError, OSError, ValueError):
            pass  # a failing webhook should never break the task
