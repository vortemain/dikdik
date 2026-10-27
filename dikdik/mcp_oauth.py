"""OAuth sign-in for remote MCP servers: authorization code with PKCE and a loopback redirect.

Discovery follows the MCP authorization spec: the server's 401 names its protected
resource metadata (RFC 9728), which names the authorization server, whose metadata
(RFC 8414 or OpenID discovery) gives the endpoints. Dikdik registers itself as a
public client (RFC 7591) when the server allows it. The browser is sent to the
authorization page; the provider redirects back to
``http://127.0.0.1:<port>/api/mcp/oauth/callback``, where the one-time ``state`` ties
the reply to a job this user started. Tokens are sealed in the secrets store under
``mcpauth.<server id>`` and refreshed before they expire. No redirect is followed, and
every endpoint must be HTTPS (or plain HTTP on this machine).
"""

import base64
import hashlib
import hmac
import json
import secrets as random
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .mcp import CLIENT, PROTOCOL, McpError, _check, _NoRedirect, oauth_ref

CALLBACK_PATH = "/api/mcp/oauth/callback"
DEFAULT_TIMEOUT = 300
MAX_JOBS = 20
FETCH_LIMIT = 1_000_000
EARLY = 60  # refresh this many seconds before a token expires


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _open(request: urllib.request.Request, timeout: float = 15) -> tuple[int, dict, bytes]:
    _check(request.full_url)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(FETCH_LIMIT + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(FETCH_LIMIT + 1) if exc.fp else b""
    except (urllib.error.URLError, TimeoutError, OSError):
        raise McpError("Could not reach the sign-in server") from None


def _json(url: str) -> dict | None:
    status, _, body = _open(urllib.request.Request(url, headers={"Accept": "application/json"}))
    if status != 200 or len(body) > FETCH_LIMIT:
        return None
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _header(headers: dict, name: str) -> str:
    return next((v for k, v in headers.items() if k.lower() == name.lower()), "")


def _resource_metadata_url(www_authenticate: str) -> str | None:
    marker = 'resource_metadata="'
    start = www_authenticate.find(marker)
    if start < 0:
        return None
    end = www_authenticate.find('"', start + len(marker))
    return www_authenticate[start + len(marker):end] if end > start else None


def _well_known(base: str, name: str) -> list[str]:
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    found = [f"{origin}/.well-known/{name}{path}"]
    if path:
        found.append(f"{origin}{path}/.well-known/{name}")
        found.append(f"{origin}/.well-known/{name}")
    return found


def discover(url: str) -> dict:
    """Find the endpoints for signing in to the MCP server at ``url``."""
    _check(url)
    probe = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                                   data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                    "params": {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT}}).encode())
    status, headers, _ = _open(probe)
    if status != 401:
        raise McpError("This server did not ask for sign-in" if status < 400 else f"The server answered HTTP {status} instead of asking for sign-in")
    resource, issuer, scopes = url, None, []
    candidates = [u for u in [_resource_metadata_url(_header(headers, "WWW-Authenticate"))] if u] + _well_known(url, "oauth-protected-resource")
    for candidate in candidates:
        try:
            _check(candidate)
        except McpError:
            continue
        meta = _json(candidate)
        if meta and isinstance(meta.get("authorization_servers"), list) and meta["authorization_servers"]:
            issuer = str(meta["authorization_servers"][0])
            resource = str(meta.get("resource") or url)
            scopes = [s for s in meta.get("scopes_supported", []) if isinstance(s, str)] if isinstance(meta.get("scopes_supported"), list) else []
            break
    if issuer is None:  # older servers: the MCP server's origin is the authorization server
        parts = urlsplit(url)
        issuer = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    _check(issuer)
    server = None
    for candidate in _well_known(issuer, "oauth-authorization-server") + _well_known(issuer, "openid-configuration"):
        server = _json(candidate)
        if server and server.get("authorization_endpoint") and server.get("token_endpoint"):
            break
        server = None
    if server is None:
        raise McpError("The server's sign-in settings could not be found")
    methods = server.get("code_challenge_methods_supported")
    if isinstance(methods, list) and "S256" not in methods:
        raise McpError("This server's sign-in does not support PKCE (S256), so Dikdik cannot use it")
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if server.get(key):
            _check(str(server[key]))
    return {"resource": resource, "issuer": issuer, "scopes": scopes,
            "authorization_endpoint": str(server["authorization_endpoint"]), "token_endpoint": str(server["token_endpoint"]),
            "registration_endpoint": str(server.get("registration_endpoint") or "")}


