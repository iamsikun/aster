import hashlib
import re
import unicodedata


def normalize_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"[^\w\s:;,.()\-\[\]'\"]+", "", t)


def title_key(t: str) -> str:
    """Aggressive normalization used only for proposing (never forcing) merges."""
    return re.sub(r"[^a-z0-9]+", "", normalize_title(t))


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi)
    return doi or None


def normalize_arxiv_id(aid: str | None) -> tuple[str | None, str | None]:
    """Return (base id, version) from inputs like 'arXiv:2410.01234v2' or an abs URL."""
    if not aid:
        return None, None
    aid = aid.strip()
    aid = re.sub(r"^(https?://arxiv\.org/(abs|pdf|html)/|arxiv:)", "", aid, flags=re.I)
    aid = re.sub(r"\.pdf$", "", aid)
    m = re.match(r"^(.*?)(v\d+)?$", aid)
    base, ver = (m.group(1), m.group(2)) if m else (aid, None)
    return (base.lower() or None), ver


def short_hash(*parts: str, n: int = 12) -> str:
    h = hashlib.sha256("\x1f".join(p or "" for p in parts).encode()).hexdigest()
    return h[:n]


def text_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()
