"""Claude through a Claude subscription (Pro/Max), via the Claude Agent SDK.

The Agent SDK drives Claude Code, an agent harness. Every trial is reduced to the closest
thing to a plain model call that a subscription allows:
  - the study's system prompt replaces Claude Code's (``system_prompt=<str>``)
  - no built-in tools, MCP servers, plugins or skills
  - no user/project settings or CLAUDE.md (``setting_sources=[]``), no auto-memory
  - a fixed, empty working directory, one turn, prompt delivered verbatim
  - no session files written to ~/.claude

Claude Code still adds context we cannot switch off with subscription auth (identity line,
environment block, date, model name, account email...). With ``audit: true`` (the default)
the exact request is captured per trial, PII-redacted, under ``meta.api_request`` so the
residual harness is documented rather than assumed. See docs/METHODOLOGY.md.

params (passed to ClaudeAgentOptions unless noted):
  thinking: {type: adaptive} | {type: enabled, budget_tokens: N} | {type: disabled}
            optional ``display: summarized`` to receive the thinking text
  effort:   low | medium | high | xhigh | max
  env:      extra environment variables for the Claude Code process
  audit:    llmtrials option, default true
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ..config import Prompt
from ..redact import identity_tokens
from .base import Completion, Provider, ProviderError

# Research hygiene for the Claude Code process: no self-updates, telemetry or auto-memory.
ISOLATION_ENV = {
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}
USAGE_LIMIT = re.compile(r"usage limit|limit reached|hit your (usage )?limit|out of usage", re.IGNORECASE)


class ClaudeAgentSDKProvider(Provider):
    name = "claude_agent_sdk"
    # Options llmtrials controls itself to keep the harness minimal.
    reserved_params = frozenset(
        """model system_prompt tools allowed_tools disallowed_tools mcp_servers strict_mcp_config plugins
        skills agents hooks setting_sources max_turns cwd add_dirs verbatim_prompts fallback_model resume
        continue_conversation session_id can_use_tool stderr extra_args output_format cli_path""".split()
    )

    def __init__(self, spec):
        super().__init__(spec)
        # One shared empty folder: the environment block Claude Code injects stays identical
        # across trials (a random temp dir would make every prompt slightly different).
        self.workdir = Path(tempfile.gettempdir()) / "llmtrials-claude-cwd"

    async def setup(self) -> dict[str, Any]:
        import claude_agent_sdk
        from claude_agent_sdk._cli_version import __cli_version__

        info: dict[str, Any] = {
            "sdk_version": claude_agent_sdk.__version__,
            "bundled_cli_version": __cli_version__,
        }
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            if os.environ.get(var):
                info.setdefault("warnings", []).append(
                    f"{var} is set: Claude Code will use it instead of your subscription login"
                )
        if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID"):
            info.setdefault("warnings", []).append(
                "running inside a Claude Code session: its environment leaks into trials; "
                "run llmtrials from a normal terminal"
            )
        status = await _auth_status()
        if status.get("loggedIn") is False:
            raise ProviderError("Claude Code is not logged in: run `llmtrials login-claude`", fatal=True)
        # Recorded without email/organisation. "claude.ai" means a Pro/Max/Team subscription login.
        # Derive identity tokens from the account email so the runner can scrub the name the
        # harness injects and models echo. The email itself is never recorded.
        self.scrub_tokens = identity_tokens(status.get("email"))
        info["auth_method"] = status.get("authMethod")
        info["subscription_type"] = status.get("subscriptionType")
        if info["auth_method"] not in (None, "claude.ai"):
            info.setdefault("warnings", []).append(f"not a subscription login (authMethod={info['auth_method']})")
        return info

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> Completion:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKError, CLINotFoundError, query

        params = dict(self.spec.params)
        audit = params.pop("audit", True)
        env = {**ISOLATION_ENV, **params.pop("env", {})}
        stderr: list[str] = []
        self.workdir.mkdir(exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="llmtrials-audit-") as audit_dir:
            if audit:
                env["OTEL_LOG_RAW_API_BODIES"] = f"file:{audit_dir}"
            options = ClaudeAgentOptions(
                model=self.spec.model,
                system_prompt=prompt.system or "",
                tools=[],
                allowed_tools=[],
                mcp_servers={},
                strict_mcp_config=True,
                plugins=[],
                skills=[],
                setting_sources=[],
                max_turns=1,
                verbatim_prompts=True,
                cwd=str(self.workdir),
                env=env,
                extra_args={"no-session-persistence": None},
                stderr=stderr.append,
                **params,
            )

            async def collect() -> list[Any]:
                return [message async for message in query(prompt=prompt.user, options=options)]

            try:
                messages = await asyncio.wait_for(collect(), timeout_s)
            except asyncio.TimeoutError as exc:
                raise ProviderError(f"timed out after {timeout_s}s", retryable=True) from exc
            except CLINotFoundError as exc:
                raise ProviderError(str(exc), fatal=True) from exc
            except ClaudeSDKError as exc:
                detail = f"{type(exc).__name__}: {exc}\n" + "\n".join(stderr[-20:])
                if USAGE_LIMIT.search(detail):
                    raise ProviderError(f"usage limit: {detail.strip()}", usage_limit=True) from exc
                raise ProviderError(detail.strip(), retryable=True) from exc

            api_request = _read_audit(Path(audit_dir), prompt) if audit else None

        return _to_completion(messages, api_request)


def claude_cli_path() -> str:
    """The Claude Code CLI bundled with the SDK (same version as the one used for trials)."""
    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
    for name in ("claude", "claude.exe"):
        if (bundled / name).exists():
            return str(bundled / name)
    found = shutil.which("claude")
    if not found:
        raise ProviderError("Claude Code CLI not found", fatal=True)
    return found


async def _auth_status() -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        claude_cli_path(),
        "auth",
        "status",
        "--json",
        env={**os.environ, **ISOLATION_ENV},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), 60)
        return json.loads(stdout)
    except (asyncio.TimeoutError, ValueError):
        return {}


def login() -> int:
    """Interactive Claude subscription login with the bundled Claude Code (browser flow)."""
    return subprocess.call([claude_cli_path(), "auth", "login", "--claudeai"], env={**os.environ, **ISOLATION_ENV})


def _to_completion(messages: list[Any], api_request: dict[str, Any] | None) -> Completion:
    from claude_agent_sdk import (
        AssistantMessage,
        RateLimitEvent,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
    )

    texts: list[str] = []
    thinking: list[str] = []
    tool_calls = 0
    model = None
    init: dict[str, Any] = {}
    result: ResultMessage | None = None
    assistant_error = None
    rate_limit = None
    for message in messages:
        if isinstance(message, SystemMessage) and message.subtype == "init":
            init = message.data
        elif isinstance(message, AssistantMessage):
            model = message.model
            assistant_error = message.error or assistant_error
            for block in message.content:
                if isinstance(block, TextBlock):
                    texts.append(block.text)
                elif isinstance(block, ThinkingBlock):
                    if block.thinking:
                        thinking.append(block.thinking)
                else:
                    tool_calls += 1
        elif isinstance(message, RateLimitEvent):
            rate_limit = message.rate_limit_info
        elif isinstance(message, ResultMessage):
            result = message

    raw = [_jsonable(m) for m in messages]
    rejected = rate_limit is not None and rate_limit.status == "rejected"
    detail = (result.result or "; ".join(result.errors or [])) if result else ""
    if rejected or assistant_error == "rate_limit" or (result and result.is_error and USAGE_LIMIT.search(detail)):
        # The subscription's usage window is exhausted: not a failure, the runner waits for the reset.
        resets_at = rate_limit.resets_at if rate_limit else None
        raise ProviderError(f"usage limit: {detail or 'rejected'}", usage_limit=True, resets_at=resets_at, raw=raw)
    if result is None:
        raise ProviderError("Claude Code returned no result message", retryable=True, raw=raw)
    if result.is_error or assistant_error:
        reason = assistant_error or result.subtype
        transient = bool(
            re.search(r"refresh OAuth token|another Claude Code process|overloaded|temporarily|"
                      r"ETIMEDOUT|ECONNRESET|50[23]|529", detail, re.I)
        )
        raise ProviderError(
            f"Claude Code error ({reason}): {detail}".strip(),
            retryable=transient or reason in ("server_error", "unknown", "error_during_execution"),
            fatal=reason in ("authentication_failed", "billing_error"),
            raw=raw,
        )

    meta: dict[str, Any] = {
        "auth_source": init.get("apiKeySource"),
        "claude_code_version": init.get("claude_code_version"),
        "tools_available": init.get("tools"),
        "num_turns": result.num_turns,
        "stop_reason": result.stop_reason,
        "duration_api_ms": result.duration_api_ms,
        "model_usage": result.model_usage,
        "quota": _quota(rate_limit),
    }
    if api_request is not None:
        meta["harness_context"] = api_request.pop("harness_context", None)
        meta["api_request"] = api_request
    return Completion(
        text=result.result if result.result is not None else "".join(texts),
        reasoning="\n\n".join(thinking) or None,
        model=model,
        finish_reason=result.stop_reason,
        usage=result.usage,
        # Under subscription auth this is the API-price equivalent, not money charged.
        cost_usd=result.total_cost_usd,
        tool_calls=tool_calls,
        meta=meta,
        raw=raw,
    )


def _quota(info: Any) -> dict[str, Any] | None:
    """Subscription usage per window, e.g. {"five_hour": {"used": 0.26, "resets_at": ...}}."""
    if info is None:
        return None
    windows = (info.raw or {}).get("unifiedWindows") or {}
    quota = {
        name: {"used": window.get("utilization"), "resets_at": window.get("resetsAt")}
        for name, window in windows.items()
        if isinstance(window, dict)
    }
    if not quota and info.rate_limit_type:
        quota = {info.rate_limit_type: {"used": info.utilization, "resets_at": info.resets_at}}
    return quota or None


def _read_audit(audit_dir: Path, prompt: Prompt) -> dict[str, Any]:
    """Return the main request Claude Code sent, minus metadata, with PII redacted.

    Request bodies are written when each request is sent. The index file is not reliable in
    SDK mode (the CLI exits before writing it), so the main request is identified as the one
    carrying our prompt verbatim as a content block. Auxiliary requests (e.g. session-title
    generation) embed the prompt inside other text and are only counted.
    """
    bodies = []
    for path in sorted(audit_dir.glob("*.request.json"), key=lambda p: p.stat().st_mtime):
        try:
            bodies.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    main = [body for body in bodies if _has_text_block(body.get("messages", []), prompt.user)]
    if not main:
        return {"note": "main request not captured", "requests_seen": len(bodies)}
    keep = ("model", "system", "messages", "tools", "max_tokens", "temperature", "thinking", "output_config")
    request = {key: main[-1][key] for key in keep if key in main[-1]}
    request["requests_seen"] = {"main": len(main), "auxiliary": len(bodies) - len(main)}
    request["harness_context"] = _harness_context(request, prompt)
    return request


def _harness_context(request: dict[str, Any], prompt: Prompt) -> dict[str, Any]:
    """Fingerprint of everything Claude Code added around our prompt (after redaction).

    Identical across trials while the harness, model, account and date stay the same; a new
    value marks a change in what the model saw (e.g. the date rolled over during a long run).
    """
    added: list[str] = []
    for block in request.get("system") or []:
        text = block.get("text", "") if isinstance(block, dict) else str(block)
        # The billing header carries per-request ids; it is metadata, not instructions.
        if text != prompt.system and not text.startswith("x-anthropic-billing-header"):
            added.append(f"[system] {text}")
    for message in request.get("messages") or []:
        content = message.get("content")
        for block in content if isinstance(content, list) else [{"text": content}]:
            text = block.get("text") if isinstance(block, dict) else None
            if text is not None and text != prompt.user:
                added.append(f"[{message.get('role')}] {text}")
    digest = hashlib.sha256("\n\0".join(added).encode()).hexdigest()[:16]
    return {"sha256": digest, "blocks": len(added)}


def _has_text_block(messages: list[dict[str, Any]], text: str) -> bool:
    for message in messages:
        content = message.get("content")
        if content == text:
            return True
        if isinstance(content, list) and any(b.get("text") == text for b in content if isinstance(b, dict)):
            return True
    return False




def _jsonable(message: Any) -> Any:
    data = asdict(message) if is_dataclass(message) else message
    return {"type": type(message).__name__, **data} if isinstance(data, dict) else data
