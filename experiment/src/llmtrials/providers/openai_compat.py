"""Plain HTTP chat-completions APIs: OpenRouter and any OpenAI-compatible endpoint.

No agent harness is involved: the request is exactly a system message (optional) plus
one user message, and the full JSON response is kept for auditing.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..config import Prompt
from .base import Completion, Provider, ProviderError

RETRYABLE_STATUS = {408, 409, 425, 429}


class OpenAICompatibleProvider(Provider):
    """POST {base_url}/chat/completions.

    params: merged as-is into the request body (temperature, max_tokens, seed, reasoning,
    provider routing, ...). Unknown keys are passed through, so check the provider docs.
    """

    name = "openai_compatible"
    reserved_params = frozenset({"model", "messages", "stream"})
    default_base_url: str | None = None
    default_api_key_env: str | None = None

    def __init__(self, spec):
        super().__init__(spec)
        self.base_url = (spec.base_url or self.default_base_url or "").rstrip("/")
        self.api_key_env = spec.api_key_env or self.default_api_key_env
        self._client: httpx.AsyncClient | None = None

    def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key_env:
                key = os.environ.get(self.api_key_env)
                if not key:
                    raise ProviderError(f"environment variable {self.api_key_env} is not set", fatal=True)
                headers["Authorization"] = f"Bearer {key}"
            self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers)
        return self._client

    def build_body(self, prompt: Prompt) -> dict[str, Any]:
        messages = []
        if prompt.system:
            messages.append({"role": "system", "content": prompt.system})
        messages.append({"role": "user", "content": prompt.user})
        return {**self.spec.params, "model": self.spec.model, "messages": messages}

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> Completion:
        client = self._client_or_new()
        try:
            response = await client.post("/chat/completions", json=self.build_body(prompt), timeout=timeout_s)
        except httpx.TransportError as exc:  # includes timeouts
            raise ProviderError(f"{type(exc).__name__}: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            status = response.status_code
            raise ProviderError(
                f"HTTP {status}: {response.text[:500]}",
                retryable=status in RETRYABLE_STATUS or status >= 500,
                fatal=status in (401, 402, 403),
                raw=_json_or_text(response),
            )
        data = _json_or_text(response)
        if not isinstance(data, dict):
            raise ProviderError("response is not JSON", retryable=True, raw=data)
        if data.get("error"):
            raise ProviderError(f"provider error: {data['error']}", retryable=True, raw=data)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("response has no choices", retryable=True, raw=data)
        choice = choices[0]
        if choice.get("error"):
            raise ProviderError(f"choice error: {choice['error']}", retryable=True, raw=data)

        message = choice.get("message") or {}
        usage = data.get("usage")
        return Completion(
            text=message.get("content") or "",
            reasoning=message.get("reasoning") or message.get("reasoning_content"),
            model=data.get("model"),
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            cost_usd=(usage or {}).get("cost"),
            tool_calls=len(message.get("tool_calls") or []),
            meta={"response_id": data.get("id"), "upstream_provider": data.get("provider")},
            raw=data,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter (https://openrouter.ai): many model families behind one API key.

    Useful params (see https://openrouter.ai/docs):
      max_tokens, temperature, seed, reasoning: {effort: low|medium|high}
      provider: {order: [...], allow_fallbacks: false, require_parameters: true}
        Pin the upstream provider. Otherwise the same model id may be served by different
        hosts (and quantisations) from one call to the next.
    """

    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"
    default_api_key_env = "OPENROUTER_API_KEY"

    async def setup(self) -> dict[str, Any]:
        # The public catalogue gives the dated canonical slug, i.e. the exact model version.
        info: dict[str, Any] = {}
        try:
            response = await self._client_or_new().get("/models", timeout=30)
            response.raise_for_status()
            catalogue = response.json().get("data", [])
        except (httpx.HTTPError, ValueError) as exc:
            return {"catalogue_error": str(exc)}
        entry = next((m for m in catalogue if m.get("id") == self.spec.model), None)
        if entry is None:
            raise ProviderError(f"model {self.spec.model!r} not found in the OpenRouter catalogue", fatal=True)
        for key in ("canonical_slug", "knowledge_cutoff", "expiration_date", "pricing", "context_length"):
            info[key] = entry.get(key)
        supported = set(entry.get("supported_parameters") or [])
        ignored = sorted(k for k in self.spec.params if k in _SAMPLING_PARAMS and k not in supported)
        if ignored:
            info["warning"] = f"params not supported by this model (OpenRouter drops them): {ignored}"
        return info


# Parameters OpenRouter silently drops when the model does not support them.
_SAMPLING_PARAMS = {
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "frequency_penalty",
    "presence_penalty",
    "repetition_penalty",
    "min_p",
    "top_a",
    "reasoning",
    "max_tokens",
    "stop",
}


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
