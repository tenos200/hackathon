# Shared contract files

`openapi.json` is the common wire contract. `fixtures/manifest.json` maps each invented response to a route/status/schema and scenario. `fixtures.json` combines the manifest and response bodies for a single upload to Lovable. Do not edit one fixture representation without regenerating and validating the other.

`validate.py` checks OpenAPI, response schemas, endpoint/status shapes, fixture dependencies, Unicode quote bounds and selected numeric invariants. It runs without network after installing `requirements-validation.txt` in an isolated environment. The tests are interface checks, not scientific evaluation.

`medrxiv_smoke_manifest.json` is provenance for an earlier real source-route check; it is not a fake biological fixture or a bundled abstract. Mock frontend records remain completely synthetic. The shared package checksum is listed in `VALIDATION_REPORT.md`.
