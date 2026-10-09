from pathlib import Path

import pymupdf

from lark.read.fetch import cached_document, candidate_urls, store_document
from lark.read.parse import Parsed, Section, get_parsed, load_cached, parse, parse_tei
from lark.read.reader import select_sections
from lark.records import Work

FIX = Path(__file__).parent / "fixtures" / "read"
HTML = (FIX / "arxiv_latexml.html").read_bytes()


def _work(**kw):
    return Work(work_id="w_test", title="Bootstrap Inference for the Selected Arm", ids={"arxiv": "2610.01234"}, **kw)


def test_candidate_urls_prefers_arxiv_html():
    w = _work(urls={"pdf": "https://example.org/p.pdf"})
    urls = candidate_urls(w)
    assert urls[0] == ("https://arxiv.org/html/2610.01234", "html")
    assert urls[1][1] == "pdf" and urls[-1] == ("https://example.org/p.pdf", "pdf")


def test_parse_latexml_html(ctx):
    doc = store_document(ctx, "w_test", "https://arxiv.org/html/2610.01234", "html", HTML)
    parsed = parse(doc)
    titles = [s.title for s in parsed.sections]
    assert "Abstract" in titles
    assert "1 Introduction" in titles and "3.1 Coverage" in titles and "References" in titles
    full = parsed.full_text
    assert "Navigation should be skipped" not in full and "var a" not in full and "footer text" not in full
    assert "$n^{-1/4}$" in full  # math kept via alttext, annotation dropped
    assert "winner’s curse" in full
    assert "0.948" in full


def test_parse_generated_pdf(ctx):
    pdf = pymupdf.open()
    page = pdf.new_page()
    y = 72
    for text, size in [
        ("A Paper Title", 18), ("Abstract", 11), ("We prove a bound on regret.", 10),
        ("1 Introduction", 12), ("Bandits are hard because of exploration.", 10),
        ("2 Main Results", 12), ("Theorem 1. Regret is at most 3.5 log T.", 10),
    ]:
        page.insert_text((72, y), text, fontsize=size)
        y += 24
    page2 = pdf.new_page()
    page2.insert_text((72, 72), "References", fontsize=12)
    page2.insert_text((72, 96), "[1] Someone 2020.", fontsize=10)
    doc = store_document(ctx, "w_pdf", "https://example.org/x.pdf", "pdf", pdf.tobytes())
    parsed = parse(doc)
    assert parsed.parser == "pymupdf"
    by_title = {s.title: s for s in parsed.sections}
    assert "Abstract" in by_title and "1 Introduction" in by_title and "2 Main Results" in by_title
    assert by_title["2 Main Results"].page_start == 1
    assert by_title["References"].page_start == 2
    assert "3.5 log T" in by_title["2 Main Results"].text


def test_get_parsed_caches_and_works_offline(ctx):
    w = _work()
    ctx.store.append(w)
    assert get_parsed(ctx, w) is None  # offline, nothing cached
    store_document(ctx, w.work_id, "https://arxiv.org/html/2610.01234", "html", HTML)
    assert cached_document(ctx, w.work_id).kind == "html"
    p = get_parsed(ctx, w)
    assert p and p.parser == "html"
    again = load_cached(ctx, w.work_id)
    assert again.sections[0].title == p.sections[0].title
    assert list((ctx.data_dir / "cache" / "fulltext" / w.work_id).glob("*.json.gz"))


def test_select_sections_budget_and_order():
    secs = [
        Section("Front matter", "x" * 500),
        Section("Abstract", "abstract " * 100),
        Section("1 Introduction", "intro " * 2000),
        Section("2 Related Work", "related " * 3000),
        Section("3 Main Results", "theorem bootstrap coverage " * 1000),
        Section("4 Simulations", "simulation coverage ties " * 200),
        Section("References", "ref " * 5000),
    ]
    parsed = Parsed("w", "u", "html", "0" * 64, "html", secs)
    chosen = select_sections(parsed, reason="bootstrap coverage at ties", budget_tokens=8000)
    titles = [s.title.replace(" (partial)", "") for s in chosen]
    assert "References" not in titles
    assert titles[0] == "Front matter" or titles[0] == "Abstract"
    assert {"Abstract", "1 Introduction", "3 Main Results"} <= set(titles)
    assert sum(len(s.text) for s in chosen) <= 8000 * 4
    order = [s.title for s in secs]
    assert [order.index(t) for t in titles] == sorted(order.index(t) for t in titles)


def test_select_sections_inserts_abstract_from_metadata():
    parsed = Parsed("w", "u", "pdf", "0" * 64, "pymupdf", [Section("1 Introduction", "intro text")])
    chosen = select_sections(parsed, abstract="Metadata abstract.")
    assert chosen[0].title == "Abstract" and chosen[0].text == "Metadata abstract."


def test_parse_tei_sections_and_pages():
    tei = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><profileDesc><abstract><div><p>
    <s coords="1,10,10,10,10">We study X.</s></p></div></abstract></profileDesc></teiHeader>
    <text><body><div><head n="1" coords="1,1,1,1,1">Introduction</head><p><s coords="1,2,2,2,2">Intro.</s></p></div>
    <div><head n="3" coords="4,1,1,1,1">Main Results</head><p><s coords="4,2,2,2,2">Theorem 1 holds.</s></p></div>
    </body><back><div><head>Proofs</head><p>Proof text.</p></div></back></text></TEI>"""
    secs = parse_tei(tei)
    assert [s.title for s in secs] == ["Abstract", "1 Introduction", "3 Main Results", "Proofs"]
    assert secs[2].page_start == 4 and "Theorem 1 holds." in secs[2].text
