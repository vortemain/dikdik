"""Shared API routing table for the M4 endpoints.

`build_routes(engine)` returns a dispatch callable `routes(method, raw_path,
body) -> (status, dict)` consumed by server.py's small hooks. The engine object
provides `store`, `data`, `root` and `automation` (the scheduler). Handlers
return `(status, dict)` with clean 400/404/409 semantics. Nothing here logs
bodies or secret material.
"""

import json
import sqlite3
import time
import urllib.parse

from .agent_profiles import AgentProfiles
from .automation import validate_schedule
from .drafts import DraftConflict, Drafts
from .mcp import McpStore, test_server
from .connections import ConnectionStore
from .model_catalog import catalog
from .profiles import ApiError, ProfileStore
from .providers import chat_completion
from .routing import STRATEGIES, RouterStore
from .secrets_store import SecretsError, SecretsStore
from .skills import SkillIndex
from .skills_market import InstallConflict, SkillStore

ROUTE_PREFIXES = ("/api/profiles", "/api/secrets", "/api/skills", "/api/automations", "/api/mcp", "/api/agent-profiles", "/api/drafts",
                  "/api/connections", "/api/routers", "/api/model-catalog")
MANAGED_PREFIX = "conn."  # keys owned by provider connections; managed there, hidden from API keys


