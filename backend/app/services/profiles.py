from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.adapters.base import AdapterHealth, AdapterProfile
from backend.app.adapters.registry import AdapterRegistry, adapter_registry
from backend.app.models import ModelProfile
from backend.app.security import SecretStore, secret_store

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def secret_reference(
    api_key: str | None,
    api_key_env: str | None,
    store: SecretStore = secret_store,
) -> str | None:
    if api_key:
        if not store.available():
            raise RuntimeError("系统钥匙串不可用；只能填写环境变量名，绝不回退到明文存储")
        return store.put(api_key)
    if api_key_env:
        if not _ENV_NAME.fullmatch(api_key_env):
            raise ValueError("环境变量名必须为大写字母、数字和下划线")
        return f"env:{api_key_env}"
    return None


def profile_snapshot(profile: ModelProfile) -> AdapterProfile:
    return AdapterProfile(
        id=profile.id,
        name=profile.name,
        adapter_kind=profile.adapter_kind,
        model_id=profile.model_id,
        base_url=profile.base_url,
        response_mode=profile.response_mode,
        api_key_ref=profile.api_key_ref,
        parameters=profile.parameters_json,
    )


async def check_profile(
    profile: ModelProfile,
    registry: AdapterRegistry = adapter_registry,
) -> AdapterHealth:
    profile.health_status = "checking"
    try:
        health = await registry.get(profile.adapter_kind).check(profile_snapshot(profile))
    except Exception as exc:
        health = AdapterHealth(status="error", message=str(exc))
    now = datetime.now(UTC)
    profile.health_status = health.status
    profile.health_details_json = {
        **health.details,
        "message": health.message,
        "version": health.version,
        "resolved_model_id": health.resolved_model_id,
    }
    profile.last_checked_at = now
    profile.health_expires_at = now + timedelta(minutes=10)
    return health


def health_is_current(profile: ModelProfile, now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    expires = profile.health_expires_at
    if expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return profile.health_status == "healthy" and expires > current


def profile_public(profile: ModelProfile) -> dict[str, Any]:
    reference = profile.api_key_ref or ""
    backend = (
        "keyring"
        if reference.startswith("keyring:")
        else "environment"
        if reference.startswith("env:")
        else "none"
    )
    return {
        "id": profile.id,
        "name": profile.name,
        "adapter_kind": profile.adapter_kind,
        "model_id": profile.model_id,
        "base_url": profile.base_url,
        "response_mode": profile.response_mode,
        "parameters": profile.parameters_json,
        "pricing": profile.pricing_json,
        "enabled": profile.enabled,
        "has_secret": bool(profile.api_key_ref),
        "secret_backend": backend,
        "health_status": profile.health_status,
        "health_details": profile.health_details_json,
        "last_checked_at": profile.last_checked_at,
        "health_expires_at": profile.health_expires_at,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
