"""Plan and execute trials: scenario x model x repetition, with retries and resume."""

from __future__ import annotations

import asyncio
import importlib.metadata
import platform
import random
import subprocess
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import ConfigError, ModelSpec, Prompt, Scenario, StudyBundle, load_models, load_study
from .providers import Provider, ProviderError, create_provider
from .redact import scrub
from .store import SCHEMA_VERSION, RunDir, utc_now


@dataclass(frozen=True)
class Trial:
    scenario: Scenario
    model: ModelSpec
    repetition: int
    prompt: Prompt

    @property
    def key(self) -> str:
        return f"{self.scenario.id}|{self.model.id}|{self.repetition}"


@dataclass
class LimitPolicy:
    """What a model's lane does about subscription usage limits.

    on_limit: "wait" for the reported reset and retry the same trial, or "stop" the lane
        (the trial stays pending; finish later with `resume`).
    quota_cap: pause the lane once any usage window reaches this fraction, leaving the rest
        of the subscription for its owner (1.0 = only stop at the real limit).
    """

    on_limit: str = "wait"
    quota_cap: float = 1.0
    margin_s: float = 60.0  # wait this long past the reported reset time
    min_wait_s: float = 60.0  # never retry sooner, even if the reset time is stale
    default_wait_s: float = 1800.0  # when the provider does not report a reset time


@dataclass
class RunSummary:
    run_dir: Path
    planned: int
    already_done: int
    ok: int = 0
    failed: int = 0
    stopped_lanes: dict[str, str] = field(default_factory=dict)

    @property
    def remaining(self) -> int:
        return self.planned - self.already_done - self.ok


def plan_trials(
    bundle: StudyBundle,
    models: list[ModelSpec],
    *,
    repetitions: int,
    seed: int,
    scenario_ids: list[str] | None = None,
    model_ids: list[str] | None = None,
) -> list[Trial]:
    """Every (scenario, model, repetition), in a seeded random order.

    The shuffle spreads each model's calls over the whole run so that time effects
    (provider load, rate limits, silent model updates) are not confounded with scenarios.
    """
    scenarios = [bundle.scenario(s) for s in scenario_ids] if scenario_ids else bundle.scenarios
    if model_ids:
        known = {m.id: m for m in models}
        unknown = sorted(set(model_ids) - known.keys())
        if unknown:
            raise ConfigError(f"unknown model ids: {unknown}")
        models = [known[m] for m in model_ids]
    trials = [
        Trial(scenario, model, rep, bundle.render(scenario))
        for scenario in scenarios
        for model in models
        for rep in range(1, repetitions + 1)
    ]
    random.Random(seed).shuffle(trials)
    return trials


def new_run(
    study_dir: str | Path,
    models_file: str | Path,
    *,
    runs_root: str | Path = "runs",
    label: str | None = None,
    repetitions: int | None = None,
    scenario_ids: list[str] | None = None,
    model_ids: list[str] | None = None,
) -> tuple[RunDir, list[Trial], dict[str, Any]]:
    """Create the run folder (input snapshot + manifest) and return the planned trials."""
    bundle = load_study(study_dir)
    models = load_models(models_file)
    check_providers(models)
    study = bundle.study
    reps = repetitions or study.repetitions
    trials = plan_trials(
        bundle, models, repetitions=reps, seed=study.seed, scenario_ids=scenario_ids, model_ids=model_ids
    )
    run_dir = RunDir.create(Path(runs_root), study.id, label)
    run_dir.snapshot_inputs(bundle, Path(models_file))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.path.name,
        "created_at": utc_now(),
        "study": {
            "id": study.id,
            "title": study.title,
            "version": study.version,
            "inputs_sha256": bundle.fingerprint(),
            "source": str(Path(study_dir)),
        },
        "plan": {
            "repetitions": reps,
            "seed": study.seed,
            "scenario_ids": scenario_ids or [s.id for s in bundle.scenarios],
            "model_ids": model_ids or [m.id for m in models],
            "n_trials": len(trials),
        },
        "models": [m.model_dump() for m in models],
        "sessions": [],
    }
    run_dir.write_manifest(manifest)
    return run_dir, trials, manifest


def check_providers(models: list[ModelSpec]) -> None:
    """Fail before anything is created if a model's params are invalid for its provider."""
    for spec in models:
        try:
            create_provider(spec)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc


