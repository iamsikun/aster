"""Mechanical evidence checks: is each quote really in the source, and are claimed numbers supported?

This establishes traceability, not correctness: a verified quote shows the passage exists,
not that it supports the claim or that the research is right.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from lark.records import Analysis, ReadingNote

FUZZY_THRESHOLD = 0.9
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def normalize(text: str) -> str:
    """NFKC (expands ligatures like 'ﬁ'), join hyphenated line breaks, unify dashes/quotes,
    drop LaTeX math delimiters, lowercase, and collapse whitespace."""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)
    t = t.translate(_DASHES).translate(_QUOTES)
    t = t.replace("$", "")
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _fuzzy_found(nq: str, ntext: str, threshold: float = FUZZY_THRESHOLD) -> bool:
    """Anchor on short shingles of the quote, then score aligned windows. Linear in practice."""
    n = len(nq)
    if n < 20:
        return False
    k = 12
    offsets = sorted({0, n // 4, n // 2, (3 * n) // 4, n - k})
    slack = max(8, n // 10)
    tried: set[int] = set()
    for off in offsets:
        anchor = nq[off : off + k]
        start = 0
        for _ in range(5):  # at most 5 occurrences per anchor
            pos = ntext.find(anchor, start)
            if pos == -1:
                break
            start = pos + 1
            begin = max(0, pos - off - slack)
            if begin in tried:
                continue
            tried.add(begin)
            window = ntext[begin : pos - off + n + slack]
            sm = SequenceMatcher(None, nq, window, autojunk=False)
            matched = sum(b.size for b in sm.get_matching_blocks())
            if matched / n >= threshold:
                return True
    return False


def quote_found(quote: str, ntext: str) -> bool:
    nq = normalize(quote).strip(" .\"'")
    if not nq:
        return False
    return nq in ntext or _fuzzy_found(nq, ntext)


_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)*%?")


def _numbers(text: str) -> list[str]:
    out = []
    for m in _NUM.findall(text or ""):
        v = m.replace(",", "").rstrip("%").lstrip("+")
        if v:
            out.append(v)
    return out


def _number_in(num: str, ntext_nocomma: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", ntext_nocomma) is not None


def verify_note(note: ReadingNote, parsed=None, extra_text: str = "") -> ReadingNote:
    """Mark Evidence.verified / Claim.verified and summarize into note.verification.

    `parsed` is a lark.read.parse.Parsed (or None for abstract-only readings); `extra_text`
    adds material that was supplied to the model but is not in `parsed` (e.g. the abstract).
    """
    source = (parsed.full_text if parsed is not None else "") + "\n" + (extra_text or "")
    ntext = normalize(source)
    ntext_nc = ntext.replace(",", "")
    n_quotes = n_verified_quotes = n_verified_claims = 0
    unsupported_numbers: list[dict] = []
    unsupported_claims: list[int] = []
    for i, claim in enumerate(note.claims):
        any_ok = False
        for ev in claim.evidence:
            n_quotes += 1
            ev.verified = quote_found(ev.quote, ntext)
            if ev.verified:
                n_verified_quotes += 1
                any_ok = True
        claim.verified = any_ok
        if any_ok:
            n_verified_claims += 1
        else:
            unsupported_claims.append(i)
        quotes_nc = normalize(" ".join(e.quote for e in claim.evidence)).replace(",", "")
        for num in _numbers(claim.text):
            if not (_number_in(num, quotes_nc) or _number_in(num, ntext_nc)):
                unsupported_numbers.append({"claim": i, "number": num})
    note.verification = {
        "n_claims": len(note.claims),
        "n_verified": n_verified_claims,
        "n_quotes": n_quotes,
        "n_verified_quotes": n_verified_quotes,
        "unsupported_claims": unsupported_claims,
        "unsupported_numbers": unsupported_numbers,
        "source_chars": len(ntext),
    }
    return note


def verify_analysis(analysis: Analysis, parsed=None, extra_text: str = "") -> Analysis:
    ntext = normalize((parsed.full_text if parsed is not None else "") + "\n" + extra_text)
    for ev in analysis.evidence:
        ev.verified = quote_found(ev.quote, ntext)
    return analysis
