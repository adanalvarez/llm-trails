from pathlib import Path

import pytest

from llmtrials.config import ConfigError, ModelSpec, load_models, load_study, render_template
from llmtrials.providers import create_provider

ROOT = Path(__file__).resolve().parents[1]


def write_study(root: Path, *, study_extra: str = "", scenarios: dict[str, str] | None = None) -> Path:
    (root / "scenarios").mkdir(parents=True)
    (root / "study.yaml").write_text(
        f"id: t\ntitle: test\nuser_prompt_file: user.md\nrepetitions: 2\n{study_extra}", encoding="utf-8"
    )
    (root / "user.md").write_text("Situation: {{situation}}\n", encoding="utf-8")
    scenarios = scenarios or {"s1.yaml": "id: s1\ninputs:\n  situation: hello\n"}
    for name, text in scenarios.items():
        (root / "scenarios" / name).write_text(text, encoding="utf-8")
    return root


def test_example_study_renders_every_scenario():
    bundle = load_study(ROOT / "studies" / "example")
    assert [s.id for s in bundle.scenarios] == ["ex-001", "ex-002", "ex-003"]
    prompt = bundle.render(bundle.scenarios[0])
    assert "{{" not in prompt.user and "web-01" in prompt.user
    assert prompt.system and prompt.system.startswith("You are an AI agent")


def test_shipped_model_files_are_valid():
    for path in sorted((ROOT / "models").glob("*.yaml")):
        assert load_models(path), path


def test_placeholders_leave_other_text_alone():
    used: set[str] = set()
    out = render_template('{"cmd": "$HOME/{x}", "v": "{{a}}"}', {"a": "1"}, where="t", used=used)
    assert out == '{"cmd": "$HOME/{x}", "v": "1"}' and used == {"a"}
    with pytest.raises(ConfigError, match="missing"):
        render_template("{{a}} {{b}}", {"a": "1"}, where="t", used=set())


def test_unused_input_is_an_error(tmp_path):
    write_study(tmp_path, scenarios={"s1.yaml": "id: s1\ninputs:\n  situation: x\n  typo: y\n"})
    with pytest.raises(ConfigError, match="not used"):
        load_study(tmp_path)


def test_unknown_keys_are_errors(tmp_path):
    write_study(tmp_path, study_extra="repetiton: 3\n")
    with pytest.raises(ConfigError, match="repetiton"):
        load_study(tmp_path)


def test_duplicate_scenario_ids(tmp_path):
    scenario = "id: s1\ninputs:\n  situation: x\n"
    write_study(tmp_path, scenarios={"a.yaml": scenario, "b.yaml": scenario})
    with pytest.raises(ConfigError, match="duplicate"):
        load_study(tmp_path)


def test_drafts_starting_with_underscore_are_skipped(tmp_path):
    write_study(tmp_path, scenarios={"s1.yaml": "id: s1\ninputs:\n  situation: x\n", "_draft.yaml": "id: d\n"})
    assert [s.id for s in load_study(tmp_path).scenarios] == ["s1"]


def test_files_outside_the_study_are_rejected(tmp_path):
    study = write_study(tmp_path / "study")
    (study / "study.yaml").write_text("id: t\ntitle: t\nuser_prompt_file: ../elsewhere.md\n")
    with pytest.raises(ConfigError, match="inside the study folder"):
        load_study(study)


def test_fingerprint_ignores_line_endings(tmp_path):
    lf = write_study(tmp_path / "lf")
    crlf = write_study(tmp_path / "crlf")
    for path in crlf.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert load_study(lf).fingerprint() == load_study(crlf).fingerprint()


def test_harness_controlled_params_cannot_be_overridden():
    spec = ModelSpec(id="c", provider="claude_agent_sdk", model="claude-x", params={"tools": ["Bash"]})
    with pytest.raises(ValueError, match="controlled by llmtrials"):
        create_provider(spec)


def test_endpoint_fields_only_for_http_providers():
    with pytest.raises(ValueError):
        ModelSpec(id="c", provider="dummy", model="d", base_url="http://x")
    with pytest.raises(ValueError):
        ModelSpec(id="c", provider="openai_compatible", model="d")
