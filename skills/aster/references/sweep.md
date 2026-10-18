# Sweep and lookup

## Sweep: recent work on a topic

A sweep gathers recent papers on a topic across preprint servers, working-paper series and
journals, and groups versions of the same work.

1. Agree on the topic and window in one line if they are unclear (default: the last 90 days).
2. Check the collection: `aster candidates --since <date> --limit 200 --json` (add `--field`
   when the topic sits in one field) and keep items that match the topic.
3. Search the web for what the collection misses, following [Web search](web-search.md):
   arXiv (listing and search pages), SSRN, NBER, OpenReview, journal "online first" pages,
   and authors' pages. The OpenAlex API search is a good cross-check for coverage.
4. Group versions: a working paper, its arXiv preprint and its journal article are one work.
   `aster add` merges them when they share an identifier (DOI, arXiv id, SSRN id); when they do
   not, judge by title and authors and say so.
5. Record new finds with `aster add`. Report the works grouped by sub-question or approach,
   newest first within each group, with status (preprint, under review, published) and links.

## Lookup: one item

For "has this been published?", "is there a new version?" or "who is discussing this?":

1. `aster lookup <ref>` with a work id, DOI, arXiv id, `ssrn:<id>`, `nber:<id>` or URL. It
   returns the work, every source it was seen in, its versions (with triage: trivial,
   substantive, withdrawn), recorded signals and whether it was shown.
2. If it is not in the catalog, or to check for newer events, search the web: the journal for
   the title, OpenAlex or Crossref for a DOI, the arXiv listing for new versions, and the
   platforms for discussion.
3. Record what you find with `aster add` (the same URL or identifier links it to the work), and
   answer with dates and links.
