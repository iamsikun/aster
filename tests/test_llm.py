import json
import subprocess
from pathlib import Path

import pytest

from lark import budget
from lark.context import make_context
from lark.llm import roles
from lark.llm.base import LLMRequest, LLMResult
from lark.llm.claude_code import ClaudeCodeWorker
from lark.llm.codex import CodexWorker
from lark.llm.fake import FakeWorker
from lark.llm.openai_compat import OpenAICompatWorker
from lark.llm.schema import strictify, synthesize, validate
from lark.records import LedgerEntry

FIX = Path(__file__).parent / "fixtures" / "llm"
SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "string", "enum": ["high", "low"]},
        "score": {"type": "number"},
        "flag": {"type": "boolean"},
        "note": {"type": ["string", "null"]},
        "nested": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
    },
    "required": ["topics", "priority", "nested"],
}


@pytest.fixture(autouse=True)
def _reset():
    roles.reset_workers()
    FakeWorker.handlers.clear()
    yield
    roles.reset_workers()
    FakeWorker.handlers.clear()


@pytest.fixture
def live_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("LARK_WRITER", "test")
    (tmp_path / "lark.yaml").write_text(
        "routes:\n"
        "  a: {worker: fake, model: a-1, billing: subscription, daily_calls: 2}\n"
        "  b: {worker: fake, model: b-1, billing: subscription, daily_calls: 5}\n"
        "  paid: {worker: fake, model: p-1, billing: api_credit, price: {input_per_m: 10000, output_per_m: 10000}}\n"
        "roles:\n"
        "  r_fallback: {routes: [a, b], strategy: fallback}\n"
        "  r_alt: {routes: [a, b], strategy: alternate}\n"
        "  r_paid: {routes: [paid], strategy: fallback}\n"
    )
    return make_context(tmp_path)


def req(role="r_fallback", schema=SCHEMA):
    return LLMRequest(role=role, system="sys", prompt="hello", schema=schema, max_output_tokens=100)


# ---------- schema helpers / fake ----------
def test_synthesize_satisfies_schema():
    data = synthesize(SCHEMA)
    assert validate(data, SCHEMA) == []
    assert data["priority"] == "high" and data["nested"] == {"n": 0} and data["note"] == "fake note"


def test_validate_reports_problems():
    problems = validate({"topics": "x", "priority": "mid"}, SCHEMA)
    assert any("topics" in p for p in problems)
    assert any("priority" in p for p in problems)
    assert any("nested" in p for p in problems)
    assert validate({"a": True}, {"type": "object", "properties": {"a": {"type": "integer"}}}) != []


def test_strictify_makes_optional_nullable():
    s = strictify(SCHEMA)
    assert s["additionalProperties"] is False
    assert set(s["required"]) == set(SCHEMA["properties"])
    assert s["properties"]["score"]["type"] == ["number", "null"]
    assert s["properties"]["nested"]["additionalProperties"] is False


def test_fake_handler_and_fake_mode(ctx):
    FakeWorker.handlers["screener"] = lambda r: {"topics": ["x"], "priority": "low", "nested": {"n": 3}}
    res = roles.call(ctx, "screener", LLMRequest(role="screener", system="s", prompt="p", schema=SCHEMA))
    assert res.ok and res.data["nested"]["n"] == 3
    assert res.call.route == "fake" and res.call.provider == "fake"


# ---------- routing ----------
class Failing(FakeWorker):
    def __init__(self, route, kind):
        super().__init__(route, {"billing": "subscription"})
        self.kind = kind

    def run(self, r):
        self.calls.append(r)
        return LLMResult(ok=False, error="boom", error_kind=self.kind)


def test_fallback_on_auth_failure(live_ctx):
    bad = Failing("a", "auth")
    roles.register_worker("a", bad)
    res = roles.call(live_ctx, "r_fallback", req())
    assert res.ok and res.call.route == "b"
    events = [e.event for e in live_ctx.store.all(LedgerEntry)]
    assert "auth_failed" in events and "release" in events
    assert budget.calls_today(live_ctx).get("a", 0) == 0  # released reservation does not count


def test_unavailable_route_is_skipped(live_ctx):
    class Off(FakeWorker):
        def available(self):
            return False, "no login"

    roles.register_worker("a", Off("a"))
    assert roles.call(live_ctx, "r_fallback", req()).call.route == "b"


def test_no_route_raises(live_ctx):
    roles.register_worker("a", Failing("a", "rate_limit"))
    roles.register_worker("b", Failing("b", "other"))
    with pytest.raises(roles.NoRouteAvailable) as e:
        roles.call(live_ctx, "r_fallback", req())
    assert "a: rate_limit" in str(e.value) and "b: other" in str(e.value)


def test_alternate_splits_calls(live_ctx):
    used = [roles.call(live_ctx, "r_alt", req("r_alt")).call.route for _ in range(4)]
    assert used.count("a") == 2 and used.count("b") == 2


def test_capacity_cap_then_fallback_then_exhaustion(live_ctx):
    routes = [roles.call(live_ctx, "r_fallback", req()).call.route for _ in range(7)]
    assert routes == ["a", "a", "b", "b", "b", "b", "b"]
    with pytest.raises(roles.NoRouteAvailable, match="over budget"):
        roles.call(live_ctx, "r_fallback", req())


def test_forced_route(live_ctx):
    assert roles.call(live_ctx, "r_fallback", req(), route="b").call.route == "b"


