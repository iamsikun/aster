# Configuration

The user's settings live in one YAML file (`aster paths` shows where; `aster init` writes a
commented template). It is merged over the packaged defaults: mappings merge, lists replace,
and `null` removes an entry. Edit it only when the user asks, show them the change, and run
`aster fields` afterwards to confirm it loads.

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
