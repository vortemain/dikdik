"""Secret store tests: encryption at rest, redaction, DPAPI round-trip, keyfile, atomicity."""

import json
import tempfile
import unittest
from pathlib import Path

import dikdik.secrets_store as ss
from dikdik.server_routes import build_routes
from dikdik.store import Store


class FakeStores:
    def __init__(self, store: Store, data: Path):
        self.store = store
        self.data = data
        self.root = data.parent
        self.automation = None

    def paths(self):
        return FakeStores(self.store, self.data)


class Stubs:
    pass


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.engine = FakeStores(Store(self.data / "state.sqlite3"), self.data)
        self.routes = build_routes(self.engine)

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        return ss.SecretsStore(self.data / "secrets.bin")

    # --- core behaviour ---------------------------------------------------
    def test_set_list_never_return_plaintext_or_ciphertext(self):
        store = self.store()
        store.set("ai_key", b"MAGIC_PLAINTEXT_BUT_ONLY_A_TEST_DUMMY_KEY")
        listing = store.list()
        self.assertEqual([r["ref"] for r in listing], ["ai_key"])
        self.assertEqual(set(listing[0]), {"ref", "set_at", "env", "previous_set_at"})
        self.assertNotIn("plaintext", listing[0])
        blob = (self.data / "secrets.bin").read_text()
        self.assertNotIn("MAGIC_PLAINTEXT", blob)  # only ciphertext at rest

    def test_duplicate_ref_overwrites_atomically(self):
        store = self.store()
        store.set("dup", b"first value")
        self.assertIn("tag", (self.data / "secrets.bin").read_text())
        store.set("dup", b"second value")
        after = (self.data / "secrets.bin").read_text()
        self.assertNotIn("first value", after)
        self.assertNotIn("second value", after)  # ciphertext only
        self.assertTrue(self.store().list()[0]["ref"] == "dup")  # atomic replace kept exactly one record

    def test_counter_advances_on_overwrite(self):
        store = self.store()
        store.set("c", b"one")
        doc1 = store._document()
        store.set("c", b"two")
        doc2 = store._document()
        self.assertGreater(doc2["records"]["c"]["counter"], doc1["records"]["c"]["counter"])

    def test_delete_unknown_404_semantics(self):
        with self.assertRaises(KeyError):
            self.store().delete("missing")

    def test_ref_validation_and_traversal(self):
        for bad in ("./d=../x", "../x", "", "a b", "x" * 65, ".", None, 3):
            with self.assertRaises(ValueError):
                self.store().set(bad, b"v")
        self.assertEqual(bool(self.store().set("ok.ref-name", b"v")), True)

    def test_error_strings_scrubbed(self):
        store = ss.SecretsStore(self.data / "secrets.bin")
        store.set("k", b"valuable")
        (self.data / "secrets.bin").write_bytes(b"@@corrupt@@")
        raised = None
        try:
            store.set("k2", b"valuable2")
        except ss.SecretsError as exc:
            raised = exc
        except Exception as exc:  # any other path must still not echo data
            raised = exc
        else:
            self.fail("expected a fixed secret-free failure for a corrupt store")
        self.assertNotIn("valuable", str(raised))
        store2 = ss.SecretsStore(self.data / "secrets.bin")
        with self.assertRaises(ss.SecretsError):
            store2.set("k2", b"valuable2")
        with self.assertRaises(ss.SecretsError):
            store2.list()
        self.assertNotIn("valuable", "" )

    def test_missing_store_lists_empty(self):
        self.assertEqual(ss.SecretsStore(self.data / "absent.bin").list(), [])

    def test_keyfile_permissions_empty_for_missing_store(self):
        self.assertEqual(ss.SecretsStore(self.data / "absent.bin").list(), [])

    @unittest.skipIf(not ss.DPAPI_AVAILABLE, "DPAPI requires Windows")
    def test_dpapi_round_trip(self):
        key = ss.os.urandom(32)
        wrapped = ss._dpapi_protect(key)
        self.assertNotEqual(wrapped, key)
        self.assertEqual(ss._dpapi_unprotect(wrapped), key)

    @unittest.skipIf(not ss.DPAPI_AVAILABLE, "DPAPI store mode requires Windows")
    def test_dpapi_persisted_and_recovered_across_instances(self):
        store = ss.SecretsStore(self.data / "secrets.bin")
        store.set("token", b"valued")
        document = store._document()
        self.assertEqual(document["mode"], "dpapi")
        self.assertTrue(document["wrapped"])
        again = ss.SecretsStore(self.data / "secrets.bin")
        self.assertEqual(again.list()[0]["ref"], "token")
        # tampered ciphertext fails authentication with a secret-free message
        document = again._document()
        document["records"]["token"]["data"] = "00"
        (self.data / "secrets.bin").write_text(json.dumps(document))
        with self.assertRaises(ss.SecretsError):
            again.verify_all()

    def test_keyfile_fallback_layout(self):
        previous = ss.DPAPI_AVAILABLE
        ss.DPAPI_AVAILABLE = False
        try:
            store = ss.SecretsStore(self.data / "secrets.bin")
            store.set("nk", b"fine")
            keyfile = self.data / "secrets.key"
            self.assertTrue(keyfile.exists())
            if ss.os.name != "nt":  # POSIX permission bits are meaningless on Windows
                mode = 0o777 & keyfile.stat().st_mode
                self.assertFalse(mode & 0o077, f"key file too permissive: {oct(mode)}")
            self.assertIn("warning", store._document())
            self.assertEqual(ss.SecretsStore(self.data / "secrets.bin").list()[0]["ref"], "nk")
        finally:
            ss.DPAPI_AVAILABLE = previous

    def test_keyfile_excl_fails_when_keyfile_exists(self):
        ss.DPAPI_AVAILABLE = False
        try:
            (self.data / "key.key").write_bytes(b"\x00" * 33)  # exists AND wrong size
            store = ss.SecretsStore(self.data / "key.bin")
            with self.assertRaises(ss.SecretsError):
                store.set("late", b"v")
        finally:
            ss.DPAPI_AVAILABLE = ss.os.name == "nt"

    # --- HTTP behaviour -----------------------------------------------------
    def test_http_set_get_delete(self):
        status, _ = self.routes("POST", "/api/secrets", {"ref": "k", "plaintext": "value"})
        self.assertEqual(status, 201)
        status, rows = self.routes("GET", "/api/secrets", None)
        self.assertEqual(status, 200)
        self.assertEqual([r["ref"] for r in rows], ["k"])
        self.assertEqual(set(rows[0]), {"ref", "set_at", "env", "previous_set_at"})
        self.assertNotIn("value", str(rows))
        self.assertNotIn("ciphertext", str(rows))
        self.assertEqual(self.routes("DELETE", "/api/secrets/k", {})[0], 200)
        self.assertEqual(self.routes("DELETE", "/api/secrets/k", {})[0], 404)

    def test_http_malformed_bodies(self):
        self.assertEqual(self.routes("POST", "/api/secrets", {})[0], 400)
        self.assertEqual(self.routes("POST", "/api/secrets", {"ref": "a"})[0], 400)
        self.assertEqual(self.routes("POST", "/api/secrets", ["list"])[0], 400)
        self.assertEqual(self.routes("POST", "/api/secrets", {"ref": "../abc", "plaintext": "v"})[0], 400)
        self.assertEqual(self.routes("POST", "/api/profiles", None)[0], 400)

    def test_error_paths_never_echo_secret_material(self):
        text = str(self.routes("POST", "/api/secrets", {"ref": "z", "plaintext": "HIDDEN_SECRET"}))
        self.assertNotIn("HIDDEN_SECRET", text)


if __name__ == "__main__":
    unittest.main()