def build_routes(engine_or_stores):
    stores = engine_or_stores
    profiles = ProfileStore(stores.store)
    secrets = SecretsStore(stores.data / "secrets.bin", stores.data / "secrets.key")
    skills = SkillIndex(stores.store, stores.data / "skills", stores.root / ".dikdik" / "skills", stores.data / "skills-installed")
    market = SkillStore(stores.data, stores.store)
    automations = stores.automation
    drafts = Drafts(stores.store)
    connections = ConnectionStore(stores.store, secrets)
    routers = getattr(stores, "routers", None) or RouterStore(stores.store)

    def draft_routes(method, parts, body, query):
        if len(parts) != 3:
            return None
        if method == 'GET':
            return 200, drafts.get(parts[2])
        if method == 'PATCH':
            try:
                return 200, drafts.save(parts[2], body)
            except DraftConflict as exc:
                return 409, {'error': 'This draft changed in another tab. Choose which version to keep.', 'current': exc.current}
        return None

    def profiles_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return profiles.list_profiles()
        if method == "POST" and len(parts) == 2:
            return profiles.create(body)
        if method == "PATCH" and len(parts) == 3:
            return profiles.patch(parts[2], body)
        if method == "DELETE" and len(parts) == 3:
            return profiles.delete(parts[2], query.get("confirm_default") in {"1", "true"})
        if method == "POST" and len(parts) == 4 and parts[3] == "duplicate":
            return profiles.duplicate(parts[2])
        if method == "POST" and len(parts) == 4 and parts[3] == "test":
            return 200, test_profile(parts[2])
        return None

    def test_profile(profile_id: str) -> dict:
        """One tiny request, only when the user clicks Test. No repository content is sent."""
        resolver = getattr(stores, "models", None)
        if resolver is None:
            raise ValueError("Model testing is unavailable here")
        config = resolver.resolve(profile_id)
        started = time.monotonic()
        data = chat_completion([{"role": "user", "content": "Reply with the single word OK."}], None, max_tokens=16, config=config)
        try:
            reply = str(data["choices"][0]["message"].get("content") or "")[:200]
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ValueError("The provider answered, but not in the chat-completions format") from None
        return {"ok": True, "model": config.model, "seconds": round(time.monotonic() - started, 2), "reply": reply}

    def connection_routes(method, parts, body, query):
        if method == "GET" and len(parts) == 2:
            return 200, connections.list()
        if method == "POST" and len(parts) == 2:
            return 201, connections.create(body)
        if method == "PATCH" and len(parts) == 3:
            return 200, connections.update(parts[2], body)
        if method == "DELETE" and len(parts) == 3:
            return 200, connections.delete(parts[2])
        if method == "POST" and len(parts) == 4 and parts[3] == "add-models":
            return 201, add_models(parts[2], body)
        return None

    def add_models(connection_id: str, body) -> dict:
        """One linked profile per chosen model, named "<connection> / <model>"; existing names are skipped."""
        connection = connections.get(connection_id)
        models = body.get("models")
        if not isinstance(models, list) or not 1 <= len(models) <= 20 or not all(isinstance(m, str) and 1 <= len(m.strip()) <= 128 for m in models):
            raise ValueError("Choose 1–20 models")
        status, listed = profiles.list_profiles()
        taken = {item["name"] for item in listed}
        created, skipped = [], []
        for model in dict.fromkeys(m.strip() for m in models):
            name = f"{connection['name'][:40]} / {model}"[:64]
            if name in taken:
                skipped.append(name)
                continue
            profiles.create({"name": name, "provider": connection["provider"], "model": model, "connection_id": connection_id})
            created.append(name)
            taken.add(name)
        return {"created": created, "skipped": skipped}

    def router_routes(method, parts, body, query):
        if method == "GET" and len(parts) == 2:
            return 200, {"routers": routers.list(), "strategies": STRATEGIES}
        if method == "POST" and len(parts) == 2:
            return 201, routers.create(body)
        if method == "PATCH" and len(parts) == 3:
            return 200, routers.update(parts[2], body)
        if method == "DELETE" and len(parts) == 3:
            return 200, routers.delete(parts[2])
        return None

    def catalog_routes(method, parts, body, query):
        return (200, catalog()) if method == "GET" and len(parts) == 2 else None

    def secrets_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return 200, [item for item in secrets.list() if not item["ref"].startswith(MANAGED_PREFIX)]
        if len(parts) >= 3 and parts[2].startswith(MANAGED_PREFIX):
            raise ValueError("This key belongs to a provider connection; change it there")
        if method == "POST" and len(parts) == 4 and parts[3] == "restore":
            secrets.restore_previous(parts[2])
            return 200, {"ref": parts[2], "restored": True}
        if method == "POST" and len(parts) == 4 and parts[3] == "rename":
            new = body.get("to")
            if not ref_valid(new) or new.startswith(MANAGED_PREFIX):
                raise ValueError("Secret reference must be 1–64 characters of letters, digits, '.', '_' or '-'")
            secrets.rename(parts[2], new)
            rename_references(parts[2], new)
            return 200, {"ref": new}
        if method == "PATCH" and len(parts) == 3:
            if set(body) != {"env"}:
                raise ValueError("Only env (the sandbox variable name, or null) can be changed")
            secrets.set_env(parts[2], body["env"] or None)
            return 200, {"ref": parts[2], "env": body["env"] or ""}
        if method == "POST" and len(parts) == 2:
            ref, plaintext = body.get("ref"), body.get("plaintext")
            if not ref_valid(ref) or ref.startswith(MANAGED_PREFIX):
                raise ValueError("Secret reference must be 1–64 characters of letters, digits, '.', '_' or '-'")
            if not isinstance(plaintext, str) or not plaintext.strip():
                raise ValueError("A secret cannot be empty; delete it instead")
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
        if len(parts) >= 3 and parts[2] == "marketplaces":
            if method == "GET" and len(parts) == 3:
                return 200, market.marketplaces()
            if method == "POST" and len(parts) == 3:
                return 201, market.add_marketplace(body)
            if method == "DELETE" and len(parts) == 4:
                return 200, market.remove_marketplace(parts[3])
            if method == "POST" and len(parts) == 5 and parts[4] == "sync":
                return 200, market.sync(parts[3], body.get("approve_network") is True)
            return None
        if len(parts) >= 3 and parts[2] == "catalog":
            if method == "GET" and len(parts) == 3:
                return 200, market.catalog()
            if method == "POST" and len(parts) == 4 and parts[3] == "install":
                return 201, market.install_from_catalog(body.get("marketplace"), body.get("repo_path"))
            return None
        if method == "POST" and len(parts) == 3 and parts[2] == "install":
            return 201, market.install(body)
        if method == "POST" and len(parts) == 4 and parts[3] == "refresh":
            return 200, market.refresh(parts[2], body.get("approve_network") is True)
        if method == "GET" and len(parts) == 3:
            return 200, skills.read(parts[2])
        if method == "DELETE" and len(parts) == 3 and (skills.find(parts[2]) or {}).get("scope") == "installed":
            return 200, market.uninstall(parts[2])
        if method == "GET" and len(parts) == 2:
            rows = skills.enumerate_skills()
            for row in rows:
                if row["scope"] == "installed":
                    record = market.record(row["id"])
                    row["source"] = record.get("source", "local")
                    row["resolved_ref"] = record.get("resolved_ref", "")
            return 200, rows
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

    def rename_references(old: str, new: str) -> None:
        """Model profiles, MCP servers and agent profiles keep pointing at a renamed key."""
        with stores.store.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "profiles" in tables:
                db.execute("UPDATE profiles SET credential_secret_ref=? WHERE credential_secret_ref=?", (new, old))
            if "mcp_servers" in tables:
                db.execute("UPDATE mcp_servers SET secret_ref=? WHERE secret_ref=?", (new, old))
            if "agent_profiles" in tables and "secret_refs" in {r["name"] for r in db.execute("PRAGMA table_info(agent_profiles)")}:
                for row in db.execute("SELECT id, secret_refs FROM agent_profiles WHERE secret_refs IS NOT NULL").fetchall():
                    refs = json.loads(row["secret_refs"])
                    if old in refs:
                        db.execute("UPDATE agent_profiles SET secret_refs=? WHERE id=?", (json.dumps([new if r == old else r for r in refs]), row["id"]))

    TABLE = {
        "/api/connections": connection_routes,
        "/api/routers": router_routes,
        "/api/model-catalog": catalog_routes,
        '/api/drafts': draft_routes,
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
        except InstallConflict as exc:
            return 409, {"error": str(exc)}
        except SecretsError as exc:
            return 500, {"error": str(exc) or "Secret store unavailable; the change was not applied"}
        except sqlite3.OperationalError:
            return 503, {"error": "The settings store is busy; try again in a moment"}
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc)}
        except KeyError:
            return 404, {"error": "Not found"}
        if result is None:
            return 404, {"error": "Not found"}
        return result

    return call
