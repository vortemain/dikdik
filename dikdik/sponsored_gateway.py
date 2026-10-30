"""Optional invite-only sponsored inference gateway.

Run behind an HTTPS reverse proxy: python -m dikdik.sponsored_gateway.
Provider keys and per-user invitation tokens come from the service environment.
Only token hashes and daily counters are persisted. No prompts are logged.
"""
from contextlib import closing
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .research import NoRedirect

MAX_BODY = 128 * 1024
MAX_RESPONSE = 2 * 1024 * 1024


class Allowances:
    """Daily counters for one invitation pool.

    A SQLite database may be shared by several pools (users and judges). Rows
    are stored as "scope:identity", and a pool's global limit counts only its
    own scope, so sponsored judging access never consumes the public allowance.
    """

    def __init__(self, path, scope="user", per_user=30, global_daily=300):
        self.path, self.scope = path, scope
        self.per_user, self.global_daily = per_user, global_daily
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS usage (day INTEGER, identity TEXT, count INTEGER, PRIMARY KEY(day, identity))")

    def identity(self, digest):
        return self.scope + ":" + digest

    def reserve(self, digest):
        key = self.identity(digest)
        day = int(time.time() // 86400)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            own = db.execute("SELECT count FROM usage WHERE day=? AND identity=?", (day, key)).fetchone()
            total = db.execute("SELECT COALESCE(SUM(count),0) FROM usage WHERE day=? AND identity LIKE ?",
                               (day, self.scope + ":%")).fetchone()[0]
            if (own[0] if own else 0) >= self.per_user or total >= self.global_daily:
                return False
            db.execute("INSERT INTO usage VALUES(?,?,1) ON CONFLICT(day,identity) DO UPDATE SET count=count+1", (day, key))
            db.execute("DELETE FROM usage WHERE day<?", (day - 7,))
        return True


def handler(model, provider_key, invitations, allowances, judge_invitations=(), judge_allowances=None):
    token_hashes = [hashlib.sha256(token.encode()).hexdigest() for token in invitations]
    missing = [name for name, value in (("NEBIUS_MODEL", model), ("NEBIUS_API_KEY", provider_key),
                                        ("DIKDIK_GATEWAY_INVITATIONS", invitations)) if not value]
    if missing:
        # Name the actual gaps so a container that did not receive Dokploy's
        # service environment is diagnosed instead of blamed on one variable.
        raise ValueError("This gateway process does not see: " + ", ".join(missing)
                         + ". The variables live in the hosting platform's service/stack environment.")
    judge_hashes = [hashlib.sha256(token.encode()).hexdigest() for token in judge_invitations]
    if judge_hashes and judge_allowances is None:
        raise ValueError("Set DIKDIK_GATEWAY_JUDGE_DAILY and DIKDIK_GATEWAY_JUDGE_GLOBAL_DAILY for judge invitations")
    if judge_hashes and not set(token_hashes).isdisjoint(judge_hashes):
        raise ValueError("Invitation tokens must not appear in both the public and judge lists")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(120)

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def identity(self):
            """Return (matching allowance pool, token digest) or None."""
            supplied = self.headers.get("Authorization", "")
            if not supplied.startswith("Bearer ") or len(supplied) > 512:
                return None
            digest = hashlib.sha256(supplied[7:].encode()).hexdigest()
            if judge_allowances is not None:
                for known in judge_hashes:
                    if hmac.compare_digest(digest, known):
                        return judge_allowances, digest
            for known in token_hashes:
                if hmac.compare_digest(digest, known):
                    return allowances, digest
            return None

        def do_GET(self):
            if self.path == "/healthz":
                self.reply(200, {"ok": True})
            elif not self.identity():
                self.reply(401, {"error": {"message": "Invitation required"}})
            elif self.path == "/v1/models":
                self.reply(200, {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "nebius"}]})
            else:
                self.reply(404, {"error": {"message": "Not found"}})

        def do_POST(self):
            matched = self.identity()
            if not matched:
                self.reply(401, {"error": {"message": "Invitation required"}})
                return
            pool, digest = matched
            if self.path != "/v1/chat/completions":
                self.reply(404, {"error": {"message": "Not found"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY:
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or body.get("model") != model or not isinstance(body.get("messages"), list) or not body["messages"]:
                    raise ValueError()
                # Fixed model, bounded output, no client-selected upstream or parallel completions.
                payload = {key: body[key] for key in ("messages", "tools", "tool_choice", "temperature", "top_p") if key in body}
                requested = body.get("max_tokens", body.get("max_completion_tokens", 2048))
                if type(requested) is not int or requested < 1:
                    raise ValueError()
                payload.update(model=model, max_tokens=min(requested, 2048), stream=False)
            except (ValueError, TypeError, OSError):
                self.reply(400, {"error": {"message": "Invalid or oversized request"}})
                return
            if not pool.reserve(digest):
                self.reply(429, {"error": {"message": "Sponsored daily allowance reached. Choose your own provider key or a local model.", "code": "rate_limit_exceeded"}})
                return
            request = urllib.request.Request("https://api.tokenfactory.nebius.com/v1/chat/completions",
                data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + provider_key, "Content-Type": "application/json"})
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                with opener.open(request, timeout=90) as response:
                    raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ValueError()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                self.reply(200, result)
            except (OSError, ValueError, TypeError):
                self.reply(503, {"error": {"message": "Sponsored inference unavailable. Retry later or choose another connection."}})

    return Handler


def parse_invitations(raw, variable):
    tokens = [token.strip() for token in raw.split(",") if token.strip()]
    if any(len(token) < 32 for token in tokens):
        raise ValueError("Use independently generated invitation tokens of at least 32 characters in " + variable)
    return tokens


ENVIRONMENT_VARIABLES = (
    "NEBIUS_API_KEY", "NEBIUS_MODEL", "DIKDIK_GATEWAY_INVITATIONS",
    "DIKDIK_GATEWAY_HOST", "DIKDIK_GATEWAY_PORT", "DIKDIK_GATEWAY_USAGE_DB",
    "DIKDIK_GATEWAY_USER_DAILY", "DIKDIK_GATEWAY_GLOBAL_DAILY",
    "DIKDIK_GATEWAY_JUDGE_INVITATIONS", "DIKDIK_GATEWAY_JUDGE_DAILY", "DIKDIK_GATEWAY_JUDGE_GLOBAL_DAILY",
)


def check_environment(emit=None) -> bool:
    """Report which gateway variables reached this process, without values.

    Run inside the container: python -m dikdik.sponsored_gateway --check-env
    Never prints secret contents, only whether each variable was received.
    """
    emit = emit if emit is not None else print
    required = set(ENVIRONMENT_VARIABLES[:3])
    missing = []
    for name in ENVIRONMENT_VARIABLES:
        value = os.environ.get(name)
        if not value:
            missing.append(name)
        emit(f"{name}: {'set (' + str(len(value)) + ' chars)' if value else 'MISSING'}")
    if missing:
        emit("The gateway cannot start until it receives: " + ", ".join(missing))
        emit("If they are set in Dokploy but seen missing here, the container did not"
             " get the service/stack environment (see docs/GATEWAY_DEPLOY.md).")
    else:
        emit("The gateway process received every configuration variable.")
    return not required.intersection(missing)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--check-env":
        raise SystemExit(0 if check_environment() else 1)
    invitations = parse_invitations(os.environ.get("DIKDIK_GATEWAY_INVITATIONS", ""), "DIKDIK_GATEWAY_INVITATIONS")
    judge_invitations = parse_invitations(os.environ.get("DIKDIK_GATEWAY_JUDGE_INVITATIONS", ""), "DIKDIK_GATEWAY_JUDGE_INVITATIONS")
    if judge_invitations and not set(invitations).isdisjoint(judge_invitations):
        raise ValueError("Invitation tokens must not appear in both the public and judge lists")
    path = Path(os.environ.get("DIKDIK_GATEWAY_USAGE_DB", "gateway-usage.sqlite3"))
    usage = Allowances(path, "user",
                       int(os.environ.get("DIKDIK_GATEWAY_USER_DAILY", "30")),
                       int(os.environ.get("DIKDIK_GATEWAY_GLOBAL_DAILY", "300")))
    judge_usage = None
    if judge_invitations:
        judge_usage = Allowances(path, "judge",
                                 int(os.environ.get("DIKDIK_GATEWAY_JUDGE_DAILY", "150")),
                                 int(os.environ.get("DIKDIK_GATEWAY_JUDGE_GLOBAL_DAILY", "600")))
    app = handler(os.environ.get("NEBIUS_MODEL", ""), os.environ.get("NEBIUS_API_KEY", ""), invitations, usage,
                  judge_invitations=judge_invitations, judge_allowances=judge_usage)
    host = os.environ.get("DIKDIK_GATEWAY_HOST", "127.0.0.1")
    if host != "127.0.0.1" and host != "localhost":
        print("sponsored_gateway: binding a non-loopback address without a reverse proxy in front is unsafe", file=sys.stderr)
    server = ThreadingHTTPServer((host, int(os.environ.get("DIKDIK_GATEWAY_PORT", "8788"))), app)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
