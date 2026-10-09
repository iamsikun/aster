import re
from pathlib import Path

import pytest
import yaml

from lark.profiles import load_profiles

ROOT = Path(__file__).parents[1] / "templates" / "data-repo"
WF = ROOT / ".github" / "workflows"


def _load(p: Path):
    return yaml.safe_load(p.read_text())


def test_all_yaml_loads():
    files = [p for p in ROOT.rglob("*") if p.suffix in (".yaml", ".yml")]
    assert len(files) >= 15
    for p in files:
        _load(p)


def test_profiles_validate():
    profiles = load_profiles(ROOT / "profiles")
    assert len(profiles) == 7
    assert {p.bucket for p in profiles} == {"active", "methods"}
    assert sum(p.bucket == "active" for p in profiles) == 6
    for p in profiles:
        assert len(p.questions) >= 2 and len(p.keywords) >= 8 and p.recognize


def test_journals_registry():
    journals = _load(ROOT / "journals.yaml")["journals"]
    ids = [j["id"] for j in journals]
    assert len(ids) == len(set(ids)) >= 40
    issn = re.compile(r"^\d{4}-\d{3}[\dX]$")
    for j in journals:
        assert j["field"] in {"economics", "business", "statistics", "science"}
        found = [j.get("issn_print"), j.get("issn_electronic"), *j.get("issn_other", [])]
        assert any(found), j["id"]
        assert all(issn.match(i) for i in found if i), j["id"]
    assert sum(j["field"] == "business" for j in journals) == 24  # UTD 24


def _triggers(name):
    wf = _load(WF / name)
    return wf.get("on", wf.get(True)), wf  # PyYAML parses a bare `on:` key as True


@pytest.mark.parametrize("name,expected", [
    ("daily.yml", {"schedule", "workflow_dispatch"}),
    ("weekly.yml", {"schedule", "workflow_dispatch"}),
    ("deep-read.yml", {"workflow_dispatch"}),
    ("feedback.yml", {"issues", "workflow_dispatch"}),
    ("gpu-drain.yml", {"schedule", "workflow_dispatch"}),
])
def test_workflow_triggers(name, expected):
    on, wf = _triggers(name)
    assert set(on) == expected
    assert wf["concurrency"]["cancel-in-progress"] is False
    for job in wf["jobs"].values():
        assert job["timeout-minutes"] <= 180


def test_schedules_and_runner():
    assert _triggers("daily.yml")[0]["schedule"][0]["cron"] == "15 11 * * *"
    assert _triggers("weekly.yml")[0]["schedule"][0]["cron"] == "0 13 * * 0"
    gpu = _triggers("gpu-drain.yml")[1]
    assert gpu["jobs"]["drain"]["runs-on"] == ["self-hosted", "gpu"]
    assert gpu["jobs"]["drain"]["env"]["LARK_WRITER"] == "gpu"
    assert gpu["concurrency"]["group"] != "lark-state"  # an offline runner must not block daily
    assert _triggers("deep-read.yml")[0]["workflow_dispatch"]["inputs"]["target"]["required"] is True


def test_no_anthropic_api_key_and_no_inline_inputs():
    for p in WF.glob("*.yml"):
        text = p.read_text()
        assert "ANTHROPIC_API_KEY:" not in text, p.name
        # user-controlled inputs must reach scripts through env, not inline interpolation
        for block in re.findall(r"run: \|\n((?:\s{10,}.*\n)+)", text):
            assert "inputs.target" not in block, p.name
