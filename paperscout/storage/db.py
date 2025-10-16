import sqlite3, json, os
from contextlib import contextmanager

DB_PATH = os.getenv("PAPERSCOUT_DB", "paperscout.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  key TEXT PRIMARY KEY,
  title TEXT,
  authors TEXT,
  venue TEXT,
  year INTEGER,
  doi TEXT,
  arxiv_id TEXT,
  url TEXT,
  pdf_url TEXT,
  abstract TEXT,
  source TEXT,
  published_date TEXT,
  added_at TEXT,
  is_oa INTEGER
);
CREATE TABLE IF NOT EXISTS signals (
  key TEXT PRIMARY KEY,
  mentions_7d INTEGER,
  score REAL
);
"""

@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    try:
        yield c
    finally:
        c.close()

def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        c.commit()

def upsert_paper(p: dict):
    with conn() as c:
        c.execute("""
        INSERT INTO papers(key,title,authors,venue,year,doi,arxiv_id,url,pdf_url,abstract,source,published_date,added_at,is_oa)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
          title=excluded.title,
          authors=excluded.authors,
          venue=excluded.venue,
          year=excluded.year,
          doi=excluded.doi,
          arxiv_id=excluded.arxiv_id,
          url=excluded.url,
          pdf_url=excluded.pdf_url,
          abstract=excluded.abstract,
          source=excluded.source,
          published_date=excluded.published_date,
          added_at=excluded.added_at,
          is_oa=excluded.is_oa
        """, (
            p["key"], p.get("title"), json.dumps(p.get("authors", [])),
            p.get("venue"), p.get("year"),
            p.get("doi"), p.get("arxiv_id"), p.get("url"), p.get("pdf_url"),
            p.get("abstract"), p.get("source"), p.get("published_date"),
            p.get("added_at"), 1 if p.get("is_oa") else 0
        ))
        c.commit()

def get_recent(limit=200):
    with conn() as c:
        cur = c.execute("SELECT key,title,authors,venue,year,url,pdf_url,published_date FROM papers ORDER BY added_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return rows

def upsert_signals(s: dict):
    with conn() as c:
        c.execute("""
        INSERT INTO signals(key, mentions_7d, score) VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET
          mentions_7d=excluded.mentions_7d,
          score=excluded.score
        """, (s["key"], s.get("mentions_7d", 0), s.get("score", 0.0)))
        c.commit()

def top_scored(limit=50):
    with conn() as c:
        cur = c.execute("""
        SELECT p.key,p.title,p.venue,p.year,p.url,p.pdf_url,s.score
        FROM papers p JOIN signals s ON p.key=s.key
        ORDER BY s.score DESC LIMIT ?
        """, (limit,))
        return cur.fetchall()
