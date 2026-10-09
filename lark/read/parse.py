"""Turn a fetched Document into sections with location anchors.

HTML: heading-based parsing that understands arXiv LaTeXML (math via alttext).
PDF: GROBID when LARK_GROBID_URL is set, Docling when LARK_USE_DOCLING=1 and
installed (GPU runner), otherwise PyMuPDF with a heading heuristic.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import statistics
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import requests

from lark.context import RunContext
from lark.read.fetch import Document, fetch_fulltext
from lark.records import Work

log = logging.getLogger(__name__)


@dataclass
class Section:
    title: str
    text: str
    page_start: int | None = None  # 1-based PDF page index
    level: int = 1

    @property
    def label(self) -> str:
        return f"{self.title} (p. {self.page_start})" if self.page_start else self.title


@dataclass
class Parsed:
    work_id: str
    source_url: str
    kind: str
    sha256: str
    parser: str
    sections: list[Section] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(f"{s.title}\n{s.text}" for s in self.sections)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Parsed:
        secs = [Section(**s) for s in d.pop("sections", [])]
        return cls(**d, sections=secs)


# ---------------------------------------------------------------- HTML

_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "button", "noscript", "svg", "head"}
_BLOCK_TAGS = {"p", "div", "li", "br", "tr", "table", "blockquote", "figcaption", "dd", "dt", "section", "pre"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _SectionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = []
        self._title: str = "Front matter"
        self._level = 1
        self._buf: list[str] = []
        self._heading: list[str] | None = None
        self._heading_level = 1
        self._skip = 0
        self._math = 0

    def _flush(self) -> None:
        text = _clean("".join(self._buf))
        if text:
            self.sections.append(Section(self._title, text, None, self._level))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if self._skip:
            if tag in _SKIP_TAGS:
                self._skip += 1
            return
        if tag in _SKIP_TAGS:
            self._skip = 1
            return
        if self._math:
            if tag == "math":
                self._math += 1
            return
        if tag == "math":
            alt = dict(attrs).get("alttext")
            if alt:
                self._emit(f" ${alt}$ ")
                self._math = 1
            return
        if tag in _HEADINGS:
            self._flush()
            self._heading = []
            self._heading_level = int(tag[1])
        elif tag in _BLOCK_TAGS:
            self._emit("\n")
        elif tag in ("td", "th"):
            self._emit(" | ")

    def handle_endtag(self, tag):
        if self._skip:
            if tag in _SKIP_TAGS:
                self._skip -= 1
            return
        if self._math:
            if tag == "math":
                self._math -= 1
            return
        if tag in _HEADINGS and self._heading is not None:
            title = _clean(" ".join(self._heading)).replace("\n", " ")
            self._heading = None
            if title:
                self._title, self._level = title, self._heading_level
        elif tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data):
        if self._skip or self._math:
            return
        self._emit(data)

    def _emit(self, s: str) -> None:
        if self._heading is not None:
            self._heading.append(s)
        else:
            self._buf.append(s)

    def close(self):
        super().close()
        self._flush()


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_html(html: str) -> list[Section]:
    p = _SectionParser()
    p.feed(html)
    p.close()
    return [s for s in p.sections if len(s.text) > 1]


# ---------------------------------------------------------------- PDF: PyMuPDF

_KNOWN_HEADINGS = re.compile(
    r"^(abstract|introduction|related work|background|preliminaries|model|setup|methods?|methodology|data|"
    r"results|main results|experiments?|empirical (results|analysis)|discussion|conclusions?|"
    r"references|bibliography|acknowledg(e)?ments?|appendix.*)$",
    re.I,
)
_NUMBERED = re.compile(r"^((\d{1,2}(\.\d{1,2}){0,2})\.?|[A-H](\.\d{1,2})?\.?|[IVX]{1,4}\.)\s+[A-Z][^.]{1,80}$")


def parse_pdf_pymupdf(data: bytes) -> list[Section]:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    lines: list[tuple[int, str, float, bool]] = []  # page, text, size, bold
    sizes: list[float] = []
    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                size = max(s.get("size", 0.0) for s in spans)
                bold = all((s.get("flags", 0) & 16) or "Bold" in s.get("font", "") for s in spans)
                lines.append((pno, text, size, bold))
                for s in spans:  # weight by characters so body text dominates the median
                    sizes.extend([s.get("size", 0.0)] * len(s["text"]))
    body = statistics.median(sizes) if sizes else 10.0

    sections: list[Section] = []
    title, page_start, buf = "Front matter", 1, []

    def flush() -> None:
        text = _dehyphenate("\n".join(buf))
        if text.strip():
            sections.append(Section(title, _clean(text), page_start))

    for pno, text, size, bold in lines:
        short = len(text) <= 90
        is_heading = short and (
            _KNOWN_HEADINGS.match(text.strip().rstrip(".:"))
            or (_NUMBERED.match(text) and (size >= body * 1.05 or bold))
            or (size >= body * 1.25 and len(text.split()) <= 12 and not text.endswith("."))
        )
        if is_heading:
            flush()
            title, page_start, buf = text.strip(), pno, []
        else:
            buf.append(text)
    flush()
    return sections


def _dehyphenate(text: str) -> str:
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


# ---------------------------------------------------------------- PDF: GROBID

_TEI = {"tei": "http://www.tei-c.org/ns/1.0"}


def parse_pdf_grobid(data: bytes, base_url: str, timeout: int = 120) -> list[Section]:
    r = requests.post(
        base_url.rstrip("/") + "/api/processFulltextDocument",
        files={"input": ("paper.pdf", data, "application/pdf")},
        data={"teiCoordinates": ["head", "s"], "segmentSentences": "1"},
        timeout=timeout,
    )
    r.raise_for_status()
    return parse_tei(r.text)


def _page_of(el: ET.Element) -> int | None:
    for node in el.iter():
        coords = node.get("coords")
        if coords:
            try:
                return int(coords.split(";")[0].split(",")[0])
            except ValueError:
                return None
    return None


def _text_of(el: ET.Element) -> str:
    return _clean(" ".join("".join(el.itertext()).split()))


def parse_tei(tei: str) -> list[Section]:
    root = ET.fromstring(tei.encode() if isinstance(tei, str) else tei)
    sections: list[Section] = []
    abstract = root.find(".//tei:profileDesc/tei:abstract", _TEI)
    if abstract is not None and (txt := _text_of(abstract)):
        sections.append(Section("Abstract", txt, _page_of(abstract) or 1))
    body = root.find(".//tei:text/tei:body", _TEI)
    back = root.find(".//tei:text/tei:back", _TEI)
    for container, default in ((body, "Body"), (back, "Appendix")):
        if container is None:
            continue
        for div in container.iter(f"{{{_TEI['tei']}}}div"):
            head = div.find("tei:head", _TEI)
            title = _text_of(head) if head is not None else default
            if head is not None and head.get("n"):
                title = f"{head.get('n')} {title}"
            paras = [_text_of(p) for p in div.findall("tei:p", _TEI) + div.findall("tei:formula", _TEI)]
            text = "\n".join(p for p in paras if p)
            if text:
                sections.append(Section(title, text, _page_of(div)))
    return sections


# ---------------------------------------------------------------- PDF: Docling (optional)


def parse_pdf_docling(path: Path) -> list[Section]:
    from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]

    md = DocumentConverter().convert(str(path)).document.export_to_markdown()
    sections: list[Section] = []
    title, buf = "Front matter", []
    for line in md.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if (t := _clean("\n".join(buf))):
                sections.append(Section(title, t))
            title, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    if (t := _clean("\n".join(buf))):
        sections.append(Section(title, t))
    return sections


# ---------------------------------------------------------------- dispatch + cache


def parse(doc: Document) -> Parsed:
    data = doc.read_bytes()
    parser, sections = "", []
    if doc.kind == "html":
        parser, sections = "html", parse_html(data.decode("utf-8", errors="replace"))
    elif doc.kind == "text":
        parser, sections = "text", [Section("Text", _clean(data.decode("utf-8", errors="replace")))]
    else:
        if url := os.getenv("LARK_GROBID_URL"):
            try:
                parser, sections = "grobid", parse_pdf_grobid(data, url)
            except Exception as e:  # noqa: BLE001
                log.warning("GROBID failed for %s: %s", doc.work_id, e)
        if not sections and os.getenv("LARK_USE_DOCLING") == "1":
            try:
                parser, sections = "docling", parse_pdf_docling(doc.path)
            except Exception as e:  # noqa: BLE001
                log.warning("Docling failed for %s: %s", doc.work_id, e)
        if not sections:
            parser, sections = "pymupdf", parse_pdf_pymupdf(data)
    return Parsed(doc.work_id, doc.source_url, doc.kind, doc.sha256, parser, sections)


def fulltext_dir(ctx: RunContext, work_id: str) -> Path:
    return ctx.data_dir / "cache" / "fulltext" / work_id


def save_parsed(ctx: RunContext, parsed: Parsed) -> Path:
    folder = fulltext_dir(ctx, parsed.work_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{parsed.sha256[:12]}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(parsed.to_dict(), f, ensure_ascii=False)
    return path


def load_cached(ctx: RunContext, work_id: str) -> Parsed | None:
    folder = fulltext_dir(ctx, work_id)
    if not folder.exists():
        return None
    files = sorted(folder.glob("*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return Parsed.from_dict(json.load(f))
        except (OSError, ValueError, TypeError) as e:
            log.warning("bad fulltext cache %s: %s", path, e)
    return None


def get_parsed(ctx: RunContext, work: Work) -> Parsed | None:
    """Cached parse if present, else fetch + parse + cache. None if no full text is obtainable."""
    if cached := load_cached(ctx, work.work_id):
        return cached
    doc = fetch_fulltext(ctx, work)
    if doc is None:
        return None
    try:
        parsed = parse(doc)
    except Exception as e:  # noqa: BLE001
        log.warning("parse failed for %s: %s", work.work_id, e)
        return None
    if not parsed.sections:
        return None
    save_parsed(ctx, parsed)
    return parsed
