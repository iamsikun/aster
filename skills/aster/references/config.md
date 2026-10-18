# Configuration

The user's settings live in one YAML file (`aster paths` shows where; `aster init` writes a
commented template). It is merged over the packaged defaults: mappings merge, lists replace,
and `null` removes an entry. Edit it only when the user asks, show them the change, and run
`aster fields` afterwards to confirm it loads.

## First-time setup

`setup.needed` in `aster fields --json` (also in `candidates --json` and `doctor --json`) is
true while there is no config file or its `fields:` is empty. The defaults (economics, machine
learning, quantitative finance, marketing) are only a stand-in, so ask before relying on them:

1. Ask, in one message, which research fields they follow and, for each, the journals,
   conferences (OpenReview venues such as `ICLR.cc/2027/Conference`), arXiv categories, working
   paper series and lab or blog feeds they care about. Ask also for a few phrases describing
   their own research (`interests`) and people to follow. Offer the defaults as a starting
   point they can trim.
2. Map the answers: journals to registry ids (`aster fields --json` lists each field's journals;
   the full registry is `src/aster/assets/journals.yaml` in the repository). A journal not in
   the registry goes under `journals:` with ISSNs you confirmed at
   `https://api.crossref.org/journals/<issn>`. Never guess an ISSN or a venue id; ask, or leave
   it out and say so.
3. Run `aster init` if there is no config file, then write the `fields:` section (and
   `interests`, `follows`, `feeds`, `journals`). Show the user the result. Default fields they
   did not mention stay active unless they mute them (`tier: mute`) or remove them (`null`), so
   ask about those too.
4. Run `aster fields` to confirm the config loads and `setup.needed` is now false, then
   `aster refresh`.

A user who simply wants the defaults ends setup by listing them, e.g.
`fields: {economics: {tier: core}}` for each field kept.

## Fields

The defaults are `economics`, `machine-learning`, `quant-finance` and `marketing`, all `core`.

```yaml
fields:
  marketing:
    tier: skim                 # core | skim | mute
  machine-learning:
    openreview: [ICLR.cc/2027/Conference]
  statistics:                  # a new field
    name: Statistics
    tier: core
    arxiv: [stat.ME, stat.TH]  # categories, or a whole archive such as q-fin
    journals: [aos, jasa, jrssb, biometrika]
    nep: [nep-ecm]
    keywords: [conformal prediction, multiple testing]
    search:
      sites: [x.com, bsky.app]
      queries: [new statistics methods paper]
```

A field may also set `nber: true`, `feeds:` (see below) and `hf_orgs:`. Keywords place an item
from any source into the field when one appears in the title, or two in the abstract, so
choose specific phrases.

## Journals

Fields refer to journals by id from the packaged registry (`aster fields --json` shows names).
To follow another journal, add it with its ISSNs (check them at
`https://api.crossref.org/journals/<issn>`), then list its id in a field:

```yaml
journals:
  - {id: jmp, name: "Journal of Marketing Policy", issn: [1234-5678, 2345-6789]}
```

## Interests and follows

```yaml
interests: [heterogeneous treatment effects, LLMs as simulated consumers]
follows:
  authors: [Susan Athey]       # or OpenAlex ids such as A5023888391
  labs: [Allen Institute]
  hf_orgs: [allenai]
  repos: [vllm-project/vllm]
```

Interests rank items within fields; follows lift an item regardless of field and are also
queried through OpenAlex.

## Your own sources

Any RSS or Atom feed:

```yaml
feeds:
  - {name: My lab, url: "https://example.org/feed.xml", kind: release, fields: [machine-learning]}
```

`kind: paper` is for a journal's feed. Turn a built-in source off with
`sources: {openreview: {enabled: false}}`.

## Environment

Optional variables: `ASTER_CONTACT_EMAIL` (polite pools of Crossref and OpenAlex),
`OPENALEX_API_KEY`, `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD` (OpenReview venues),
`GITHUB_TOKEN`. `aster doctor` shows which are set and how each source last ran.
