"""SSRN research-alert emails, read over IMAP from a Gmail label.

SSRN has no public harvesting API we can rely on (and its commercial feeds are closing), so
alert emails are the discovery route. The parser is deliberately tolerant: it looks for
links to SSRN abstract pages (abstract_id=NNN or ssrn.com/abstract=NNN), takes the link text
as the title, and the text that follows as authors and abstract.
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from email.message import Message
from html.parser import HTMLParser
from typing import Any

from lark.context import RunContext
from lark.records import SourceRecord
from lark.sources._common import fixtures_dir, iso_date, split_authors
from lark.sources.base import FetchResult

log = logging.getLogger(__name__)

SSRN_ID_RE = re.compile(r"(?:abstract_id=|ssrn\.com/abstract=|abstractid=)(\d{5,})", re.I)
NOISE_RE = re.compile(r"^(download|view|read more|full text|pdf|abstract|unsubscribe|manage)\b", re.I)


class _Tokenizer(HTMLParser):
    """Flattens HTML into ("link", href, text) and ("text", text) tokens."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._flush()
            self._href = dict(attrs).get("href")
        elif tag in ("p", "br", "div", "tr", "li", "td", "h1", "h2", "h3", "h4"):
            self._flush()

    def handle_endtag(self, tag):
        if tag == "a":
            text = " ".join("".join(self._buf).split())
            self.tokens.append(("link", self._href or "", text))
            self._buf, self._href = [], None
        elif tag in ("p", "div", "tr", "li", "td", "h1", "h2", "h3", "h4"):
            self._flush()

    def handle_data(self, data):
        self._buf.append(data)

    def _flush(self):
        if self._href is not None:
            return
        text = " ".join("".join(self._buf).split())
        if text:
            self.tokens.append(("text", text))
        self._buf = []

    def close(self):
        super().close()
        self._flush()


def _body(msg: Message) -> tuple[str | None, str | None]:
    html_part = text_part = None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/html" and html_part is None:
            html_part = text
        elif ctype == "text/plain" and text_part is None:
            text_part = text
    return html_part, text_part


def _from_tokens(tokens: list[tuple]) -> list[dict]:
    papers: list[dict] = []
    cur: dict | None = None
    for tok in tokens:
        if tok[0] == "link":
            _, href, text = tok
            m = SSRN_ID_RE.search(href or "")
            if m and text and len(text) > 12 and not NOISE_RE.match(text):
                if cur and cur["id"] == m.group(1):
                    continue
                cur = {"id": m.group(1), "title": text, "lines": []}
                papers.append(cur)
            continue
        if cur is not None:
            cur["lines"].append(tok[1])
    return papers


def _from_text(text: str) -> list[dict]:
    papers: list[dict] = []
    lines = [ln.strip() for ln in text.splitlines()]
    cur: dict | None = None
    prev = ""
    for ln in lines:
        m = SSRN_ID_RE.search(ln)
        if m:
            title = re.sub(r"<?https?://\S+>?", "", ln).strip(" -:") or prev
            cur = {"id": m.group(1), "title": title, "lines": []}
            papers.append(cur)
        elif cur is not None and ln:
            cur["lines"].append(ln)
        if ln:
            prev = ln
    return papers


def parse_alert(raw: bytes | str) -> list[SourceRecord]:
    msg = email.message_from_bytes(raw) if isinstance(raw, bytes) else email.message_from_string(raw)
    html_body, text_body = _body(msg)
    if html_body:
        tk = _Tokenizer()
        tk.feed(html_body)
        tk.close()
        papers = _from_tokens(tk.tokens)
    else:
        papers = _from_text(text_body or "")
    sent = iso_date(email.utils.parsedate_to_datetime(msg["Date"]).date().isoformat()) if msg["Date"] else None
    out: list[SourceRecord] = []
    seen = set()
    for p in papers:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        lines = [ln for ln in p["lines"] if not NOISE_RE.match(ln) and not SSRN_ID_RE.search(ln)]
        authors, abstract = [], None
        if lines:
            first = re.sub(r"^(by|authors?:)\s*", "", lines[0], flags=re.I)
            if len(first) < 200 and not first.endswith("."):
                authors = split_authors(first)
                lines = lines[1:]
            body = [ln for ln in lines if len(ln) > 80]
            abstract = re.sub(r"^abstract[:.]?\s*", "", body[0], flags=re.I) if body else None
        out.append(
            SourceRecord(
                source="ssrn_alerts",
                source_id=p["id"],
                title=p["title"],
                authors=authors,
                abstract=abstract,
                ids={"ssrn": p["id"]},
                venue="SSRN",
                status="preprint",
                url=f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={p['id']}",
                extra={"alert_date": sent, "alert_subject": msg.get("Subject")},
            )
        )
    return out


class SsrnAlertsCollector:
    name = "ssrn_alerts"

    def fetch(self, ctx: RunContext, checkpoint: dict[str, Any]) -> FetchResult:
        last_uid = int(checkpoint.get("last_uid", 0))
        if ctx.offline:
            msgs = [(i + 1, p.read_bytes()) for i, p in enumerate(sorted(fixtures_dir(self.name).glob("*.eml")))]
            msgs = [(u, m) for u, m in msgs if u > last_uid]
            uidvalidity = checkpoint.get("uidvalidity")
        else:
            user, pw = os.getenv("LARK_IMAP_USER"), os.getenv("LARK_IMAP_PASSWORD")
            if not (user and pw):
                return FetchResult(self.name, "failed", checkpoint=checkpoint, error="not configured")
            cfg = ctx.config.get("sources.ssrn_alerts", {}) or {}
            try:
                msgs, uidvalidity = self._imap_fetch(cfg.get("imap_host", "imap.gmail.com"),
                                                     cfg.get("mailbox", "ssrn"), user, pw, checkpoint)
            except Exception as e:
                return FetchResult(self.name, "failed", checkpoint=checkpoint, error=f"imap: {e}")
        records: list[SourceRecord] = []
        for _, raw in msgs:
            try:
                records += parse_alert(raw)
            except Exception as e:  # one malformed email shouldn't block the rest
                log.warning("Could not parse SSRN alert: %s", e)
        cp = {**checkpoint, "since": ctx.run_date, "uidvalidity": uidvalidity}
        if msgs:
            cp["last_uid"] = max(u for u, _ in msgs)
        return FetchResult(self.name, "ok" if records else "empty", records=records, checkpoint=cp)

    @staticmethod
    def _imap_fetch(host: str, mailbox: str, user: str, pw: str, checkpoint: dict) -> tuple[list, str | None]:
        conn = imaplib.IMAP4_SSL(host)
        try:
            conn.login(user, pw)
            typ, _ = conn.select(f'"{mailbox}"', readonly=True)
            if typ != "OK":
                raise RuntimeError(f"mailbox {mailbox!r} not found")
            uv = conn.response("UIDVALIDITY")[1]
            uidvalidity = uv[0].decode() if uv and uv[0] else None
            last = int(checkpoint.get("last_uid", 0)) if checkpoint.get("uidvalidity") == uidvalidity else 0
            typ, data = conn.uid("search", None, f"UID {last + 1}:*")
            uids = [int(u) for u in (data[0] or b"").split() if int(u) > last]
            out = []
            for uid in uids[-200:]:
                typ, parts = conn.uid("fetch", str(uid), "(RFC822)")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    out.append((uid, raw))
            return out, uidvalidity
        finally:
            try:
                conn.logout()
            except Exception:
                pass
