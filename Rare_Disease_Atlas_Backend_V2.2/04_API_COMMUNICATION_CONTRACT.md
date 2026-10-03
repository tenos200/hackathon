# Shared API and agent communication contract

Version 1.0.0 · Applies equally to frontend and backend

## One interface

Lovable's browser makes read-only HTTPS requests directly to Render FastAPI. FastAPI reads one pinned Supabase snapshot. No browser Supabase client, server secret, authentication cookie, frontend AI call, or live pipeline invocation is part of this interface.

The machine-readable authority for wire shapes is `contracts/openapi.json` (OpenAPI 3.1). All fields listed as required must be present, even when their value is null. Unknown fields are rejected by the supplied schemas. Internal domain records in the master plan are not identical to these public DTOs.

| Method and full path | Purpose | Data schema |
|---|---|---|
| GET `/v1/meta` | Snapshot, capabilities, source versions and example contexts | MetaData |
| GET `/v1/search?q=...` | Typed matches and explicit context choices | SearchData |
| GET `/v1/contexts/{id}` | Research scope, definitions and two profile inputs | ContextData |
| GET `/v1/contexts/{id}/connections` | Comparisons, unknowns, optional graph | ConnectionsData |
| GET `/v1/assertions/{id}` | Sourced statement, context, supporting/opposing evidence | AssertionData |
| GET `/v1/calculations/{id}` | Algorithm, inputs, score contributions and limitations | CalculationData |
| GET `/v1/contexts/{id}/actions` | Exact assets, cautious leads and coverage-scoped gaps | ActionsData |

Operational routes: `/healthz` for liveness, `/readyz` for validated pinned-snapshot readiness, and `/openapi.json` for API documentation. The two health routes use their small Health shape rather than the product envelope. They are outside the `/v1` frontend base.

## Envelopes

Success:

```json
{
  "contract_version": "1.0.0",
  "snapshot_id": "snap_fixture_20261003",
  "data_mode": "synthetic_fixture",
  "data": {"query": "unknown-fixture", "matches": []},
  "warnings": [{"code": "SYNTHETIC_DATA", "message": "Invented interface fixtures. Not scientific evidence."}]
}
```

Product error:

```json
{
  "contract_version": "1.0.0",
  "snapshot_id": null,
  "error": {
    "code": "SNAPSHOT_UNAVAILABLE",
    "message": "The reviewed snapshot is not ready.",
    "request_id": "example-request-id",
    "retryable": true
  }
}
```

Use HTTP status, not a successful status with an embedded error. Errors: 404 unknown resource; 422 invalid input; 429 rate limited; 500 unexpected failure; 503 unavailable publication/service. Framework 422 responses must be adapted to this shape. `snapshot_id` may be null when no snapshot is available. No stack trace or internal configuration enters the public message. A genuine successful empty search is 200 with `matches: []`.

Search query is 1-200 characters after trimming; reject whitespace-only. Use URL encoding, never raw SQL/filter input. Search responses cap at 20 matches and 20 contexts per match; truncation is explicit in warnings. IDs are opaque strings up to 180 characters and may contain colons; clients encode them once. Unknown IDs are 404. Resource data is read-only; no mutation verbs or review/admin routes are public.

## Meaning of important fields

- `data_mode`: `real` means an actual reviewed source snapshot; `synthetic_fixture` means invented interface data. This is separate from the frontend's local `mock|live` transport selection.
- `profile_level`: a Context uses `disease|subgroup`; a Score uses `disease_baseline|subgroup`. Preserve that intentional distinction.
- `score: null`: unavailable at this profile level. It must not become zero or inherit the other level. `calculation_id` can be non-null for a calculation that documents why a result is unavailable.
- `direct_term_counts` and `publication_counts`: show beside each score. Publication counts deduplicate publication families; they are not an independent-replication count. Derived ancestors do not add observed terms/publications.
- `mapped_annotation_ids`: exact-mapped source disease IDs contributing annotations, retained per profile and per calculation side. Literature-only subgroup fixtures have empty lists; the source assertions carry their provenance.
- `reference_coverage`: direct terms assessed in the pinned reference, plus unassessed IDs. Any unassessed direct term withholds the numeric score and ranking at that level. Profiles may still be inspected qualitatively.
- `same_parent_disease`: explains a potentially identical disease baseline; it says nothing about subgroup equivalence.
- `ranking_basis`: backend decision; frontend does not invent an alternative or blend levels. A null subgroup score cannot rank a subgroup comparison using disease baseline.
- `Sentence`: displayed reviewed text with `assertion_ids`, `calculation_ids`, `opportunity_ids`. Each substantive sentence needs at least one resolving dependency. Opportunity IDs resolve within the actions payload; there is no extra opportunity endpoint. A calculation's explanation may reference that calculation; rendering does not recursively fetch itself.
- `review.status=accepted`: faithful representation accepted for publication, not proof of biology. `expert_validation` must reflect an actual qualified review. Synthetic fixtures explicitly disclaim scientific review.
- `Source.metadata`: nullable fields are retained for server, DOI, manuscript version, license, later published DOI, publication family, fetch backend and original target URL. A later article link does not relabel the captured preprint version or establish independence.
- `kind=structured_record`: render locator and field/value pairs. Text evidence uses context plus highlights. A source with `public_text_policy=link_only` has no redistributed source text.
- `access_status`/`readiness`: render faithfully. Unknown access and unknown compatibility do not imply permission or suitability.
- `coverage.completion`: completeness of that executed query, not of all possible knowledge. Partial/failed source access is not evidence of absence.

