"""Command line: llmtrials validate | check | run | resume | status | export | login-claude | login-codex."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from dotenv import load_dotenv

from .config import ConfigError, load_models, load_study
from .export import export_run, summarize
from .runner import LimitPolicy, RunSummary, check_providers, execute, load_run, new_run, plan_trials


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # API keys from ./.env
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ConfigError, FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted: completed trials are saved; continue with `llmtrials resume`", file=sys.stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmtrials", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check a study (and models file) without calling any model")
    p.add_argument("study", help="study folder (contains study.yaml)")
    p.add_argument("--models", help="models YAML file")
    p.add_argument("--show", metavar="SCENARIO_ID", help="print the exact prompt for one scenario")
    p.set_defaults(handler=cmd_validate)

    p = sub.add_parser("run", help="start a new run")
    p.add_argument("study", help="study folder (contains study.yaml)")
    p.add_argument("--models", required=True, help="models YAML file")
    p.add_argument("--model", action="append", dest="model_ids", metavar="ID", help="only this model (repeatable)")
    p.add_argument(
        "--scenario", action="append", dest="scenario_ids", metavar="ID", help="only this scenario (repeatable)"
    )
    p.add_argument("--repetitions", type=int, help="override the study's repetitions")
    p.add_argument("--label", help="suffix for the run folder name, e.g. pilot")
    p.add_argument("--runs-dir", default="runs", help="where run folders are created (default: runs)")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    _limit_options(p)
    p.set_defaults(handler=cmd_run)

    p = sub.add_parser("resume", help="continue a run: missing and failed trials only")
    p.add_argument("run_dir")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    _limit_options(p)
    p.set_defaults(handler=cmd_resume)

    p = sub.add_parser("status", help="progress and per-model summary of a run")
    p.add_argument("run_dir")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser("export", help="write review files for a run")
    p.add_argument("run_dir")
    p.add_argument("--format", choices=["csv", "md"], default="csv")
    p.add_argument("--blind", action="store_true", help="hide model identity; key saved separately")
    p.add_argument("--force", action="store_true", help="overwrite an existing blind key")
    p.set_defaults(handler=cmd_export)

    p = sub.add_parser("login-claude", help="log in to your Claude subscription for the claude_agent_sdk route")
    p.set_defaults(handler=cmd_login_claude)

    p = sub.add_parser("login-codex", help="log in to ChatGPT for the codex_sdk route (isolated Codex profile)")
    p.add_argument("--codex-home", default=None, help="profile folder (default: .llmtrials/codex-home)")
    p.add_argument("--device-code", action="store_true", help="device-code login (for machines without a browser)")
    p.set_defaults(handler=cmd_login_codex)

    p = sub.add_parser("check", help="check logins, model availability and quota for a models file (no trials)")
    p.add_argument("models", help="models YAML file")
    p.set_defaults(handler=cmd_check)
    return parser


def _percent(value: str) -> float:
    number = float(value)
    if not 0 < number <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return number


def _limit_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--on-limit",
        choices=["wait", "stop"],
        default="wait",
        help="when a subscription usage limit is hit: wait for the reset (default) or stop that model",
    )
    p.add_argument(
        "--quota-cap",
        type=_percent,
        default=100,
        metavar="PERCENT",
        help="pause a model when its subscription usage reaches this %% of any window (default 100)",
    )


def cmd_validate(args) -> int:
    bundle = load_study(args.study)
    study = bundle.study
    print(f"study    {study.id} v{study.version}: {study.title}")
    print(f"inputs   sha256 {bundle.fingerprint()[:16]}")
    print(
        f"settings repetitions={study.repetitions} seed={study.seed} timeout={study.timeout_s}s "
        f"max_attempts={study.max_attempts}"
    )
    print(f"scenarios ({len(bundle.scenarios)}):")
    for scenario in bundle.scenarios:
        prompt = bundle.render(scenario)
        tags = f" [{', '.join(scenario.tags)}]" if scenario.tags else ""
        print(f"  {scenario.id:<24} prompt {prompt.sha256[:12]}  {scenario.title}{tags}")
    if args.models:
        models = load_models(args.models)
        check_providers(models)
        print(f"models ({len(models)}):")
        for model in models:
            print(f"  {model.id:<36} {model.provider:<18} {model.model}")
        print(
            f"trials   {len(bundle.scenarios)} x {len(models)} x {study.repetitions} = "
            f"{len(bundle.scenarios) * len(models) * study.repetitions}"
        )
    if args.show:
        prompt = bundle.render(bundle.scenario(args.show))
        print(f"\n===== system =====\n{prompt.system or '(none)'}\n===== user =====\n{prompt.user}")
    return 0


def cmd_run(args) -> int:
    bundle = load_study(args.study)
    models = load_models(args.models)
    check_providers(models)
    study = bundle.study
    reps = args.repetitions or study.repetitions
    trials = plan_trials(
        bundle, models, repetitions=reps, seed=study.seed, scenario_ids=args.scenario_ids, model_ids=args.model_ids
    )
    model_ids = sorted({t.model.id for t in trials})
    scenario_count = len({t.scenario.id for t in trials})
    print(
        f"study {study.id} v{study.version}: {scenario_count} scenarios x {len(model_ids)} models x "
        f"{reps} repetitions = {len(trials)} trials"
    )
    for model_id in model_ids:
        print(f"  - {model_id}")
    if not _confirm(args.yes):
        return 1
    run_dir, trials, manifest = new_run(
        args.study,
        args.models,
        runs_root=args.runs_dir,
        label=args.label,
        repetitions=args.repetitions,
        scenario_ids=args.scenario_ids,
        model_ids=args.model_ids,
    )
    print(f"run folder: {run_dir.path}")
    return _execute(run_dir, trials, manifest, study, args)


def cmd_resume(args) -> int:
    run_dir, trials, manifest = load_run(args.run_dir)
    done = run_dir.completed_keys()
    pending = sum(1 for t in trials if t.key not in done)
    print(f"run {manifest['run_id']}: {len(trials) - pending}/{len(trials)} trials done, {pending} to run")
    if pending == 0 or not _confirm(args.yes):
        return 0 if pending == 0 else 1
    study = load_study(run_dir.study_snapshot).study
    return _execute(run_dir, trials, manifest, study, args)


def cmd_status(args) -> int:
    summary = summarize(args.run_dir)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    study = summary["study"]
    print(f"run {summary['run_id']}  study {study['id']} v{study['version']}")
    header = (
        f"{'model':<36} {'planned':>7} {'ok':>5} {'error':>5} {'pending':>7} {'tools':>5} {'ctx':>4} "
        f"{'med s':>7} {'cost $':>8}"
    )
    print(header)
    print("-" * len(header))
    for model_id, s in summary["models"].items():
        latency = f"{s['median_latency_s']:.1f}" if s["median_latency_s"] is not None else "-"
        contexts = s["harness_contexts"] or "-"
        print(
            f"{model_id:<36} {s['planned']:>7} {s['ok']:>5} {s['error']:>5} {s['pending']:>7} "
            f"{s['tool_calls']:>5} {contexts:>4} {latency:>7} {s['cost_usd']:>8.4f}"
        )
    quotas = {m: s["quota"] for m, s in summary["models"].items() if s["quota"]}
    if quotas:
        print("\nlast reported subscription usage:")
        for model_id, quota in quotas.items():
            windows = ", ".join(
                f"{name} {w['used']:.0%}" for name, w in quota.items() if name != "at" and w.get("used") is not None
            )
            print(f"  {model_id:<36} {windows}  (at {quota['at'][:16]})")
    print("\ntools: trials where the harness attempted a tool call (should be 0)")
    print("ctx:   distinct contexts the harness added around the prompt (1 expected; +1 per date change)")
    return 0


def cmd_export(args) -> int:
    for path in export_run(args.run_dir, fmt=args.format, blind=args.blind, force=args.force):
        print(f"wrote {path}")
    return 0


def cmd_login_claude(args) -> int:
    from .providers.claude_sdk import login

    return login()


def cmd_login_codex(args) -> int:
    from pathlib import Path

    from .providers.codex_sdk import DEFAULT_CODEX_HOME, login

    home = Path(args.codex_home) if args.codex_home else DEFAULT_CODEX_HOME
    asyncio.run(login(home, device_code=args.device_code, log=lambda line: print(line, flush=True)))
    return 0


def cmd_check(args) -> int:
    from .providers import create_provider

    models = load_models(args.models)
    check_providers(models)
    keys = (
        "auth_method",
        "subscription_type",
        "account_type",
        "plan_type",
        "sdk_version",
        "bundled_cli_version",
        "canonical_slug",
        "default_reasoning_effort",
        "quota",
        "render_check",
        "warnings",
        "warning",
        "catalogue_error",
    )

    async def run() -> bool:
        all_ok = True
        for spec in models:
            provider = create_provider(spec)
            try:
                info = await provider.setup()
            except Exception as exc:
                all_ok = False
                print(f"FAIL {spec.id}: {exc}")
                continue
            finally:
                await provider.close()
            print(f"ok   {spec.id}")
            for key in keys:
                if info.get(key) not in (None, [], {}):
                    print(f"       {key}: {json.dumps(info[key], default=str)[:300]}")
        return all_ok

    return 0 if asyncio.run(run()) else 1


def _execute(run_dir, trials, manifest, study, args) -> int:
    summary: RunSummary = asyncio.run(
        execute(
            run_dir,
            trials,
            manifest,
            timeout_s=study.timeout_s,
            max_attempts=study.max_attempts,
            max_consecutive_failures=study.max_consecutive_failures,
            limits=LimitPolicy(on_limit=args.on_limit, quota_cap=args.quota_cap / 100),
            log=lambda line: print(line, flush=True),
        )
    )
    print(
        f"\ndone: {summary.ok} ok, {summary.failed} failed this session; "
        f"{summary.remaining} of {summary.planned} trials still to complete"
    )
    for model_id, reason in summary.stopped_lanes.items():
        print(f"stopped {model_id}: {reason}")
    if summary.remaining:
        print(f"continue with: llmtrials resume {run_dir.path}")
    return 0 if summary.remaining == 0 else 1


def _confirm(assume_yes: bool) -> bool:
    if assume_yes or not sys.stdin.isatty():
        return True
    return input("Start? [y/N] ").strip().lower() in ("y", "yes")


if __name__ == "__main__":
    raise SystemExit(main())
