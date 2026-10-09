"""Lark command line."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from dotenv import load_dotenv

from lark.logging_config import configure_logging

app = typer.Typer(help="Lark: a research briefing pipeline.", no_args_is_help=True)
state_app = typer.Typer(help="Inspect or rebuild local state.")
contract_app = typer.Typer(help="Report contract utilities.")
eval_app = typer.Typer(help="Evaluation: screening recall, reader comparison, coverage, cost.")
feedback_app = typer.Typer(help="User feedback from the data repo's issues.")
app.add_typer(state_app, name="state")
app.add_typer(contract_app, name="contract")
app.add_typer(eval_app, name="eval")
app.add_typer(feedback_app, name="feedback")

DataDir = typer.Option(None, "--data-dir", "-d", envvar="LARK_DATA_DIR", help="Path to the lark-data checkout.")
DryRun = typer.Option(False, "--dry-run", help="Don't send anything; write emails to <data-dir>/out/.")
FakeLLM = typer.Option(False, "--fake-llm", help="Route every model call to the offline fake worker.")
Offline = typer.Option(False, "--offline", help="Collectors read fixtures instead of the network.")


@app.callback()
def _setup() -> None:
    load_dotenv()
    configure_logging()


def _ctx(data_dir, dry_run=False, fake_llm=False, offline=False, rebuild=False):
    from lark.context import make_context

    return make_context(data_dir, dry_run=dry_run, fake_llm=fake_llm, offline=offline, rebuild=rebuild)


def _print_report(report, out: Path | None) -> None:
    from lark.report import render

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2))
    typer.echo(render(report, "text"))


@app.command()
def daily(
    data_dir: Path = DataDir,
    dry_run: bool = DryRun,
    fake_llm: bool = FakeLLM,
    offline: bool = Offline,
    source: list[str] = typer.Option(None, "--source", "-s", help="Only these sources (repeatable)."),
    limit: int = typer.Option(None, help="Cap records per source (testing)."),
    skip_read: bool = typer.Option(False, help="Stop after screening (abstract-level briefing)."),
    no_deliver: bool = typer.Option(False, help="Build the report but don't deliver it."),
    out: Path = typer.Option(None, help="Also write report.json here."),
) -> None:
    """Run the full daily pipeline and deliver the briefing."""
    from lark.pipelines.daily import run_daily

    ctx = _ctx(data_dir, dry_run, fake_llm, offline)
    report = run_daily(ctx, sources=source or None, collect_limit=limit, skip_read=skip_read, skip_deliver=no_deliver)
    _print_report(report, out)


@app.command()
def weekly(
    data_dir: Path = DataDir, dry_run: bool = DryRun, fake_llm: bool = FakeLLM, no_deliver: bool = False
) -> None:
    """Weekly synthesis across the week's readings."""
    from lark.pipelines.weekly import run_weekly

    _print_report(run_weekly(_ctx(data_dir, dry_run, fake_llm), skip_deliver=no_deliver), None)


@app.command()
def deep(
    target: str = typer.Argument(..., help="arXiv id/URL, DOI, or paper URL."),
    depth: str = typer.Option("full", help="full | technical"),
    data_dir: Path = DataDir,
    dry_run: bool = DryRun,
    fake_llm: bool = FakeLLM,
    no_deliver: bool = False,
) -> None:
    """Deep-read one paper on request and deliver it as a report."""
    from lark.pipelines.deep import run_deep

    _print_report(run_deep(_ctx(data_dir, dry_run, fake_llm), target, depth=depth, skip_deliver=no_deliver), None)


@app.command()
def collect(
    data_dir: Path = DataDir,
    offline: bool = Offline,
    source: list[str] = typer.Option(None, "--source", "-s"),
    limit: int = typer.Option(None),
) -> None:
    """Collect and catalog only (no model calls)."""
    from lark.pipelines.daily import collect_and_catalog

    records, stats, health = collect_and_catalog(_ctx(data_dir, offline=offline), source or None, limit)
    for h in health:
        typer.echo(f"{h.source:14} {h.status:8} {h.n_records:5d}  {h.error or ''}")
    typer.echo(f"records={len(records)} new_works={len(stats.new_work_ids)} updated={len(stats.updated_work_ids)}")


@app.command()
def drain(
    tag: str = typer.Option("gpu"),
    data_dir: Path = DataDir,
    fake_llm: bool = FakeLLM,
    limit: int = 200,
    max_minutes: float = typer.Option(100, help="Stop starting new jobs after this long."),
) -> None:
    """Drain backlog jobs on the GPU workstation runner."""
    from lark.pipelines.drain import run_drain

    out = run_drain(_ctx(data_dir, fake_llm=fake_llm), tag=tag, limit=limit, max_minutes=max_minutes)
    typer.echo(json.dumps(out, indent=2))


