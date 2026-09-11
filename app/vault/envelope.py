"""The vault. The ONLY place in this codebase where a plaintext token exists.

Rule 3 of the brief:

    "A token is encrypted on the way in. It is never written into an agent's
     configuration, never appears in a prompt, never lands in a log or a saved
     conversation. When a tool needs it: fetch it, use it, drop it.
     How this is tested: we search everything your system stored for the token
     we gave you. Finding it anywhere is a fail."

ENVELOPE ENCRYPTION, and why it is worth the extra twenty lines
---------------------------------------------------------------
Each connection gets its own random data key (DEK). The secret is encrypted
under the DEK; the DEK is then encrypted ("wrapped") under one master key from
the environment. Only wrapped DEKs are stored.

  * rotating the master key means re-wrapping N small DEKs, not re-encrypting
    every secret
  * the master key is never used on user data directly, so it is exposed to far
    less ciphertext
  * `key_version` records which master key wrapped each row, so rotation can be
    gradual rather than a big-bang migration

AES-GCM is authenticated, and we bind each ciphertext to WHERE IT LIVES using
additional authenticated data (AAD) of `tenant:server`. A row copied out of one
company's schema into another's will not decrypt - the AAD no longer matches.
That turns a copy-paste mistake into a loud failure instead of a quiet breach.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # GCM standard


class VaultError(Exception):
    """The secret could not be read. Never says why, beyond the caller's need."""


@dataclass(frozen=True)
class SealedSecret:
    """Exactly what goes in the database. No field here is readable."""

    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    dek_nonce: bytes
    key_version: int


def _master_key() -> bytes:
    """The one key that is not stored in the database.

    In production this comes from a secrets manager. Here it is an env var, and
    the dev default exists only so a fresh clone runs - anything real must set
    FORGE_MASTER_KEY.
    """
    raw = settings.forge_master_key
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise VaultError("FORGE_MASTER_KEY is not valid base64") from exc

    if len(key) != KEY_BYTES:
        raise VaultError(
            f"FORGE_MASTER_KEY must decode to {KEY_BYTES} bytes, got {len(key)}. "
            "Generate one with: python -c \"import os,base64;"
            "print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
        )
    return key


def _aad(tenant: str, server_name: str) -> bytes:
    """Binds a ciphertext to the company and server it belongs to."""
    return f"{tenant}:{server_name}".encode()


def seal(secret: str, *, tenant: str, server_name: str) -> SealedSecret:
    """Encrypt on the way in. The plaintext does not survive this function."""
    if not secret:
        raise VaultError("refusing to store an empty secret")

    aad = _aad(tenant, server_name)

    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(dek).encrypt(nonce, secret.encode(), aad)

    dek_nonce = os.urandom(NONCE_BYTES)
    wrapped_dek = AESGCM(_master_key()).encrypt(dek_nonce, dek, aad)

    del dek  # the unwrapped data key must not outlive this call either
    return SealedSecret(ciphertext, nonce, wrapped_dek, dek_nonce, key_version=1)


def open_(sealed: SealedSecret, *, tenant: str, server_name: str) -> str:
    """Decrypt for ONE use. The caller must drop the result immediately.

    Never call this to display a secret. There is no endpoint that returns one.
    """
    aad = _aad(tenant, server_name)
    try:
        dek = AESGCM(_master_key()).decrypt(sealed.dek_nonce, sealed.wrapped_dek, aad)
        plaintext = AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, aad)
    except InvalidTag as exc:
        # Wrong master key, or the row was moved between tenants/servers.
        raise VaultError("could not decrypt this connection") from exc
    finally:
        dek = None  # noqa: F841

    return plaintext.decode()


def generate_master_key() -> str:
    """For the README and for tests."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode()
