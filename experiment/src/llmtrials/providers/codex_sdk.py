"""OpenAI models through a ChatGPT subscription, via the official Codex Python SDK.

The SDK (`openai-codex`) drives a bundled, version-pinned Codex CLI in app-server mode. Every
trial is a fresh ephemeral thread reduced to the closest thing to a plain model call:
  - the study's system prompt replaces Codex's own (``base_instructions``)
  - optional context blocks off: environment/date, permissions, collaboration, apps, skills,
    multi-agent; no AGENTS.md, no memories, no session history
  - nested tools removed (shell, apply_patch, web search, plan, images, MCP, plugins...)
  - read-only sandbox, approvals denied, an empty working directory
  - an isolated CODEX_HOME: your normal ~/.codex config, plugins and memories are never used

Residual harness: current Codex models always receive the code-mode ``exec``/``wait`` tools.
Any tool activity is counted in ``tool_calls`` (should be 0). See docs/METHODOLOGY.md.

One-time login (separate from your normal Codex login): ``llmtrials login-codex``

params:
  effort:     reasoning effort, e.g. low | medium | high | xhigh. Set it explicitly: when unset
              Codex uses a per-model default (gpt-6.1-sol: low).
  summary:    reasoning summary returned with the answer: none | concise | detailed | auto
  verbosity:  low | medium | high
  config:     extra Codex config overrides as {dotted.key: value}
  codex_home: login/profile folder (default .llmtrials/codex-home)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..config import Prompt
from .base import Completion, Provider, ProviderError

DEFAULT_CODEX_HOME = Path(".llmtrials") / "codex-home"

# Everything Codex would otherwise add to the request, switched off (validated against
# codex-cli 0.160.1 with `codex debug prompt-input`).
MINIMAL_CONFIG: dict[str, Any] = {
    "web_search": "disabled",
    "project_doc_max_bytes": 0,
    "include_permissions_instructions": False,
    "include_apps_instructions": False,
    "include_collaboration_mode_instructions": False,
    "include_environment_context": False,
    "check_for_update_on_startup": False,
    "forced_login_method": "chatgpt",
    "cli_auth_credentials_store": "file",
    "history.persistence": "none",
    "skills.include_instructions": False,
    "skills.bundled.enabled": False,
    "agents.enabled": False,
    "memories.use_memories": False,
    "memories.generate_memories": False,
    "tools.update_plan.enabled": False,
    "tools.experimental_request_user_input.enabled": False,
    **{
        f"features.{name}": False
        for name in (
            "memories",
            "apps",
            "plugins",
            "remote_plugin",
            "multi_agent",
            "shell_tool",
            "unified_exec",
            "view_image",
            "image_generation",
            "sleep_tool",
            "goals",
            "tool_suggest",
            "browser_use",
            "computer_use",
            "hooks",
            "skill_mcp_dependency_install",
        )
    },
}
# No local execution environment: removes shell, apply_patch and view_image entirely.
MINIMAL_ENV = {"CODEX_EXEC_SERVER_URL": "none"}
ANSWER_ITEMS = {"agentMessage", "reasoning", "userMessage"}
WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}


class CodexSDKProvider(Provider):
    name = "codex_sdk"
    reserved_params = frozenset(
        {
            "model",
            "base_instructions",
            "developer_instructions",
            "approval_mode",
            "sandbox",
            "ephemeral",
            "cwd",
        }
    )
    known_params = frozenset({"effort", "summary", "verbosity", "config", "codex_home"})

    def __init__(self, spec):
        super().__init__(spec)
        unknown = set(spec.params) - self.known_params
        if unknown:
            raise ValueError(f"model {spec.id}: unknown codex_sdk params {sorted(unknown)}")
        self.codex_home = Path(spec.params.get("codex_home", DEFAULT_CODEX_HOME)).expanduser().resolve()
        self.workdir = Path(tempfile.gettempdir()) / "llmtrials-codex-cwd"
        self._codex = None
        self._harness_context: dict[str, Any] | None = None

    def launch_overrides(self) -> tuple[str, ...]:
        config = {**MINIMAL_CONFIG, **_flatten(self.spec.params.get("config", {}))}
        if "verbosity" in self.spec.params:
            config["model_verbosity"] = self.spec.params["verbosity"]
        return tuple(f"{key}={_toml(value)}" for key, value in config.items())

    async def _client(self):
        if self._codex is None:
            from openai_codex import AsyncCodex, CodexConfig

            if not (self.codex_home / "auth.json").exists():
                raise ProviderError(f"no Codex login in {self.codex_home}: run `llmtrials login-codex`", fatal=True)
            self.workdir.mkdir(exist_ok=True)
            config = CodexConfig(
                config_overrides=self.launch_overrides(),
                env={**MINIMAL_ENV, "CODEX_HOME": str(self.codex_home)},
                cwd=str(self.workdir),
            )
            codex = AsyncCodex(config)
            await codex.__aenter__()
            self._codex = codex
        return self._codex

    async def setup(self) -> dict[str, Any]:
        from openai_codex import __version__ as sdk_version

        codex = await self._client()
        account = (await codex.account()).account
        account = getattr(account, "root", account)
        info: dict[str, Any] = {
            "sdk_version": sdk_version,
            "codex_home": str(self.codex_home).replace(str(Path.home()), "~"),
            "account_type": getattr(account, "type", None),
            "plan_type": getattr(getattr(account, "plan_type", None), "value", None),
            "launch_overrides": list(self.launch_overrides()),
        }
        warnings = []
        if info["account_type"] != "chatgpt":
            warnings.append(f"Codex is not using a ChatGPT login (account: {info['account_type']})")
        if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            warnings.append("CODEX_API_KEY/OPENAI_API_KEY is set; llmtrials forces ChatGPT login for codex_sdk")
        catalogue = {m.id: m for m in (await codex.models(include_hidden=True)).data}
        model = catalogue.get(self.spec.model)
        if model is None:
            raise ProviderError(
                f"model {self.spec.model!r} not available to this account; available: {sorted(catalogue)}",
                fatal=True,
            )
        effort = model.default_reasoning_effort
        info["default_reasoning_effort"] = str(getattr(effort, "value", effort))
        if "effort" not in self.spec.params:
            warnings.append(f"no effort set: Codex default for this model is {info['default_reasoning_effort']!r}")
        info["quota"] = await self.quota()
        info["render_check"] = await self._render_check()
        if info["render_check"].get("extra_items"):
            warnings.append("Codex adds context beyond the user message; see render_check in the manifest")
        if warnings:
            info["warnings"] = warnings
        return info

    async def _render_check(self) -> dict[str, Any]:
        """Render the model-visible input locally (no model call) with the same overrides.

        Shows every message Codex would add around the prompt. It does not show the base
        instructions (replaced by the study's system prompt) or the tool schemas.
        """
        from codex_cli_bin import bundled_codex_path

        sentinel = "LLMTRIALS_PROMPT_SENTINEL"
        args = [a for kv in (*self.launch_overrides(), f"model={_toml(self.spec.model)}") for a in ("-c", kv)]
        try:
            proc = await asyncio.create_subprocess_exec(
                str(bundled_codex_path()),
                "debug",
                "prompt-input",
                *args,
                sentinel,
                env={**os.environ, **MINIMAL_ENV, "CODEX_HOME": str(self.codex_home)},
                cwd=str(self.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), 60)
            items = json.loads(stdout)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        extra = [
            {"role": item.get("role"), "text": "".join(c.get("text", "") for c in item.get("content", []))[:2000]}
            for item in items
            if not (item.get("role") == "user" and [c.get("text") for c in item.get("content", [])] == [sentinel])
        ]
        digest = hashlib.sha256(json.dumps(extra, sort_keys=True).encode()).hexdigest()[:16]
        self._harness_context = {"sha256": digest, "blocks": len(extra), "source": "render_check"}
        return {"items": len(items), "extra_items": extra}

    async def quota(self) -> dict[str, Any] | None:
        """Account usage per window from Codex, e.g. {"five_hour": {"used": 0.12, "resets_at": ...}}."""
        from openai_codex.generated.v2_all import GetAccountRateLimitsResponse

        try:
            codex = await self._client()
            # No public wrapper in openai-codex 0.160.1; this is the SDK's typed JSON-RPC call.
            response = await codex._client.request(
                "account/rateLimits/read", None, response_model=GetAccountRateLimitsResponse
            )
        except Exception:  # quota is informative only; never fail a trial over it
            return None
        quota = {}
        for window in (response.rate_limits.primary, response.rate_limits.secondary):
            if window is not None:
                name = WINDOW_NAMES.get(window.window_duration_mins, f"{window.window_duration_mins}_min")
                quota[name] = {"used": window.used_percent / 100, "resets_at": window.resets_at}
        return quota or None

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> Completion:
        from openai_codex import ApprovalMode, CodexError, Sandbox, is_retryable_error

        if not prompt.system:
            # Without base_instructions Codex falls back to its ~20k-character coding-agent prompt.
            raise ProviderError("codex_sdk needs a system prompt in the study", fatal=True)
        codex = await self._client()
        params = self.spec.params
        try:
            thread = await codex.thread_start(
                model=self.spec.model,
                base_instructions=prompt.system,
                ephemeral=True,
                approval_mode=ApprovalMode.deny_all,
                sandbox=Sandbox.read_only,
                cwd=str(self.workdir),
            )
            result = await asyncio.wait_for(
                thread.run(prompt.user, effort=params.get("effort"), summary=params.get("summary")), timeout_s
            )
            thread_info = (await thread.read()).thread
        except asyncio.TimeoutError as exc:
            raise ProviderError(f"timed out after {timeout_s}s", retryable=True) from exc
        except CodexError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", retryable=is_retryable_error(exc)) from exc

        items = [getattr(item, "root", item) for item in result.items]
        raw = {"items": [item.model_dump(mode="json", by_alias=True) for item in items]}
        status = getattr(result.status, "value", str(result.status))
        if status != "completed":
            error = result.error
            info = getattr(getattr(error, "codex_error_info", None), "root", None)
            kind = getattr(info, "value", str(info or ""))
            message = getattr(error, "message", "") or status
            if kind == "usageLimitExceeded":
                quota = await self.quota() or {}
                exhausted = [w["resets_at"] for w in quota.values() if (w["used"] or 0) >= 1 and w["resets_at"]]
                resets_at = max(exhausted) if exhausted else None
                raise ProviderError(f"usage limit: {message}", usage_limit=True, resets_at=resets_at, raw=raw)
            raise ProviderError(
                f"Codex turn {status} ({kind}): {message}",
                retryable=kind != "contextWindowExceeded",
                raw=raw,
            )

        reasoning = [
            text for item in items if item.type == "reasoning" for text in (item.summary or []) + (item.content or [])
        ]
        return Completion(
            text=result.final_response or "",
            reasoning="\n\n".join(reasoning) or None,
            model=thread_info.model,
            finish_reason=status,
            usage=result.usage.model_dump(mode="json") if result.usage else None,
            tool_calls=sum(1 for item in items if item.type not in ANSWER_ITEMS),
            meta={
                "thread_id": thread_info.id,
                "cli_version": thread_info.cli_version,
                "model_provider": thread_info.model_provider,
                "effort": params.get("effort"),
                "item_types": [item.type for item in items],
                "duration_ms": result.duration_ms,
                "harness_context": self._harness_context,
                "quota": await self.quota(),
            },
            raw=raw,
        )

    async def close(self) -> None:
        if self._codex is not None:
            await self._codex.close()
            self._codex = None


async def login(codex_home: Path = DEFAULT_CODEX_HOME, *, device_code: bool = False, log=print) -> None:
    """ChatGPT login into the isolated Codex home used by llmtrials (separate from ~/.codex)."""
    from openai_codex import AsyncCodex, CodexConfig

    codex_home = codex_home.expanduser().resolve()
    codex_home.mkdir(parents=True, exist_ok=True)
    env = {"CODEX_HOME": str(codex_home)}
    overrides = ('forced_login_method="chatgpt"', 'cli_auth_credentials_store="file"')
    async with AsyncCodex(CodexConfig(env=env, config_overrides=overrides)) as codex:
        if device_code:
            handle = await codex.login_chatgpt_device_code()
            log(f"Open {handle.verification_url} and enter the code: {handle.user_code}")
        else:
            handle = await codex.login_chatgpt()
            log(f"Open this URL in your browser and sign in with ChatGPT:\n{handle.auth_url}")
        await handle.wait()
        account = (await codex.account()).account
        account = getattr(account, "root", account)
        plan = getattr(getattr(account, "plan_type", None), "value", None)
        log(f"Logged in ({getattr(account, 'type', '?')}, plan {plan}) -> {codex_home}")


def _flatten(config: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in config.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _toml(value: Any) -> str:
    """TOML literal for `--config key=value` (scalars and lists; JSON syntax is valid TOML here)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str, list)):
        return json.dumps(value)
    raise ValueError(f"unsupported Codex config value: {value!r}")
