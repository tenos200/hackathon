# Handoff validation report

Checked 3 October 2026. These checks validate the specification package and invented interface fixtures, not a production application or scientific results.

| Check | Result |
|---|---|
| OpenAPI 3.1 specification | Valid; contract 1.0.0 |
| Public schemas | 40 |
| Endpoint inventory | Seven product routes plus two health routes |
| Synthetic response fixtures | 44; all validate against named schema and route/status |
| Combined fixture upload file | Same manifest and bodies as individual files |
| Dependency-reference occurrences | 150 checked against the fixture graph |
| Quote occurrences | 16 code-point slices checked, including text after an emoji |
| Complete synthetic text hash and window bounds | Checked |
| Score contribution sums and reference coverage arithmetic | Checked |
| Incomplete-reference numeric score | Required null in the supplied example |
| Deliberately invalid payloads | Four rejected: score out of range, missing profile level, extra field, invalid data mode |
| Master acceptance matrix | T01-T38 present; implementation tests remain to be built |

## Reproduce

In an isolated Python environment, install `contracts/requirements-validation.txt`, then run `python contracts/validate.py` from the extracted package. Validation is offline. Validators used here: {'jsonschema': '4.26.0', 'openapi-spec-validator': '0.9.0'}.

## Shared-file checksums

- `contracts/openapi.json`: `3eedc557dc8be2e7d82e3bb00213f6a7fa906141de72b246fe115771b53b845c`
- `contracts/fixtures.json`: `ca02ddde6de94dcbeac208d154c7e49df4cf1b1e196e2db8489c133455f34355`

## What has not been claimed

No backend, frontend, hosting account, database, public deployment, live model run or reviewed scientific snapshot was implemented by this planning task. Synthetic source hashes/review values demonstrate transport fields; they are not real reviewed publications or production snapshot fingerprints. Optional scenario overrides are isolated component examples, not a scientifically consistent merged snapshot. Real API/browser, database permission, scientific fidelity, cold-start and cost checks belong to implementation acceptance.

The separate `contracts/medrxiv_smoke_manifest.json` records an actual prior read-only source-API check. The remote response contained a preprint version and a `published: NA` sentinel. Its abstract is not redistributed in this package. Source availability and biological validity are separate questions.
