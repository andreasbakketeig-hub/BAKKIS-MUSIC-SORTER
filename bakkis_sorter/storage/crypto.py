"""macOS Keychain & CommonCrypto AES-256-CBC authenticated encryption."""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from ..config import config_dir

KEYCHAIN_SERVICE = "com.bakkis.music-sorter"
KEYCHAIN_ACCOUNT = "db-master-key"
DB_FILENAME = "bakkis.db"
CIPHER_PREFIX = "enc:v1:"

# CommonCrypto constants from <CommonCrypto/CommonCryptor.h>
_kCCEncrypt = 0
_kCCDecrypt = 1
_kCCAlgorithmAES = 0
_kCCOptionPKCS7Padding = 0x0001
_kCCKeySizeAES256 = 32
_kCCBlockSizeAES128 = 16
_kCCSuccess = 0


def _load_common_crypto() -> Any:
    lib_path = ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib"
    lib = ctypes.CDLL(lib_path)
    lib.CCCrypt.argtypes = [
        ctypes.c_uint32,  # op (kCCEncrypt / kCCDecrypt)
        ctypes.c_uint32,  # alg (kCCAlgorithmAES)
        ctypes.c_uint32,  # options (kCCOptionPKCS7Padding)
        ctypes.c_void_p,  # key
        ctypes.c_size_t,  # keyLength
        ctypes.c_void_p,  # iv
        ctypes.c_void_p,  # dataIn
        ctypes.c_size_t,  # dataInLength
        ctypes.c_void_p,  # dataOut
        ctypes.c_size_t,  # dataOutAvailable
        ctypes.POINTER(ctypes.c_size_t),  # dataOutMoved
    ]
    lib.CCCrypt.restype = ctypes.c_int32
    return lib


_CC_LIB: Optional[Any] = None
_CC_LOCK = threading.Lock()


def _get_cc_lib() -> Any:
    global _CC_LIB
    if _CC_LIB is None:
        with _CC_LOCK:
            if _CC_LIB is None:
                _CC_LIB = _load_common_crypto()
    return _CC_LIB


class CryptoError(RuntimeError):
    """Raised when ciphertext authentication or decryption fails."""


