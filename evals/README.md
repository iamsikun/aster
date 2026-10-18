# Evals

`cases.json` lists scenarios for checking that the skill activates when it should, follows its
workflow and keeps its rules (verified links, labelled provenance, no invented numbers).

Run a case by giving its `prompt` to an agent with the skill installed, in a scratch folder with
`ASTER_CONFIG` and `ASTER_DATA_DIR` pointing at temporary paths. Score each `expect` and
`must_not` line by hand as met or not, and note the date: answers depend on what is published
that week, so compare behaviour, not items. `expect_activation: false` cases check that the
skill stays out of the way.
