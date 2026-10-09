# Lark

A research briefing pipeline. Lark collects new papers and research-technology releases
broadly (arXiv, journals via Crossref, OpenAlex, NBER, NEP, OpenReview, SSRN alerts,
GitHub/Hugging Face/blogs). It screens them against your research profiles and reads the
selected ones with evidence anchored to the source text. Each morning it sends a short
briefing: at most eight items, and none on a quiet day. It also produces a weekly
synthesis and on-request deep reads.

- **Runs on GitHub Actions.** No machine has to stay on. Private state and workflows live in a separate `lark-data` repo.
- **Uses the subscriptions you already have.** Claude Haiku 5.5 via `claude -p` and the Luna model offered to ChatGPT Pro in Codex via `codex exec` read papers. Gemini screens abstracts on the Google AI Pro developer credit. An optional local model runs on a GPU workstation.
- **Enforces budgets in code.** Cash and subscription capacity are both tracked, and over-limit work is deferred rather than dropped.
- **Uses one report contract for every channel** ([JSON Schema](docs/report-contract/v1.schema.json)). Email is the default; Telegram, Discord, Slack, ntfy, Notion, and a Markdown archive are included.

## Quick start (local, no credentials)

```bash
uv sync
uv run lark init-data ../lark-data
uv run lark daily --data-dir ../lark-data --dry-run --fake-llm
open ../lark-data/out/*.eml          # the rendered email
```

## Docs

- [Setup](docs/setup.md): data repo, secrets, model routes, SSRN alerts, GPU runner
- [Architecture](docs/architecture.md): stages, reading depths, model roles, budgets, state
- [Channels](docs/channels.md): the report contract, adding a channel, free options

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```
