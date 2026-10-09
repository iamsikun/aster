# Setting up Lark

Lark runs on GitHub Actions. The code lives in the public `iamsikun/lark` repo; your
profiles, state, briefings, and the scheduled workflows live in a **private**
`iamsikun/lark-data` repo. Your machines can stay off. The 4090 workstation is an
optional extra that drains backlog work when it happens to be on.

## 1. Create the data repo

```bash
cd ~/research
uv run --project lark lark init-data lark-data      # copies templates/data-repo
cd lark-data
git init -b main && git add -A && git commit -m "Initialize lark-data"
gh repo create iamsikun/lark-data --private --source . --push
```

Then edit, in this order:

1. `profiles/*.yaml`: fill `seeds` (3–10 central papers each), `negatives`, and `known`.
   The questions and keywords are drafts; sharpen them.
2. `journals.yaml`: drop journals you don't want, or add more (verify ISSNs at
   `https://api.crossref.org/journals/<issn>`).
3. `watchlist.yaml`: authors, labs, repos, and blogs to follow explicitly.
4. `lark.yaml`: limits, model routes, and channels.

## 2. Add secrets and variables

In `lark-data` → Settings → Secrets and variables → Actions.

| Secret | How to get it |
|---|---|
| `LARK_SMTP_USER` | The Gmail address Lark sends from (a dedicated account is fine) |
| `LARK_SMTP_PASSWORD` | Gmail app password: Google Account → Security → 2-Step Verification (must be on) → App passwords |
| `LARK_EMAIL_TO` | Where briefings go, e.g. `sikun@wustl.edu` (comma-separate several) |
| `LARK_IMAP_USER` / `LARK_IMAP_PASSWORD` | Same Gmail account and app password; used to read SSRN alerts (step 4) |
| `GEMINI_API_KEY` | Google AI Studio → Get API key. See the note on developer credits below |
| `CLAUDE_CODE_OAUTH_TOKEN` | Run `claude setup-token` locally while signed into your Claude account; paste the token. **First check with your Claude Enterprise admin** that automated use of your seat is permitted; if it isn't, leave this unset and Lark falls back to the Codex (Luna) reader |
| `CODEX_AUTH_JSON` | Run `codex login` locally and choose "Sign in with ChatGPT" (Pro). Paste the full contents of `~/.codex/auth.json` |
| `LARK_ADMIN_TOKEN` | A fine-grained PAT limited to `iamsikun/lark-data` with **Secrets: read and write**. Lark uses it only to save rotated Codex credentials back into `CODEX_AUTH_JSON` |
| `OPENALEX_API_KEY` | Free key from openalex.org (optional, raises limits) |

| Variable | Value |
|---|---|
| `LARK_CONTACT_EMAIL` | Your email; sent in the User-Agent and used for Crossref's polite pool |

Notes:
- **Google AI Pro developer credit.** Link a Google Developer Program profile to the
  account that holds the Google AI Pro subscription and claim the monthly GenAI credit.
  Google's billing may require a small prepaid balance before credits apply. Lark's
  `budget.monthly_cash_usd` (default $10) caps Gemini spend regardless.
- **Never set `ANTHROPIC_API_KEY`** in this repo; in `claude -p` it would override the
  subscription token and bill the API instead.
- Codex refreshes its token on use. The workflows detect a changed `auth.json` and
  update the secret, so it doesn't go stale. If a run reports a Codex auth failure,
  repeat `codex login` and update the secret.

## 3. Check the model routes

Model IDs change. Before the first live run, confirm them and override in `lark.yaml`:

```bash
codex exec --help          # check the model flag and the Luna id your ChatGPT plan offers
                           # (GPT-6 Luna is API-only; Codex on ChatGPT Pro offered gpt-5.6-luna as of 2026-10-09)
claude -p --model claude-haiku-5-5 "say ok"
lark doctor --data-dir .   # checks every route's credentials and binaries without spending
```

## 4. Route SSRN alerts into Gmail

1. On SSRN, subscribe to the research alerts (subject-matter eJournals) that you want.
   Use the Gmail address from step 2.
2. In Gmail, create a filter: *From* `ssrn.com` → *Apply the label* `ssrn`, *Skip the Inbox*.
3. Gmail → Settings → Forwarding and POP/IMAP → enable IMAP.

Lark reads that label over IMAP. SSRN alerts are a discovery route, not complete
coverage, and SSRN's products are in transition through 2026; the briefing's source
health line will show when the alerts stop arriving.

## 5. Test

1. Actions → **daily** → Run workflow with *dry_run* checked. The run commits state and
   writes the email to `out/` instead of sending it. Check the logs and the committed
   `briefings/` file.
2. Run **daily** again without dry run; the email should arrive.
3. Click a feedback link in the email, submit the issue, and run **feedback** (or wait
   for the next daily run). The issue should be closed with "Recorded by Lark".
4. Actions → **deep-read** → paste an arXiv URL.

Local dry run without any credentials:

```bash
uv run lark daily --data-dir ../lark-data --dry-run --fake-llm
open ../lark-data/out/*.eml
```

## 6. Optional: the 4090 workstation as a GPU runner

1. In `lark-data` → Settings → Actions → Runners → New self-hosted runner. Follow the
   Linux instructions on the workstation and add the label `gpu`
   (`./config.sh --labels gpu`). Install it as a service (`sudo ./svc.sh install`) so
   it picks up jobs whenever the machine is on.
2. Install Ollama and pull the local model:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull gemma4:12b      # check the exact tag on ollama.com/library before pulling
   ```
   If the tag differs, set `routes.local_gemma.model` in `lark.yaml`.
3. Install `uv` on the workstation. The **gpu-drain** workflow runs every two hours;
   while the machine is off, its runs simply wait in the queue and expire. The daily
   briefing never depends on them.

Measure before relying on local inference: compare its screening and reading on the
same items as the hosted routes (`lark eval reader`).
