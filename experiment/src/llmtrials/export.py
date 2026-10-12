"""Summaries and review exports of a run (CSV for spreadsheets, Markdown for reading)."""

from __future__ import annotations

import csv
import hashlib
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import load_study
from .runner import load_run
from .store import RunDir

# Rater columns left empty in blind exports; adapt them to the study's rubric.
RATING_COLUMNS = ["rating", "rater", "rater_notes"]


def summarize(run_path: str | Path) -> dict[str, Any]:
    """Per-model progress and totals for `llmtrials status`."""
    run_dir, trials, manifest = load_run(run_path)
    latest = run_dir.latest_records()
    per_model: dict[str, dict[str, Any]] = {}
    for model_id in manifest["plan"]["model_ids"]:
        per_model[model_id] = {
            "planned": 0,
            "ok": 0,
            "error": 0,
            "pending": 0,
            "tool_calls": 0,
            "latency_s": [],
            "cost_usd": 0.0,
            "contexts": set(),
            "quota": None,
        }
    for trial in trials:
        stats = per_model[trial.model.id]
        stats["planned"] += 1
        record = latest.get(trial.key)
        if record is None:
            stats["pending"] += 1
            continue
        stats[record["status"]] += 1
        if record["status"] == "ok":
            response = record["response"]
            stats["latency_s"].append(record["latency_s"])
            stats["cost_usd"] += response.get("cost_usd") or 0.0
            stats["tool_calls"] += 1 if response.get("tool_calls") else 0
            meta = record.get("meta") or {}
            if (meta.get("harness_context") or {}).get("sha256"):
                stats["contexts"].add(meta["harness_context"]["sha256"])
            if meta.get("quota") and record["finished_at"] >= (stats["quota"] or {}).get("at", ""):
                stats["quota"] = {"at": record["finished_at"], **meta["quota"]}
    for stats in per_model.values():
        # Distinct variants of the context a harness added around the prompt (None: no harness).
        stats["harness_contexts"] = len(stats.pop("contexts")) or None
        latencies = sorted(stats.pop("latency_s"))
        stats["median_latency_s"] = latencies[len(latencies) // 2] if latencies else None
        stats["cost_usd"] = round(stats["cost_usd"], 4)
    return {"run_id": manifest["run_id"], "study": manifest["study"], "models": per_model}


def export_run(run_path: str | Path, *, fmt: str = "csv", blind: bool = False, force: bool = False) -> list[Path]:
    run_dir = RunDir(run_path)
    manifest = run_dir.read_manifest()
    bundle = load_study(run_dir.study_snapshot)
    titles = {s.id: s.title for s in bundle.scenarios}
    records = sorted(
        run_dir.latest_records().values(),
        key=lambda r: (r["scenario_id"], r["model_id"], r["repetition"]),
    )
    out_dir = run_dir.path / "exports"
    out_dir.mkdir(exist_ok=True)

    rows = [_row(r, titles) for r in records]
    if not blind:
        for i, row in enumerate(rows, 1):
            row["item_id"] = f"T{i:04d}"
        path = out_dir / f"trials.{fmt}"
        _write(path, rows, fmt, bundle, label=lambda row: f"{row['model_id']} #{row['repetition']}")
        return [path]

    key_path = out_dir / "blind_key.csv"
    if key_path.exists() and not force:
        raise FileExistsError(
            f"{key_path} exists; raters may already be using it. Use --force to regenerate the mapping."
        )
    rows = [row for row in rows if row["status"] == "ok"]
    seed = int(hashlib.sha256(manifest["run_id"].encode()).hexdigest()[:8], 16)
    random.Random(seed).shuffle(rows)
    key_rows, blind_rows = [], []
    for i, row in enumerate(rows, 1):
        item_id = f"R{i:04d}"
        key_rows.append(
            {
                "item_id": item_id,
                "trial_key": row["trial_key"],
                "model_id": row["model_id"],
                "repetition": row["repetition"],
            }
        )
        blind_rows.append(
            {
                "item_id": item_id,
                "scenario_id": row["scenario_id"],
                "scenario_title": row["scenario_title"],
                "response": row["response"],
                **{column: "" for column in RATING_COLUMNS},
            }
        )
    path = out_dir / f"blind.{fmt}"
    _write(path, blind_rows, fmt, bundle, label=lambda row: row["item_id"])
    _write_csv(key_path, key_rows)
    return [path, key_path]


def _row(record: dict[str, Any], titles: dict[str, str]) -> dict[str, Any]:
    response = record.get("response") or {}
    return {
        "item_id": "",
        "trial_key": record["trial_key"],
        "scenario_id": record["scenario_id"],
        "scenario_title": titles.get(record["scenario_id"], ""),
        "model_id": record["model_id"],
        "repetition": record["repetition"],
        "status": record["status"],
        "response": response.get("text", ""),
        "reasoning": response.get("reasoning") or "",
        "finish_reason": response.get("finish_reason") or "",
        "tool_calls": response.get("tool_calls", ""),
        "latency_s": record.get("latency_s"),
        "cost_usd": response.get("cost_usd"),
        "error": (record.get("error") or {}).get("message", ""),
    }


def _write(path: Path, rows: list[dict[str, Any]], fmt: str, bundle, *, label) -> None:
    if fmt == "csv":
        _write_csv(path, rows)
    elif fmt == "md":
        _write_markdown(path, rows, bundle, label=label)
    else:
        raise ValueError(f"unknown format {fmt!r}")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM: Excel opens UTF-8 correctly
        if not rows:
            return
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, rows: list[dict[str, Any]], bundle, *, label) -> None:
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scenario[row["scenario_id"]].append(row)
    lines: list[str] = []
    for scenario in bundle.scenarios:
        group = sorted(by_scenario.get(scenario.id, []), key=lambda r: r["item_id"])
        if not group:
            continue
        prompt = bundle.render(scenario)
        lines += [f"# {scenario.id}: {scenario.title}", "", "<details><summary>Prompt</summary>", ""]
        if prompt.system:
            lines += ["**System**", "", _fence(prompt.system), ""]
        lines += ["**User**", "", _fence(prompt.user), "", "</details>", ""]
        for row in group:
            body = row["response"] if row.get("status", "ok") == "ok" else f"ERROR: {row.get('error', '')}"
            lines += [f"## {label(row)}", "", _fence(body), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"
