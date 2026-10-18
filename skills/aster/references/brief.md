# Brief

A brief answers "what is new in my fields since last time?" Aim for 3–8 items per core field at
most, fewer for skim fields, none for quiet ones.

## 1. Refresh the collection

Run `aster refresh` (add `--field <id>` to limit it to one field). Skip it if the `sources`
block of the last `aster candidates --json` shows every source checked within the last few
hours. The first run catches up on the past week and can take a minute or two. Note any source
reported `failed` or `stale`; mention it only if it leaves a field uncovered.

## 2. Read the candidates

```
aster candidates --days 1 --json        # daily
aster candidates --days 7 --json        # weekly, or the first brief
aster candidates --days 7 --field marketing --json
```

Each item carries `fields`, `score`, `reasons` (`fields:`, `keyword:`, `interest:`, `follow:`,
`signals:`), `signals` (recorded discussion) and `urls`. The score orders your reading; it is
not a verdict. Read titles and abstracts and keep what a researcher in that field would want to
know about: new results, influential authors, data or methods others will use, and items that
match the user's interests. Drop routine items, errata and duplicates of the same work.

## 3. Search the web for each core field

The collection cannot see SSRN, X, Bluesky, Reddit or lab pages without feeds. For each core
field, run two to four searches built from its `search` hints (sites and queries in
`aster fields --json`) and the user's interests, restricted to the brief's window. Follow
[Web search](web-search.md). Typical searches:

- new working papers on SSRN in the field (SSRN is the main gap for economics, finance and
  marketing);
- discussion of papers or releases in the field on X and Bluesky;
- for machine learning, model releases and model cards from labs without feeds (Anthropic,
  Meta) and the most discussed papers on Hacker News.

Record each verified new item and each discussion signal with `aster add`, then run
`aster candidates` again if the additions might change the order.

## 4. Write the brief

Use the report format in SKILL.md. Lead each field with its most consequential item. Mark items
found by web search as such. If nothing new is worth reporting in a field, say so in one line.

## 5. Remember what was shown

```
aster seen w_1a2b3c w_4d5e6f --context brief
```

Mark only what you actually presented.
