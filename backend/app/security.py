from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

import keyring
from keyring.errors import KeyringError, NoKeyringError

SERVICE_NAME = "llm-text-to-sql-benchmark"
_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;]+"),
]


def redact_secrets(value: Any) -> Any:
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        return redacted
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if str(key).lower() in {"authorization", "api_key", "token", "secret"}
            else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact_secrets(item) for item in value]
    return value


class SecretStore:
    def available(self) -> bool:
        try:
            backend = keyring.get_keyring()
            priority = getattr(backend, "priority", 0)
            return bool(priority and priority > 0)
        except (KeyringError, NoKeyringError, RuntimeError):
            return False

    def put(self, secret: str) -> str:
        if not self.available():
            raise RuntimeError("No usable system keyring backend")
        reference = f"keyring:{uuid4()}"
        keyring.set_password(SERVICE_NAME, reference, secret)
        return reference

    def get(self, reference: str | None) -> str | None:
        if not reference:
            return None
        if reference.startswith("env:"):
            return os.environ.get(reference.removeprefix("env:"))
        if not reference.startswith("keyring:"):
            raise ValueError("Unsupported secret reference")
        return keyring.get_password(SERVICE_NAME, reference)

    def delete(self, reference: str | None) -> None:
        if not reference or not reference.startswith("keyring:"):
            return
        try:
            keyring.delete_password(SERVICE_NAME, reference)
        except (KeyringError, NoKeyringError):
            pass


secret_store = SecretStore()
