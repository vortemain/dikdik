"""Encrypted-at-rest secret store.

DECISION (docs/M4_CONTRACT.md, stdlib-only authenticated scheme), format v2:

    enc_key, mac_key = HMAC-SHA256(master, "dikdik-secrets/v2/encrypt" | ".../mac")
    nonce            = 16 random bytes per sealed value
    keystream block  = HMAC-SHA256(enc_key, nonce || 0x1e || ref || 0x1e || uint32 BE index)
    ciphertext       = plaintext XOR keystream (stream cipher)
    header           = JSON of {"alg", "ref", "counter", "len", "nonce", "v"} (no plaintext)
    tag              = HMAC-SHA256(mac_key, header || ciphertext)   (encrypt-then-MAC)

The random nonce means a keystream is never reused even if an old copy of the store file is
restored. Format v1 (SHA-256(key||ref||counter) keystream, one key for encryption and MAC) is
still read, and `upgrade()` re-seals v1 values as v2.

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
import re
import time
from pathlib import Path
from . import session_credentials

MAGIC = "dikdik-secrets"
ALG = "dikdik-enc-mac-v2"
ALG_V1 = "dikdik-enc-mac-v1"  # read and upgraded, never written
VERSION = 2
NONCE_SIZE = 16
KEY_SIZE = 32
MAX_VALUE = 65536
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
RESERVED_ENV = {"HOME", "PATH", "CI", "PWD", "USER", "SHELL", "TMPDIR", "CARGO_TARGET_DIR", "BUN_INSTALL_CACHE_DIR", "npm_config_cache"}


def _next_counter(document: dict, ref: str) -> int:
    """Counters per name only grow, even across deletes and undone rotations, so a keystream is never reused."""
    record = document["records"].get(ref) or {}
    seen = document.setdefault("counters", {})
    counter = max(int(seen.get(ref, 0)), int(record.get("counter", 0)), int(record.get("max_counter", 0))) + 1
    seen[ref] = counter
    return counter


class SecretsError(Exception):
    """Fixed, secret-free failure message surface."""

    def __init__(self, message="Secret store unavailable; the change was not applied"):
        super().__init__(message)


def _keystream_v1(key: bytes, ref: str, counter: int, length: int) -> bytes:
    """Legacy v1 keystream; read-only, so records sealed by earlier releases still open."""
    seed = hashlib.sha256(key + b"\x1e" + ref.encode("utf-8") + b"\x1e" + str(counter).encode("ascii")).digest()
    out = bytearray()
    index = 0
    while len(out) < length:
        out.extend(hashlib.sha256(seed + index.to_bytes(4, "big")).digest())
        index += 1
    return bytes(out[:length])


def _subkeys(master: bytes) -> tuple[bytes, bytes]:
    """Independent encryption and MAC keys (HKDF-Expand style, one block each)."""
    return (hmac.new(master, b"dikdik-secrets/v2/encrypt\x01", hashlib.sha256).digest(),
            hmac.new(master, b"dikdik-secrets/v2/mac\x01", hashlib.sha256).digest())


def _keystream(enc_key: bytes, nonce: bytes, ref: str, length: int) -> bytes:
    """HMAC-SHA256 in counter mode over a random per-record nonce: a fresh stream for every seal."""
    prefix = nonce + b"\x1e" + ref.encode("utf-8") + b"\x1e"
    out = bytearray()
    index = 0
    while len(out) < length:
        out.extend(hmac.new(enc_key, prefix + index.to_bytes(4, "big"), hashlib.sha256).digest())
        index += 1
    return bytes(out[:length])


def _xor(data: bytes, padding: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, padding))


def _encrypt_record(key: bytes, ref: str, counter: int, plaintext: bytes):
    enc_key, mac_key = _subkeys(key)
    nonce = os.urandom(NONCE_SIZE)
    header = json.dumps({"alg": ALG, "ref": ref, "counter": counter, "len": len(plaintext), "nonce": nonce.hex(), "v": VERSION},
                        sort_keys=True).encode("utf-8")
    cipher = _xor(plaintext, _keystream(enc_key, nonce, ref, len(plaintext)))
    tag = hmac.new(mac_key, header + cipher, hashlib.sha256).digest()
    return header, cipher, tag


def _record_alg(record: dict) -> str:
    try:
        return json.loads(bytes.fromhex(record["header"]).decode("utf-8")).get("alg", "")
    except (KeyError, ValueError, AttributeError):
        return ""


def _decrypt_record(key: bytes, record: dict) -> bytes:
    """Authenticates, then decrypts. The header names the format; the tag covers the header,
    so an edited format name fails authentication instead of selecting a weaker path."""
    try:
        header = bytes.fromhex(record["header"])
        cipher = bytes.fromhex(record["data"])
        meta = json.loads(header.decode("utf-8"))
    except (KeyError, ValueError, AttributeError):
        raise SecretsError("A stored secret record failed its integrity check") from None
    alg = meta.get("alg") if isinstance(meta, dict) else None
    mac_key = _subkeys(key)[1] if alg == ALG else key
    tag = hmac.new(mac_key, header + cipher, hashlib.sha256).digest()
    if alg not in (ALG, ALG_V1) or not hmac.compare_digest(tag.hex(), str(record.get("tag", ""))) or meta.get("ref") != record.get("ref"):
        raise SecretsError("A stored secret record failed its integrity check")
    if alg == ALG_V1:
        return _xor(cipher, _keystream_v1(key, record["ref"], meta["counter"], len(cipher)))
    return _xor(cipher, _keystream(_subkeys(key)[0], bytes.fromhex(meta["nonce"]), record["ref"], len(cipher)))


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
        old = records.get(ref)
        counter = _next_counter(document, ref)
        header, cipher, tag = _encrypt_record(key, ref, counter, plaintext)
        record = {"ref": ref, "set_at": time.time(), "counter": counter, "header": header.hex(), "data": cipher.hex(), "tag": tag.hex()}
        if old:
            # Keep the value being replaced (still sealed) so a bad rotation can be undone.
            record["previous"] = {k: old[k] for k in ("ref", "set_at", "counter", "header", "data", "tag")}
            if old.get("env"):
                record["env"] = old["env"]
        records[ref] = record
        self._save(document)  # atomic replace
        session_credentials.remove(self.path, ref)
        return record["set_at"]

    def restore_previous(self, ref: str):
        """Undo the last rotation: the previous value becomes current again."""
        self._validate_ref(ref)
        document = self._document() if self.path.exists() else None
        record = (document or {}).get("records", {}).get(ref)
        if record is None:
            raise KeyError(ref)
        if not record.get("previous"):
            raise ValueError("There is no previous value to restore")
        restored = {**record["previous"], "max_counter": max(int(record["counter"]), int(record.get("max_counter", 0))),
                    **({"env": record["env"]} if record.get("env") else {})}
        _decrypt_record(self._key(document), restored)  # integrity check before switching
        document["records"][ref] = restored
        self._save(document)

    def rename(self, ref: str, new: str):
        """Values are bound to their name when sealed, so renaming re-seals them under the new one."""
        self._validate_ref(ref)
        self._validate_ref(new)
        document = self._document() if self.path.exists() else None
        records = (document or {}).get("records", {})
        if ref not in records:
            raise KeyError(ref)
        if new in records:
            raise ValueError("A secret with that name already exists")
        key = self._key(document)
        old = records[ref]
        value = _decrypt_record(key, old)
        counter = _next_counter(document, new)
        header, cipher, tag = _encrypt_record(key, new, counter, value)
        records[new] = {"ref": new, "set_at": old["set_at"], "counter": counter, "header": header.hex(), "data": cipher.hex(), "tag": tag.hex(),
                        **({"env": old["env"]} if old.get("env") else {})}
        del records[ref]
        self._save(document)

    def set_env(self, ref: str, env):
        """The variable name a command sees this secret as (None: never exposed)."""
        self._validate_ref(ref)
        if env is not None and (not isinstance(env, str) or not ENV_NAME.fullmatch(env) or env in RESERVED_ENV):
            raise ValueError("Variable name must be 1–64 of A–Z, 0–9 and _, not starting with a digit, and not a reserved name")
        document = self._document() if self.path.exists() else None
        records = (document or {}).get("records", {})
        if ref not in records:
            raise KeyError(ref)
        if env and any(r.get("env") == env and name != ref for name, r in records.items()):
            raise ValueError(f"Another secret already uses {env}")
        if env:
            records[ref]["env"] = env
        else:
            records[ref].pop("env", None)
        self._save(document)

    def sandbox_values(self, refs=None) -> dict[str, str]:
        """{VARIABLE: value} for secrets exposed to commands, optionally limited to refs. In-process only."""
        if not self.path.exists():
            return {}
        document = self._document()
        key = self._key(document)
        out = {}
        for name, record in document["records"].items():
            if record.get("env") and (refs is None or name in refs):
                try:
                    out[record["env"]] = _decrypt_record(key, record).decode("utf-8")
                except UnicodeDecodeError:
                    continue
        return out

    def delete(self, ref: str):
        self._validate_ref(ref)
        removed = session_credentials.remove(self.path, ref)
        document = self._document() if self.path.exists() else None
        if document is None or ref not in document["records"]:
            if removed:
                return
            raise KeyError(ref)
        del document["records"][ref]
        self._save(document)

    def list(self):
        transient = [dict(ref=ref, set_at=None, env="", previous_set_at=None, session_only=True)
                     for ref in session_credentials.entries(self.path)]
        if not self.path.exists():
            return transient
        document = self._document()
        self._key(document)  # availability check only; values stay sealed
        return [dict(ref=r["ref"], set_at=r["set_at"], env=r.get("env") or "", previous_set_at=(r.get("previous") or {}).get("set_at"))
                for r in sorted(document["records"].values(), key=lambda r: r["ref"])
                if r["ref"] not in {item["ref"] for item in transient}] + transient

    def set_session(self, ref: str, plaintext: bytes):
        """Replace any saved credential with a process-only credential."""
        self._validate_ref(ref)
        if not isinstance(plaintext, bytes) or not plaintext or len(plaintext) > MAX_VALUE:
            raise ValueError("Secret value must be non-empty bytes, at most 64KB")
        try:
            self.delete(ref)
        except KeyError:
            pass
        session_credentials.put(self.path, ref, plaintext)

    def reveal(self, ref: str) -> bytes:
        """Decrypt one value for an in-process provider call. Never route this."""
        self._validate_ref(ref)
        transient = session_credentials.entries(self.path)
        if ref in transient:
            return transient[ref]
        if not self.path.exists():
            raise KeyError(ref)
        document = self._document()
        record = document["records"].get(ref)
        if record is None:
            raise KeyError(ref)
        return _decrypt_record(self._key(document), record)

    def upgrade(self) -> int:
        """Re-seal every v1 record (and kept previous value) in the v2 format. Each value is
        authenticated before it is re-sealed and the whole store is replaced atomically, so a
        failure leaves the file exactly as it was. Returns how many values were re-sealed."""
        if not self.path.exists():
            return 0
        document = self._document()
        stale = [(name, part) for name, record in document["records"].items()
                 for part in ("current", "previous")
                 if (record if part == "current" else record.get("previous")) and
                 _record_alg(record if part == "current" else record["previous"]) != ALG]
        if not stale:
            return 0
        key = self._key(document)
        for name, part in stale:
            record = document["records"][name]
            sealed = record if part == "current" else record["previous"]
            value = _decrypt_record(key, sealed)
            header, cipher, tag = _encrypt_record(key, sealed["ref"], int(sealed["counter"]), value)
            sealed.update(header=header.hex(), data=cipher.hex(), tag=tag.hex())
        document["version"] = VERSION
        self._save(document)
        return len(stale)

    def verify_all(self) -> int:
        """At-most-once integrity sweep; returns sealed record count."""
        if not self.path.exists():
            return 0
        document = self._document()
        key = self._key(document)
        for record in list(document["records"].values()):
            _decrypt_record(key, record)  # plaintext ignored; auth errors only
        return len(document["records"])
