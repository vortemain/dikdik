"""Encrypted-at-rest secret store.

DECISION (docs/M4_CONTRACT.md, stdlib-only authenticated scheme):

    keystream seed   = SHA-256(key || 0x1e || ref || 0x1e || counter)
    keystream blocks = SHA-256(seed || SHA256-less uint32 BE block index)
    ciphertext       = plaintext XOR keystream (stream cipher)
    header           = JSON of {"alg", "ref", "counter", "len", "v"} (no plaintext)
    tag              = HMAC-SHA256(key, header || ciphertext)   (encrypt-then-MAC)

Key material never leaves the process:
- Windows: a random 32-byte master key wrapped as a DPAPI blob
  (CryptProtectData via ctypes, scope = current user) stored in the store header.
- Other platforms: the master key is its own file `<data>/secrets.key`, created
  with mode 0600 (access + permission warning is returned by the creator).

Plaintext is never returned over HTTP, never written under any key other than
ciphertext, and never appears in an error string of this module. The one
in-process accessor, `reveal`, exists so the model resolver can authenticate a
provider call; no route exposes it. Saving is
a temp-file + os.replace atomic swap. The only backend failure raised is
`SecretsError`; its message is fixed and secret-free.
"""

import ctypes
import hmac
import hashlib
import json
import os
import time
from pathlib import Path

MAGIC = "dikdik-secrets"
ALG = "dikdik-enc-mac-v1"
VERSION = 1
KEY_SIZE = 32
MAX_VALUE = 65536


class SecretsError(Exception):
    """Fixed, secret-free failure message surface."""

    def __init__(self, message="Secret store unavailable; the change was not applied"):
        super().__init__(message)


def _keystream(key: bytes, ref: str, counter: int, length: int) -> bytes:
    seed = hashlib.sha256(key + b"\x1e" + ref.encode("utf-8") + b"\x1e" + str(counter).encode("ascii")).digest()
    out = bytearray()
    index = 0
    while len(out) < length:
        out.extend(hashlib.sha256(seed + index.to_bytes(4, "big")).digest())
        index += 1
    return bytes(out[:length])


def _xor(data: bytes, padding: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, padding))


def _encrypt_record(key: bytes, ref: str, counter: int, plaintext: bytes):
    header = json.dumps({"alg": ALG, "ref": ref, "counter": counter, "len": len(plaintext), "v": VERSION}, sort_keys=True).encode("utf-8")
    cipher = _xor(plaintext, _keystream(key, ref, counter, len(plaintext)))
    tag = hmac.new(key, header + cipher, hashlib.sha256).digest()
    return header, cipher, tag


def _decrypt_record(key: bytes, record: dict) -> bytes:
    """Authenticates, then decrypts."""
    header = bytes.fromhex(record["header"])
    cipher = bytes.fromhex(record["data"])
    tag = hmac.new(key, header + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag.hex(), record["tag"]):
        raise SecretsError("A stored secret record failed its integrity check")
    meta = json.loads(header.decode("utf-8"))
    if meta.get("alg") != ALG or meta.get("ref") != record["ref"]:
        raise SecretsError("A stored secret record failed its integrity check")
    return _xor(cipher, _keystream(key, record["ref"], meta["counter"], len(cipher)))


# --- Windows DPAPI wrapping (ctypes, stdlib-only) ----------------------

class _WindowsDPAPI:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


def _dpapi_protect(data: bytes) -> bytes:
    blob = _WindowsDPAPI.DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.c_void_p))
    out = _WindowsDPAPI.DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out.pbData))


def _dpapi_unprotect(blob: bytes) -> bytes:
    blob_in = _WindowsDPAPI.DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.c_void_p))
    out = _WindowsDPAPI.DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(out)):
        raise SecretsError("Local encryption key could not be recovered with this Windows user")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out.pbData))


# --- store ---------------------------------------------------------------

DPAPI_AVAILABLE = os.name == "nt"


