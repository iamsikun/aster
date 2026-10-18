# Web search

Use the web search and fetch tools your host provides. Aster's command does not search the web.

## Build queries

Combine the field's `search` hints (`aster fields --json`), the user's interests, and the
window. Prefer specific phrases over generic ones, and restrict by site when a platform is the
point:

| Goal | Example query |
|---|---|
| New SSRN papers | `site:papers.ssrn.com "asset pricing" 2026` |
| NBER working papers | `site:nber.org/papers "marketing"` |
| Discussion on X | `site:x.com "arxiv.org/abs/2610" agents` or `"<paper title>" site:x.com` |
| Discussion on Bluesky | `site:bsky.app "<topic>"` |
| Reddit threads | `site:reddit.com/r/MachineLearning "<topic>"` |
| Model cards | `site:huggingface.co "<model name>" model card` |
| Lab announcements | `site:anthropic.com/news`, `site:ai.meta.com/blog "<topic>"` |

Search engines lag and index only part of X, Bluesky and Reddit, so treat the results as a
sample. Use the search tool's date filter or recency wording when the window is short.

## Fetchable APIs

These return JSON and work with a plain fetch:

- Hacker News: `https://hn.algolia.com/api/v1/search?query=<terms>&tags=story&numericFilters=created_at_i><unix-time>`
  (fields `points`, `num_comments`, `url`, `objectID`; the thread is
  `https://news.ycombinator.com/item?id=<objectID>`).
- OpenAlex: `https://api.openalex.org/works?search=<title>` to confirm a paper, its DOI, SSRN or
  arXiv id, and its venue.
- arXiv: `https://export.arxiv.org/api/query?search_query=ti:"<title>"` for versions and dates.
- Hugging Face: `https://huggingface.co/api/models/<org>/<name>` for a model's metadata; the card
  is at `https://huggingface.co/<org>/<name>`.

SSRN, Reddit and Bluesky usually block automated fetches. When you cannot open an SSRN page,
confirm the paper through OpenAlex or the authors' page instead, and keep the SSRN link.

## Verify

Before reporting or recording an item:

- Open the primary page (or confirm it through OpenAlex, arXiv or the DOI) and take the title,
  authors and date from it, not from a post or a search snippet.
- Check the date is inside the window; first-posted dates on SSRN and arXiv differ from
  revision dates.
- For a post, confirm it is about this work (it links to it or names it), and note the author.
- Take metrics only as displayed, and only when they matter.

## Record

Write one JSON payload and pass it to `aster add` on stdin:

```
aster add <<'JSON'
{"items": [
  {"title": "Pricing with LLM Shoppers", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5123456",
   "authors": ["A. Author", "B. Author"], "date": "2026-10-08", "venue": "SSRN", "status": "preprint",
   "fields": ["marketing"],
   "signals": [{"url": "https://x.com/someone/status/1", "platform": "x", "author": "someone",
                "query": "LLM shoppers pricing site:x.com"}]}],
 "signals": [
  {"url": "https://news.ycombinator.com/item?id=123", "platform": "hackernews",
   "about": "arXiv:2610.01234", "metrics": {"points": 210, "comments": 95}}]}
JSON
```

- `items` are works you verified: papers, posts or releases (`"kind": "release"` for model
  cards and announcements). Give identifiers in `ids` when you know them
  (`{"doi": "...", "arxiv": "..."}`); URLs of arXiv, DOI, SSRN, NBER, OpenReview and Hugging Face
  pages are recognized automatically.
- `signals` are posts, threads or pages showing discussion. Attach them to an item, or point
  `about` at a work id, DOI, arXiv id, `ssrn:<id>` or URL. A signal about a work not yet in the
  catalog is kept and linked when the work arrives.
- Use the field ids from `aster fields`. The command reports the resulting work ids and any
  warnings.
