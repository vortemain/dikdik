"""Shared API routing table for the M4 endpoints.

`build_routes(engine)` returns a dispatch callable `routes(method, raw_path,
body) -> (status, dict)` consumed by server.py's small hooks. The engine object
provides `store`, `data`, `root` and `automation` (the scheduler). Handlers
return `(status, dict)` with clean 400/404/409 semantics. Nothing here logs
bodies or secret material.
"""

import urllib.parse

from .agent_profiles import AgentProfiles
from .automation import validate_schedule
from .mcp import McpStore, test_server
from .profiles import ApiError, ProfileStore
from .secrets_store import SecretsError, SecretsStore
from .skills import SkillIndex

ROUTE_PREFIXES = ("/api/profiles", "/api/secrets", "/api/skills", "/api/automations", "/api/mcp", "/api/agent-profiles")


def build_routes(engine_or_stores):
    stores = engine_or_stores
    profiles = ProfileStore(stores.store)
    secrets = SecretsStore(stores.data / "secrets.bin", stores.data / "secrets.key")
    skills = SkillIndex(stores.store, stores.data / "skills", stores.root / ".dikdik" / "skills")
    automations = stores.automation

    def profiles_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return profiles.list_profiles()
        if method == "POST" and len(parts) == 2:
            return profiles.create(body)
        if method == "PATCH" and len(parts) == 3:
            return profiles.patch(parts[2], body)
        if method == "DELETE" and len(parts) == 3:
            return profiles.delete(parts[2], query.get("confirm_default") in {"1", "true"})
        return None

    def secrets_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return 200, secrets.list()
        if method == "POST" and len(parts) == 2:
            ref, plaintext = body.get("ref"), body.get("plaintext")
            if not ref_valid(ref):
                raise ValueError("Secret reference must be 1–64 characters of letters, digits, '.', '_' or '-'")
            secrets.set(ref, plaintext if isinstance(plaintext, bytes) else (plaintext or "").encode("utf-8"))
            return 201, {"ref": ref, "set": True}
        if method == "DELETE" and len(parts) == 3:
            try:
                secrets.delete(parts[2])
            except KeyError:
                return 404, {"error": "No secret stored under that reference"}
            return 200, {"deleted": parts[2]}
        return None

    def skills_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return 200, skills.enumerate_skills()
        if method == "PUT" and len(parts) == 3:
            return skills.set_enabled(parts[2], body.get("enabled") if isinstance(body, dict) else None)
        if method == "POST" and len(parts) == 2:
            return skills.create(body)
        if method == "DELETE" and len(parts) == 3:
            return skills.delete(parts[2])
        return None

    def automations_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return automations.list_automations()
        if method == "POST" and len(parts) == 2:
            validate_schedule(body.get("schedule"))
            return automations.create(body)
        if method == "PUT" and len(parts) == 3:
            return automations.toggle(parts[2], body)
        if method == "PATCH" and len(parts) == 3:
            return automations.update(parts[2], body)
        if method == "POST" and len(parts) == 4 and parts[3] == "run":
            return automations.run_now(parts[2])
        if method == "DELETE" and len(parts) == 3:
            return automations.delete(parts[2])
        if method == "GET" and len(parts) == 4 and parts[3] == "runs":
            return automations.runs(parts[2])
        return None

    mcp = getattr(stores, "mcp", None) or McpStore(stores.store)

    def mcp_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return 200, mcp.list()
        if method == "POST" and len(parts) == 2:
            return 201, mcp.create(body)
        if method == "PATCH" and len(parts) == 3:
            return 200, mcp.set_enabled(parts[2], body.get("enabled"))
        if method == "DELETE" and len(parts) == 3:
            return 200, mcp.delete(parts[2])
        if method == "POST" and len(parts) == 4 and parts[3] == "test":
            return 200, test_server(mcp.get(parts[2]), secrets.reveal)
        return None

    agents = getattr(stores, "agents", None) or AgentProfiles(stores.store)

    def agent_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return 200, agents.list()
        if method == "POST" and len(parts) == 2:
            return 201, agents.create(body)
        if method == "PATCH" and len(parts) == 3:
            return 200, agents.update(parts[2], body)
        if method == "DELETE" and len(parts) == 3:
            return 200, agents.delete(parts[2])
        return None

    TABLE = {
        "/api/profiles": profiles_routes,
        "/api/secrets": secrets_routes,
        "/api/skills": skills_routes,
        "/api/automations": automations_routes,
        "/api/mcp": mcp_routes,
        "/api/agent-profiles": agent_routes,
    }

    def ref_valid(ref) -> bool:
        return isinstance(ref, str) and not ref.startswith(".") and 1 <= len(ref) <= 64 and all(c.isalnum() or c in "_.-" for c in ref)

    def call(method: str, raw_path: str, body) -> tuple[int, dict]:
        if body is None:
            body = {}
        elif not isinstance(body, dict):
            return 400, {"error": "Body must be a JSON object"}
        split = urllib.parse.urlsplit(raw_path or "/")
        parts = [segment for segment in split.path.split("/") if segment]
        handler = TABLE.get("/api/" + (parts[1] if len(parts) > 1 else "---"))
        if handler is None:
            return 404, {"error": "Not found"}
        query = {key: value[0] for key, value in urllib.parse.parse_qs(split.query, keep_blank_values=True).items()}
        try:
            result = handler(method, parts, body, query)
        except ApiError as exc:
            return exc.status, {"error": exc.error}
        except SecretsError:
            return 500, {"error": "Secret store unavailable; the change was not applied"}
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc)}
        except KeyError:
            return 404, {"error": "Not found"}
        if result is None:
            return 404, {"error": "Not found"}
        return result

    return call
