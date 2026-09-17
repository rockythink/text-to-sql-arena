from __future__ import annotations

from backend.app.adapters.base import ModelAdapter
from backend.app.adapters.pi import PiAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ModelAdapter] = {"pi": PiAdapter()}

    def get(self, adapter_kind: str) -> ModelAdapter:
        try:
            return self._adapters[adapter_kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported adapter kind: {adapter_kind}") from exc


adapter_registry = AdapterRegistry()
