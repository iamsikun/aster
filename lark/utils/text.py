import re, unicodedata

def normalize_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"[^\w\s:;,.()\-\[\]'\"]+", "", t)

def canonical_key(doi: str | None, arxiv_id: str | None, s2_id: str | None, title: str | None) -> str:
    if doi: return f"doi:{doi.lower()}"
    if arxiv_id: return f"arxiv:{arxiv_id.lower()}"
    if s2_id: return f"s2:{s2_id}"
    return f"title:{normalize_title(title or '')[:120]}"
