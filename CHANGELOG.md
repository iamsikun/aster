# Changelog

Aster uses semantic versioning:
- **Major** releases change the skill's workflow noticeably, remove CLI commands, or break the
  `aster.candidates` JSON format.
- **Minor** releases add sources, commands, fields or guidance.
- **Patch** releases fix bugs and wording.

## Unreleased

## 0.1.0 — 2026-10-18

First release of Aster, rebuilt from the Lark briefing pipeline (whose last state is tagged
`v0.2.0-pipeline`).

- Add the `aster` skill, with brief, buzz, sweep, lookup and configuration modes. The agent
  searches the web for SSRN, X, Bluesky, Hacker News, Reddit and lab pages, verifies each item,
  and records it.
- Add the `aster` command: `init`, `paths`, `fields`, `refresh`, `candidates`, `add`, `lookup`,
  `seen` and `doctor`. It never runs a model or searches the web.
- Collect from arXiv, Crossref, OpenAlex, NBER, RePEc NEP, OpenReview, Hugging Face (models and
  Daily Papers) and RSS/Atom feeds, configured by research fields. The defaults are economics,
  machine learning, quantitative finance and marketing.
- Rank candidates deterministically with stated reasons: field tier, keywords, interests,
  follows and discussion signals. Output is versioned JSON (`aster.candidates/1`).
- Remove the model-driven screening, reading, budgets, email delivery and the private data
  repository from Lark.
- Add the installer (skill links, uv-installed command, daily updates from `stable`) and the
  Claude Code plugin manifest.