class Aes256Cipher:
    """Authenticated AES-256-CBC + HMAC-SHA256 (Encrypt-then-MAC) using macOS CommonCrypto."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) < 32:
            raise ValueError("Master key must be at least 32 bytes")
        self._enc_key = hmac.new(master_key, b"bakkis-aes256-enc-v1", hashlib.sha256).digest()
        self._mac_key = hmac.new(master_key, b"bakkis-hmac-sha256-v1", hashlib.sha256).digest()

    def encrypt_bytes(self, plaintext: bytes) -> str:
        iv = secrets.token_bytes(_kCCBlockSizeAES128)
        lib = _get_cc_lib()
        out_len = len(plaintext) + _kCCBlockSizeAES128
        out_buf = ctypes.create_string_buffer(out_len)
        moved = ctypes.c_size_t(0)
        status = lib.CCCrypt(
            _kCCEncrypt,
            _kCCAlgorithmAES,
            _kCCOptionPKCS7Padding,
            self._enc_key,
            _kCCKeySizeAES256,
            iv,
            plaintext,
            len(plaintext),
            out_buf,
            out_len,
            ctypes.byref(moved),
        )
        if status != _kCCSuccess:
            raise CryptoError(f"CommonCrypto encrypt failed with status {status}")
        ct = out_buf.raw[: moved.value]
        tag = hmac.new(self._mac_key, iv + ct, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(iv + tag + ct).decode("ascii")
        return f"{CIPHER_PREFIX}{token}"

    def decrypt_bytes(self, token: str) -> bytes:
        if not isinstance(token, str) or not token.startswith(CIPHER_PREFIX):
            raise CryptoError("Invalid ciphertext prefix")
        b64_part = token[len(CIPHER_PREFIX) :]
        try:
            raw = base64.urlsafe_b64decode(b64_part.encode("ascii"))
        except Exception as exc:
            raise CryptoError("Malformed base64 ciphertext") from exc
        if len(raw) < _kCCBlockSizeAES128 + 32 + _kCCBlockSizeAES128:
            raise CryptoError("Ciphertext too short")
        iv = raw[:_kCCBlockSizeAES128]
        tag = raw[_kCCBlockSizeAES128 : _kCCBlockSizeAES128 + 32]
        ct = raw[_kCCBlockSizeAES128 + 32 :]
        expected_tag = hmac.new(self._mac_key, iv + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise CryptoError("Ciphertext HMAC verification failed")

        lib = _get_cc_lib()
        out_len = len(ct) + _kCCBlockSizeAES128
        out_buf = ctypes.create_string_buffer(out_len)
        moved = ctypes.c_size_t(0)
        status = lib.CCCrypt(
            _kCCDecrypt,
            _kCCAlgorithmAES,
            _kCCOptionPKCS7Padding,
            self._enc_key,
            _kCCKeySizeAES256,
            iv,
            ct,
            len(ct),
            out_buf,
            out_len,
            ctypes.byref(moved),
        )
        if status != _kCCSuccess:
            raise CryptoError(f"CommonCrypto decrypt failed with status {status}")
        return out_buf.raw[: moved.value]

    def encrypt_json(self, obj: Any) -> str:
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.encrypt_bytes(raw)

    def decrypt_json(self, token: str) -> Any:
        raw = self.decrypt_bytes(token)
        return json.loads(raw.decode("utf-8"))


_KEY_LOCK = threading.Lock()
_CACHED_MASTER_KEY: Optional[bytes] = None


def get_or_create_master_key(*, db_dir: Optional[Path] = None) -> bytes:
    """Load the 256-bit master key from macOS Keychain (or create and store one on first run)."""
    global _CACHED_MASTER_KEY
    env_key = os.environ.get("BAKKIS_MASTER_KEY", "").strip()
    if env_key:
        return hashlib.sha256(env_key.encode("utf-8")).digest()

    if os.environ.get("BAKKIS_CONFIG_DIR", "").strip() or db_dir is not None:
        target_dir = Path(db_dir) if db_dir is not None else config_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        key_file = target_dir / ".master_key"
        if key_file.is_file():
            try:
                raw = base64.urlsafe_b64decode(key_file.read_text(encoding="ascii").strip())
                if len(raw) == 32:
                    return raw
            except Exception:
                pass
        new_key = secrets.token_bytes(32)
        try:
            key_file.write_text(base64.urlsafe_b64encode(new_key).decode("ascii"), encoding="ascii")
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return new_key

    with _KEY_LOCK:
        if _CACHED_MASTER_KEY is not None:
            return _CACHED_MASTER_KEY

        try:
            res = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                decoded = base64.urlsafe_b64decode(res.stdout.strip().encode("ascii"))
                if len(decoded) == 32:
                    _CACHED_MASTER_KEY = decoded
                    return decoded
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

        new_key = secrets.token_bytes(32)
        b64_key = base64.urlsafe_b64encode(new_key).decode("ascii")
        stored_in_keychain = False
        try:
            res = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-s",
                    KEYCHAIN_SERVICE,
                    "-a",
                    KEYCHAIN_ACCOUNT,
                    "-w",
                    b64_key,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            stored_in_keychain = res.returncode == 0
        except (subprocess.SubprocessError, OSError):
            stored_in_keychain = False

        if not stored_in_keychain:
            fallback = config_dir() / ".master_key"
            if fallback.is_file():
                try:
                    raw = base64.urlsafe_b64decode(fallback.read_text(encoding="ascii").strip())
                    if len(raw) == 32:
                        _CACHED_MASTER_KEY = raw
                        return raw
                except Exception:
                    pass
            try:
                fallback.write_text(b64_key, encoding="ascii")
                os.chmod(fallback, 0o600)
            except OSError:
                pass

        _CACHED_MASTER_KEY = new_key
        return new_key