@feedback_app.command("sync")
def feedback_sync(data_dir: Path = DataDir) -> None:
    """Import feedback issues from the data repo."""
    from lark.feedback.issues import sync

    typer.echo(f"Synced {sync(_ctx(data_dir))} feedback item(s)")


@app.command()
def doctor(data_dir: Path = DataDir, live: bool = typer.Option(False, help="Make one tiny call per route.")) -> None:
    """Check configuration, credentials, and model routes."""
    from lark.llm import roles
    from lark.report.channels import get_channels

    ctx = _ctx(data_dir)
    typer.echo(f"data dir: {ctx.data_dir}  profiles: {len(ctx.config.profiles)} (version {ctx.config.profiles_version})")
    for r in roles.doctor(ctx, live=live):
        typer.echo(f"route   {r['route']:14} {'ok ' if r['ok'] else 'BAD'}  {r.get('note') or ''}")
    for ch in get_channels(ctx):
        ok, why = ch.configured(ctx)
        typer.echo(f"channel {ch.name:14} {'ok ' if ok else 'BAD'}  {why}")


def _template_dir() -> Path:
    packaged = Path(__file__).with_name("_templates") / "data-repo"
    return packaged if packaged.exists() else Path(__file__).parents[1] / "templates" / "data-repo"


@app.command("init-data")
def init_data(target: Path = typer.Argument(..., help="Directory for the new lark-data repo.")) -> None:
    """Create a lark-data directory from the template (profiles, journals, workflows)."""
    src = _template_dir()
    if target.exists() and any(target.iterdir()):
        raise typer.BadParameter(f"{target} exists and is not empty")
    shutil.copytree(src, target, dirs_exist_ok=True)
    typer.echo(f"Created {target} from {src}. Next: edit profiles/*.yaml, then see docs/setup.md.")


@app.command()
def render(
    report_json: Path = typer.Argument(...),
    fmt: str = typer.Option("html", help="markdown | html | text"),
    out: Path = typer.Option(None),
) -> None:
    """Render a report.json (preview a channel's output)."""
    from lark.report import render as _render
    from lark.report.contract import Report

    text = _render(Report.model_validate_json(report_json.read_text()), fmt)
    if out:
        out.write_text(text)
    else:
        typer.echo(text)


@state_app.command("rebuild")
def state_rebuild(data_dir: Path = DataDir) -> None:
    """Rebuild the SQLite index from the JSONL logs."""
    ctx = _ctx(data_dir, rebuild=True)
    typer.echo(f"Indexed {ctx.store.count_events()} events from {ctx.store.log_dir}")


@contract_app.command("export")
def contract_export(out: Path = typer.Option(Path("docs/report-contract/v1.schema.json"))) -> None:
    """Write the Report JSON Schema."""
    from lark.report.contract import json_schema

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(json_schema(), indent=2) + "\n")
    typer.echo(f"Wrote {out}")


@eval_app.command("screening")
def eval_screening(labels: Path = typer.Argument(None), data_dir: Path = DataDir) -> None:
    """Screening recall on a labeled pool (default: <data-dir>/eval/labels.jsonl)."""
    from lark.eval import screening_recall

    ctx = _ctx(data_dir)
    typer.echo(json.dumps(screening_recall(ctx, labels or ctx.data_dir / "eval" / "labels.jsonl"), indent=2))


@eval_app.command("reader")
def eval_reader(
    work_id: list[str] = typer.Option(None, "--work", "-w"),
    n: int = typer.Option(5, help="If no --work given, use the n most recent read works."),
    route: list[str] = typer.Option(["claude_haiku", "codex_luna"], "--route", "-r"),
    data_dir: Path = DataDir,
) -> None:
    """Run the same papers through several readers and write a side-by-side comparison."""
    from lark.eval import compare_readers
    from lark.records import ReadingNote

    ctx = _ctx(data_dir)
    ids = work_id or list(dict.fromkeys(n_.work_id for n_ in reversed(ctx.store.all(ReadingNote))))[:n]
    typer.echo(f"Wrote {compare_readers(ctx, ids, route)}")


@eval_app.command("cost")
def eval_cost(data_dir: Path = DataDir) -> None:
    """Cost per useful briefed item, from the ledger and feedback."""
    from lark.eval import cost_per_useful

    typer.echo(json.dumps(cost_per_useful(_ctx(data_dir)), indent=2))


@eval_app.command("coverage")
def eval_coverage(reference: Path = typer.Argument(...), data_dir: Path = DataDir) -> None:
    """Share of an independent reference list present in the catalog."""
    from lark.eval import coverage_audit

    typer.echo(json.dumps(coverage_audit(_ctx(data_dir), reference), indent=2))


if __name__ == "__main__":
    app()