def register(endpoint: str, redirect_uri: str) -> dict:
    if not endpoint:
        raise McpError("This server needs a client registered in advance; Dikdik can only sign in where it may register itself")
    body = {"client_name": "Dikdik", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none"}
    status, _, raw = _open(urllib.request.Request(endpoint, method="POST", data=json.dumps(body).encode(),
                                                  headers={"Content-Type": "application/json", "Accept": "application/json"}))
    try:
        reply = json.loads(raw) if status in (200, 201) else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict) or not isinstance(reply.get("client_id"), str):
        raise McpError(f"The server refused to register Dikdik (HTTP {status})")
    return {"client_id": reply["client_id"], "client_secret": reply.get("client_secret") if isinstance(reply.get("client_secret"), str) else ""}


def _token_request(endpoint: str, form: dict) -> dict:
    status, _, raw = _open(urllib.request.Request(endpoint, method="POST", data=urlencode(form).encode(),
                                                  headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}))
    try:
        reply = json.loads(raw)
    except ValueError:
        reply = None
    if status != 200 or not isinstance(reply, dict) or not isinstance(reply.get("access_token"), str):
        error = reply.get("error") if isinstance(reply, dict) and isinstance(reply.get("error"), str) else f"HTTP {status}"
        raise McpError(f"The sign-in server refused the token request ({error[:60]})")
    return reply


def _tokens(reply: dict, previous: dict) -> dict:
    expires = reply.get("expires_in")
    return {**previous, "access_token": reply["access_token"],
            "refresh_token": reply.get("refresh_token") if isinstance(reply.get("refresh_token"), str) else previous.get("refresh_token", ""),
            "expires_at": time.time() + float(expires) if isinstance(expires, (int, float)) and expires > 0 else 0,
            "scope": reply.get("scope") if isinstance(reply.get("scope"), str) else previous.get("scope", "")}


def access_token(server: dict, reveal, save=None) -> str:
    """The server's current access token, refreshed first when it is about to expire."""
    try:
        stored = json.loads(reveal(oauth_ref(server["id"])))
    except KeyError:
        raise McpError(f"Sign in to {server['name']} first") from None
    if stored.get("expires_at") and time.time() > stored["expires_at"] - EARLY:
        if not stored.get("refresh_token") or save is None:
            raise McpError(f"The sign-in for {server['name']} has expired; sign in again")
        form = {"grant_type": "refresh_token", "refresh_token": stored["refresh_token"], "client_id": stored["client_id"], "resource": stored["resource"]}
        if stored.get("client_secret"):
            form["client_secret"] = stored["client_secret"]
        try:
            stored = _tokens(_token_request(stored["token_endpoint"], form), stored)
        except McpError:
            raise McpError(f"The sign-in for {server['name']} has expired; sign in again") from None
        save(oauth_ref(server["id"]), json.dumps(stored).encode())
    return stored["access_token"]


