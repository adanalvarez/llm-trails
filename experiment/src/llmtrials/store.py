"""Run folders: an input snapshot, a manifest and an append-only JSONL of trials.

runs/<study_id>/<run_id>/
  manifest.json      what was planned, with which code, models and environment
  inputs/study/      exact copy of the study files used (resume reads from here)
  inputs/models.yaml exact copy of the models file used
  trials.jsonl       one JSON object per attempt-group (a trial may appear again after resume)
  exports/           review files produced by `llmtrials export`
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import StudyBundle

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RunDir:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    @property
    def trials_path(self) -> Path:
        return self.path / "trials.jsonl"

    @property
    def study_snapshot(self) -> Path:
        return self.path / "inputs" / "study"

    @property
    def models_snapshot(self) -> Path:
        return self.path / "inputs" / "models.yaml"

    @classmethod
    def create(cls, runs_root: Path, study_id: str, label: str | None) -> RunDir:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if label:
            run_id += f"-{label}"
        path = Path(runs_root) / study_id / run_id
        path.mkdir(parents=True, exist_ok=False)
        return cls(path)

    def snapshot_inputs(self, bundle: StudyBundle, models_file: Path) -> None:
        # The whole study folder (protocol and notes travel with the data); the inputs hash
        # covers only the files that reach the models.
        shutil.copytree(bundle.root, self.study_snapshot, ignore=shutil.ignore_patterns(".*", "__pycache__"))
        shutil.copy2(models_file, self.models_snapshot)

    def read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"not a run folder (no manifest.json): {self.path}")
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self.trials_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def records(self) -> Iterator[dict[str, Any]]:
        if not self.trials_path.exists():
            return
        with self.trials_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    def latest_records(self) -> dict[str, dict[str, Any]]:
        """Latest record per trial key; a later success replaces an earlier error."""
        latest: dict[str, dict[str, Any]] = {}
        for record in self.records():
            previous = latest.get(record["trial_key"])
            if previous is None or record["status"] == "ok" or previous["status"] != "ok":
                latest[record["trial_key"]] = record
        return latest

    def completed_keys(self) -> set[str]:
        return {key for key, record in self.latest_records().items() if record["status"] == "ok"}
