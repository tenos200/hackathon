# Contract change proposal: 1.1.0 (additive)

Status: **proposed**. Contract 1.0.0 (`contracts/openapi.json`, fixtures, envelopes) is unchanged and still served at
`/openapi.json`. Both repositories must agree before the frontend relies on these routes.

## Reason

The challenge brief (`docs/challenge/hackathon5.pdf`) asks for a navigable knowledge graph, explainable clusters
("Mechanistic overlap"), one global search across diseases, genes, symptoms, groups and mechanisms, and "Network overlap"
(two unrelated communities sharing a key researcher or funder). Contract 1.0.0 exposes per-context cards and a small
optional graph only.

## Additions

| Route | Schema | Purpose |
|---|---|---|
| `GET /v1/entities/{id}` | `EntityResponse` | Any published entity (gene, disease, phenotype, organization, person, study, asset, process) with its published assertions (max 200) and contexts |
| `GET /v1/graph?focus=<id>&depth=1\|2` | `GraphResponse` | Bounded neighborhood (60 nodes / 100 links). Every sourced link names `assertion_id`; computed links name `calculation_id`; `context_scope` links come from reviewed context definitions |
| `GET /v1/clusters` | `ClustersResponse` | Descriptive facets with stated basis: recorded functional effect, shared gene, phenotype neighborhoods (connected top-ranked similarity links) |
| `GET /v1/contexts/{id}/network` | `NetworkResponse` | People and organizations around a context, and overlaps with otherwise unconnected contexts (different disease, no shared gene) |
| `GET /openapi-extension.json` | — | This extension's OpenAPI document |

Envelopes keep `contract_version: "1.0.0"` so existing clients' envelope validation still passes; errors use the shared
error envelope (404/422/429/500/503); CORS and rate limits are unchanged.

New **predicate values** (the `predicate` field is already a free string in `AssertionData`): `mentions` (study/asset
text names a gene/disease; no relationship asserted), `investigator_on` (person listed on a funded/registered project),
`funds` (organization recorded as funder). They come only from structured source records, never from model extraction.

## Fixtures

`contracts/extension-1.1.0/fixtures.json`: synthetic example responses projected from the offline test world, served in
`synthetic_fixture` mode with the `SYNTHETIC_DATA` warning. Regenerate with `python scripts/generate_extension_contract.py`;
a test fails if the committed files drift from the code.

## Frontend impact

Optional. A graph view can call `/v1/graph`; a cluster view `/v1/clusters`; entity pages `/v1/entities/{id}`; the
collaborator panel `/v1/contexts/{id}/network`. Render `mentions` links as "named in text", never as a relationship, and
present shared people as leads to ask about, not endorsements.

## Acceptance checks

`tests/test_extension.py`: schema validation over real HTTP, link provenance (assertion or calculation for every non-scope
link), bounds, 404/422 envelopes, fixture-mode examples, and drift against the committed extension contract.