def load_run(run_path: str | Path) -> tuple[RunDir, list[Trial], dict[str, Any]]:
    """Re-plan a run from its own input snapshot (never from the live study folder)."""
    run_dir = RunDir(run_path)
    manifest = run_dir.read_manifest()
    bundle = load_study(run_dir.study_snapshot)
    if bundle.fingerprint() != manifest["study"]["inputs_sha256"]:
        raise ConfigError(f"input snapshot in {run_dir.path} was modified; refusing to continue")
    models = load_models(run_dir.models_snapshot)
    plan = manifest["plan"]
    trials = plan_trials(
        bundle,
        models,
        repetitions=plan["repetitions"],
        seed=plan["seed"],
        scenario_ids=plan["scenario_ids"],
        model_ids=plan["model_ids"],
    )
    return run_dir, trials, manifest


async def execute(
    run_dir: RunDir,
    trials: list[Trial],
    manifest: dict[str, Any],
    *,
    timeout_s: float,
    max_attempts: int,
    max_consecutive_failures: int,
    limits: LimitPolicy | None = None,
    log=print,
) -> RunSummary:
    """Run every trial not yet completed in this run folder (see LimitPolicy for subscriptions)."""
    limits = limits or LimitPolicy()
    done = run_dir.completed_keys()
    pending = [t for t in trials if t.key not in done]
    summary = RunSummary(run_dir.path, planned=len(trials), already_done=len(trials) - len(pending))

    specs = {t.model.id: t.model for t in pending}
    providers: dict[str, Provider] = {model_id: create_provider(spec) for model_id, spec in specs.items()}
    session: dict[str, Any] = {
        "index": len(manifest["sessions"]),
        "started_at": utc_now(),
        "command": " ".join(["llmtrials", *sys.argv[1:]]),
        "environment": environment_info(),
        "limits": asdict(limits),
        "providers": {},
        "pauses": [],
    }
    manifest["sessions"].append(session)
    try:
        for model_id, provider in providers.items():
            try:
                info = await provider.setup()
            except ProviderError as exc:
                info = {"error": str(exc)}
                if exc.fatal:
                    summary.stopped_lanes[model_id] = f"setup failed: {exc}"
            except Exception as exc:  # e.g. the harness process failed to start
                info = {"error": f"{type(exc).__name__}: {exc}"}
                summary.stopped_lanes[model_id] = f"setup failed: {info['error']}"
            session["providers"][model_id] = info
            for warning in info.get("warnings", []) + ([info["warning"]] if "warning" in info else []):
                log(f"warning [{model_id}]: {warning}")
            if model_id in summary.stopped_lanes:
                log(f"skipping [{model_id}]: {summary.stopped_lanes[model_id]}")
        run_dir.write_manifest(manifest)

        lanes: dict[str, list[Trial]] = {}
        for trial in pending:  # keeps the shuffled order inside each lane
            lanes.setdefault(trial.model.id, []).append(trial)
        counter = {"n": summary.already_done}

        async def pause(model_id: str, reason: str, resets_at: float | None) -> None:
            wait = (resets_at - time.time() + limits.margin_s) if resets_at else limits.default_wait_s
            wait = max(limits.min_wait_s, wait)
            until = datetime.now() + timedelta(seconds=wait)
            session["pauses"].append({"model_id": model_id, "reason": reason, "at": utc_now(), "wait_s": round(wait)})
            run_dir.write_manifest(manifest)
            log(f"pause [{model_id}]: {reason}; waiting {wait / 60:.0f} min (until {until:%a %H:%M})")
            await asyncio.sleep(wait)

        async def run_lane(model_id: str, lane_trials: list[Trial]) -> None:
            if model_id in summary.stopped_lanes:
                return
            queue = deque(lane_trials)
            consecutive = 0

            async def worker() -> None:
                nonlocal consecutive
                while queue and model_id not in summary.stopped_lanes:
                    trial = queue.popleft()
                    record = await run_trial(
                        providers[model_id],
                        trial,
                        run_id=manifest["run_id"],
                        session=session["index"],
                        timeout_s=timeout_s,
                        max_attempts=max_attempts,
                    )
                    if record["status"] == "usage_limit":  # not an outcome: retry the same trial later
                        queue.appendleft(trial)
                        if limits.on_limit == "stop":
                            summary.stopped_lanes[model_id] = "usage limit reached"
                            return
                        await pause(model_id, "usage limit reached", record["error"].get("resets_at"))
                        continue
                    run_dir.append(record)
                    counter["n"] += 1
                    ok = record["status"] == "ok"
                    summary.ok += ok
                    summary.failed += not ok
                    log(
                        f"[{counter['n']:>{len(str(summary.planned))}}/{summary.planned}] "
                        f"{record['status']:<5} {model_id}  {trial.scenario.id} #{trial.repetition}  "
                        f"{record['latency_s']:.1f}s"
                        + (_quota_text(record["meta"].get("quota")) if ok else f"  {record['error']['message'][:160]}")
                    )
                    consecutive = 0 if ok else consecutive + 1
                    if not ok and record["error"].get("fatal"):
                        summary.stopped_lanes[model_id] = record["error"]["message"][:300]
                    elif consecutive >= max_consecutive_failures:
                        summary.stopped_lanes[model_id] = f"{consecutive} consecutive failures"
                    over = _over_cap(record["meta"].get("quota") if ok else None, limits.quota_cap)
                    if over and queue:
                        name, window = over
                        reason = f"{name} quota at {window['used']:.0%} (cap {limits.quota_cap:.0%})"
                        await pause(model_id, reason, window.get("resets_at"))

            spec = specs[model_id]
            await asyncio.gather(*(worker() for _ in range(spec.max_concurrency)))

        await asyncio.gather(*(run_lane(model_id, lane) for model_id, lane in lanes.items()))
    finally:
        for provider in providers.values():
            await provider.close()
        session["finished_at"] = utc_now()
        session["results"] = {"ok": summary.ok, "failed": summary.failed, "stopped_lanes": summary.stopped_lanes}
        run_dir.write_manifest(manifest)
    return summary


