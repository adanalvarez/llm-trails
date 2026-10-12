"""Study, scenario and model definitions (YAML) with strict validation.

A *study* is the fixed set of stimuli: a system prompt, a user prompt template and a
directory of scenarios. A *model spec* is one experimental condition: a model reached
through a specific provider with specific settings. Both are plain YAML so they can be
reviewed, versioned and shared.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# Safe for file names, CSV cells and trial keys.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@:+-]*$"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

ProviderName = Literal["claude_agent_sdk", "codex_sdk", "openrouter", "openai_compatible", "dummy"]


class ConfigError(Exception):
    """Invalid study, scenario or model configuration."""


class _Strict(BaseModel):
    # Unknown keys are errors: a typo must never silently change an experiment.
    model_config = ConfigDict(extra="forbid")


class Study(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    title: str
    version: str = "0.1.0"
    description: str = ""
    system_prompt_file: str | None = None
    user_prompt_file: str
    scenarios_dir: str = "scenarios"
    repetitions: int = Field(default=5, ge=1)
    seed: int = 0
    timeout_s: float = Field(default=600, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)


class Scenario(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    inputs: dict[str, str]
    # Never sent to models: context for researchers and reference material for evaluators.
    notes: str = ""
    reference: dict[str, Any] = Field(default_factory=dict)


class ModelSpec(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    provider: ProviderName
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    base_url: str | None = None
    api_key_env: str | None = None
    max_concurrency: int = Field(default=1, ge=1)
    notes: str = ""

    @model_validator(mode="after")
    def _endpoint_fields_only_for_http(self) -> ModelSpec:
        http = self.provider in ("openrouter", "openai_compatible")
        if not http and (self.base_url or self.api_key_env):
            raise ValueError("base_url/api_key_env only apply to openrouter/openai_compatible")
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError("openai_compatible requires base_url")
        return self


@dataclass(frozen=True)
class Prompt:
    """The exact text sent to a model for one scenario."""

    system: str | None
    user: str

    @property
    def sha256(self) -> str:
        payload = json.dumps({"system": self.system, "user": self.user}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class StudyBundle:
    """A loaded study: settings, prompt templates and scenarios."""

    root: Path
    study: Study
    system_template: str | None
    user_template: str
    scenarios: list[Scenario]
    files: list[Path]  # every input file, relative to root (for snapshots and hashing)

    def scenario(self, scenario_id: str) -> Scenario:
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        raise ConfigError(f"unknown scenario id: {scenario_id}")

    def render(self, scenario: Scenario) -> Prompt:
        values = {key: value.strip("\n") for key, value in scenario.inputs.items()}
        where = f"scenario {scenario.id}"
        used: set[str] = set()
        system = None
        if self.system_template is not None:
            system = render_template(self.system_template, values, where=where, used=used)
        user = render_template(self.user_template, values, where=where, used=used)
        unused = sorted(set(values) - used)
        if unused:
            raise ConfigError(f"{where}: inputs not used by any prompt template: {unused}")
        return Prompt(system=system, user=user)

    def fingerprint(self) -> str:
        """Hash of all input files (line endings normalised), stable across OSes."""
        digest = hashlib.sha256()
        for rel in sorted(self.files, key=lambda p: p.as_posix()):
            digest.update(rel.as_posix().encode() + b"\0")
            digest.update(_read_text(self.root / rel).encode() + b"\0")
        return digest.hexdigest()


def render_template(template: str, values: dict[str, str], *, where: str, used: set[str]) -> str:
    """Fill ``{{name}}`` placeholders. Missing values are errors, other text is left as-is."""
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.add(key)
            return match.group(0)
        used.add(key)
        return values[key]

    rendered = PLACEHOLDER.sub(replace, template)
    if missing:
        raise ConfigError(f"{where}: missing inputs for placeholders: {sorted(missing)}")
    return rendered


def load_study(study_dir: str | Path) -> StudyBundle:
    root = Path(study_dir).resolve()
    study_file = root / "study.yaml"
    if not study_file.is_file():
        raise ConfigError(f"no study.yaml in {root}")
    study = _validate(Study, _load_yaml(study_file), study_file)

    files = [Path("study.yaml")]
    system_template = None
    if study.system_prompt_file:
        system_template = _read_text(_inside(root, study.system_prompt_file)).strip()
        files.append(Path(study.system_prompt_file))
    user_template = _read_text(_inside(root, study.user_prompt_file)).strip()
    files.append(Path(study.user_prompt_file))

    scenario_dir = _inside(root, study.scenarios_dir)
    if not scenario_dir.is_dir():
        raise ConfigError(f"scenarios directory not found: {scenario_dir}")
    scenarios: list[Scenario] = []
    seen: dict[str, Path] = {}
    # Files starting with "_" are drafts/templates and are skipped.
    paths = sorted(p for p in scenario_dir.iterdir() if p.suffix in (".yaml", ".yml"))
    for path in paths:
        if path.name.startswith("_"):
            continue
        scenario = _validate(Scenario, _load_yaml(path), path)
        if scenario.id in seen:
            raise ConfigError(f"duplicate scenario id {scenario.id!r} in {path} and {seen[scenario.id]}")
        seen[scenario.id] = path
        scenarios.append(scenario)
        files.append(path.relative_to(root))
    if not scenarios:
        raise ConfigError(f"no scenarios found in {scenario_dir}")

    bundle = StudyBundle(root, study, system_template, user_template, scenarios, files)
    for scenario in scenarios:  # fail fast on any template/input mismatch
        bundle.render(scenario)
    return bundle


def load_models(path: str | Path) -> list[ModelSpec]:
    path = Path(path)
    data = _load_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise ConfigError(f"{path}: expected a top-level 'models:' list")
    models = [_validate(ModelSpec, item, path) for item in data["models"]]
    ids = [m.id for m in models]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ConfigError(f"{path}: duplicate model ids: {duplicates}")
    if not models:
        raise ConfigError(f"{path}: no models defined")
    return models


def _validate(cls: type[BaseModel], data: Any, path: Path) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping")
    try:
        return cls.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}:\n{exc}") from exc


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc


def _read_text(path: Path) -> str:
    # Universal newlines: CRLF checkouts on Windows produce the same text and hashes.
    return path.read_text(encoding="utf-8")


def _inside(root: Path, rel: str) -> Path:
    """Resolve a path from study.yaml and require it to stay inside the study folder."""
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise ConfigError(f"{rel}: study files must live inside the study folder")
    return path
