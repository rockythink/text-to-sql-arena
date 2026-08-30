from __future__ import annotations

from backend.app.adapters.base import ModelAdapter
from backend.app.adapters.claude_cli import ClaudeCliAdapter
from backend.app.adapters.codex_cli import CodexCliAdapter
from backend.app.adapters.gemini_cli import GeminiCliAdapter
from backend.app.adapters.openai_compatible import OpenAICompatibleAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ModelAdapter] = {
            "openai_compatible": OpenAICompatibleAdapter(),
            "codex_cli": CodexCliAdapter(),
            "claude_cli": ClaudeCliAdapter(),
            "gemini_cli": GeminiCliAdapter(),
        }

    def get(self, adapter_kind: str) -> ModelAdapter:
        try:
            return self._adapters[adapter_kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported adapter kind: {adapter_kind}") from exc


adapter_registry = AdapterRegistry()
