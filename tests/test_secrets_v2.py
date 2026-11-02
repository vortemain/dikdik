"""Secret store format v2: separate keys, a fresh nonce per seal, and a safe v1 upgrade."""

import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

import dikdik.secrets_store as ss


def seal_v1(key: bytes, ref: str, counter: int, value: bytes) -> dict:
    """A record exactly as releases before v2 wrote it."""
    header = json.dumps({"alg": ss.ALG_V1, "ref": ref, "counter": counter, "len": len(value), "v": 1}, sort_keys=True).encode()
    cipher = ss._xor(value, ss._keystream_v1(key, ref, counter, len(value)))
    return {"ref": ref, "set_at": 1.0, "counter": counter, "header": header.hex(), "data": cipher.hex(),
            "tag": hmac.new(key, header + cipher, hashlib.sha256).hexdigest()}


class SecretsV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ss.SecretsStore(Path(self.temp.name) / "secrets.bin")

    def document(self):
        return json.loads(self.store.path.read_bytes())

    def key(self):
        return self.store._key(self.document())

    def test_new_values_use_v2_with_a_fresh_nonce_each_time(self):
        self.store.set("provider", b"same value")
        first = self.document()["records"]["provider"]
        self.store.set("provider", b"same value")
        second = self.document()["records"]["provider"]
        self.assertEqual(ss._record_alg(second), ss.ALG)
        nonces = {json.loads(bytes.fromhex(r["header"]))["nonce"] for r in (first, second)}
        self.assertEqual(len(nonces), 2)
        self.assertNotEqual(first["data"], second["data"])
        self.assertEqual(self.store.reveal("provider"), b"same value")

    def test_a_rolled_back_store_file_cannot_reuse_a_keystream(self):
        # The v1 weakness: restoring an old file reused counters, so two values shared a keystream.
        self.store.set("k", b"A" * 32)
        snapshot = self.store.path.read_bytes()
        first = bytes.fromhex(self.document()["records"]["k"]["data"])
        self.store.path.write_bytes(snapshot)  # e.g. restored from a backup
        self.store.set("k", b"B" * 32)
        self.store.path.write_bytes(snapshot)
        self.store.set("k", b"C" * 32)
        third = bytes.fromhex(self.document()["records"]["k"]["data"])
        # With a shared keystream, cipher XOR cipher would equal plaintext XOR plaintext.
        self.assertNotEqual(ss._xor(first, third), ss._xor(b"A" * 32, b"C" * 32))

    def test_v1_records_still_open_and_upgrade_in_place(self):
        self.store.set("seed", b"x")  # creates the store and its key
        key = self.key()
        document = self.document()
        current = seal_v1(key, "openai", 2, b"sk-new-value")
        current["previous"] = {k: v for k, v in seal_v1(key, "openai", 1, b"sk-old-value").items()}
        document["records"]["openai"] = current
        document["version"] = 1
        self.store._save(document)
        self.assertEqual(self.store.reveal("openai"), b"sk-new-value")  # backward compatible

        self.assertEqual(self.store.upgrade(), 2)  # current and kept previous value
        record = self.document()["records"]["openai"]
        self.assertEqual((ss._record_alg(record), ss._record_alg(record["previous"])), (ss.ALG, ss.ALG))
        self.assertEqual(self.document()["version"], ss.VERSION)
        self.assertEqual(self.store.reveal("openai"), b"sk-new-value")
        self.store.restore_previous("openai")
        self.assertEqual(self.store.reveal("openai"), b"sk-old-value")
        self.assertEqual(self.store.upgrade(), 0)  # idempotent

    def test_a_corrupt_v1_record_aborts_the_upgrade_without_changes(self):
        self.store.set("seed", b"x")
        document = self.document()
        broken = seal_v1(self.key(), "bad", 1, b"value")
        broken["data"] = "00" + broken["data"][2:]
        document["records"]["bad"] = broken
        self.store._save(document)
        before = self.store.path.read_bytes()
        with self.assertRaises(ss.SecretsError):
            self.store.upgrade()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_editing_the_format_name_fails_authentication(self):
        self.store.set("k", b"value")
        document = self.document()
        record = document["records"]["k"]
        meta = json.loads(bytes.fromhex(record["header"]))
        meta["alg"] = ss.ALG_V1  # try to steer decryption to the legacy path
        record["header"] = json.dumps(meta, sort_keys=True).encode().hex()
        self.store._save(document)
        with self.assertRaises(ss.SecretsError):
            self.store.reveal("k")

    def test_encryption_and_mac_keys_are_distinct(self):
        enc, mac = ss._subkeys(b"\x01" * 32)
        self.assertNotEqual(enc, mac)
        self.assertNotIn(b"\x01" * 32, (enc, mac))


if __name__ == "__main__":
    unittest.main()
