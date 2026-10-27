"""OpenAI-compatible gateway: /v1/models, /v1/chat/completions and /v1/responses.

Loopback only and always keyed with the server's session bearer token. Each
request creates one ordinary Dikdik task and waits (bounded) for a terminal
state; the caller's explicit approval Booleans in the ``dikdik`` object are the
task's consent. No streaming yet: the reply is returned once the turn ends.
"""

import time


class Gateway:
    def __init__(self, engine):
        self.engine = engine

    # --- helpers --------------------------------------------------------
    def _profiles(self) -> list[dict]:
        with self.engine.store.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='profiles'").fetchone() is None:
                return []
            return [{"id": row["id"], "name": row["name"], "model": row["model"]}
                    for row in db.execute("SELECT id,name,model FROM profiles ORDER BY created").fetchall()]

    def _transcript(self, messages) -> str:
        """Turn a chat transcript into the task objective, bounded like every objective."""
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages/input must be a non-empty list or string")
        lines, length = [], 0
        for item in reversed(messages):  # newest first; earlier turns are the first to drop
            if not isinstance(item, dict) or item.get("role") not in ("system", "user", "assistant"):
                raise ValueError("Every message needs a role of system, user or assistant")
            text = item.get("content")
            if isinstance(text, list):  # content is untrusted data, never instructions
                text = " ".join(part.get("text", "") for part in text
                                if isinstance(part, dict) and isinstance(part.get("text"), str))
            if not isinstance(text, str):
                raise ValueError("Message content must be text")
            text = text[:1200]
            line = {"user": "User", "assistant": "Assistant", "system": "Note"}[item["role"]] + ": " + text
            if length + len(line) > 4000:
                break
            lines.append(line)
            length += len(line)
        if not lines:
            raise ValueError("The transcript is longer than the 4000-character objective limit")
        return "\n".join(reversed(lines))

    def _settings(self, body: dict) -> tuple[dict, int]:
        extra = body.get("dikdik")
        accepted = {"mode", "verification", "approve_external", "approve_execution", "approve_browser",
                    "approve_secrets", "model_profile", "agent_profile", "goal_max_turns", "confirmation",
                    "timeout_seconds"}
        extra = {} if extra is None else extra
        if not isinstance(extra, dict) or set(extra) - accepted:
            raise ValueError("dikdik accepts mode, verification, approvals, model_profile, agent_profile, "
                             "goal_max_turns, confirmation and timeout_seconds")
        settings = {key: value for key, value in extra.items() if key != "timeout_seconds"}
        settings.setdefault("mode", "agent")
        settings.setdefault("verification", "syntax")
        timeout = extra.get("timeout_seconds", 300)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 10 <= timeout <= 3600:
            raise ValueError("dikdik.timeout_seconds must be a number between 10 and 3600")
        return settings, timeout

    def _profile_id(self, requested: str, settings: dict) -> None:
        profiles = self._profiles()
        if requested == "dikdik-default":
            return  # the profile the resolver picks on its own
        if requested not in {profile["id"] for profile in profiles}:
            raise ValueError(f"Unknown model {requested[:80]}; use GET /v1/models to list the saved model profiles")
        settings["model_profile"] = requested

    def _submit(self, body: dict, messages) -> tuple[str, int]:
        if body.get("stream") is True:
            raise ValueError("Streaming is not offered yet; leave stream unset")
        settings, timeout = self._settings(body)
        self._profile_id(body.get("model") or "dikdik-default", settings)
        return self.engine.submit({"objective": self._transcript(messages), **settings}), timeout

    def _wait(self, task: str, timeout: int) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.engine.store.get(task)
            if state["state"] in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
                return state
            time.sleep(0.3)
        return self.engine.store.get(task)  # still running; the caller then answers 504

    def _reply(self, state: dict) -> str:
        for event in reversed(state["events"]):
            if event["kind"] == "agent_message" and isinstance(event["data"].get("content"), str) and event["data"]["content"].strip():
                return event["data"]["content"]
        for event in reversed(state["events"]):
            if event["kind"] in {"failed", "blocked"} and isinstance(event["data"], dict):
                text = event["data"].get("error")
                if isinstance(text, str) and text.strip():
                    return text
        return ""

    def _usage(self, state: dict) -> dict:
        for event in reversed(state["events"]):
            if event["kind"] == "completed" and isinstance(event["data"], dict):
                usage = (event["data"].get("agent") or {}).get("usage")
                if isinstance(usage, dict) and usage.get("total_tokens"):
                    return {"prompt_tokens": int(usage.get("prompt_tokens", 0)),
                            "completion_tokens": int(usage.get("completion_tokens",
                                                               int(usage.get("total_tokens", 0)) - int(usage.get("prompt_tokens", 0)))),
                            "total_tokens": int(usage.get("total_tokens", 0))}
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _finish(self, state: dict, kind: str) -> dict:
        content = self._reply(state)
        usage = self._usage(state)
        if kind == "chat":
            return {"id": f"chatcmpl-{state['id'][:24]}", "object": "chat.completion", "created": int(time.time()), "model": "dikdik",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    "dikdik": {"task": state["id"], "state": state["state"]}, "usage": usage}
        return {"id": f"resp-{state['id'][:24]}", "object": "response", "created": int(time.time()), "status": "completed",
                "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": content}]}],
                "output_text": content, "dikdik": {"task": state["id"], "state": state["state"]}, "usage": usage}

    # --- routes ---------------------------------------------------------
    def route(self, method: str, path: str, body):
        if path == "/v1/models" and method == "GET":
            return 200, {"object": "list", "data": [{"id": profile["id"], "object": "model", "created": 0,
                                                     "owned_by": "dikdik"} for profile in self._profiles()]}
        if path == "/v1/chat/completions" and method == "POST":
            if not isinstance(body, dict):
                raise ValueError("Body must be a JSON object")
            task, timeout = self._submit(body, body.get("messages"))
            return self._answer(task, timeout, "chat")
        if path == "/v1/responses" and method == "POST":
            if not isinstance(body, dict):
                raise ValueError("Body must be a JSON object")
            input_value = body.get("input")
            if isinstance(input_value, str):
                input_value = [{"role": "user", "content": input_value}]
            task, timeout = self._submit(body, input_value)
            return self._answer(task, timeout, "responses")
        return None

    def _answer(self, task: str, timeout: int, kind: str):
        state = self._wait(task, timeout)
        if state["state"] not in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
            return 504, {"error": {"message": f"The task is still running after the {timeout}s wait; inspect task {task} in Dikdik",
                                   "type": "timeout_error"}, "dikdik": {"task": task}}
        return 200, self._finish(state, kind)
