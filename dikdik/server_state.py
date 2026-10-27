"""Deferred initialization: the server starts dormant and /api/init flips it ready.

This is the Cloud seam: a fresh server boots dormant (no workspace, no session
keys), and POST /api/init delivers the workspace path, session key, and
optional configuration. Local mode starts ready as before; the dormant state
is only entered when the server is started with --dormant.
"""

import os
import time


class ServerState:
    """Tracks whether the server is ready or dormant."""

    def __init__(self, ready: bool = True):
        self._ready = ready
        self._init_key = os.environ.get("DIKDIK_INIT_KEY", "")
        self._initialized_at = time.time() if ready else None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def dormant(self) -> bool:
        return not self._ready

    def initialize(self, body: dict, engine) -> dict:
        if self._ready:
            raise ValueError("Server is already initialized")
        if not isinstance(body, dict):
            raise ValueError("Init body must be a JSON object")
        if self._init_key:
            supplied = body.get("init_key", "")
            if not isinstance(supplied, str) or supplied != self._init_key:
                raise ValueError("Invalid init key")
        workspace = body.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workspace must be a path string")
        # In local mode, the workspace is fixed at startup. We accept the
        # init call but only validate that the workspace matches.
        from .repository import approved_root
        try:
            root = approved_root(workspace)
        except (ValueError, OSError) as exc:
            raise ValueError(str(exc)) from None
        if str(root) != str(engine.root):
            raise ValueError("Workspace does not match the server's --workspace")
        self._ready = True
        self._initialized_at = time.time()
        return {"ready": True, "workspace": str(root), "initialized_at": self._initialized_at}

    def info(self) -> dict:
        return {"ready": self._ready, "dormant": self.dormant,
                "initialized_at": self._initialized_at}