# Delivery channels

## Contract first

Every briefing is a `Report` (`lark/report/contract.py`, JSON Schema in
`docs/report-contract/v1.schema.json`, example in `example.json`). A channel receives
the whole `Report` and adapts it to its medium. It may shorten or omit parts, but it
must not add content that isn't in the report, and it must keep two labels intact:

- the **reading depth** (abstract / focused / full / technical), and
- **"Lark's inference"** on connections to your research, so it is never mistaken for a
  finding of the paper.

Three renderers cover most media: `render(report, "markdown" | "html" | "text")`. The
chat-style channels share `short_text()` in `channels/_common.py`.

`deliver_all(ctx, report)` sends to every channel listed in `delivery.channels`.

- **Idempotent:** a channel that already has a `sent` delivery for that report is skipped.
- **Isolated:** one failing channel doesn't stop the others.
- **Recorded:** each outcome is saved as a `Delivery` record.
- **Dry run:** sends nothing. Email writes an `.eml` to `out/`.

## Adding a channel

```python
# lark/report/channels/mastodon.py
import os

from lark.context import RunContext
from lark.report.channels._common import chunk, post, short_text
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report


class MastodonChannel:
    name = "mastodon"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        ok = bool(os.getenv("MASTODON_URL") and os.getenv("MASTODON_TOKEN"))
        return ok, "MASTODON_URL, MASTODON_TOKEN"

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        parts = chunk(short_text(report, max_items=5, bold=""), 480)
        if ctx.dry_run:
            return DeliveryResult(self.name, "skipped", f"dry run: {len(parts)} posts")
        headers = {"Authorization": f"Bearer {os.environ['MASTODON_TOKEN']}"}
        for p in parts:
            post(f"{os.environ['MASTODON_URL']}/api/v1/statuses",
                 json={"status": p, "visibility": "direct"}, headers=headers)
        return DeliveryResult(self.name, "sent", f"{len(parts)} posts")
```

Then register it in `REGISTRY` (`lark/report/channels/__init__.py`), add its secrets to
the workflows, and list it in `delivery.channels` in `lark.yaml`.

## Free options

| Channel | Name | Setup | Good for | Limits / tradeoffs |
|---|---|---|---|---|
| Gmail SMTP | `email` | App password on a Gmail account | Full briefing with rich HTML, evidence quotes, and feedback links; searchable archive in your inbox | Gmail caps sending (hundreds/day, irrelevant here); app passwords need 2-Step Verification |
| Archive (Markdown + JSON) | `archive` | None | Permanent record in `lark-data/briefings/`; open as an **Obsidian vault** or browse on GitHub | Not a notification |
| RSS/Atom feed | via `archive` | Generate `feed.xml` from `briefings/*.json` (small script); serve from a private gist or a Pages site | Reading in a feed reader | Private repos can't use free GitHub Pages; a public feed would expose your profiles' matches |
| Telegram bot | `telegram` | @BotFather → token; message the bot; get chat id | Instant mobile push with the short version | 4096 chars/message (Lark chunks); no rich layout |
| Discord webhook | `discord` | Server → channel → Integrations → Webhooks | Short version in a private server | 2000 chars/message (chunked) |
| Slack incoming webhook | `slack` | Free workspace → app with incoming webhook | Short version alongside work chat | Free plan hides history older than 90 days |
| ntfy.sh | `ntfy` | Pick an unguessable topic; install the ntfy app | One-line phone push ("3 items today") with a tap-through link | Public topics are readable by anyone who guesses the name; self-host or use a token for privacy |
| Notion (free plan) | `notion` | Integration token + share a database with it | A browsable page per report | 100 blocks per page creation (Lark truncates); API changes occasionally |

A good free combination: **email** (full briefing) + **archive** (record) + **ntfy** or
**Telegram** (a mobile nudge).
