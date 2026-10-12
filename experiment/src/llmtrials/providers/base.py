"""Common interface for all providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..config import ModelSpec, Prompt


@dataclass
class Completion:
    """What a provider returns for one trial."""

    text: str  # the final answer, exactly as returned
    reasoning: str | None = None  # visible reasoning/thinking, when the provider exposes it
    model: str | None = None  # model id reported back by the provider
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None
    # Tool calls the harness attempted. Should always be 0; >0 means the answer was not
    # a pure single-turn reply and the trial should be flagged at analysis time.
    tool_calls: int = 0
    # Route details: auth, versions, routing... Subscription routes also set
    # meta["quota"] = {window: {"used": 0..1, "resets_at": unix_time}} after each trial.
    meta: dict[str, Any] = field(default_factory=dict)
    raw: Any = None  # raw provider payload, kept for auditing


class ProviderError(Exception):
    """A failed call.

    retryable: transient (network, 5xx, 429) and worth retrying with backoff.
    fatal: configuration/auth problem; every further call to this model will fail too.
    usage_limit: a subscription's usage limit was reached. The trial is not a failure: the
        runner pauses the model until ``resets_at`` (unix time, when known) and tries again.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        fatal: bool = False,
        usage_limit: bool = False,
        resets_at: float | None = None,
        raw: Any = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal
        self.usage_limit = usage_limit
        self.resets_at = resets_at
        self.raw = raw


class Provider(ABC):
    name: ClassVar[str]
    # params keys the provider controls itself (to keep the harness minimal).
    reserved_params: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, spec: ModelSpec):
        # Identity strings (from the account email) to scrub from saved records; a provider
        # that knows the account email (e.g. Claude) fills this in setup(). See redact.py.
        self.scrub_tokens: list[str] = []
        clash = self.reserved_params & spec.params.keys()
        if clash:
            raise ValueError(f"model {spec.id}: params {sorted(clash)} are controlled by llmtrials")
        self.spec = spec

    async def setup(self) -> dict[str, Any]:
        """Check the environment before a run; return facts worth recording in the manifest."""
        return {}

    @abstractmethod
    async def complete(self, prompt: Prompt, *, timeout_s: float) -> Completion: ...

    async def close(self) -> None:
        return None
