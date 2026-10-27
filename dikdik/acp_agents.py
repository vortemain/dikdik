"""ACP agent profiles: run external coding agents (Claude Code, Codex, Gemini CLI).

An ACP agent is an external CLI tool that runs as a subprocess instead of the
built-in agent. The profile stores the command, environment variables, and
credentials for each supported agent. No agent source code is imported; the
subprocess is launched with the user's explicit approval.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess


PRESETS = {
    "claude-code": {
        "name": "Claude Code",
        "command": "claude",
        "credentials": ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"],
        "description": "Anthropic's Claude Code CLI agent",
    },
    "codex": {
        "name": "Codex",
        "command": "codex",
        "credentials": ["OPENAI_API_KEY"],
        "description": "OpenAI's Codex CLI agent",
    },
    "gemini-cli": {
        "name": "Gemini CLI",
        "command": "gemini",
        "credentials": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "description": "Google's Gemini CLI agent",
    },
    "opencode": {
        "name": "OpenCode",
        "command": "opencode",
        "credentials": [],
        "description": "OpenCode local agent",
    },
}


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
            return [dict(r) for r in db.execute("SELECT * FROM acp_agents ORDER BY created").fetchall()]

    def presets(self) -> list[dict]:
        return [{"id": k, **v} for k, v in PRESETS.items()]

    def get(self, agent_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM acp_agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return dict(row)

    def create(self, body: dict, now: float) -> dict:
        if not isinstance(body, dict):
            raise ValueError("ACP agent must be a JSON object")
        name = body.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("name must contain 1-128 characters")
        command = body.get("command", "")
        if not isinstance(command, str) or not command.strip() or len(command) > 500:
            raise ValueError("command must be a non-empty string")
        if command.startswith("-"):
            raise ValueError("command must not start with -")
        preset = body.get("preset")
        if preset is not None and (not isinstance(preset, str) or preset not in PRESETS):
            raise ValueError("preset must be a known ACP preset id")
        args = body.get("args", "")
        if not isinstance(args, str) or len(args) > 2000:
            raise ValueError("args must be a string under 2000 characters")
        credentials = body.get("credentials", [])
        if not isinstance(credentials, list) or len(credentials) > 20:
            raise ValueError("credentials must be a list of 0-20 entries")
        for cred in credentials:
            if not isinstance(cred, str) or not cred.strip() or len(cred) > 100:
                raise ValueError("each credential must be a 1-100 char string")
        model = body.get("model")
        if model is not None and (not isinstance(model, str) or len(model) > 200):
            raise ValueError("model must be a string under 200 characters")
        import secrets as _s
        agent_id = _s.token_hex(12)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO acp_agents(id,name,preset,command,args,credentials,model,enabled,created) "
                "VALUES(?,?,?,?,?,?,?,0,?)",
                (agent_id, name.strip(), preset, command.strip(), args,
                 json.dumps(credentials), model, now))
        return self.get(agent_id)

    def update(self, agent_id: str, body: dict) -> dict:
        self.get(agent_id)
        if not isinstance(body, dict) or set(body) - {"name", "command", "args", "credentials", "model", "enabled", "preset"}:
            raise ValueError("ACP agent accepts name, command, args, credentials, model, enabled and preset")
        fields, values = [], []
        for key in ("name", "command", "args", "model", "preset"):
            if key in body:
                fields.append(f"{key}=?"); values.append(body[key])
        if "credentials" in body:
            fields.append("credentials=?"); values.append(json.dumps(body["credentials"]))
        if "enabled" in body:
            fields.append("enabled=?"); values.append(1 if body["enabled"] else 0)
        if fields:
            values.append(agent_id)
            with self.store.connect() as db:
                db.execute(f"UPDATE acp_agents SET {', '.join(fields)} WHERE id=?", values)
        return self.get(agent_id)

    def delete(self, agent_id: str) -> None:
        self.get(agent_id)
        with self.store.connect() as db:
            db.execute("DELETE FROM acp_agents WHERE id=?", (agent_id,))

    def available(self, agent_id: str) -> bool:
        """Check if the agent's command is available on this machine."""
        agent = self.get(agent_id)
        return shutil.which(agent["command"]) is not None

    def discover_models(self, agent_id: str) -> list[str]:
        """Ask the ACP agent which models it offers (if available)."""
        agent = self.get(agent_id)
        if not self.available(agent_id):
            return []
        try:
            result = subprocess.run(
                [agent["command"], "--list-models"],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "NO_COLOR": "1"},
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0 and result.stdout.strip():
                return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()][:50]
        except (subprocess.SubprocessError, OSError):
            pass
        return []

    def launch(self, agent_id: str, objective: str, workspace: str, reveal=None) -> dict:
        """Launch the external agent with the given objective. Returns launch info."""
        agent = self.get(agent_id)
        if not self.available(agent_id):
            raise ValueError(f"{agent['command']} is not installed or not on PATH")
        cmd = [agent["command"]]
        if agent.get("args"):
            import shlex
            cmd += shlex.split(agent["args"])
        env = {**os.environ, "NO_COLOR": "1"}
        # Inject credentials from the secret store if provided
        if reveal:
            for cred in json.loads(agent.get("credentials") or "[]"):
                try:
                    value = reveal(cred)
                    if value:
                        env[cred] = value
                except Exception:
                    pass
        if agent.get("model"):
            cmd += ["--model", agent["model"]]
        try:
            process = subprocess.Popen(
                cmd + ["--", objective],
                cwd=workspace, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            )
            return {"pid": process.pid, "agent": agent["name"], "command": " ".join(cmd),
                     "model": agent.get("model")}
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"Could not start {agent['name']}: {exc}") from None