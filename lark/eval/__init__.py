"""Evaluation: separate discovery, screening, reading, and spending failures.

- screening_recall: did screening keep the works the user labeled relevant?
- compare_readers: the same reading under several routes, with automatic metrics.
- cost_per_useful: spending against items the user marked useful.
- coverage_audit: did the collectors find works from an independent reference list?
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from lark.context import RunContext
from lark.records import Briefed, Feedback, LedgerEntry, Screening, Work
from lark.utils.text import normalize_arxiv_id, normalize_doi, title_key
from lark.utils.timez import now_utc

RETAINED_ACTIONS = {"focused_read", "full_read", "abstract_only"}


def _read_jsonl(path: str | Path) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def _work_index(ctx: RunContext) -> tuple[dict[str, Work], dict[str, Work]]:
    by_id: dict[str, Work] = {}
    by_title: dict[str, Work] = {}
    for w in ctx.store.all(Work):
        by_title.setdefault(title_key(w.title), w)
        for k, v in w.ids.items():
            by_id[f"{k}:{v.lower()}"] = w
        by_id[w.work_id] = w
    return by_id, by_title


def _canon_ref(ref: str) -> str | None:
    """'doi:10.1/x', 'https://doi.org/..', 'arxiv:2410.1v2', '2410.01234' -> 'doi:..' / 'arxiv:..'."""
    r = ref.strip()
    if not r:
        return None
    if r.startswith("w_"):
        return r
    low = r.lower()
    if low.startswith("doi:") or "doi.org/" in low or low.startswith("10."):
        return f"doi:{normalize_doi(r)}"
    if low.startswith("arxiv:") or "arxiv.org/" in low or low[:4].isdigit():
        base, _ = normalize_arxiv_id(r)
        return f"arxiv:{base}"
    if ":" in r:
        k, v = r.split(":", 1)
        return f"{k.lower()}:{v.lower()}"
    return None


def _resolve(row: dict, by_id: dict[str, Work], by_title: dict[str, Work]) -> Work | None:
    for key in ("work_id", "id", "doi", "arxiv"):
        if row.get(key):
            ref = row[key] if key in ("work_id", "id") else f"{key}:{row[key]}"
            if (c := _canon_ref(ref)) and c in by_id:
                return by_id[c]
    if row.get("title"):
        return by_title.get(title_key(row["title"]))
    return None


# ---------------------------------------------------------------- screening


def _latest_screenings(ctx: RunContext) -> dict[str, Screening]:
    latest: dict[str, Screening] = {}
    for s in ctx.store.all(Screening):  # ordered by ts; last wins
        latest[s.work_id] = s
    return latest


def is_retained(s: Screening) -> bool:
    return s.next_action in RETAINED_ACTIONS or s.priority in ("high", "medium")


def screening_recall(ctx: RunContext, labels_path: str | Path) -> dict:
    """Labels: jsonl rows {work_id|doi|arxiv|title, relevant: bool, profile?}.

    `retrieval_miss` = relevant works in the catalog that never reached LLM screening;
    `not_in_catalog` = labeled works the collectors never found (a discovery failure).
    """
    labels = _read_jsonl(labels_path)
    by_id, by_title = _work_index(ctx)
    screenings = _latest_screenings(ctx)
    n_rel = tp = fp = 0
    retrieval_miss, not_in_catalog, screened_out = [], [], []
    per_profile: dict[str, Counter] = defaultdict(Counter)
    for row in labels:
        w = _resolve(row, by_id, by_title)
        relevant = bool(row.get("relevant"))
        label = row.get("title") or row.get("work_id") or row.get("doi") or row.get("arxiv")
        if w is None:
            if relevant:
                not_in_catalog.append(label)
            continue
        s = screenings.get(w.work_id)
        kept = bool(s and is_retained(s))
        prof = row.get("profile") or "_all"
        if relevant:
            n_rel += 1
            per_profile[prof]["relevant"] += 1
            if kept:
                tp += 1
                per_profile[prof]["kept"] += 1
            elif s is None:
                retrieval_miss.append(w.title)
            else:
                screened_out.append(w.title)
        elif kept:
            fp += 1
    return {
        "n_labels": len(labels),
        "n_relevant_in_catalog": n_rel,
        "recall": tp / n_rel if n_rel else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "retrieval_miss": retrieval_miss,
        "screened_out": screened_out,
        "not_in_catalog": not_in_catalog,
        "per_profile": {
            p: {"relevant": c["relevant"], "kept": c["kept"], "recall": c["kept"] / c["relevant"] if c["relevant"] else None}
            for p, c in per_profile.items()
        },
    }


# ---------------------------------------------------------------- readers


def _note_metrics(note, elapsed: float) -> dict:
    v = note.verification or {}
    usage = (note.llm.usage if note.llm else {}) or {}
    nq = v.get("n_quotes", 0)
    return {
        "schema_valid": True,
        "depth": note.depth,
        "n_claims": v.get("n_claims", len(note.claims)),
        "verified_quote_rate": (v.get("n_verified_quotes", 0) / nq) if nq else None,
        "unsupported_claims": len(v.get("unsupported_claims", [])),
        "unsupported_numbers": len(v.get("unsupported_numbers", [])),
        "n_conditions": len(note.conditions),
        "n_escalate": len(note.escalate),
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "latency_s": (note.llm.latency_s if note.llm and note.llm.latency_s is not None else round(elapsed, 2)),
        "model": note.llm.model if note.llm else None,
    }


def compare_readers(ctx: RunContext, work_ids: list[str], routes: list[str], depth: str = "focused") -> Path:
    """Run the same reading under each route (not persisted as readings); write JSON + Markdown side by side."""
    from lark.read.reader import read_work

    results: list[dict] = []
    for wid in work_ids:
        work = ctx.store.get(Work, wid)
        row: dict = {"work_id": wid, "title": work.title if work else None, "routes": {}}
        for route in routes:
            t0 = time.monotonic()
            try:
                note = read_work(ctx, wid, depth, route=route, persist=False)
            except Exception as e:  # noqa: BLE001 - record the failure as a result
                row["routes"][route] = {"schema_valid": False, "error": f"{type(e).__name__}: {e}"}
                continue
            if note is None:
                row["routes"][route] = {"schema_valid": False, "error": "unknown work"}
                continue
            row["routes"][route] = {
                **_note_metrics(note, time.monotonic() - t0),
                "note": note.model_dump(mode="json", exclude={"llm"}),
            }
        results.append(row)

    summary = {}
    for route in routes:
        ms = [r["routes"].get(route, {}) for r in results]
        ok = [m for m in ms if m.get("schema_valid")]
        rates = [m["verified_quote_rate"] for m in ok if m.get("verified_quote_rate") is not None]
        lat = [m["latency_s"] for m in ok if m.get("latency_s") is not None]
        summary[route] = {
            "n": len(ms),
            "schema_valid_rate": len(ok) / len(ms) if ms else None,
            "mean_verified_quote_rate": statistics.mean(rates) if rates else None,
            "unsupported_numbers": sum(m.get("unsupported_numbers", 0) for m in ok),
            "unsupported_claims": sum(m.get("unsupported_claims", 0) for m in ok),
            "mean_latency_s": statistics.mean(lat) if lat else None,
            "tokens_out": sum(m.get("tokens_out") or 0 for m in ok),
        }

    out_dir = ctx.data_dir / "eval" / "runs" / ctx.run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%H%M%S")
    payload = {"depth": depth, "routes": routes, "summary": summary, "results": results}
    (out_dir / f"readers-{stamp}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    md_path = out_dir / f"readers-{stamp}.md"
    md_path.write_text(_readers_markdown(payload), encoding="utf-8")
    return md_path


def _fmt(v) -> str:
    if v is None:
        return "–"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def _readers_markdown(p: dict) -> str:
    routes = p["routes"]
    lines = [f"# Reader comparison ({p['depth']})", "", "| metric | " + " | ".join(routes) + " |",
             "|---|" + "---|" * len(routes)]
    keys = ["schema_valid_rate", "mean_verified_quote_rate", "unsupported_claims", "unsupported_numbers",
            "mean_latency_s", "tokens_out"]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(_fmt(p["summary"][r].get(k)) for r in routes) + " |")
    for row in p["results"]:
        lines += ["", f"## {row['title'] or row['work_id']}", ""]
        for r in routes:
            m = row["routes"].get(r, {})
            lines.append(f"### {r}")
            if not m.get("schema_valid"):
                lines += [f"Failed: {m.get('error')}", ""]
                continue
            n = m["note"]
            lines += [
                f"- quotes verified: {_fmt(m['verified_quote_rate'])}; unsupported numbers: {m['unsupported_numbers']}",
                f"- **Idea:** {n.get('idea') or '–'}",
                f"- **Main result:** {n.get('main_result') or '–'}",
                f"- **Conditions:** {'; '.join(n.get('conditions') or []) or '–'}",
            ]
            for c in n.get("claims", []):
                mark = "✓" if c.get("verified") else "✗"
                lines.append(f"  - {mark} [{c['kind']}] {c['text']}")
            lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- spending


def cost_per_useful(ctx: RunContext, since: str | None = None) -> dict:
    """Join ledger spending/calls with briefed items and 'useful' feedback."""
    where, params = ("ts >= ?", (since,)) if since else ("", ())
    ledger = ctx.store.all(LedgerEntry, where, params)
    cash = sum(e.cash_usd for e in ledger if e.event == "settle")
    calls_by_route: Counter = Counter()
    for e in ledger:
        if e.event == "settle":
            calls_by_route[e.route] += e.calls or 1
    briefed = {b.work_id for b in ctx.store.all(Briefed) if not since or b.at >= since}
    useful = {f.work_id for f in ctx.store.all(Feedback) if f.label == "useful" and f.work_id and (not since or f.at >= since)}
    useful_briefed = useful & briefed if briefed else useful
    n_useful = len(useful_briefed)
    calls = sum(calls_by_route.values())
    return {
        "since": since,
        "cash_usd": round(cash, 4),
        "calls": calls,
        "calls_by_route": dict(calls_by_route),
        "briefed": len(briefed),
        "useful": n_useful,
        "useful_rate": n_useful / len(briefed) if briefed else None,
        "cost_per_useful_usd": round(cash / n_useful, 4) if n_useful else None,
        "calls_per_useful": calls / n_useful if n_useful else None,
    }


# ---------------------------------------------------------------- coverage


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=None)


def coverage_audit(ctx: RunContext, reference_path: str | Path) -> dict:
    """Reference: jsonl rows {id|doi|arxiv|title, date?, source?} or a plain list of ids, one per line.
    Must come from outside Lark's collectors (e.g. a journal's own TOC) to reveal discovery gaps."""
    path = Path(reference_path)
    text = path.read_text(encoding="utf-8")
    rows = _read_jsonl(path) if text.lstrip().startswith("{") else [{"id": ln.strip()} for ln in text.splitlines() if ln.strip()]
    by_id, by_title = _work_index(ctx)
    found, missing, lags = 0, [], []
    per_source: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        w = _resolve(row, by_id, by_title)
        src = row.get("source", "_all")
        per_source[src]["n"] += 1
        if w is None:
            missing.append(row.get("id") or row.get("doi") or row.get("arxiv") or row.get("title"))
            continue
        found += 1
        per_source[src]["found"] += 1
        ref_date, seen = _parse_date(row.get("date")), _parse_date(w.first_seen)
        if ref_date and seen:
            lags.append((seen - ref_date).total_seconds() / 86400)
    return {
        "n_reference": len(rows),
        "n_found": found,
        "coverage": found / len(rows) if rows else None,
        "median_lag_days": round(statistics.median(lags), 2) if lags else None,
        "max_lag_days": round(max(lags), 2) if lags else None,
        "missing": missing,
        "per_source": {s: {"n": c["n"], "found": c["found"], "coverage": c["found"] / c["n"]} for s, c in per_source.items()},
    }
