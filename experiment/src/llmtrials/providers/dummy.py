"""Offline provider for tests and dry runs. Never calls a network service."""

from __future__ import annotations

import json
import time

from ..config import Prompt
from .base import Completion, Provider, ProviderError


class DummyProvider(Provider):
    """Returns a deterministic fake answer.

    params:
      text: fixed reply (default: a small JSON object derived from the prompt hash)
      fail_if_contains: raise a non-retryable error when the user prompt contains this text
      usage_limit_on_call: simulate a subscription usage limit on the Nth call (once)
      quota_used: report this fraction of a simulated five-hour usage window as used
      resets_in_s: seconds until the simulated window resets (default 0.01)
    """

    name = "dummy"

    def __init__(self, spec):
        super().__init__(spec)
        self.calls = 0

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> Completion:
        self.calls += 1
        params = self.spec.params
        resets_at = time.time() + params.get("resets_in_s", 0.01)
        if self.calls == params.get("usage_limit_on_call"):
            raise ProviderError("usage limit: simulated", usage_limit=True, resets_at=resets_at)
        trigger = params.get("fail_if_contains")
        if trigger and trigger in prompt.user:
            raise ProviderError(f"dummy failure: prompt contains {trigger!r}")
        text = params.get("text") or json.dumps(
            {"next_action": f"dummy action {prompt.sha256[:8]}", "rationale": "offline test"}
        )
        meta = {}
        if "quota_used" in params:
            meta["quota"] = {"five_hour": {"used": params["quota_used"], "resets_at": resets_at}}
        return Completion(
            text=text,
            model=self.spec.model,
            finish_reason="stop",
            usage={"input_tokens": len(prompt.user) // 4, "output_tokens": len(text) // 4},
            meta=meta,
        )
