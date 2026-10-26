"""Profile endpoint/store tests: CRUD, default-delete 409, credential redaction, validation."""

import json
import tempfile
import unittest
from pathlib import Path

from dikdik.profiles import ApiError, ProfileStore
from dikdik.server_routes import build_routes
from dikdik.store import Store


class StoreEngine:
    """Minimal stores bundle for build_routes."""

    def __init__(self, data: Path, root: Path):
        self.store = Store(data / "state.sqlite3")
        self.data = data
        self.root = root
        self.automation = None


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "ws"
        data = Path(self.tmp.name) / "data"
        root.mkdir()
        data.mkdir()
        self.engine = StoreEngine(data, root)
        self.store = ProfileStore(self.engine.store)
        self.routes = build_routes(self.engine)
        self.engine.automation = self  # routes only touch automation for its own table

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, name="a", **extra):
        body = {"name": name, "provider": "openai", "model": "gpt-x", **extra}
        return self.store.create(body)

    # --- CRUD ----------------------------------------------------------
    def test_create_and_list(self):
        status, payload = self.create(credential_secret_ref="openai_key")
        self.assertEqual(status, 201)
        self.assertTrue(set(payload) == {"id"})
        status, rows = self.store.list_profiles()
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(set(row), {"id", "name", "provider", "model", "endpoint", "timeout_seconds", "is_default"})
        self.assertEqual(row["is_default"], True)  # first profile becomes default
        self.assertNotIn("credential_secret_ref", json.dumps(rows))  # never returns credentials/refs

    def test_duplicate_name_400(self):
        self.create(name="dup")
        with self.assertRaises(ApiError) as ctx:
            self.create(name="dup")
        self.assertEqual(ctx.exception.status, 400)

    def test_patch(self):
        _, created = self.create()
        other = self.store.create({"name": "b", "provider": "local", "model": "m"})[1]
        status, _ = self.store.patch(other["id"], {"is_default": True})
        self.assertEqual(status, 200)
        rows = self.store.list_profiles()[1]
        self.assertEqual([r["is_default"] for r in rows if r["id"] == other["id"]], [True])
        self.assertNotIn(True, [r["is_default"] for r in rows if r["id"] == created["id"]])
        status, _ = self.store.patch(created["id"], {"timeout_seconds": 90})
        self.assertEqual(status, 200)
        rows = self.store.list_profiles()[1]
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[created["id"]]["timeout_seconds"], 90)

    def test_patch_rejects_forbidden_fields(self):
        _, created = self.create()
        for field in ({"name": "x"}, {"provider": "anthropic"}, {"credential_secret_ref": "..."}):
            with self.assertRaises(ApiError) as ctx:
                self.store.patch(created["id"], field)
            self.assertEqual(ctx.exception.status, 400)

    def test_patch_unknown_404(self):
        with self.assertRaises(ApiError) as ctx:
            self.store.patch("nope", {"model": "m"})
        self.assertEqual(ctx.exception.status, 404)

    def test_default_delete_409_flow(self):
        _, created = self.create()  # default
        with self.assertRaises(ApiError) as ctx:
            self.store.delete(created["id"], confirm_default=False)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.routes("DELETE", f"/api/profiles/{created['id']}", {})[0], 409)
        _, other = self.store.create({"name": "b", "provider": "local", "model": "m"})
        status, payload = self.store.delete(created["id"], confirm_default=True)
        self.assertEqual(status, 200)
        after = self.store.list_profiles()[1]
        self.assertTrue(after and all(r["is_default"] for r in after if r["id"] == other["id"]))

    def test_delete_unknown_404(self):
        with self.assertRaises(ApiError) as ctx:
            self.store.delete("gone", True)
        self.assertEqual(ctx.exception.status, 404)

    # --- validation ------------------------------------------------------
    def test_provider_whitelist(self):
        for bad in (None, "gemini", "", 1):
            with self.assertRaises(ApiError) as ctx:
                self.create(provider=bad)
            self.assertEqual(ctx.exception.status, 400)

    def test_endpoint_validation(self):
        for url in ("https://127.0.0.1:8080/v1", "http://localhost:9000/api", None,
                    "https://mojave.cloud/v1", "https://api.anthropic.com/v1"):
            self.create(name="ok-" + str(url), endpoint=url)
        for bad in ("https://api.openai.com.evil.example/v1", "ftp://x", "https://example.com/v1", 5):
            with self.assertRaises(ApiError) as ctx:
                self.create(name="bad-" + str(bad), endpoint=bad)
            self.assertEqual(ctx.exception.status, 400)

    def test_timeout_validation(self):
        for bad in (0, 601, 1.5, "30", True, None):
            with self.assertRaises(ApiError) as ctx:
                self.create(name=f"t{bad!r}", timeout_seconds=bad)
            self.assertEqual(ctx.exception.status, 400)
        self.create(name="good", timeout_seconds=600)

    def test_credential_ref_rejects_traversal(self):
        for bad in ("../secret", ".hidden", "a b", "x" * 65, 123):
            with self.assertRaises(ApiError) as ctx:
                self.create(credential_secret_ref=bad)
            self.assertEqual(ctx.exception.status, 400)

    # --- routing ---------------------------------------------------------
    def test_routes_end_to_end(self):
        status, payload = self.routes("POST", "/api/profiles", {"name": "r", "provider": "azure", "model": "m"})
        self.assertEqual(status, 201)
        status, rows = self.routes("GET", "/api/profiles", None)
        self.assertEqual(status, 200)
        # JSON round-trip keeps the contract shape and loses no credential field
        again = json.loads(json.dumps(rows))
        self.assertEqual(set(again[0]), {"id", "name", "provider", "model", "endpoint", "timeout_seconds", "is_default"})
        profile_id = rows[0]["id"]
        self.assertEqual(self.routes("PATCH", f"/api/profiles/{profile_id}", {"timeout_seconds": 5})[0], 200)
        self.assertEqual(self.routes("DELETE", f"/api/profiles/{profile_id}?confirm_default=1", {})[0], 200)
        self.assertEqual(self.routes("DELETE", f"/api/profiles/{profile_id}", {})[0], 404)
        self.assertEqual(self.routes("POST", "/api/profiles", "not a dict")[0], 400)
        self.assertEqual(self.routes("POST", "/api/profiles", None)[0], 400)  # empty body → 400
        self.assertEqual(self.routes("GET", "/api/profiles/extra/segments", {})[0], 404)
        self.assertEqual(self.routes("POST", "/api/unknown", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