class SecretsStore:
    """Backend for `<data>/secrets.bin`. Additive to the shared Store."""

    def __init__(self, path: Path, keyfile: Path | None = None):
        self.path = Path(path)
        self.keyfile = Path(keyfile) if keyfile else self.path.with_suffix(".key")

    # -- key lifecycle --------------------------------------------------
    def _key(self, document: dict) -> bytes:
        if document.get("mode") == "dpapi":
            if not DPAPI_AVAILABLE:
                raise SecretsError("Local encryption key could not be recovered on this platform")
            return _dpapi_unprotect(bytes.fromhex(document["wrapped"]))
        try:
            data = self.keyfile.read_bytes()
        except OSError as exc:
            raise SecretsError(f"Local key file unavailable ({exc})") from None
        if len(data) != KEY_SIZE:
            raise SecretsError("Local key file has an unexpected size")
        return data

    def _document(self):
        if self.path.exists():
            try:
                document = json.loads(self.path.read_bytes())
            except (ValueError, OSError):
                raise SecretsError("Secret store file is unreadable") from None
            if not isinstance(document, dict) or document.get(MAGIC) is not True:
                raise SecretsError("Secret store file is unreadable")
            return document
        key = os.urandom(KEY_SIZE)
        if DPAPI_AVAILABLE:
            document = {MAGIC: True, "version": VERSION, "mode": "dpapi", "wrapped": _dpapi_protect(key).hex(), "records": {}}
        else:
            try:
                fd = os.open(self.keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(key)
                os.chmod(self.keyfile, 0o600)
            except OSError as exc:
                raise SecretsError(f"Key file unavailable ({exc}); no secret was stored") from None
            document = {MAGIC: True, "version": VERSION, "mode": "keyfile", "wrapped": "", "warning": "Key file fallback: aes-free development store keyed by a 0600 file" , "records": {}}
        self._save(document)
        return document

    def _save(self, document: dict):
        replace = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(replace, "wb") as handle:
                handle.write(json.dumps(document).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(replace, self.path)
        except OSError:
            try:
                replace.unlink()
            except OSError:
                pass
            raise SecretsError("Secret store could not be written; the change was not applied") from None

    # -- public surface (never returns plaintext) -----------------------
    @staticmethod
    def _validate_ref(ref) -> None:
        if isinstance(ref, bool) or not isinstance(ref, str) or not 1 <= len(ref) <= 64 or not all(c.isalnum() or c in "_.-" for c in ref) or ref.startswith("."):
            raise ValueError("Secret reference must be 1–64 characters of letters, digits, '.', '_' or '-'")

    def set(self, ref: str, plaintext: bytes):
        self._validate_ref(ref)
        if not isinstance(plaintext, (bytes, bytearray)) or not plaintext:
            raise ValueError("Plaintext must be non-empty bytes")
        plaintext = bytes(plaintext)
        if len(plaintext) > MAX_VALUE:
            raise ValueError("Secret value must be at most 64KB")
        document = self._document()
        try:
            key = self._key(document)
        except SecretsError:
            raise
        except Exception:
            raise SecretsError("Local encryption key could not be recovered; nothing was changed") from None
        records = document["records"]
        counter = int(records.get(ref, {}).get("counter", 0)) + 1
        header, cipher, tag = _encrypt_record(key, ref, counter, plaintext)
        records[ref] = {"ref": ref, "set_at": time.time(), "counter": counter, "header": header.hex(), "data": cipher.hex(), "tag": tag.hex()}
        self._save(document)  # atomic replace
        return records[ref]["set_at"]

    def delete(self, ref: str):
        self._validate_ref(ref)
        document = self._document() if self.path.exists() else None
        if document is None or ref not in document["records"]:
            raise KeyError(ref)
        del document["records"][ref]
        self._save(document)

    def list(self):
        if not self.path.exists():
            return []
        document = self._document()
        self._key(document)  # availability check only; values stay sealed
        return [dict(ref=r["ref"], set_at=r["set_at"]) for r in sorted(document["records"].values(), key=lambda r: r["ref"])]

    def reveal(self, ref: str) -> bytes:
        """Decrypt one value for an in-process provider call. Never route this."""
        self._validate_ref(ref)
        if not self.path.exists():
            raise KeyError(ref)
        document = self._document()
        record = document["records"].get(ref)
        if record is None:
            raise KeyError(ref)
        return _decrypt_record(self._key(document), record)

    def verify_all(self) -> int:
        """At-most-once integrity sweep; returns sealed record count."""
        if not self.path.exists():
            return 0
        document = self._document()
        key = self._key(document)
        for record in list(document["records"].values()):
            _decrypt_record(key, record)  # plaintext ignored; auth errors only
        return len(document["records"])
