"""A small built-in library of well-known MCP servers, and installing one from it.

Commands and addresses come from each project's own documentation. Dikdik ships
them as data and has not checked them against the live services. Installing a
command server runs nothing until the user approves the command; with approval
the install connects once to check it before anything is saved.
"""

import re

from .mcp import MANAGED, NAME, REF, McpError, test_server

LOCAL = "Runs on this machine"
CATALOG = [
    {"id": "filesystem", "name": "Filesystem", "description": "Read, write and search files inside the folders you list.",
     "homepage": "https://github.com/modelcontextprotocol/servers", "options": [
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "npx -y @modelcontextprotocol/server-filesystem",
         "args": [{"name": "paths", "label": "Paths (space separated)", "required": True}]}]},
    {"id": "git", "name": "Git", "description": "Read and search a local Git repository: status, diffs, logs and branches.",
     "homepage": "https://github.com/modelcontextprotocol/servers", "options": [
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "uvx mcp-server-git --repository",
         "args": [{"name": "repository", "label": "Repository folder", "required": True}]}]},
    {"id": "fetch", "name": "Fetch", "description": "Fetch a web page and turn it into Markdown for the agent.",
     "homepage": "https://github.com/modelcontextprotocol/servers", "options": [
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "uvx mcp-server-fetch"}]},
    {"id": "memory", "name": "Memory", "description": "A small knowledge graph the agent can store facts in between tasks.",
     "homepage": "https://github.com/modelcontextprotocol/servers", "options": [
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "npx -y @modelcontextprotocol/server-memory"}]},
    {"id": "sequential-thinking", "name": "Sequential thinking", "description": "A tool for step-by-step problem solving with revisions.",
     "homepage": "https://github.com/modelcontextprotocol/servers", "options": [
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "npx -y @modelcontextprotocol/server-sequential-thinking"}]},
    {"id": "time", "name": "Time", "description": "Current time and time-zone conversion.",
     "homepage": "https://github.com/modelcontextprotocol/servers", "options": [
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "uvx mcp-server-time"}]},
    {"id": "everything", "name": "Everything (test server)", "description": "The MCP reference test server: sample tools, prompts and resources.",
     "homepage": "https://github.com/modelcontextprotocol/servers", "options": [
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "npx -y @modelcontextprotocol/server-everything"}]},
    {"id": "github", "name": "GitHub", "description": "Issues, pull requests, code search and repository files on GitHub.",
     "homepage": "https://github.com/github/github-mcp-server", "options": [
        {"id": "remote", "label": "GitHub's hosted server", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
         "credentials": [{"name": "token", "label": "GitHub personal access token"}]},
        {"id": "docker", "label": "Docker on this machine", "transport": "stdio",
         "command": "docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server",
         "credentials": [{"name": "token", "label": "GitHub personal access token", "env_var": "GITHUB_PERSONAL_ACCESS_TOKEN"}]}]},
    {"id": "sentry", "name": "Sentry", "description": "Errors, issues and releases from Sentry.",
     "homepage": "https://github.com/getsentry/sentry-mcp", "options": [
        {"id": "remote", "label": "Sentry's hosted server (sign in)", "transport": "http", "url": "https://mcp.sentry.dev/mcp", "auth": "oauth"},
        {"id": "local", "label": LOCAL, "transport": "stdio", "command": "npx -y @sentry/mcp-server",
         "credentials": [{"name": "token", "label": "Sentry user auth token", "env_var": "SENTRY_ACCESS_TOKEN"}]}]},
    {"id": "linear", "name": "Linear", "description": "Find, create and update Linear issues and projects.",
     "homepage": "https://linear.app/docs/mcp", "options": [
        {"id": "remote", "label": "Linear's hosted server (sign in)", "transport": "http", "url": "https://mcp.linear.app/mcp", "auth": "oauth"}]},
    {"id": "notion", "name": "Notion", "description": "Search and edit pages and databases in Notion.",
     "homepage": "https://developers.notion.com/docs/mcp", "options": [
        {"id": "remote", "label": "Notion's hosted server (sign in)", "transport": "http", "url": "https://mcp.notion.com/mcp", "auth": "oauth"}]},
    {"id": "context7", "name": "Context7", "description": "Up-to-date library documentation and code examples.",
     "homepage": "https://github.com/upstash/context7", "options": [
        {"id": "remote", "label": "Context7's hosted server", "transport": "http", "url": "https://mcp.context7.com/mcp"}]},
]
TOKEN = re.compile(r"^[^\"'\s]{1,400}$")


