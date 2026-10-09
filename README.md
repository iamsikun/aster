# Lark — daily "new & trending" papers bot

Lark collects **new** and **trending** papers each day (arXiv + OpenAlex + Crossref Event Data),
ranks them, and delivers a digest to **Notion** and/or **Slack**.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill secrets
python scripts/run_daily.py
```