async def run_trial(
    provider: Provider, trial: Trial, *, run_id: str, session: int, timeout_s: float, max_attempts: int
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "session": session,
        "trial_key": trial.key,
        "scenario_id": trial.scenario.id,
        "model_id": trial.model.id,
        "repetition": trial.repetition,
        "provider": trial.model.provider,
        "model": trial.model.model,
        "params": trial.model.params,
        "prompt": {"system": trial.prompt.system, "user": trial.prompt.user, "sha256": trial.prompt.sha256},
        "started_at": utc_now(),
    }
    start = time.monotonic()
    error: ProviderError | None = None
    for attempt in range(1, max_attempts + 1):
        record["attempts"] = attempt
        try:
            completion = await provider.complete(trial.prompt, timeout_s=timeout_s)
        except ProviderError as exc:
            error = exc
        except Exception as exc:  # a bug or an unexpected SDK failure: record it, do not retry
            error = ProviderError(f"{type(exc).__name__}: {exc}")
        else:
            record.update(
                status="ok",
                response={
                    "text": completion.text,
                    "reasoning": completion.reasoning,
                    "model": completion.model,
                    "finish_reason": completion.finish_reason,
                    "usage": completion.usage,
                    "cost_usd": completion.cost_usd,
                    "tool_calls": completion.tool_calls,
                },
                meta=completion.meta,
                error=None,
                raw=completion.raw,
            )
            break
        if error.usage_limit:
            record.update(status="usage_limit", error={"message": str(error), "resets_at": error.resets_at})
            break
        if error.fatal or not error.retryable or attempt == max_attempts:
            record.update(
                status="error",
                response=None,
                meta={},
                error={"message": str(error), "retryable": error.retryable, "fatal": error.fatal},
                raw=error.raw,
            )
            break
        await asyncio.sleep(min(60.0, 2.0**attempt) + random.random())
    # Remove the account email and name tokens the harness injected / the model echoed, so no
    # personal data is written to disk (any provider; tokens come from the provider).
    for key in ("response", "meta", "raw", "error", "prompt"):
        if record.get(key) is not None:
            record[key] = scrub(record[key], provider.scrub_tokens)
    record["finished_at"] = utc_now()
    record["latency_s"] = round(time.monotonic() - start, 3)
    return record


def _over_cap(quota: dict[str, Any] | None, cap: float) -> tuple[str, dict[str, Any]] | None:
    if not quota or cap >= 1:
        return None
    for name, window in quota.items():
        if window.get("used") is not None and window["used"] >= cap:
            return name, window
    return None


def _quota_text(quota: dict[str, Any] | None) -> str:
    short = {"five_hour": "5h", "seven_day": "7d"}
    parts = [f"{short.get(n, n)} {w['used']:.0%}" for n, w in (quota or {}).items() if w.get("used") is not None]
    return f"  quota {' '.join(parts)}" if parts else ""


def environment_info() -> dict[str, Any]:
    packages = {}
    for name in ("llmtrials", "claude-agent-sdk", "openai-codex", "httpx", "pydantic", "pyyaml"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "git": _git_info(),
    }


def _git_info() -> dict[str, Any] | None:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "dirty": bool(dirty)}
