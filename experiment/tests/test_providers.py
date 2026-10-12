"""Provider adapters without network or subscriptions (HTTP is mocked)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from llmtrials.config import ModelSpec, Prompt
from llmtrials.providers import ProviderError, create_provider
from llmtrials.providers.claude_sdk import _has_text_block
from llmtrials.providers.codex_sdk import MINIMAL_CONFIG, _flatten, _toml

PROMPT = Prompt(system="You are a test.", user="What next?")


def openrouter(handler, **params) -> object:
    spec = ModelSpec(id="m@or", provider="openrouter", model="vendor/model", params=params)
    provider = create_provider(spec)
    provider._client = httpx.AsyncClient(base_url=provider.base_url, transport=httpx.MockTransport(handler))
    return provider


def test_openrouter_sends_exact_body_and_parses_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": "vendor/model-20260101",
                "provider": "SomeHost",
                "choices": [{"finish_reason": "stop", "message": {"content": "do X", "reasoning": "because"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.0001},
            },
        )

    provider = openrouter(handler, max_tokens=100, provider={"only": ["somehost"]})
    completion = asyncio.run(provider.complete(PROMPT, timeout_s=5))
    assert seen == {
        "max_tokens": 100,
        "provider": {"only": ["somehost"]},
        "model": "vendor/model",
        "messages": [{"role": "system", "content": "You are a test."}, {"role": "user", "content": "What next?"}],
    }
    assert (completion.text, completion.reasoning, completion.model) == ("do X", "because", "vendor/model-20260101")
    assert completion.cost_usd == 0.0001 and completion.meta["upstream_provider"] == "SomeHost"


@pytest.mark.parametrize(
    ("status", "retryable", "fatal"), [(429, True, False), (503, True, False), (401, False, True), (400, False, False)]
)
def test_openrouter_error_classes(status, retryable, fatal):
    provider = openrouter(lambda request: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ProviderError) as info:
        asyncio.run(provider.complete(PROMPT, timeout_s=5))
    assert (info.value.retryable, info.value.fatal) == (retryable, fatal)


def test_openrouter_setup_records_version_and_flags_dropped_params():
    catalogue = {
        "data": [
            {"id": "vendor/model", "canonical_slug": "vendor/model-20260101", "supported_parameters": ["max_tokens"]}
        ]
    }
    provider = openrouter(lambda request: httpx.Response(200, json=catalogue), max_tokens=10, temperature=0)
    info = asyncio.run(provider.setup())
    assert info["canonical_slug"] == "vendor/model-20260101"
    assert "temperature" in info["warning"]


def test_codex_config_rendering():
    assert _toml(False) == "false" and _toml(0) == "0" and _toml("disabled") == '"disabled"'
    assert _flatten({"a": {"b": 1, "c": {"d": True}}}) == {"a.b": 1, "a.c.d": True}
    spec = ModelSpec(id="c", provider="codex_sdk", model="gpt-x", params={"effort": "high", "verbosity": "low"})
    overrides = create_provider(spec).launch_overrides()
    assert "include_environment_context=false" in overrides and 'model_verbosity="low"' in overrides
    assert len(overrides) == len(MINIMAL_CONFIG) + 1


def test_codex_rejects_unknown_params_and_missing_system_prompt():
    with pytest.raises(ValueError, match="unknown codex_sdk params"):
        create_provider(ModelSpec(id="c", provider="codex_sdk", model="gpt-x", params={"temperature": 0}))
    provider = create_provider(ModelSpec(id="c", provider="codex_sdk", model="gpt-x"))
    with pytest.raises(ProviderError, match="needs a system prompt"):
        asyncio.run(provider.complete(Prompt(system=None, user="hi"), timeout_s=5))


def test_claude_audit_helpers():
    home = str(Path.home())
    from llmtrials.redact import scrub
    assert scrub({"t": [f"mail a.b@example.org in {home}/x"]}) == {"t": ["mail <redacted-email> in ~/x"]}
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<system-reminder>x</system-reminder>"},
                {"type": "text", "text": "What next?"},
            ],
        }
    ]
    assert _has_text_block(messages, "What next?")
    assert not _has_text_block([{"role": "user", "content": "<session>\nWhat next?\n</session>"}], "What next?")


def test_claude_quota_and_context_fingerprint():
    from types import SimpleNamespace

    from llmtrials.providers.claude_sdk import _harness_context, _quota

    info = SimpleNamespace(
        raw={"unifiedWindows": {"five_hour": {"utilization": 0.26, "resetsAt": 1}, "seven_day": {"utilization": 0.3}}},
        rate_limit_type="five_hour",
        utilization=None,
        resets_at=1,
    )
    assert _quota(info) == {"five_hour": {"used": 0.26, "resets_at": 1}, "seven_day": {"used": 0.3, "resets_at": None}}
    assert _quota(None) is None

    def request(date: str) -> dict:
        return {
            "system": [{"text": "x-anthropic-billing-header: cc_prompt_id=123"}, {"text": "identity"}, {"text": "SYS"}],
            "messages": [
                {"role": "user", "content": [{"text": f"<system-reminder>{date}</system-reminder>"}, {"text": "USER"}]}
            ],
        }

    prompt = Prompt(system="SYS", user="USER")
    first = _harness_context(request("2026-10-06"), prompt)
    assert first["blocks"] == 2  # identity line + reminder; billing header and our prompt excluded
    assert first == _harness_context(request("2026-10-06"), prompt)
    assert first["sha256"] != _harness_context(request("2026-10-07"), prompt)["sha256"]


def test_redact_scrubs_email_and_derived_identity():
    from llmtrials.redact import identity_tokens, scrub, scrub_text

    toks = identity_tokens("jane.doe.42@corp.com")
    assert "jane.doe.42" in toks and "jane" in toks and "jane-doe" in toks
    # a session name the model built from the injected email is scrubbed
    out = scrub_text("aws sts assume-role --role-session-name jane-doe-admin", toks)
    assert "jane" not in out and "doe" not in out and "<redacted-id>" in out
    # literal emails always go, for any provider, even with no tokens
    assert scrub_text("contact bob@evil.net now") == "contact <redacted-email> now"
    # recursive over nested records
    r = scrub({"text": "user jane.doe.42 did X", "n": [{"s": "jane-doe-session"}]}, toks)
    assert "jane" not in r["text"] and "jane" not in r["n"][0]["s"] and "doe" not in r["n"][0]["s"]
    # short names (<4) aren't substring-scrubbed, but the email/local still are
    assert identity_tokens("al@x.com") == ["al@x.com"]
