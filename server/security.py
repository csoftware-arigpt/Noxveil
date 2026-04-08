import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from collections import defaultdict, deque
from typing import Callable, Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, status

from server.database import DATA_DIR


VAULT_FILE = os.path.join(DATA_DIR, "secrets.vault")
VAULT_MASTER_KEY_FILE = os.path.join(DATA_DIR, "vault_master.key")
ENCRYPTED_PREFIX = "enc::"
TOTP_ISSUER = os.getenv("TOTP_ISSUER", "Noxveil")


def _load_or_create_master_key() -> bytes:
    env_key = os.getenv("VAULT_MASTER_KEY")
    if env_key:
        return env_key.encode("utf-8")

    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(VAULT_MASTER_KEY_FILE):
        with open(VAULT_MASTER_KEY_FILE, "rb") as key_file:
            value = key_file.read().strip()
            if value:
                return value

    value = Fernet.generate_key()
    with open(VAULT_MASTER_KEY_FILE, "wb") as key_file:
        key_file.write(value)
    try:
        os.chmod(VAULT_MASTER_KEY_FILE, 0o600)
    except OSError:
        pass
    return value


class LocalVault:
    def __init__(self):
        self._fernet = Fernet(_load_or_create_master_key())

    def _read_all(self) -> dict[str, str]:
        if not os.path.exists(VAULT_FILE):
            return {}

        with open(VAULT_FILE, "rb") as vault_file:
            encrypted = vault_file.read().strip()
            if not encrypted:
                return {}

        try:
            raw = self._fernet.decrypt(encrypted)
        except InvalidToken as exc:
            raise RuntimeError("Unable to decrypt local vault") from exc
        return json.loads(raw.decode("utf-8"))

    def _write_all(self, data: dict[str, str]) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        raw = json.dumps(data, sort_keys=True).encode("utf-8")
        encrypted = self._fernet.encrypt(raw)
        with open(VAULT_FILE, "wb") as vault_file:
            vault_file.write(encrypted)
        try:
            os.chmod(VAULT_FILE, 0o600)
        except OSError:
            pass

    def get(self, key: str) -> Optional[str]:
        return self._read_all().get(key)

    def set(self, key: str, value: str) -> str:
        data = self._read_all()
        data[key] = value
        self._write_all(data)
        return value

    def get_or_create(self, key: str, generator: Callable[[], str]) -> str:
        existing = self.get(key)
        if existing:
            return existing
        value = generator()
        self.set(key, value)
        return value


_vault = LocalVault()
_field_fernet = Fernet(
    _vault.get_or_create("field_encryption_key", lambda: Fernet.generate_key().decode("utf-8")).encode("utf-8")
)


def get_secret_from_env_or_vault(env_name: str, vault_key: str, generator: Callable[[], str]) -> str:
    env_value = os.getenv(env_name)
    if env_value:
        return env_value
    return _vault.get_or_create(vault_key, generator)


def encrypt_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value.startswith(ENCRYPTED_PREFIX):
        return value
    token = _field_fernet.encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    token = value[len(ENCRYPTED_PREFIX):]
    try:
        return _field_fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return value


def sanitize_text(value: Optional[str], max_length: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    sanitized = str(value).replace("\x00", "").strip()
    if max_length is not None:
        sanitized = sanitized[:max_length]
    return sanitized


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def build_totp_uri(secret: str, username: str, issuer: str = TOTP_ISSUER) -> str:
    account_name = urllib.parse.quote(username)
    issuer_name = urllib.parse.quote(issuer)
    return f"otpauth://totp/{issuer_name}:{account_name}?secret={secret}&issuer={issuer_name}&digits=6&period=30"


def _normalize_base32(secret: str) -> bytes:
    padded = secret.upper().strip()
    padded += "=" * ((8 - len(padded) % 8) % 8)
    return base64.b32decode(padded, casefold=True)


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    key = _normalize_base32(secret)
    counter_bytes = counter.to_bytes(8, "big")
    digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, period: int = 30, skew: int = 1) -> bool:
    normalized_code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(normalized_code) != 6:
        return False

    current_counter = int(time.time() // period)
    for offset in range(-skew, skew + 1):
        if hmac.compare_digest(_hotp(secret, current_counter + offset), normalized_code):
            return True
    return False


class SlidingWindowRateLimiter:
    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        window_start = now - window_seconds
        bucket = self._events[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


rate_limiter = SlidingWindowRateLimiter()

_TRUSTED_PROXIES: set[str] = {
    ip.strip()
    for ip in os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if ip.strip()
}


def get_client_ip(request: Request) -> str:
    client_host = request.client.host if request.client else None
    if client_host in _TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if client_host:
        return client_host
    return "unknown"


def enforce_rate_limit(request: Request, scope: str, limit: int, window_seconds: int) -> None:
    key = f"{scope}:{get_client_ip(request)}"
    if rate_limiter.allow(key, limit, window_seconds):
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded for {scope}. Try again later.",
    )
