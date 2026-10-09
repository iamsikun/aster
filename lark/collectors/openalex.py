import os
from datetime import datetime
from ..utils.http import get_json
from ..utils.text import canonical_key
from ..utils.timez import today_str

def fetch_openalex(filter_fragment: str, per_page: int = 100, max_pages: int = 2):
    base = os.getenv("OPENALEX_BASE", "https://api.openalex.org")
    url = f"{base}/works"
    page = 1
    out = []
    while page <= max_pages:
        params = {
            "filter": filter_fragment.replace("{today}", today_str()),
            "per_page": per_page,
            "page": page,
            "select": "id,title,doi,authorships,host_venue,publication_year,primary_location,cited_by_count,counts_by_year,open_access,publication_date"
        }
        data = get_json(url, params=params)
        for w in data.get("results", []):
            title = w.get("title", "")
            doi = (w.get("doi") or "").lower() or None
            arxiv_id = None
            loc = (w.get("primary_location") or {})
            pdf_url = (loc.get("source") or {}).get("url_for_pdf")
            url2 = loc.get("landing_page_url") or None
            venue = (w.get("host_venue") or {}).get("display_name")
            year = w.get("publication_year")
            authors = [a.get("author", {}).get("display_name") for a in w.get("authorships", [])]
            key = canonical_key(doi, arxiv_id, None, title)
            out.append({
                "key": key, "title": title, "authors": authors, "venue": venue, "year": year,
                "doi": doi, "arxiv_id": arxiv_id, "url": url2, "pdf_url": pdf_url, "abstract": None,
                "source": "openalex", "published_date": (w.get("publication_date") or "")[:10] or None,
                "added_at": datetime.utcnow().isoformat(), "is_oa": (w.get("open_access") or {}).get("is_oa", False)
            })
        page += 1
    return out
