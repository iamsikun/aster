"""Identifiers: normalize DOIs, arXiv ids, RePEc handles and URLs into one `ids` mapping.

Exact identifiers are what the catalog merges on, so every path into Aster (collectors, the
agent's `aster add`, `aster lookup`) goes through these functions.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

from aster.utils.text import normalize_arxiv_id, normalize_doi

ARXIV_NEW = r"\d{4}\.\d{4,5}"
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(" + ARXIV_NEW + r"|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
DOI_IN_TEXT = re.compile(r"(10\.\d{4,9}/[^\s\"'<>?#]+)", re.I)
SSRN_URL = re.compile(r"ssrn\.com/(?:abstract=|abstract_id=|sol3/papers\.cfm\?abstract_id=)(\d+)", re.I)
NBER_URL = re.compile(r"nber\.org/papers/(w\d+)", re.I)
OPENREVIEW_URL = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)", re.I)
HF_MODEL_URL = re.compile(r"huggingface\.co/(?!papers/|blog/|datasets/|spaces/|docs/)([\w\-.]+/[\w\-.]+)", re.I)
HF_PAPER_URL = re.compile(r"huggingface\.co/papers/(" + ARXIV_NEW + r")", re.I)


def ids_from_doi(doi: str | None) -> dict[str, str]:
    """Normalize a DOI into identifiers; arXiv and SSRN DOIs become their native ids."""
    d = normalize_doi(doi)
    if not d:
        return {}
    m = re.match(r"^10\.48550/arxiv\.(.+)$", d)
    if m:
        base, _ = normalize_arxiv_id(m.group(1))
        return {"arxiv": base} if base else {}
    m = re.match(r"^10\.2139/ssrn\.(\d+)$", d)
    if m:
        return {"ssrn": m.group(1)}
    m = re.match(r"^10\.3386/w(\d+)$", d)
    if m:
        return {"nber": f"w{m.group(1)}", "doi": d}
    return {"doi": d}


def ids_from_repec(handle: str | None) -> dict[str, str]:
    """RePEc handle -> identifiers, mapping arXiv/NBER-backed series to native ids."""
    if not handle:
        return {}
    h = handle.strip()
    out = {"repec": h}
    m = re.match(r"^RePEc:arx:papers:(.+)$", h, flags=re.I)
    if m:
        base, _ = normalize_arxiv_id(m.group(1))
        if base:
            out["arxiv"] = base
    m = re.match(r"^RePEc:nbr:nberwo:(\d+)$", h, flags=re.I)
    if m:
        out["nber"] = f"w{m.group(1)}"
    return out


def repec_handle_from_url(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    vals = q.get("u")
    return vals[0] if vals else None


def canonical_url(url: str) -> str:
    """Lower-case scheme and host, drop fragments, tracking parameters and a trailing slash."""
    p = urlparse(url.strip())
    query = "&".join(
        part for part in p.query.split("&") if part and not re.match(r"(utm_[a-z]+|ref|ref_src|s|t)=", part, re.I)
    )
    path = p.path.rstrip("/") or "/"
    host = p.netloc.lower().removeprefix("www.")
    if host in ("twitter.com", "mobile.twitter.com", "mobile.x.com"):
        host = "x.com"
    return urlunparse(((p.scheme or "https").lower(), host, path, "", query, ""))


def ids_from_url(url: str | None) -> dict[str, str]:
    """Identifiers recognizable in a URL (arXiv, DOI, SSRN, NBER, OpenReview, Hugging Face)."""
    if not url:
        return {}
    out: dict[str, str] = {}
    if m := ARXIV_URL.search(url) or HF_PAPER_URL.search(url):
        base, _ = normalize_arxiv_id(m.group(1))
        if base:
            out["arxiv"] = base
    if m := SSRN_URL.search(url):
        out["ssrn"] = m.group(1)
    if m := NBER_URL.search(url):
        out["nber"] = m.group(1).lower()
    if m := OPENREVIEW_URL.search(url):
        out["openreview"] = m.group(1)
    if "doi.org/" in url.lower() and (m := DOI_IN_TEXT.search(url)):
        out = {**ids_from_doi(m.group(1)), **out}
    if not out and (m := HF_MODEL_URL.search(url)):
        out["hf"] = m.group(1)
    return out


def parse_ref(ref: str) -> dict[str, str]:
    """A user or agent reference -> identifiers.

    Accepts `doi:..`, `arxiv:..`, `ssrn:..`, `nber:..`, `openreview:..`, `hf:..`, a bare DOI or
    arXiv id, or any URL (which also becomes a `url` identifier).
    """
    ref = ref.strip()
    if m := re.match(r"^(doi|arxiv|ssrn|nber|openreview|hf|repec|openalex|github):(.+)$", ref, re.I):
        kind, value = m.group(1).lower(), m.group(2).strip()
        if kind == "doi":
            return ids_from_doi(value)
        if kind == "arxiv":
            base, _ = normalize_arxiv_id(value)
            return {"arxiv": base} if base else {}
        return {kind: value}
    if re.match(r"^https?://", ref, re.I):
        return {**ids_from_url(ref), "url": canonical_url(ref)}
    if re.match(r"^10\.\d{4,9}/", ref):
        return ids_from_doi(ref)
    if re.match(r"^" + ARXIV_NEW + r"(v\d+)?$", ref):
        base, _ = normalize_arxiv_id(ref)
        return {"arxiv": base} if base else {}
    return {}
