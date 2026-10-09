import pytest

from lark.context import make_context


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("LARK_WRITER", "test")
    return make_context(tmp_path, fake_llm=True, dry_run=True, offline=True)
