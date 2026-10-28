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
import hashlib
from .model_discovery import discover_models

from .agent_profiles import AgentProfiles
from .automation import validate_schedule
from .automation_git import AutomationGitSync
from .automation_templates import templates as automation_templates
from .drafts import DraftConflict, Drafts
from .mcp import OAUTH_PREFIX, McpError, McpStore, oauth_ref, test_server
from .mcp_catalog import catalog as mcp_library, install as mcp_install
from .mcp_oauth import OAuthJobs
from .connections import ConnectionStore, secret_ref as connection_ref
from .model_catalog import add_custom, catalog, provider_info
from .profiles import ApiError, ProfileStore
from .providers import chat_completion
from .routing import STRATEGIES, RouterStore
from .secrets_store import SecretsError, SecretsStore
from .skills import SkillIndex
from .plugins import fill_prompt
from .skills_market import InstallConflict, SkillStore
from .webhooks import WebhookStore
from .backends import BackendStore as _BackendStore
from .acp_agents import AcpAgentStore as _AcpAgentStore

ROUTE_PREFIXES = ("/api/profiles", "/api/secrets", "/api/skills", "/api/automations", "/api/mcp", "/api/agent-profiles", "/api/drafts",
                  "/api/connections", "/api/routers", "/api/model-catalog", "/api/plugins", "/api/webhooks", "/api/backends", "/api/acp-agents")
MANAGED_PREFIX = ("conn.", OAUTH_PREFIX)  # provider connection keys and MCP sign-ins; managed there, hidden from API keys