def catalog(servers: list[dict]) -> list[dict]:
    installed = {s["catalog_id"] for s in servers if s.get("catalog_id")}
    return [{**entry, "installed": entry["id"] in installed} for entry in CATALOG]


def _entry(entry_id, option_id) -> tuple[dict, dict]:
    entry = next((e for e in CATALOG if e["id"] == entry_id), None)
    if entry is None:
        raise KeyError(entry_id)
    options = entry["options"]
    option = options[0] if option_id in (None, "") else next((o for o in options if o["id"] == option_id), None)
    if option is None:
        raise McpError("Choose one of this server's connection methods")
    return entry, option


def install(mcp, secrets, body: dict) -> dict:
    """Add a library server. Credentials typed here are sealed as API keys only once
    the check passes; nothing is stored when it fails."""
    entry, option = _entry(body.get("entry"), body.get("option"))
    name = body.get("name") or entry["id"]
    if not isinstance(name, str) or not NAME.match(name):
        raise McpError("Name must be 1–32 letters, digits, '_' or '-', starting with a letter or digit")
    if any(s["name"] == name for s in mcp.list()):
        raise McpError("A server with this name already exists; choose another name")
    args = body.get("args") or {}
    if not isinstance(args, dict):
        raise McpError("args must be an object")
    server = {"id": "", "name": name, "transport": option["transport"], "catalog_id": entry["id"], "env": {},
              "auth": option.get("auth", ""), "secret_ref": "", "env_var": "", "url": option.get("url", ""), "command": option.get("command", "")}
    if option["transport"] == "stdio":
        tokens = []
        for arg in option.get("args", []):
            value = args.get(arg["name"], "")
            if not isinstance(value, str):
                raise McpError(f"{arg['label']}: enter text")
            parts = value.split()
            if arg.get("required") and not parts:
                raise McpError(f"{arg['label']} is required")
            if any(not TOKEN.match(p) for p in parts):
                raise McpError(f"{arg['label']}: values cannot contain quotes")
            tokens += parts
        server["command"] = " ".join([option["command"], *tokens])
    # Credentials: a new value sealed under a key name, or an existing key name.
    typed, given = {}, body.get("credentials") or {}
    if not isinstance(given, dict):
        raise McpError("credentials must be an object")
    stored = {s["ref"] for s in secrets.list()}
    for credential in option.get("credentials", []):
        item = given.get(credential["name"])
        if not isinstance(item, dict):
            raise McpError(f"{credential['label']} is required")
        ref = item.get("ref") or item.get("save_as")
        if not isinstance(ref, str) or not REF.match(ref) or ref.startswith(MANAGED):
            raise McpError(f"{credential['label']}: name the API key (letters, digits, '.', '_' or '-')")
        if "value" in item:
            if not isinstance(item["value"], str) or not item["value"].strip():
                raise McpError(f"{credential['label']} is required")
            if ref in stored:
                raise McpError(f"An API key named “{ref}” already exists; choose another name or use the stored key")
            typed[ref] = item["value"].strip().encode("utf-8")
        elif ref not in stored:
            raise McpError(f"No API key named “{ref}” is stored")
        if option["transport"] == "http":
            server["secret_ref"] = ref
        else:
            server["env"][credential["env_var"]] = ref
    if option["transport"] == "stdio" and body.get("approve_command") is not True:
        raise McpError("Approve running this command on this machine first")
    checked = None
    if body.get("verify", True) and not server["auth"]:
        reveal = lambda ref: typed[ref] if ref in typed else secrets.reveal(ref)
        checked = test_server(server, reveal)  # raises McpError; nothing has been stored yet
    for ref, value in typed.items():
        secrets.set(ref, value)
    try:
        created = mcp.create({**server, "enabled": body.get("enable") is True})
    except McpError:
        for ref in typed:
            secrets.delete(ref)
        raise
    return {"id": created["id"], "name": name, "oauth": bool(server["auth"]),
            **({"tools": len(checked["tools"])} if checked else {})}