def test_invalid_output_repaired_once(live_ctx):
    class Sloppy(FakeWorker):
        def run(self, r):
            if not self.calls:
                self.calls.append(r)
                return LLMResult(ok=True, text="not json", data=None)
            return super().run(r)

    w = Sloppy("a", {"billing": "subscription"})
    roles.register_worker("a", w)
    res = roles.call(live_ctx, "r_fallback", req())
    assert res.ok and res.call.route == "a" and len(w.calls) == 2
    assert "previous reply could not be used" in w.calls[1].prompt


# ---------- budget ----------
def test_cash_caps(live_ctx):
    cfg = roles.route_config(live_ctx, "paid")
    cfg = {**cfg, "price": None}
    assert budget.reserve(live_ctx, "paid", cfg, 0.3) is not None
    assert budget.reserve(live_ctx, "paid", cfg, 0.3) is None  # daily cap 0.50
    rid = budget.reserve(live_ctx, "paid", cfg, 0.1)
    budget.settle(live_ctx, rid, 0.05, {"input_tokens": 10})
    s = budget.summary(live_ctx)
    assert s["cash_today_usd"] == pytest.approx(0.35)
    paid_cfg = {**cfg, "billing": "api_paid"}
    assert budget.reserve(live_ctx, "paid2", paid_cfg, 0.01) is None  # paid fallback disabled at $0


def test_estimated_cost_refused_by_route(live_ctx):
    with pytest.raises(roles.NoRouteAvailable, match="over budget"):
        roles.call(live_ctx, "r_paid", req("r_paid"))  # huge price -> estimate exceeds daily cap


def test_reserve_is_thread_safe(live_ctx):
    from concurrent.futures import ThreadPoolExecutor

    cfg = {"worker": "fake", "billing": "subscription", "daily_calls": 10}
    with ThreadPoolExecutor(8) as pool:
        got = list(pool.map(lambda _: budget.reserve(live_ctx, "t", cfg, 0), range(40)))
    assert sum(1 for g in got if g) == 10


# ---------- CLI workers ----------
def test_claude_command_and_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    w = ClaudeCodeWorker("claude_haiku", {"model": "claude-haiku-5-5"})
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, (FIX / "claude_ok.json").read_text(), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = w.run(LLMRequest(role="reader", system="SYS", prompt="PROMPT", schema={"type": "object"}))
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and "--bare" not in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == {"type": "object"}
    assert seen["input"] == "PROMPT"
    assert res.ok and res.data == {"ok": True} and res.usage["output_tokens"] == 52


def test_claude_error_classification():
    w = ClaudeCodeWorker("c", {})
    out = json.dumps({"is_error": True, "result": "Invalid API key · Please run /login", "api_error_status": 401})
    assert w.parse(1, out, "", 0.1, True).error_kind == "auth"
    out = json.dumps({"is_error": True, "result": "Claude usage limit reached", "api_error_status": 429})
    assert w.parse(1, out, "", 0.1, True).error_kind == "rate_limit"


def test_codex_command(monkeypatch, tmp_path):
    w = CodexWorker("codex_luna", {"model": "gpt-5.6-luna"})
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        last = cmd[cmd.index("-o") + 1]
        Path(last).write_text('{"ok": true}')
        seen["schema"] = json.loads(Path(cmd[cmd.index("--output-schema") + 1]).read_text())
        return subprocess.CompletedProcess(cmd, 0, (FIX / "codex_ok.jsonl").read_text(), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = w.run(LLMRequest(role="reader", system="SYS", prompt="PROMPT", schema={"type": "object", "properties": {"ok": {"type": "boolean"}}}))
    cmd = seen["cmd"]
    assert cmd[:2] == ["codex", "exec"] and cmd[-1] == "-"
    for flag in ("--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json"):
        assert flag in cmd
    assert cmd[cmd.index("-s") + 1] == "read-only" and cmd[cmd.index("-m") + 1] == "gpt-5.6-luna"
    assert seen["schema"]["additionalProperties"] is False
    assert "SYS" in seen["input"] and "PROMPT" in seen["input"]
    assert res.ok and res.data == {"ok": True} and res.usage["input_tokens"] == 1200


def test_codex_unsupported_model_is_unavailable():
    w = CodexWorker("codex_luna", {"model": "gpt-6-luna"})
    res = w.parse(1, (FIX / "codex_unsupported_model.jsonl").read_text(), "", "", 1.0, True)
    assert not res.ok and res.error_kind == "unavailable" and "ChatGPT account" in res.error


def test_codex_available_checks_account_models(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/" + b)
    (tmp_path / "auth.json").write_text("{}")
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{"slug": "gpt-5.6-luna"}]}))
    assert CodexWorker("x", {"model": "gpt-5.6-luna"}).available()[0]
    ok, why = CodexWorker("x", {"model": "gpt-6-luna"}).available()
    assert not ok and "not offered" in why


def test_openai_compat_body():
    w = OpenAICompatWorker("local", {"model": "gemma4:12b", "base_url": "http://box:11434/v1"})
    body = w.body(LLMRequest(role="r", system="s", prompt="p", schema={"type": "object"}))
    assert body["model"] == "gemma4:12b" and body["response_format"]["type"] == "json_schema"
    assert body["messages"][0] == {"role": "system", "content": "s"}


def test_doctor_lists_routes(ctx):
    rows = roles.doctor(ctx)
    names = {r["route"] for r in rows}
    assert {"claude_haiku", "codex_luna", "gemini_flash", "fake"} <= names