class OAuthJobs:
    """Sign-in attempts in progress. ``port`` is set once the HTTP server is listening."""

    def __init__(self, save):
        self.save, self.port = save, 0
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{CALLBACK_PATH}"

    @staticmethod
    def _public(job: dict) -> dict:
        return {"id": job["id"], "server_id": job["server_id"], "status": job["status"], "ok": job["status"] == "completed",
                "error": job.get("error", ""), "error_kind": job.get("error_kind", ""),
                **({"authorization_url": job["authorization_url"]} if job["status"] == "authorizing" else {})}

    def _expire(self, job: dict) -> None:
        if job["status"] == "authorizing" and time.monotonic() > job["deadline"]:
            job.update(status="failed", error="Sign-in was not completed in time", error_kind="timeout")

    def start(self, server: dict, timeout=DEFAULT_TIMEOUT) -> dict:
        if server["transport"] != "http":
            raise McpError("Only remote servers sign in with OAuth")
        if server.get("auth") != "oauth":
            raise McpError("Turn on OAuth sign-in for this server first")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
            raise McpError("timeout must be between 1 and 600 seconds")
        job = {"id": uuid.uuid4().hex, "server_id": server["id"], "status": "authorizing", "deadline": time.monotonic() + timeout}
        try:
            found = discover(server["url"])
            client = register(found["registration_endpoint"], self.redirect_uri)
        except McpError as exc:
            job.update(status="failed", error=str(exc), error_kind="unreachable" if "reach" in str(exc) else "discovery")
            self._remember(job)
            return self._public(job)
        verifier = _b64(random.token_bytes(48))
        state = _b64(random.token_bytes(32))
        query = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": self.redirect_uri,
                 "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
                 "state": state, "resource": found["resource"]}
        if found["scopes"]:
            query["scope"] = " ".join(found["scopes"])
        endpoint = found["authorization_endpoint"]
        job.update(state=state, verifier=verifier, found=found, client=client,
                   authorization_url=endpoint + ("&" if "?" in endpoint else "?") + urlencode(query))
        self._remember(job)
        return self._public(job)

    def _remember(self, job: dict) -> None:
        with self.lock:
            if len(self.jobs) >= MAX_JOBS:
                for old in sorted(self.jobs.values(), key=lambda j: j["deadline"])[: len(self.jobs) - MAX_JOBS + 1]:
                    del self.jobs[old["id"]]
            self.jobs[job["id"]] = job

    def status(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            self._expire(job)
            return self._public(job)

    def job_for_state(self, state: str) -> str | None:
        with self.lock:
            for job in self.jobs.values():
                self._expire(job)
                if job["status"] == "authorizing" and job.get("state") and hmac.compare_digest(job["state"], state):
                    return job["id"]
            return None

    def callback(self, job_id: str, callback_url) -> tuple[int, dict]:
        """Finish a job with the URL the provider redirected to."""
        with self.lock:
            job = self.jobs[job_id]
            self._expire(job)
            if job["status"] != "authorizing":
                return 409, self._public(job)
            if not isinstance(callback_url, str) or len(callback_url) > 8000:
                raise McpError("Send callback_url: the address the sign-in page redirected to")
            parts = urlsplit(callback_url)
            try:
                port = parts.port
            except ValueError:
                raise McpError("The callback address has an invalid port") from None
            if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"} or port != self.port or parts.path != CALLBACK_PATH:
                raise McpError(f"The callback address must be {self.redirect_uri}")
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            if not hmac.compare_digest(query.get("state", ""), job["state"]):
                raise McpError("The callback does not belong to this sign-in")
            if query.get("error"):
                denied = query["error"] == "access_denied"
                job.update(status="failed", error="Sign-in was cancelled" if denied else f"The sign-in server reported {query['error'][:60]}",
                           error_kind="denied" if denied else "provider")
                return 200, self._public(job)
            if not query.get("code"):
                raise McpError("The callback has no authorization code")
            job["status"] = "exchanging"  # one exchange per job, even if the callback arrives twice
        try:
            form = {"grant_type": "authorization_code", "code": query["code"], "redirect_uri": self.redirect_uri,
                    "client_id": job["client"]["client_id"], "code_verifier": job["verifier"], "resource": job["found"]["resource"]}
            if job["client"]["client_secret"]:
                form["client_secret"] = job["client"]["client_secret"]
            tokens = _tokens(_token_request(job["found"]["token_endpoint"], form), {
                "token_endpoint": job["found"]["token_endpoint"], "resource": job["found"]["resource"], **job["client"]})
            self.save(oauth_ref(job["server_id"]), json.dumps(tokens).encode())
            job.update(status="completed")
        except McpError as exc:
            job.update(status="failed", error=str(exc), error_kind="token")
        for key in ("verifier", "client"):
            job.pop(key, None)
        return 200, self._public(job)
