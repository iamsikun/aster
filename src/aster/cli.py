"""The `aster` command. Every subcommand is deterministic: it fetches, stores and ranks, and
never runs a model. The agent reads the JSON output and does the judging."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from aster import __version__, paths
from aster.catalog import find_work, link_signals, resolve
from aster.config import ASSETS, ConfigError, load_config
from aster.context import Context, open_context
from aster.findings import add_findings
from aster.ids import parse_ref
from aster.rank import candidate_json, candidates, signal_json, work_date
from aster.records import Checkpoint, Shown, Signal, SourceHealth, SourceRecord, Version, Work
from aster.sources import COLLECTORS, collect

CANDIDATES_SCHEMA = "aster.candidates/1"


def _print_json(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _context(args, **kw) -> Context:
    return open_context(args.config, args.data_dir, **kw)


def _latest_health(ctx: Context) -> dict[str, SourceHealth]:
    out: dict[str, SourceHealth] = {}
    for h in ctx.store.all(SourceHealth):
        out[h.source] = h
    return out


def _find(ctx: Context, ref: str) -> Work | None:
    ref = ref.strip()
    if ref.startswith("w_"):
        return ctx.store.get(Work, ref)
    ids = parse_ref(ref)
    return find_work(ctx.store, ids) if ids else None


# ---------------- commands ----------------
def cmd_init(args) -> int:
    path = Path(args.config).expanduser() if args.config else paths.config_file()
    if path.exists() and not args.force:
        print(f"{path} already exists; edit it, or pass --force to replace it with the template.")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((ASSETS / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_paths(args) -> int:
    cfg = load_config(args.config, args.data_dir)
    info = {
        "config": str(cfg.path),
        "config_exists": cfg.path.exists(),
        "data_dir": str(cfg.data_dir),
        "data_exists": cfg.data_dir.exists(),
    }
    if args.json:
        _print_json(info)
    else:
        print(f"config  {info['config']}{'' if info['config_exists'] else '  (not created; run `aster init`)'}")
        print(f"data    {info['data_dir']}{'' if info['data_exists'] else '  (created on first refresh)'}")
    return 0


def _field_info(cfg) -> list[dict]:
    return [
        {
            "id": f.id,
            "name": f.label,
            "tier": f.tier,
            "arxiv": f.arxiv,
            "journals": [{"id": j, "name": cfg.journals[j].name} for j in f.journals],
            "nep": f.nep,
            "nber": f.nber,
            "openreview": f.openreview,
            "feeds": [feed.model_dump() for feed in f.feeds],
            "hf_orgs": f.hf_orgs,
            "keywords": f.keywords,
            "search": f.search.model_dump(),
        }
        for f in cfg.fields.values()
    ]


def cmd_fields(args) -> int:
    cfg = load_config(args.config, args.data_dir)
    info = _field_info(cfg)
    if args.json:
        _print_json({"fields": info, "interests": cfg.interests, "follows": cfg.follows.model_dump()})
        return 0
    for f in info:
        print(f"{f['id']} ({f['name']}): {f['tier']}")
        print(
            f"  arXiv {len(f['arxiv'])}, journals {len(f['journals'])}, NEP {len(f['nep'])}, "
            f"feeds {len(f['feeds'])}, HF orgs {len(f['hf_orgs'])}, keywords {len(f['keywords'])}"
            f"{', NBER' if f['nber'] else ''}"
        )
    return 0


def cmd_refresh(args) -> int:
    ctx = _context(args, offline=args.offline)
    if args.field:
        unknown = [f for f in args.field if f not in ctx.config.fields]
        if unknown:
            print(f"unknown field(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        for fid, f in ctx.config.fields.items():
            if fid not in args.field:
                ctx.config.fields[fid] = f.model_copy(update={"tier": "mute"})
    try:
        results = collect(ctx, only=args.source or None, limit=args.limit)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    records: list[SourceRecord] = [r for res in results for r in res.records]
    stats = resolve(ctx.store, records)
    linked = link_signals(ctx.store)
    summary = {
        "sources": [
            {
                "source": r.source,
                "status": r.status,
                "records": len(r.records),
                "signals": len(r.signals),
                "truncated": r.truncated,
                "error": r.error,
            }
            for r in results
        ],
        "new_works": len(stats.new_work_ids),
        "updated_works": len(stats.updated_work_ids),
        "merge_proposals": len(stats.proposals),
        "signals_linked": linked,
    }
    if args.json:
        _print_json(summary)
    else:
        for s in summary["sources"]:
            note = f"  {s['error']}" if s["error"] else ""
            print(f"{s['source']:<12} {s['status']:<8} {s['records']:>5} records{note}")
        print(f"{summary['new_works']} new works, {summary['updated_works']} updated")
    return 1 if results and all(r.status == "failed" for r in results) else 0


def cmd_candidates(args) -> int:
    ctx = _context(args)
    window = int(args.days or ctx.config.get("rank.window_days", 7))
    since = args.since or (date.fromisoformat(ctx.run_date) - timedelta(days=window)).isoformat()
    limit = args.limit or int(ctx.config.get("rank.limit", 40))
    try:
        found = candidates(
            ctx, since, until=args.until, field_id=args.field, limit=limit, include_shown=args.include_shown
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    health = _latest_health(ctx)
    if args.json:
        _print_json(
            {
                "schema": CANDIDATES_SCHEMA,
                "generated_at": ctx.run_date,
                "window": {"since": since, "until": args.until},
                "field": args.field,
                "fields": {f.id: {"name": f.label, "tier": f.tier} for f in ctx.config.fields.values()},
                "sources": [
                    {
                        "source": h.source,
                        "status": h.status,
                        "error": h.error,
                        "newest_item": h.newest_item_date,
                        "checked_at": h.run_at,
                    }
                    for h in health.values()
                ],
                "items": [candidate_json(c) for c in found],
            }
        )
        return 0
    if not found:
        print(f"No candidates since {since}." + ("" if health else " Run `aster refresh` first."))
    for i, c in enumerate(found, 1):
        w = c.work
        url = w.urls.get("landing") or next(iter(w.urls.values()), "")
        print(f"{i:>2}. [{c.score:.2f}] {w.title}")
        print(f"    {w.venue or w.kind}, {work_date(w)} · {', '.join(c.fields) or '-'} · {w.work_id}  {url}")
    return 0


def cmd_add(args) -> int:
    ctx = _context(args)
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
        result = add_findings(ctx, payload)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"invalid findings: {e}", file=sys.stderr)
        return 2
    _print_json(result)
    return 0


def cmd_lookup(args) -> int:
    ctx = _context(args)
    work = _find(ctx, args.ref)
    if work is None:
        print(f"not in the catalog: {args.ref}", file=sys.stderr)
        return 1
    sources = []
    for key in work.source_keys:
        rec = ctx.store.get(SourceRecord, key)
        if rec:
            sources.append(
                {
                    "source": rec.source,
                    "source_id": rec.source_id,
                    "venue": rec.venue,
                    "status": rec.status,
                    "url": rec.url,
                    "date": rec.released_date or rec.published_date,
                    "fetched_at": rec.fetched_at,
                }
            )
    versions = ctx.store.all(Version, "json_extract(json, '$.work_id') = ?", (work.work_id,))
    signals = ctx.store.all(Signal, "json_extract(json, '$.work_id') = ?", (work.work_id,))
    shown = ctx.store.get(Shown, work.work_id)
    _print_json(
        {
            "work": work.model_dump(mode="json"),
            "sources": sources,
            "versions": [v.model_dump(mode="json", exclude={"work_id"}) for v in versions],
            "signals": [signal_json(s) for s in signals],
            "shown": shown.model_dump(mode="json") if shown else None,
        }
    )
    return 0


def cmd_seen(args) -> int:
    ctx = _context(args)
    marked, missing = [], []
    for ref in args.refs:
        work = _find(ctx, ref)
        if work is None:
            missing.append(ref)
        else:
            marked.append(Shown(work_id=work.work_id, context=args.context))
    ctx.store.append_many(marked)
    print(f"marked {len(marked)} as shown" + (f"; not found: {', '.join(missing)}" if missing else ""))
    return 1 if missing else 0


def cmd_doctor(args) -> int:
    problems = []
    try:
        cfg = load_config(args.config, args.data_dir)
    except ConfigError as e:
        print(f"config: {e}")
        return 1
    print(f"config  {cfg.path}{'' if cfg.path.exists() else '  (not created; defaults in use)'}")
    print(f"data    {cfg.data_dir}")
    active = cfg.active_fields()
    print(f"fields  {', '.join(f'{f.id} ({f.tier})' for f in cfg.fields.values()) or 'none'}")
    if not active:
        problems.append("no active fields: set a field's tier to core or skim")
    env = {
        "ASTER_CONTACT_EMAIL": "polite pools of Crossref and OpenAlex",
        "OPENALEX_API_KEY": "higher OpenAlex limits",
        "OPENREVIEW_USERNAME": "OpenReview venues",
        "GITHUB_TOKEN": "higher GitHub limits",
    }
    for name, why in env.items():
        print(f"env     {name}: {'set' if os.getenv(name) else 'not set'} ({why})")
    if cfg.data_dir.exists():
        ctx = open_context(args.config, args.data_dir)
        health = _latest_health(ctx)
        for name in COLLECTORS:
            enabled = cfg.get(f"sources.{name}.enabled", False)
            h = health.get(name)
            cp = ctx.store.get(Checkpoint, name)
            state = f"{h.status} at {h.run_at}" + (f" ({h.error})" if h.error else "") if h else "never run"
            last_ok = f", last success {cp.last_success}" if cp and cp.last_success else ""
            print(f"source  {name:<12} {'on ' if enabled else 'off'} {state}{last_ok}")
            if enabled and h and h.status == "failed":
                problems.append(f"{name} failed on its last run")
        print(
            f"catalog {ctx.store.count(Work)} works, {ctx.store.count(Signal)} signals, {ctx.store.count(Shown)} shown"
        )
    else:
        print("catalog empty (run `aster refresh`)")
    for p in problems:
        print(f"problem {p}")
    return 1 if problems else 0


# ---------------- parser ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aster", description=__doc__)
    p.add_argument("--version", action="version", version=f"aster {__version__}")
    p.add_argument("--config", help="config file (default: $ASTER_CONFIG or ~/.config/aster/config.yaml)")
    p.add_argument("--data-dir", help="data folder (default: $ASTER_DATA_DIR or ~/.local/share/aster-data)")
    p.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="write a commented config file")
    s.add_argument("--force", action="store_true", help="replace an existing config file")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("paths", help="show where the config and data live")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_paths)

    s = sub.add_parser("fields", help="list fields and what each follows")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_fields)

    s = sub.add_parser("refresh", help="collect new items from the configured sources")
    s.add_argument("--source", action="append", choices=list(COLLECTORS), help="only this source (repeatable)")
    s.add_argument("--field", action="append", help="only sources of this field (repeatable)")
    s.add_argument("--limit", type=int, help="keep at most this many records per source")
    s.add_argument("--offline", action="store_true", help="read fixtures from $ASTER_FIXTURES_DIR (testing)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_refresh)

    s = sub.add_parser("candidates", help="rank recent items for the agent to review")
    s.add_argument("--since", help="YYYY-MM-DD (default: the last rank.window_days days)")
    s.add_argument("--days", type=int, help="window length in days, instead of --since")
    s.add_argument("--until", help="YYYY-MM-DD")
    s.add_argument("--field", help="only this field")
    s.add_argument("--limit", type=int)
    s.add_argument("--include-shown", action="store_true", help="include items already shown")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_candidates)

    s = sub.add_parser("add", help="record items and signals found by web search (JSON)")
    s.add_argument("file", nargs="?", default="-", help="JSON file, or - for stdin (default)")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("lookup", help="everything known about one item")
    s.add_argument("ref", help="work id, DOI, arXiv id, prefixed id (ssrn:..., nber:...) or URL")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("seen", help="mark items as shown so later briefs skip them")
    s.add_argument("refs", nargs="+", help="work ids or other references")
    s.add_argument("--context", default=None, help="where it was shown, e.g. brief")
    s.set_defaults(func=cmd_seen)

    s = sub.add_parser("doctor", help="check configuration and the last run of each source")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return int(args.func(args) or 0)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
