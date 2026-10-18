"""arXiv: daily category announcements (rss.arxiv.org Atom) plus weekly OAI-PMH reconciliation.

The Atom feed distinguishes announce types: new, cross, replace, replace-cross. OAI-PMH
catches anything the daily feed missed (e.g. a skipped run). All requests go through the
shared limiter (arXiv asks for at most one request every 3 seconds, one connection).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

from aster.context import Context
from aster.fields import arxiv_index, fields_for_categories
from aster.ids import ids_from_doi
from aster.records import SourceRecord
from aster.sources._common import fetch_text, fixtures_dir, iso_date, run_date, split_authors
from aster.sources.base import FetchResult
from aster.utils.text import normalize_arxiv_id

log = logging.getLogger(__name__)

FEED_URL = "https://rss.arxiv.org/atom/{cats}"
OAI_URL = "https://oaipmh.arxiv.org/oai"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "ax": "http://arxiv.org/OAI/arXiv/",
}
WITHDRAWN_RE = re.compile(r"\b(this (paper|article|submission) (has been|is) withdrawn|withdrawn by the author)", re.I)


def _abs_url(base: str) -> str:
    return f"https://arxiv.org/abs/{base}"


def parse_feed(xml_text: str) -> tuple[str | None, list[SourceRecord]]:
    """Parse an rss.arxiv.org Atom feed. Returns (feed <updated>, records)."""
    root = ET.fromstring(xml_text)
    feed_updated = root.findtext("a:updated", default=None, namespaces=NS)
    out: list[SourceRecord] = []
    for e in root.findall("a:entry", NS):
        raw_id = (e.findtext("a:id", default="", namespaces=NS) or "").replace("oai:arXiv.org:", "")
        base, ver = normalize_arxiv_id(raw_id)
        if not base:
            continue
        summary = e.findtext("a:summary", default="", namespaces=NS) or ""
        m = re.search(r"Abstract:\s*(.*)", summary, flags=re.S)
        abstract = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
        announce = (e.findtext("arxiv:announce_type", default="", namespaces=NS) or "").strip() or None
        published = iso_date(e.findtext("a:published", default=None, namespaces=NS))
        cats = [c.attrib.get("term", "") for c in e.findall("a:category", NS) if c.attrib.get("term")]
        authors = []
        for c in e.findall("dc:creator", NS):
            authors += split_authors(c.text)
        title = re.sub(r"\s+", " ", e.findtext("a:title", default="", namespaces=NS) or "").strip()
        withdrawn = bool(abstract and WITHDRAWN_RE.search(abstract))
        is_new = announce in ("new", "cross")
        out.append(
            SourceRecord(
                source="arxiv",
                source_id=base,
                title=title,
                authors=authors,
                abstract=abstract,
                ids={"arxiv": base},
                venue="arXiv",
                status="withdrawn" if withdrawn else "preprint",
                url=_abs_url(base),
                pdf_url=f"https://arxiv.org/pdf/{base}{ver or ''}",
                html_url=f"https://arxiv.org/html/{base}{ver or ''}",
                released_date=published if is_new and ver in (None, "v1") else None,
                version=ver,
                announce_type=announce,
                categories=cats,
                extra={"announce_date": published},
            )
        )
    return feed_updated, out


def parse_oai(xml_text: str) -> tuple[list[SourceRecord], str | None]:
    """Parse one OAI-PMH ListRecords page (metadataPrefix=arXiv). Returns (records, resumptionToken)."""
    root = ET.fromstring(xml_text)
    out: list[SourceRecord] = []
    for rec in root.iter(f"{{{NS['oai']}}}record"):
        header = rec.find("oai:header", NS)
        if header is not None and header.attrib.get("status") == "deleted":
            continue
        meta = rec.find("oai:metadata/ax:arXiv", NS)
        if meta is None:
            continue
        base, _ = normalize_arxiv_id(meta.findtext("ax:id", default="", namespaces=NS))
        if not base:
            continue
        authors = []
        for a in meta.findall("ax:authors/ax:author", NS):
            fore = (a.findtext("ax:forenames", default="", namespaces=NS) or "").strip()
            key = (a.findtext("ax:keyname", default="", namespaces=NS) or "").strip()
            authors.append(f"{fore} {key}".strip())
        comments = meta.findtext("ax:comments", default="", namespaces=NS) or ""
        abstract = re.sub(r"\s+", " ", meta.findtext("ax:abstract", default="", namespaces=NS) or "").strip() or None
        doi = (meta.findtext("ax:doi", default="", namespaces=NS) or "").strip()
        jref = (meta.findtext("ax:journal-ref", default="", namespaces=NS) or "").strip()
        ids = {"arxiv": base}
        if doi:
            ids.update({k: v for k, v in ids_from_doi(doi).items() if k != "arxiv"})
        withdrawn = bool(WITHDRAWN_RE.search(comments) or WITHDRAWN_RE.search(abstract or ""))
        status = "withdrawn" if withdrawn else ("published" if jref else "preprint")
        out.append(
            SourceRecord(
                source="arxiv",
                source_id=base,
                title=re.sub(r"\s+", " ", meta.findtext("ax:title", default="", namespaces=NS) or "").strip(),
                authors=authors,
                abstract=abstract,
                ids=ids,
                venue=jref or "arXiv",
                status=status,
                url=_abs_url(base),
                pdf_url=f"https://arxiv.org/pdf/{base}",
                html_url=f"https://arxiv.org/html/{base}",
                released_date=iso_date(meta.findtext("ax:created", default=None, namespaces=NS)),
                categories=(meta.findtext("ax:categories", default="", namespaces=NS) or "").split(),
                extra={"comments": comments or None, "journal_ref": jref or None, "via": "oai-pmh"},
            )
        )
    token_el = root.find(".//oai:resumptionToken", NS)
    token = (token_el.text or "").strip() if token_el is not None and token_el.text else None
    return out, token or None


def oai_set(category: str) -> str:
    """'stat.ME' -> 'stat:stat:ME'; 'econ.EM' -> 'econ:econ:EM'."""
    archive, _, sub = category.partition(".")
    return f"{archive}:{archive}:{sub}" if sub else archive


class ArxivCollector:
    name = "arxiv"

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult:
        cfg = ctx.config.get("sources.arxiv", {}) or {}
        index = arxiv_index(ctx.config.active_fields())
        cats: list[str] = list(dict.fromkeys([*index, *cfg.get("extra_categories", [])]))
        if not cats:
            return FetchResult(self.name, "empty", checkpoint=checkpoint, error="no arXiv categories in active fields")
        new_cp = dict(checkpoint)
        records: list[SourceRecord] = []
        truncated = False

        # Daily announcements
        if ctx.offline:
            feeds = [p.read_text(encoding="utf-8") for p in sorted(fixtures_dir(ctx, "arxiv").glob("*.atom"))]
        else:
            url = FEED_URL.format(cats="+".join(cats))
            text = fetch_text(ctx, "arxiv", url, fixture="feed.atom")
            feeds = [text] if text else []
        feed_updated = None
        for text in feeds:
            feed_updated, recs = parse_feed(text)
            records += recs
        if feed_updated and feed_updated == checkpoint.get("feed_updated"):
            records = []  # already ingested this announcement
        if feed_updated:
            new_cp["feed_updated"] = feed_updated

        # Weekly reconciliation
        every = int(cfg.get("reconcile_every_days", 7))
        last = checkpoint.get("last_reconcile")
        # First run: start the clock instead of back-filling a week (back-fills are a separate, explicit job).
        due = bool(last) and (run_date(ctx) - date.fromisoformat(last)).days >= every
        if not last:
            new_cp["last_reconcile"] = ctx.run_date
        if due and cats:
            rec, trunc = self.reconcile(
                ctx, int(cfg.get("reconcile_days", 7)), cats, cfg.get("reconcile_max_pages", 20)
            )
            truncated = truncated or trunc
            seen = {r.source_id for r in records}
            records += [r for r in rec if r.source_id not in seen]
            new_cp["last_reconcile"] = ctx.run_date

        for r in records:
            r.fields = fields_for_categories(index, r.categories)
        new_cp["since"] = ctx.run_date
        status = "partial" if truncated else ("ok" if records else "empty")
        return FetchResult(source=self.name, status=status, records=records, checkpoint=new_cp, truncated=truncated)

    def reconcile(
        self, ctx: Context, days: int, categories: list[str], max_pages: int = 20
    ) -> tuple[list[SourceRecord], bool]:
        """Harvest the last `days` of records per category via OAI-PMH. Returns (records, truncated)."""
        frm = (run_date(ctx) - timedelta(days=days)).isoformat()
        out: list[SourceRecord] = []
        truncated = False
        for cat in categories:
            if ctx.offline:
                path = fixtures_dir(ctx, "arxiv") / f"oai_{cat}.xml"
                if path.exists():
                    out += parse_oai(path.read_text(encoding="utf-8"))[0]
                continue
            params: dict[str, str] = {
                "verb": "ListRecords",
                "metadataPrefix": "arXiv",
                "set": oai_set(cat),
                "from": frm,
            }
            pages = 0
            while True:
                text = fetch_text(ctx, "arxiv", OAI_URL, fixture="", params=params)
                if not text:
                    break
                if "noRecordsMatch" in text:
                    break
                recs, token = parse_oai(text)
                out += recs
                pages += 1
                if not token:
                    break
                if pages >= max_pages:
                    truncated = True
                    log.warning("arXiv OAI-PMH reconcile for %s truncated at %d pages", cat, pages)
                    break
                params = {"verb": "ListRecords", "resumptionToken": token}
        return out, truncated
