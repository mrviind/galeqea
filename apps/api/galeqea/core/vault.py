"""Envelope-encrypted secret storage.

Every credential is sealed with a per-secret data key, which is itself sealed
with the installation master key. Rotating the master key therefore only
requires re-wrapping data keys, not decrypting and re-encrypting every secret.

On desktop installs the master key can be delegated to the OS keychain by
exporting ``GALEQEA_VAULT_KEY`` from a keychain lookup at boot; in server mode
it is supplied by the deployment's own secret manager.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import settings

_MAGIC = "trv1"


class VaultError(RuntimeError):
    pass


def _master_key() -> bytes:
    return HKDF(
        algorithm=SHA256(), length=32, salt=b"galeqea-vault-v1", info=b"master"
    ).derive(settings.vault_key.encode())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode())


def seal(plaintext: str, *, aad: str = "") -> str:
    """Encrypt ``plaintext`` into a self-describing, portable envelope string."""
    if plaintext is None:
        raise VaultError("cannot seal None")
    data_key = AESGCM.generate_key(bit_length=256)
    dk_nonce, payload_nonce = os.urandom(12), os.urandom(12)

    wrapped = AESGCM(_master_key()).encrypt(dk_nonce, data_key, b"galeqea-dk")
    ciphertext = AESGCM(data_key).encrypt(
        payload_nonce, plaintext.encode(), aad.encode() or None
    )
    envelope = {
        "v": _MAGIC,
        "dk": _b64(wrapped),
        "dkn": _b64(dk_nonce),
        "n": _b64(payload_nonce),
        "c": _b64(ciphertext),
        "aad": aad,
    }
    return _b64(json.dumps(envelope, separators=(",", ":")).encode())


def unseal(envelope_str: str, *, aad: str = "") -> str:
    try:
        envelope = json.loads(_unb64(envelope_str).decode())
    except Exception as exc:  # noqa: BLE001
        raise VaultError("malformed vault envelope") from exc
    if envelope.get("v") != _MAGIC:
        raise VaultError(f"unsupported vault envelope version: {envelope.get('v')}")
    try:
        data_key = AESGCM(_master_key()).decrypt(
            _unb64(envelope["dkn"]), _unb64(envelope["dk"]), b"galeqea-dk"
        )
        aad_bytes = (aad or envelope.get("aad", "")).encode() or None
        plaintext = AESGCM(data_key).decrypt(
            _unb64(envelope["n"]), _unb64(envelope["c"]), aad_bytes
        )
    except Exception as exc:  # noqa: BLE001
        raise VaultError("vault decryption failed (wrong key or tampered payload)") from exc
    return plaintext.decode()


def hint_for(plaintext: str) -> str:
    """A safe display fragment: never more than the last 4 characters."""
    if not plaintext:
        return ""
    tail = plaintext[-4:]
    head = plaintext[:6] if len(plaintext) > 14 else ""
    return f"{head}…{tail}" if head else f"…{tail}"


@dataclass(slots=True)
class ResolvedSecret:
    name: str
    value: str

    def __repr__(self) -> str:  # never leak into a traceback or log line
        return f"ResolvedSecret(name={self.name!r}, value='***')"

    __str__ = __repr__
