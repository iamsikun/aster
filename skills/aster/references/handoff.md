# Handoff

Aster finds things worth reading; it does not read them in depth or keep notes.

- **Reading.** When the user picks an item to understand, hand it to the Finch skill if it is
  installed ("read this with Finch"), passing the primary link. Otherwise offer a short summary
  from the abstract and say that is all it is.
- **A knowledge base.** If knowledge-base tools are connected (for example Cairn's `search` and
  `save_reading`), search them for each item before recommending it, and mention when the user
  already has a note on it or on its topic. Save to the knowledge base only when the user picks
  an item or asks; a brief is not a reading session.
- **Scheduling.** Aster has no scheduler. A scheduled job (cron, a gardener, CI) can run
  `aster refresh` and `aster candidates --json`, and then an agent with this skill for the web
  searches and the write-up. Mark items as shown with `aster seen ... --context <job>` so
  interactive briefs do not repeat them. A job that tracks its own discoveries can instead pass
  `--include-shown --match-file <topics> --first-seen-since <last run>`, and check freshness with
  `aster doctor --json`.
