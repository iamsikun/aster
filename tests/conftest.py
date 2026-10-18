from pathlib import Path

import pytest
import yaml

from aster.context import open_context

FIX = Path(__file__).parent / "fixtures" / "sources"

# A small configuration on top of the defaults: a statistics field (the arXiv fixture is
# stat.ME), followed people and repositories, one user feed and an OpenReview venue.
TEST_CONFIG = {
    "interests": ["selective inference", "winner's curse"],
    "follows": {"authors": ["Isaiah Andrews"], "repos": ["vllm-project/vllm"], "hf_orgs": ["google"]},
    "feeds": [
        {"name": "Example Research", "url": "https://research.example.com/feed.xml", "fields": ["machine-learning"]}
    ],
    "fields": {
        "statistics": {
            "name": "Statistics",
            "tier": "core",
            "arxiv": ["stat.ME"],
            "journals": ["jasa"],
            "keywords": ["selective inference", "conformal"],
        },
        "machine-learning": {"openreview": ["ICLR.cc/2027/Conference"]},
    },
}


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(TEST_CONFIG))
    return path


@pytest.fixture
def ctx(tmp_path, config_file, monkeypatch):
    monkeypatch.delenv("ASTER_TZ", raising=False)
    c = open_context(config_file, tmp_path / "data", offline=True, fixtures=FIX)
    c.run_date = "2026-10-09"
    return c
