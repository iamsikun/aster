import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_validates():
    spec = importlib.util.spec_from_file_location("validate", ROOT / "scripts" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.validate() == []


def test_skill_mentions_only_real_commands():
    """Every `aster <command>` in the skill exists in the CLI."""
    import re

    from aster.cli import build_parser

    commands = set(build_parser()._subparsers._group_actions[0].choices)
    text = "\n".join(p.read_text() for p in (ROOT / "skills" / "aster").rglob("*.md"))
    used = set(re.findall(r"`aster ([a-z]+)", text)) | set(re.findall(r"^aster ([a-z]+)", text, re.M))
    assert used - {"add"} <= commands and "add" in commands
    assert used <= commands, used - commands
