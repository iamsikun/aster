import pytest
import yaml

from aster.config import ConfigError, deep_merge, load_config


def test_defaults_without_a_config_file(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml", tmp_path / "data")
    assert list(cfg.fields) == ["economics", "machine-learning", "quant-finance", "marketing"]
    assert all(f.tier == "core" for f in cfg.fields.values())
    assert cfg.get("sources.arxiv.enabled") is True
    assert "jmlr" not in cfg.journals and "mksc" in cfg.journals


def test_user_config_merges_and_removes(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "fields": {
                    "marketing": {"tier": "skim"},
                    "economics": None,
                    "biology": {"arxiv": ["q-bio"], "journals": ["nature"]},
                },
                "journals": [{"id": "nature", "name": "Nature (override)", "issn": ["0028-0836"]}],
                "sources": {"github": {"enabled": False}},
            }
        )
    )
    cfg = load_config(path, tmp_path / "data")
    assert "economics" not in cfg.fields
    assert cfg.fields["marketing"].tier == "skim" and cfg.fields["marketing"].journals  # keys merged
    assert cfg.fields["biology"].arxiv == ["q-bio"]
    assert cfg.journals["nature"].name == "Nature (override)"
    assert cfg.get("sources.github.enabled") is False and cfg.get("sources.arxiv.enabled") is True


def test_empty_sections_in_the_template_mean_unset(tmp_path):
    from aster.config import ASSETS

    path = tmp_path / "config.yaml"
    path.write_text((ASSETS / "config.example.yaml").read_text())
    cfg = load_config(path, tmp_path / "data")
    assert len(cfg.fields) == 4 and cfg.get("sources.nep.enabled") is True and cfg.interests == []


def test_unknown_journal_is_an_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"fields": {"economics": {"journals": ["nope"]}}}))
    with pytest.raises(ConfigError, match="nope"):
        load_config(path, tmp_path / "data")


def test_feeds_combine_field_and_user_feeds(ctx):
    feeds = {f.name: f for f in ctx.config.feeds()}
    assert feeds["JMLR"].kind == "paper" and feeds["JMLR"].fields == ["machine-learning"]
    assert feeds["Example Research"].fields == ["machine-learning"]


def test_deep_merge():
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 3, "c": None}}) == {"a": {"b": 3}}


def test_setup_is_needed_until_fields_are_chosen(tmp_path):
    from aster.config import ASSETS

    missing = load_config(tmp_path / "missing.yaml", tmp_path / "data")
    assert missing.setup == {"needed": True, "reason": "no config file", "config": str(tmp_path / "missing.yaml")}
    path = tmp_path / "config.yaml"
    path.write_text((ASSETS / "config.example.yaml").read_text())  # `fields:` is empty
    template = load_config(path, tmp_path / "data")
    assert template.setup_needed and template.setup["reason"] == "no fields in the config file"
    assert len(template.active_fields()) == 4  # the defaults still apply meanwhile
    path.write_text(yaml.safe_dump({"fields": {"economics": {"tier": "core"}}}))
    assert not load_config(path, tmp_path / "data").setup_needed