Text context has original canonical `[canonical_start, canonical_end)` bounds; highlights have `[start,end)` offsets relative to the returned window. All are Unicode code points, end-exclusive. Require `text[start:end] == quote` in code-point space. Source hashes identify the original canonical version, not necessarily the displayed excerpt. Window length equals `canonical_end-canonical_start`. Public context contains all necessary reviewed qualifiers. Store private originals separately.

The source and EvidenceView IDs remain versioned. If one evidence record needs disjoint permitted windows, backend may project separate stable view IDs tied to that evidence/window; retain the source version and original offsets. Do not silently clip a quote to fit a fixed card height.

## Browser transport

Frontend base: `VITE_ATLAS_API_BASE_URL=https://<actual-render-host>/v1`.

Append `/meta`, `/search`, etc. to this base; do not use a root-relative URL constructor that discards `/v1`. Requests use `Accept: application/json`, `credentials: omit`, no Authorization header and an AbortController timeout. Backend permits GET/OPTIONS from the exact published and development/preview origins supplied by the frontend. Origin strings have scheme/host/port, no path or trailing slash.

CORS is browser interoperability, not access control: the read API is intentionally public. Configure no credentialed wildcard. Error responses need the same CORS headers so the UI can read them. `X-Request-ID` can additionally mirror the body ID and be exposed by CORS. If Retry-After is emitted for 429, expose that header too.

Request flow: meta -> search -> user selects context -> context -> connections/actions in parallel -> assertion/calculation on demand. Cache only within the served snapshot; include API base, snapshot and resource ID. No frontend polling or live subscriptions are required.

The server pins one snapshot at startup. Every successful product response returns its ID. No request `snapshot_id` parameter and no 409 recovery protocol are required or defined in contract 1.0.0. The master's optional pin hardening is deferred unless both sides amend this contract. Freeze deployments during the demo. On an intentional update, reload and clear cached data; continuous consistency across a mid-session redeploy is not claimed.

## Shared fixture protocol

`contracts/fixtures/manifest.json` lists method, route, HTTP status, response schema, file and scenario. `contracts/fixtures.json` combines that same manifest with `responses` keyed by filename for convenient upload. They are two packaging forms of the same records, not separate datasets. `VALIDATION_REPORT.md` identifies the tested checksum.

Default records form a coherent synthetic world with explicit ambiguity, same-parent baseline overlap, missing subgroup evidence, source paragraphs, calculations, asset lead and gap. Other scenarios include no results, contested evidence, incomplete IC reference and error statuses. Run scenario overrides independently in the relevant component test; they are not a scientifically valid merged snapshot. Never publish them as real findings.

Both repositories copy the same schema and fixture set and record its SHA-256. Backend HTTP contract tests use the schema, not merely a snapshot string comparison. Frontend types/client/component tests exercise the same examples. The example meta hash and fixture IDs illustrate transport; only the real publisher applies production content-addressing/review rules.

## Agent-to-agent handoff and contract changes

The human relays short handoffs between environments. No shared filesystem, automatic chat access or ability to send messages is assumed. Do not place secrets in handoffs.

Frontend's first handoff: contract version and checksum; preview/published origin(s) actually in use; fixture journey status; pending URL needed. Backend's first handoff: contract version and checksum; actual API base, `/readyz` and OpenAPI URLs; served data mode/snapshot; allowed origins; example context IDs; tested/error cases; known limitations.

Frontend then configures the returned base URL, rebuilds and reports the published app URL plus real-browser CORS/journey results. Backend configures each exact origin before testing. See the JSON templates in deployment notes.

Changes to fields, enums, paths, requiredness or meaning need a written contract-change proposal: reason, affected schemas, fixture changes, frontend impact and acceptance checks. Backend owner maintains the shared contract; both sides consume the identical amended file. Human resolves scope/scientific tradeoffs. Do not silently add an endpoint to get around a UI problem, allow arbitrary fields or return different shapes in mock and live modes. Freeze contract 1.0.0 for the hackathon unless a blocking contradiction is found.

## Joint integration acceptance

1. `/readyz` returns ready for the intended mode and snapshot; all product payloads validate.
2. Real Lovable preview and published origins can call the API, including readable error responses.
3. Search -> explicit context -> existing asset -> comparison -> evidence/calculation -> action/gap works.
4. Unicode highlight, ambiguous alias, null subgroup, sparse counts, partial reference, opposing evidence and error states are inspectable.
5. Footer/banner reflect server mode and snapshot. There are no browser secrets or DB calls.
6. Repeat the journey on a reviewed REAL snapshot before claiming the live scientific demo is complete; record blockers separately.
