import asyncio
import json
from pathlib import Path

import pytest

from llmtrials.config import ConfigError, load_models, load_study
from llmtrials.export import export_run, summarize
from llmtrials.runner import LimitPolicy, execute, load_run, new_run, plan_trials

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "studies" / "example"
DUMMY = ROOT / "models" / "dummy.yaml"


def run(run_dir, trials, manifest, **kwargs):
    options = {"timeout_s": 10, "max_attempts": 2, "max_consecutive_failures": 3, "log": lambda _: None}
    return asyncio.run(execute(run_dir, trials, manifest, **{**options, **kwargs}))


def test_order_is_shuffled_but_reproducible():
    bundle, models = load_study(EXAMPLE), load_models(DUMMY)
    first = [t.key for t in plan_trials(bundle, models, repetitions=3, seed=1)]
    again = [t.key for t in plan_trials(bundle, models, repetitions=3, seed=1)]
    other = [t.key for t in plan_trials(bundle, models, repetitions=3, seed=2)]
    assert first == again != other
    assert len(set(first)) == 3 * 2 * 3


def test_run_resume_status_and_export(tmp_path):
    run_dir, trials, manifest = new_run(EXAMPLE, DUMMY, runs_root=tmp_path, repetitions=2)
    assert (run_dir.study_snapshot / "prompts" / "user.md").exists()
    summary = run(run_dir, trials, manifest)
    assert (summary.ok, summary.failed, summary.remaining) == (12, 0, 0)

    record = next(run_dir.records())
    assert record["status"] == "ok" and record["prompt"]["user"] and record["response"]["text"]

    # Simulate a crash that lost the last 5 trials: resume runs exactly those.
    lines = run_dir.trials_path.read_text(encoding="utf-8").splitlines()
    run_dir.trials_path.write_text("\n".join(lines[:-5]) + "\n", encoding="utf-8")
    run_dir, trials, manifest = load_run(run_dir.path)
    summary = run(run_dir, trials, manifest)
    assert (summary.already_done, summary.ok, summary.remaining) == (7, 5, 0)
    assert len(json.loads(run_dir.manifest_path.read_text())["sessions"]) == 2

    status = summarize(run_dir.path)
    assert {m: s["ok"] for m, s in status["models"].items()} == {"dummy-a": 6, "dummy-b": 6}

    blind, key = export_run(run_dir.path, blind=True)
    assert "dummy-" not in blind.read_text(encoding="utf-8-sig")
    assert len(key.read_text(encoding="utf-8-sig").splitlines()) == 1 + 12  # header + one row per item
    with pytest.raises(FileExistsError):
        export_run(run_dir.path, blind=True)

    markdown = export_run(run_dir.path, fmt="md")[0].read_text(encoding="utf-8")
    assert "# ex-001" in markdown and "dummy-a #1" in markdown


def test_failures_are_recorded_and_the_lane_stops(tmp_path):
    models = tmp_path / "models.yaml"
    models.write_text(
        "models:\n  - id: broken\n    provider: dummy\n    model: x\n    params: {fail_if_contains: '## Situation'}\n",
        encoding="utf-8",
    )
    run_dir, trials, manifest = new_run(EXAMPLE, models, runs_root=tmp_path / "runs", repetitions=2)
    summary = run(run_dir, trials, manifest)
    assert summary.failed == 3 and summary.ok == 0
    assert "broken" in summary.stopped_lanes
    errors = [r for r in run_dir.records() if r["status"] == "error"]
    assert len(errors) == 3 and "dummy failure" in errors[0]["error"]["message"]


def test_resume_refuses_a_modified_snapshot(tmp_path):
    run_dir, _, _ = new_run(EXAMPLE, DUMMY, runs_root=tmp_path, repetitions=1)
    (run_dir.study_snapshot / "prompts" / "user.md").write_text("changed {{situation}} {{tools}}")
    with pytest.raises(ConfigError, match="modified"):
        load_run(run_dir.path)


FAST = {"margin_s": 0, "min_wait_s": 0, "default_wait_s": 0}


def one_dummy(tmp_path, params: str) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(f"models:\n  - id: m\n    provider: dummy\n    model: x\n    params: {params}\n", encoding="utf-8")
    return path


def test_usage_limit_pauses_then_retries_the_same_trial(tmp_path):
    run_dir, trials, manifest = new_run(
        EXAMPLE, one_dummy(tmp_path, "{usage_limit_on_call: 2}"), runs_root=tmp_path, repetitions=1
    )
    summary = run(run_dir, trials, manifest, limits=LimitPolicy(**FAST))
    assert (summary.ok, summary.failed, summary.remaining) == (3, 0, 0)
    assert [r["status"] for r in run_dir.records()] == ["ok"] * 3  # the limit is not a trial outcome
    pauses = json.loads(run_dir.manifest_path.read_text())["sessions"][0]["pauses"]
    assert [p["reason"] for p in pauses] == ["usage limit reached"]


def test_usage_limit_can_stop_the_lane_instead(tmp_path):
    run_dir, trials, manifest = new_run(
        EXAMPLE, one_dummy(tmp_path, "{usage_limit_on_call: 2}"), runs_root=tmp_path, repetitions=1
    )
    summary = run(run_dir, trials, manifest, limits=LimitPolicy(on_limit="stop", **FAST))
    assert (summary.ok, summary.failed, summary.remaining) == (1, 0, 2)
    assert summary.stopped_lanes == {"m": "usage limit reached"}


def test_quota_cap_pauses_before_the_real_limit(tmp_path):
    run_dir, trials, manifest = new_run(
        EXAMPLE, one_dummy(tmp_path, "{quota_used: 0.85}"), runs_root=tmp_path, repetitions=1
    )
    summary = run(run_dir, trials, manifest, limits=LimitPolicy(quota_cap=0.8, **FAST))
    assert summary.ok == 3
    pauses = json.loads(run_dir.manifest_path.read_text())["sessions"][0]["pauses"]
    assert len(pauses) == 2  # after trials 1 and 2, not after the last one
    assert pauses[0]["reason"] == "five_hour quota at 85% (cap 80%)"
