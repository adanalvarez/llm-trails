"""Provider registry. Each provider turns a Prompt into a Completion for one model spec."""

from __future__ import annotations

from ..config import ModelSpec
from .base import Completion, Provider, ProviderError

__all__ = ["Completion", "Provider", "ProviderError", "create_provider"]


def create_provider(spec: ModelSpec) -> Provider:
    # Imported lazily so one missing optional tool does not break the others.
    if spec.provider == "claude_agent_sdk":
        from .claude_sdk import ClaudeAgentSDKProvider as cls
    elif spec.provider == "codex_sdk":
        from .codex_sdk import CodexSDKProvider as cls
    elif spec.provider == "openrouter":
        from .openai_compat import OpenRouterProvider as cls
    elif spec.provider == "openai_compatible":
        from .openai_compat import OpenAICompatibleProvider as cls
    elif spec.provider == "dummy":
        from .dummy import DummyProvider as cls
    else:  # pragma: no cover - guarded by config validation
        raise ValueError(f"unknown provider {spec.provider!r}")
    return cls(spec)
