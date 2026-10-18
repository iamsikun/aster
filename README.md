# Aster

Aster tells you what is new in your research fields and what people are discussing. It is an
agent skill for Claude Code, Codex and other coding agents, with a small command-line tool
behind it.

Ask your agent *"What's new in my fields this week?"*, *"What is everyone saying about the new
open-weights models?"* or *"Find recent working papers on LLMs as simulated consumers."* You get
a short list of new journal articles, working papers, lab posts and model releases. Each item
links to its primary source, and the discussion around it is linked post by post.

- **Collects what has an API.** arXiv, journals through Crossref, OpenAlex, NBER, RePEc NEP,
  OpenReview, Hugging Face (models and Daily Papers) and any RSS/Atom feed. Duplicates merge
  across sources, a working paper is linked to its published version, and revisions are told
  apart from new work.
- **Searches the web for the rest.** SSRN, X, Bluesky, Hacker News, Reddit, and lab pages and
  model cards without feeds. Your agent does the searching, verifies each item, and records it
  so the next brief knows about it.
- **Never runs a model itself.** The `aster` command fetches, stores and ranks, and gives a
  reason for every ranking. Judgment and writing come from the agent you already use.
- **Remembers what you've seen,** so later briefs don't repeat items.

The default fields are **economics, machine learning, quantitative finance and marketing**.
Probability, applied mathematics, mathematics and physics are packaged too, but muted. All of
them are configurable: add your own fields, journals, feeds and people.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/iamsikun/aster/stable/install.sh | bash
```

This:
- clones Aster to `~/.local/share/aster`;
- links the skill into every agent it detects (`~/.claude/skills`, and `~/.agents/skills`,
  which Codex, Gemini CLI and Cursor read);
- installs the `aster` command with [uv](https://docs.astral.sh/uv/) (skipped if uv is missing;
  the skill then works in web-only mode);
- schedules a daily update from the `stable` branch.

Restart your agent afterwards. Run `~/.local/share/aster/install.sh status`, `update` or
`uninstall` to check, update or remove it. Uninstalling keeps your configuration and collected
data. Add `--no-auto-update` (`... | bash -s -- --no-auto-update`) to update yourself.

**Claude Code plugin (skill only).** This installs the skill without the command, so Aster
runs in web-only mode until you also install the command:

```bash
claude plugin marketplace add iamsikun/aster#stable
claude plugin install aster@aster
```

## Configure

```bash
aster init      # write a commented ~/.config/aster/config.yaml
aster fields    # what each field follows
aster doctor    # configuration, environment, and how each source last ran
```

Example:

```yaml
interests: [heterogeneous treatment effects, LLMs as simulated consumers]
follows:
  authors: [Susan Athey]
fields:
  machine-learning: {tier: skim}
  statistics:
    name: Statistics
    arxiv: [stat.ME, stat.TH]
    journals: [aos, jasa, jrssb]
    keywords: [conformal prediction]
feeds:
  - {name: My lab, url: "https://example.org/feed.xml", kind: release, fields: [statistics]}
```

The packaged defaults and the full journal registry are in
[`src/aster/assets`](src/aster/assets). Optional environment variables:
- `ASTER_CONTACT_EMAIL`: for the Crossref and OpenAlex polite pools;
- `OPENALEX_API_KEY`;
- `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD`: for OpenReview venues;
- `GITHUB_TOKEN`.

## The command

| Command | What it does |
|---|---|
| `aster refresh [--field F] [--source S]` | Collect new items; the first run catches up on the past week |
| `aster candidates --days 7 [--field F] [--json]` | Recent items in active fields, ranked, each with its reasons |
| `aster add < findings.json` | Record items and discussion the agent found on the web |
| `aster lookup <doi \| arXiv id \| url \| work id>` | Everything known about one item: sources, versions, discussion |
| `aster seen <work id>...` | Mark items as shown |

`aster candidates --json` returns a versioned format (`aster.candidates/1`), so schedulers
and other tools can use it without an agent. Data lives in `~/.local/share/aster-data` as an
append-only log with a rebuildable index. `aster paths` shows the locations.

## Related

- [Finch](https://github.com/iamsikun/finch): read and situate one paper in depth.
- [Quill](https://github.com/iamsikun/quill): draft and revise academic writing.

Aster grew out of Lark, an earlier pipeline that ran its own models and emailed briefings; the
last version of it is tagged `v0.2.0-pipeline`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run python scripts/validate.py
```

See [AGENTS.md](AGENTS.md) for the rules of the codebase.
