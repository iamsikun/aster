# Working on Aster

Aster has one job: tell a researcher what is new in their fields and what is being discussed,
with every item traceable to a primary source. Keep it small. Before adding a feature, ask
whether a brief, a buzz report, a sweep or a lookup needs it.

Aster may hand off to Finch and Quill, and to any connected knowledge base, but never names a
particular knowledge base or the tools built on one (such as Cairn or the Cairn workbench).

## Layout

- `skills/aster/` is the installable skill and must stay self-contained:
  - `SKILL.md` holds the workflow and the rules;
  - `references/` holds one guide per mode, each linked from `SKILL.md` with a note on when to
    read it;
  - `agents/openai.yaml` holds Codex display metadata;
  - `LICENSE` is a copy of the root licence.
- `src/aster/` is the `aster` command:
  - `sources/` holds the collectors;
  - `catalog.py` and `versions.py` merge and triage works;
  - `rank.py` ranks candidates;
  - `findings.py` handles `aster add`;
  - `ids.py` normalizes identifiers;
  - `config.py` and `fields.py` hold the configuration;
  - `assets/` holds the defaults, fields, journal registry and config template.
- `tests/` runs offline against `tests/fixtures/sources`. `evals/` holds the behavioural
  scenarios.

## Rules

- **The command never runs a model and never searches the web.** It fetches public APIs and
  feeds, stores, merges and ranks. The agent searches, judges and writes.
- **Collectors never decide relevance.** They normalize records, label fields from their
  sources, report `ok`/`empty`/`failed`/`partial`/`stale` honestly, and return a checkpoint.
  One failing source never stops the others.
- **Only exact identifiers merge works.** Title and author similarity only proposes a merge.
- **The log is append-only.** The JSONL log under the data folder is the record, and the SQLite
  index is rebuilt from it. Never rewrite or drop log entries.
- **Ranking is deterministic and explained.** Every score comes with `reasons`. Change the
  `aster.candidates/1` JSON only additively; a breaking change needs a new schema version.
- **Nothing personal in the repository.** No real configs, data, email addresses or
  credentials. User settings live in the config file and secrets in environment variables.
- **Skill frontmatter** uses only the standard fields (`name`, `description`, `license`,
  `compatibility`, `metadata`, `allowed-tools`):
  - `name` matches the directory;
  - the description is at most 1024 characters;
  - `SKILL.md` stays under 500 lines;
  - the version line keeps the exact form `  version: "x.y.z"`, which `install.sh` reads.
- **Writing style.** Write the skill in the imperative and explain why. Avoid all-caps rules.

## Develop

```bash
uv sync
uv run pytest                                   # offline; no network in tests
uv run ruff check . && uv run ruff format --check .
uv run python scripts/validate.py               # skill spec, links, versions
uvx --from skills-ref agentskills validate skills/aster
claude plugin validate .
```

- Use synthetic data and temporary folders, with `ASTER_CONFIG` and `ASTER_DATA_DIR` set.
- New collectors need recorded fixtures.
- After packaging changes, run `uv build`.
- Make small commits.

## Releasing

Development happens on `main`. Users install from the `stable` branch. Their installs update
automatically: `install.sh` runs a daily fast-forward, and Claude Code checks the plugin
version. Only move `stable` deliberately:

1. Run the tests, the validators above, and at least one case from `evals/cases.json`.
2. Bump the version in all four places: `SKILL.md`, `.claude-plugin/plugin.json`,
   `pyproject.toml` and `src/aster/__init__.py`.
3. Add an entry to `CHANGELOG.md`.
4. Tag and publish:
   ```bash
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   git push origin main:stable      # the release; fast-forwards stable
   ```

Never force-push `stable`. Installs update with `--ff-only`, so rewriting its history would
leave them stuck.