def build_routes(engine_or_stores):
    stores = engine_or_stores
    profiles = ProfileStore(stores.store)
    secrets = SecretsStore(stores.data / "secrets.bin", stores.data / "secrets.key")
    plugins = getattr(stores, "plugins", None)
    skills = SkillIndex(stores.store, stores.data / "skills", stores.root / ".dikdik" / "skills", stores.data / "skills-installed",
                        (lambda: plugins.skill_dirs()) if plugins else None)
    market = SkillStore(stores.data, stores.store)
    automations = stores.automation
    automation_git = AutomationGitSync(stores, automations)
    webhooks = getattr(stores, "webhooks", None) or WebhookStore(stores.store)
    backends = getattr(stores, "backends", None) or _BackendStore(stores.store)
    acp_agents = getattr(stores, "acp_agents", None) or _AcpAgentStore(stores.store)
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

    def plugin_routes(method, parts, body, query):
        if plugins is None:
            return None
        if len(parts) >= 3 and parts[2] == "catalog":
            if method == "GET" and len(parts) == 3:
                return 200, plugins.catalog(market.marketplaces())
            if method == "POST" and len(parts) == 4 and parts[3] == "install":
                return 201, plugins.install_from_catalog(body.get("marketplace"), body.get("repo_path"))
            return None
        if method == "GET" and len(parts) == 2:
            return 200, plugins.list()
        if method == "POST" and len(parts) == 3 and parts[2] == "install":
            return 201, plugins.install(body)
        if method == "GET" and len(parts) == 3:
            return 200, plugins.get(parts[2])
        if method == "GET" and len(parts) == 4 and parts[3] == "files":
            return 200, plugins.read_file(parts[2], query.get("path", ""))
        if method == "PATCH" and len(parts) == 3:
            return 200, plugins.set_enabled(parts[2], body)
        if method == "POST" and len(parts) == 4 and parts[3] == "refresh":
            return 200, plugins.refresh(parts[2], body.get("approve_network") is True)
        if method == "POST" and len(parts) == 4 and parts[3] == "launch":
            plugin = plugins.get(parts[2])
            if not (plugin["enabled"] and plugin["trusted"]):
                raise ValueError("Trust and enable the plugin before starting a conversation with it")
            return 200, {"prompt": fill_prompt(plugin["prompt"], plugin["parameters"], body.get("parameters") or {})}
        if method == "DELETE" and len(parts) == 3:
            return 200, plugins.uninstall(parts[2])
        return None

    def catalog_routes(method, parts, body, query):
        """/api/model-catalog: providers, connect with a key, live model lists, use a model."""
        if method == "GET" and len(parts) == 2:
            return 200, catalog(stores.store, connections.list())
        if method == "POST" and len(parts) == 3 and parts[2] == "custom":
            added = add_custom(stores.store, body)
            if body.get("api_key") or provider_info(stores.store, added["id"]).get("no_key"):
                connect(added["id"], body)
            return 201, catalog(stores.store, connections.list())["providers"][added["id"]]
        if len(parts) != 4:
            return None
        try:
            info = provider_info(stores.store, parts[2])
        except KeyError:
            return 404, {"error": "Unknown provider"}
        current = catalog(stores.store, connections.list())["providers"][info["id"]]
        if method == "POST" and parts[3] == "connect":
            connect(info["id"], body)
            return 200, catalog(stores.store, connections.list())["providers"][info["id"]]
        if method == "GET" and parts[3] == "models":
            key = None
            if current["connection"]:
                try:
                    key = secrets.reveal(connection_ref(current["connection"])).decode("utf-8")
                except KeyError:
                    key = None
            elif not info.get("public_models") and not info.get("no_key"):
                raise ValueError(f"Connect {info['name']} first")
            endpoint = connections.get(current["connection"])["endpoint"] if current["connection"] else ""
            return 200, {"provider": info["id"], **discover_models({**info, "base": endpoint or info["base"]}, key)}
        if method == "POST" and parts[3] == "use":
            if not current["connection"] and info.get("no_key"):
                connect(info["id"], {})
                current = catalog(stores.store, connections.list())["providers"][info["id"]]
            if not current["connection"]:
                raise ValueError(f"Connect {info['name']} first")
            model = body.get("model")
            if not isinstance(model, str) or not model.strip() or len(model) > 200:
                raise ValueError("Choose a model")
            name = f"{info['name']} · {model.strip()}"
            if len(name) > 64:
                name = name[:53] + " · " + hashlib.sha256(name.encode()).hexdigest()[:8]
            existing = next((p for p in profiles.list_profiles()[1] if p["name"] == name), None)
            if existing is None:
                status, created = profiles.create({"name": name, "provider": info["kind"], "model": model.strip(),
                                                   "connection_id": current["connection"]})
                existing = {"id": created["id"]}
            if body.get("default", True) is True:
                profiles.patch(existing["id"], {"is_default": True})
            return 200, {"profile": existing["id"], "name": name}
        return None

    def connect(provider_id, body):
        """Store the provider's key as a connection whose endpoint is the provider's base URL."""
        info = provider_info(stores.store, provider_id)
        if not isinstance(body, dict):
            raise ValueError("Send the API key")
        endpoint = info["base"]
        if info["kind"] == "azure":
            endpoint = body.get("endpoint") or ""
            if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
                raise ValueError("Azure needs your resource endpoint, e.g. https://my-resource.openai.azure.com/openai/v1")
        current = catalog(stores.store, connections.list())["providers"][provider_id]
        if current["connection"]:
            return connections.update(current["connection"], {"api_key": body.get("api_key")}) if body.get("api_key") else {"id": current["connection"]}
        names = {c["name"] for c in connections.list()}
        name = info["name"] if info["name"] not in names else f"{info['name']} ({len(names) + 1})"
        payload = {"name": name, "provider": info["kind"], "endpoint": None if info["id"] in {"openai", "anthropic"} else endpoint}
        if body.get("api_key"):
            payload["api_key"] = body["api_key"]
        elif not info.get("no_key"):
            raise ValueError(f"Paste your {info['name']} API key")
        return connections.create(payload)

    def secrets_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return 200, [item for item in secrets.list() if not item["ref"].startswith(MANAGED_PREFIX)]
        if len(parts) >= 3 and parts[2].startswith(MANAGED_PREFIX):
            raise ValueError("This key belongs to a provider connection or an MCP sign-in; change it there")
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
        if method == "GET" and len(parts) == 3 and parts[2] == "templates":
            return 200, automation_templates()
        if method == "POST" and len(parts) == 3 and parts[2] == "import":
            return automations.import_spec(body)
        if len(parts) >= 3 and parts[2] == "git-sync":
            if method == "GET" and len(parts) == 3:
                return automation_git.get()
            if method == "PATCH" and len(parts) == 3:
                return automation_git.save(body, time.time())
            if method == "GET" and len(parts) == 4 and parts[3] == "status":
                return automation_git.status()
            if method == "POST" and len(parts) == 4 and parts[3] == "sync":
                return automation_git.sync(body, time.time())
            return None
        if len(parts) >= 3 and parts[2] == "drafts":
            if method == "GET" and len(parts) == 3:
                return automations.list_drafts()
            if method == "POST" and len(parts) == 3:
                return automations.save_draft(body)
            if method == "PATCH" and len(parts) == 4:
                return automations.save_draft(body, parts[3])
            if method == "DELETE" and len(parts) == 4:
                return automations.delete_draft(parts[3])
            if method == "POST" and len(parts) == 5 and parts[4] == "test":
                return automations.test_draft(parts[3])
            return None
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
        if method == "GET" and len(parts) == 4 and parts[3] == "export":
            return automations.export(parts[2])
        if method == "GET" and len(parts) == 5 and parts[3] == "runs":
            return automations.run_detail(parts[2], parts[4])
        return None

    mcp = getattr(stores, "mcp", None) or McpStore(stores.store)
    oauth = OAuthJobs(secrets.set)

    def mcp_listed() -> list[dict]:
        signed = {item["ref"] for item in secrets.list()}
        return [{**s, "signed_in": oauth_ref(s["id"]) in signed} for s in mcp.list()]

    def forget_sign_in(server_id: str) -> None:
        try:
            secrets.delete(oauth_ref(server_id))
        except KeyError:
            pass

    def mcp_routes(method: str, parts: list[str], body: dict, query: dict):
        if method == "GET" and len(parts) == 2:
            return 200, mcp_listed()
        if method == "POST" and len(parts) == 2:
            return 201, mcp.create(body)
        if len(parts) >= 3 and parts[2] == "catalog":
            if method == "GET" and len(parts) == 3:
                return 200, mcp_library(mcp.list())
            if method == "POST" and len(parts) == 4 and parts[3] == "install":
                return 201, mcp_install(mcp, secrets, body)
            return None
        if len(parts) >= 4 and parts[2] == "oauth":  # /api/mcp/oauth/<job>[/callback]
            if method == "GET" and len(parts) == 4:
                return 200, oauth.status(parts[3])
            if method == "POST" and len(parts) == 5 and parts[4] == "callback":
                if "callback_url" not in body:
                    oauth.status(parts[3])  # 404 for an unknown job first
                    return 422, {"error": "Send callback_url"}
                return oauth.callback(parts[3], body["callback_url"])
            return None
        if method == "PATCH" and len(parts) == 3:
            if set(body) == {"enabled"}:
                return 200, mcp.set_enabled(parts[2], body.get("enabled"))
            before = mcp.get(parts[2])
            after = mcp.update(parts[2], body)
            if before["auth"] == "oauth" and (after["auth"] != "oauth" or after["url"] != before["url"]):
                forget_sign_in(parts[2])  # a token is only good for the server it was issued for
            return 200, after
        if method == "DELETE" and len(parts) == 3:
            done = mcp.delete(parts[2])
            forget_sign_in(parts[2])
            return 200, done
        if method == "POST" and len(parts) == 4 and parts[3] == "test":
            return 200, test_server(mcp.get(parts[2]), secrets.reveal, secrets.set, body.get("tool_call"))
        if len(parts) == 5 and parts[3:] == ["oauth", "start"] and method == "POST":
            return 200, oauth.start(mcp.get(parts[2]), body.get("timeout", 300))
        if len(parts) == 4 and parts[3] == "oauth" and method == "DELETE":
            mcp.get(parts[2])
            forget_sign_in(parts[2])
            return 200, {"id": parts[2], "signed_in": False}
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

    def webhook_routes(method, parts, body, query):
        if method == "GET" and len(parts) == 2:
            return 200, webhooks.list()
        if method == "POST" and len(parts) == 2:
            return 201, webhooks.create(body, time.time())
        if len(parts) == 3:
            if method == "GET":
                return 200, webhooks.get(parts[2])
            if method == "PATCH":
                return 200, webhooks.update(parts[2], body)
            if method == "DELETE":
                webhooks.delete(parts[2])
                return 200, {"deleted": parts[2]}
        return None

    def backend_routes(method, parts, body, query):
        if method == "POST" and len(parts) == 4 and parts[3] == "check":
            return 200, backends.check(parts[2], time.time())
        if method == "GET" and len(parts) == 2:
            return 200, backends.list()
        if method == "POST" and len(parts) == 2:
            return 201, backends.create(body, time.time())
        if len(parts) == 3:
            if method == "GET":
                return 200, backends.get(parts[2])
            if method == "PATCH":
                return 200, backends.update(parts[2], body)
            if method == "DELETE":
                backends.delete(parts[2])
                return 200, {"deleted": parts[2]}
        return None

    def acp_routes(method, parts, body, query):
        if method == "GET" and len(parts) == 2:
            return 200, {"agents": acp_agents.list(), "presets": acp_agents.presets()}
        if method == "POST" and len(parts) == 2:
            return 201, acp_agents.create(body, time.time())
        if len(parts) == 3:
            if method == "GET":
                return 200, acp_agents.get(parts[2])
            if method == "PATCH":
                return 200, acp_agents.update(parts[2], body)
            if method == "DELETE":
                acp_agents.delete(parts[2])
                return 200, {"deleted": parts[2]}
        if len(parts) == 4 and parts[2] and parts[3] == "models":
            return 200, {"models": acp_agents.discover_models(parts[2])}
        if len(parts) == 4 and parts[2] and parts[3] == "available":
            return 200, {"available": acp_agents.available(parts[2])}
        return None

    def rename_references(old: str, new: str) -> None:
        """Model profiles, MCP servers and agent profiles keep pointing at a renamed key."""
        with stores.store.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "profiles" in tables:
                db.execute("UPDATE profiles SET credential_secret_ref=? WHERE credential_secret_ref=?", (new, old))
            if "mcp_servers" in tables:
                db.execute("UPDATE mcp_servers SET secret_ref=? WHERE secret_ref=?", (new, old))
        mcp.rename_ref(old, new)
        with stores.store.connect() as db:
            if "agent_profiles" in tables and "secret_refs" in {r["name"] for r in db.execute("PRAGMA table_info(agent_profiles)")}:
                for row in db.execute("SELECT id, secret_refs FROM agent_profiles WHERE secret_refs IS NOT NULL").fetchall():
                    refs = json.loads(row["secret_refs"])
                    if old in refs:
                        db.execute("UPDATE agent_profiles SET secret_refs=? WHERE id=?", (json.dumps([new if r == old else r for r in refs]), row["id"]))

    TABLE = {
        "/api/connections": connection_routes,
        "/api/routers": router_routes,
        "/api/model-catalog": catalog_routes,
        "/api/plugins": plugin_routes,
        '/api/drafts': draft_routes,
        "/api/profiles": profiles_routes,
        "/api/secrets": secrets_routes,
        "/api/skills": skills_routes,
        "/api/automations": automations_routes,
        "/api/mcp": mcp_routes,
        "/api/agent-profiles": agent_routes,
        "/api/webhooks": webhook_routes,
        "/api/backends": backend_routes,
        "/api/acp-agents": acp_routes,
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
        except McpError as exc:
            return 400, {"error": str(exc)}
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

    call.oauth = oauth  # the server sets its port and serves the browser's callback
    return call
