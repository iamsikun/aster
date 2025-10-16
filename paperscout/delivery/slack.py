import os, logging, requests

log = logging.getLogger(__name__)

def post_to_slack(rows: list[dict]):
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    token = os.getenv("SLACK_BOT_TOKEN")
    channel = os.getenv("SLACK_CHANNEL", "#general")
    text_lines = []
    for r in rows:
        line = f"• *{r.get('title','')}* ({r.get('venue','?')}, {r.get('year','?')}) — score {r.get('score',0):.3f}\n{r.get('url') or r.get('pdf_url') or ''}"
        text_lines.append(line)
    text = "*PaperScout — Daily brief*\n" + "\n".join(text_lines[:50])
    if webhook:
        try:
            requests.post(webhook, json={"text": text}, timeout=20)
        except Exception as e:
            log.warning("Slack webhook failed: %s", e)
        return
    if token:
        try:
            requests.post("https://slack.com/api/chat.postMessage",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"channel": channel, "text": text}, timeout=20)
        except Exception as e:
            log.warning("Slack bot post failed: %s", e)
    else:
        log.info("No Slack creds found; skip")
