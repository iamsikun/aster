"""Lark command line."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import load_dotenv

from lark.logging_config import configure_logging

app = typer.Typer(help="Lark: a research briefing pipeline.", no_args_is_help=True)
state_app = typer.Typer(help="Inspect or rebuild local state.")
contract_app = typer.Typer(help="Report contract utilities.")
app.add_typer(state_app, name="state")
app.add_typer(contract_app, name="contract")

DataDir = typer.Option(None, "--data-dir", "-d", envvar="LARK_DATA_DIR", help="Path to the lark-data checkout.")


@app.callback()
def _setup() -> None:
    load_dotenv()
    configure_logging()


@state_app.command("rebuild")
def state_rebuild(data_dir: Path = DataDir) -> None:
    """Rebuild the SQLite index from the JSONL logs."""
    from lark.context import make_context

    ctx = make_context(data_dir, rebuild=True)
    typer.echo(f"Indexed {ctx.store.count_events()} events from {ctx.store.log_dir}")


@contract_app.command("export")
def contract_export(out: Path = typer.Option(Path("docs/report-contract/v1.schema.json"))) -> None:
    """Write the Report JSON Schema."""
    from lark.report.contract import json_schema

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(json_schema(), indent=2) + "\n")
    typer.echo(f"Wrote {out}")


if __name__ == "__main__":
    app()
