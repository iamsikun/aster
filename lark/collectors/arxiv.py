import xml.etree.ElementTree as ET
from datetime import datetime
from ..utils.text import canonical_key
from ..utils.http import get_json

def fetch_arxiv(categories: list[str], query: str = "", max_results: int = 100):
    cats = " OR ".join([f"cat:{c}" for c in categories])
    q = cats if not query else f"({cats}) AND ({query})"
    base = "https://export.arxiv.org/api/query"
    params = {"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": max_results}
    text = get_json(base, params=params)   # returns text if not json
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        published = entry.findtext("a:published", default="", namespaces=ns) or ""
        aid = (entry.findtext("a:id", default="", namespaces=ns) or "").split("/")[-1]
        pdf_url = None; url = None
        for link in entry.findall("a:link", ns):
            if link.attrib.get("title") == "pdf": pdf_url = link.attrib.get("href")
            if link.attrib.get("rel") == "alternate": url = link.attrib.get("href")
        authors = [a.findtext("a:name", default="", namespaces=ns) or "" for a in entry.findall("a:author", ns)]
        abstr = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        key = canonical_key(None, aid, None, title)
        out.append({
            "key": key, "title": title, "authors": authors, "venue": "arXiv",
            "year": int(published[:4]) if published else None, "doi": None, "arxiv_id": aid,
            "url": url, "pdf_url": pdf_url, "abstract": abstr, "source": "arxiv",
            "published_date": published[:10] if published else None, "added_at": datetime.utcnow().isoformat(),
            "is_oa": True
        })
    return out
