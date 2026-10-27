"""ACP agent profiles: external coding agents (Claude Code, Codex, Gemini CLI, OpenCode).

An ACP agent is a program that speaks the Agent Client Protocol over stdio. A
profile stores how to start it, which saved secrets it receives as environment
variables, and an optional model. Running one is a task mode (`acp`) in the
engine: the agent works in a throwaway copy and its result is a normal candidate
diff, so nothing here starts a process except the PATH lookup in available().
"""

from __future__ import annotations

import json
import re
import secrets
import shlex
import shutil

PRESETS = {
    "claude-code": {
        "name": "Claude Code",
        "command": "claude-code-acp",
        "args": "",
        "credentials": ["ANTHROPIC_API_KEY"],
        "install": "npm install -g @zed-industries/claude-code-acp",
        "description": "Anthropic's Claude Code through Zed's ACP adapter",
    },
    "codex": {
        "name": "Codex",
        "command": "codex-acp",
        "args": "",
        "credentials": ["OPENAI_API_KEY"],
        "install": "npm install -g @zed-industries/codex-acp",
        "description": "OpenAI's Codex through Zed's ACP adapter",
    },
    "gemini-cli": {
        "name": "Gemini CLI",
        "command": "gemini",
        "args": "--experimental-acp",
        "credentials": ["GEMINI_API_KEY"],
        "install": "npm install -g @google/gemini-cli",
        "description": "Google's Gemini CLI in ACP mode",
    },
    "opencode": {
        "name": "OpenCode",
        "command": "opencode",
        "args": "acp",
        "credentials": [],
        "install": "npm install -g opencode-ai",
        "description": "OpenCode in ACP mode",
    },
}
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
SECRET_REF = re.compile(r"[A-Za-z0-9._-]{1,64}")


def credential_pairs(entries) -> list[tuple[str, str]]:
    """Each entry is `ENV_NAME=secret-ref`, or a bare name used as both."""
    pairs = []
    for entry in entries:
        env, _, ref = entry.partition("=")
        env, ref = env.strip(), (ref or env).strip()
        if not ENV_NAME.fullmatch(env) or not SECRET_REF.fullmatch(ref):
            raise ValueError("Each credential must be ENV_NAME or ENV_NAME=saved-key-name")
        pairs.append((env, ref))
    return pairs


class AcpAgentStore:
    def __init__(self, store):
        self.store = store
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS acp_agents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, preset TEXT,
                    command TEXT NOT NULL, args TEXT, credentials TEXT,
                    model TEXT, enabled INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL
                );
            """)

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM acp_agents ORDER BY created").fetchall()]
        return [{**row, "available": shutil.which(row["command"]) is not None} for row in rows]

    def presets(self) -> list[dict]:
        return [{"id": k, **v} for k, v in PRESETS.items()]

    def get(self, agent_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM acp_agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return dict(row)

    @staticmethod
    def _check(fields: dict) -> dict:
        """Validate whichever profile fields are present; return them normalised."""
        out = {}
        if "name" in fields:
            name = fields["name"]
            if not isinstance(name, str) or not name.strip() or len(name) > 128:
                raise ValueError("name must contain 1-128 characters")
            out["name"] = name.strip()
        if "command" in fields:
            command = fields["command"]
            if not isinstance(command, str) or not command.strip() or len(command) > 500:
                raise ValueError("command must be a non-empty string")
            if command.strip().startswith("-"):
                raise ValueError("command must not start with -")
            out["command"] = command.strip()
        if "preset" in fields:
            preset = fields["preset"]
            if preset is not None and (not isinstance(preset, str) or preset not in PRESETS):
                raise ValueError("preset must be a known ACP preset id")
            out["preset"] = preset
        if "args" in fields:
            args = fields["args"] or ""
            if not isinstance(args, str) or len(args) > 2000:
                raise ValueError("args must be a string under 2000 characters")
            try:
                shlex.split(args)
            except ValueError:
                raise ValueError("args has an unmatched quote") from None
            out["args"] = args
        if "credentials" in fields:
            credentials = fields["credentials"] or []
            if not isinstance(credentials, list) or len(credentials) > 20 or not all(isinstance(c, str) for c in credentials):
                raise ValueError("credentials must be a list of 0-20 entries")
            credential_pairs(credentials)
            out["credentials"] = json.dumps(credentials)
        if "model" in fields:
            model = fields["model"]
            if model is not None and (not isinstance(model, str) or len(model) > 200):
                raise ValueError("model must be a string under 200 characters")
            out["model"] = model or None
        if "enabled" in fields:
            out["enabled"] = 1 if fields["enabled"] else 0
        return out

    def create(self, body: dict, now: float) -> dict:
        if not isinstance(body, dict):
            raise ValueError("ACP agent must be a JSON object")
        preset = PRESETS.get(body.get("preset")) if isinstance(body.get("preset"), str) else None
        filled = {"name": (preset or {}).get("name"), "command": (preset or {}).get("command"),
                  "args": (preset or {}).get("args", ""), "credentials": (preset or {}).get("credentials", []),
                  **{k: v for k, v in body.items() if v not in (None, "")}}
        fields = self._check({"preset": None, "model": None, **filled})
        for required in ("name", "command"):
            if required not in fields:
                raise ValueError(f"{required} must be a non-empty string")
        agent_id = secrets.token_hex(12)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO acp_agents(id,name,preset,command,args,credentials,model,enabled,created) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (agent_id, fields["name"], fields.get("preset"), fields["command"], fields.get("args", ""),
                 fields.get("credentials", "[]"), fields.get("model"), fields.get("enabled", 0), now))
        return self.get(agent_id)

    def update(self, agent_id: str, body: dict) -> dict:
        self.get(agent_id)
        if not isinstance(body, dict) or set(body) - {"name", "command", "args", "credentials", "model", "enabled", "preset"}:
            raise ValueError("ACP agent accepts name, command, args, credentials, model, enabled and preset")
        fields = self._check(body)
        if fields:
            with self.store.connect() as db:
                db.execute(f"UPDATE acp_agents SET {', '.join(f'{key}=?' for key in fields)} WHERE id=?",
                           [*fields.values(), agent_id])
        return self.get(agent_id)

    def delete(self, agent_id: str) -> None:
        self.get(agent_id)
        with self.store.connect() as db:
            db.execute("DELETE FROM acp_agents WHERE id=?", (agent_id,))

    def available(self, agent_id: str) -> bool:
        """Whether the agent's command is on PATH (nothing is started)."""
        return shutil.which(self.get(agent_id)["command"]) is not None

    def discover_models(self, agent_id: str) -> list[str]:
        """The configured model, if any. ACP has no stable model listing yet, and
        starting the agent just to ask would run a host program from a GET request."""
        model = self.get(agent_id).get("model")
        return [model] if model else []

    def launch_command(self, agent_id: str) -> tuple[dict, list[str], list[tuple[str, str]]]:
        """The profile, its argv with the executable resolved on PATH, and its credentials."""
        agent = self.get(agent_id)
        executable = shutil.which(agent["command"])
        if executable is None:
            hint = PRESETS.get(agent.get("preset") or "", {}).get("install")
            raise ValueError(f"{agent['command']} is not installed or not on PATH" + (f"; install it with: {hint}" if hint else ""))
        argv = [executable, *shlex.split(agent.get("args") or "", posix=True)]
        return agent, argv, credential_pairs(json.loads(agent.get("credentials") or "[]"))
