# lark-data (private)

Private state and configuration for [Lark](https://github.com/iamsikun/lark), the
research briefing pipeline. **Keep this repository private**: it holds your research
profiles, reading history, and feedback.

| Path | What it is |
|---|---|
| `lark.yaml` | Overrides for Lark's defaults (limits, model routes, channels) |
| `profiles/*.yaml` | Research profiles: questions, keywords, seeds, negatives, known work |
| `journals.yaml` | Journal registry (ISSNs) for the Crossref collector |
| `watchlist.yaml` | Authors, repos, Hugging Face orgs, and blogs to follow explicitly |
| `data/YYYY/MM/DD/*.jsonl` | Append-only state log written by every run (works, screenings, readings, jobs, ledger...) |
| `cache/fulltext/` | Parsed text of papers Lark actually read (gzipped) |
| `briefings/YYYY/` | Every delivered report as Markdown + JSON |
| `eval/` | Labeled examples for evaluating screening and reading |
| `.github/workflows/` | Scheduled runs: daily, weekly, deep read, feedback, GPU drain |

The SQLite index (`.cache/`) is rebuilt from `data/` on each run and is not committed.

Feedback: click the links in a briefing. Each opens a prefilled issue here; Lark
records and closes it on the next run.
