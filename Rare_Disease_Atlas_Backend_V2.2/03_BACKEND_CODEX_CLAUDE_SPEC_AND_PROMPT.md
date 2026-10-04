# Python backend — build specification and prompt

Status: ready for implementation · Master plan 2.2 · Contract 1.0.0

## Goal

Implement the backend for a 24-hour Rare Disease Atlas research-navigation demo. It connects a source-bounded research context to inspectable evidence, explainable phenotype neighborhoods, existing assets and cautious next research questions. It is not Jacq's causal simulation engine, a diagnosis tool or an automated treatment recommender.

Read `01_MASTER_PLAN_V2.2.md` for the source/predicate rules, algorithms, domain models, review gates, CLI commands, time cuts and tests T01-T38. This role combines its D1 and D2 packages. Read the public API contract before implementing response DTOs. The master plan's internal domain records and the public DTOs are different: project public fields deliberately, retain required provenance, and never serialize private review records automatically.

## Hosting and process boundaries

Python FastAPI runs on Render. Published immutable snapshots live in a private `atlas` schema in a team-owned Supabase Postgres project. Lovable calls FastAPI directly over HTTPS. No Supabase Edge Function rewrite, direct browser database connection, backend model call on page load, or ingestion on API startup.

Offline pipeline credentials and the publisher role are separate from the public API's read-only DB role. The API starts only against `ATLAS_SNAPSHOT_ID`, validates it, and reports readiness. Source/model keys must not be installed in the API service. A deployed fixture mode is explicitly opt-in and visibly synthetic; it must never satisfy real snapshot acceptance.

## Files and ownership

Create a new backend repository, suggested name `atlas`. Use the following areas; choose concrete files within these areas as implementation requires:

```text
atlas/models/        strict internal domain schemas
atlas/sources/       pinned adapters, canonicalization, source caches
atlas/linking/       identifiers, typed synonyms and ambiguity
atlas/extraction/    candidates and configurable model adapter
atlas/checking/      independent contextual checks
atlas/review/        review artifacts, fingerprints and impact reports
atlas/analytics/     HPO reference, two profile levels, neighborhoods
atlas/assembly/      publication projection and deterministic snapshots
atlas/storage/       Postgres publisher/reader
atlas/api/           public DTOs, seven routes, common errors, health
atlas/__main__.py    master plan CLI commands
config/             target/source pins, approved URL list, budgets, prompts
contracts/          supplied OpenAPI, fixtures and checksum records
migrations/         private schema, snapshot tables, least-privilege grants
scripts/            setup, smoke checks and deployment support
handoff/            non-secret backend readiness information
tests/             unit, fixture replay, database and API integration
pyproject.toml, requirements.txt, .env.example, .gitignore,
render.yaml, README.md, CHANGELOG.md
```

Do not touch or copy Jacq repositories. Do not add frontend components. No generic orchestration framework, ontology-wide knowledge-graph platform or vector database is needed for this slice. Use validated JSONB records with focused indexes as the master plan specifies.

## Implementation sequence

### A. Contract, fixture API and deployment skeleton

Commit unchanged shared contract and fixtures. Implement typed public DTOs, the seven `/v1` routes, common product error envelope, `/healthz`, `/readyz`, CORS configuration and OpenAPI. Serve a fixture-backed journey with an explicit `ATLAS_DATA_MODE=synthetic_fixture` switch. It returns the supplied invented transport fixtures and synthetic notices, not a purported real content-hashed publication.

The fixture package's IDs/hashes are illustrative transport values. They are not input accepted by the real snapshot publisher. For real mode, construct and hash a validated canonical package according to the master plan. Keep the fixture loader and real storage reader behind the same read interface; reject missing/unknown runtime mode values rather than silently using fixtures.

Implement `atlas.api.main:app`. Render starts with `uvicorn atlas.api.main:app --host 0.0.0.0 --port $PORT`; build installs pinned `requirements.txt`. Commit a tested Python runtime choice. Include `render.yaml` without credentials and documentation of manual secret configuration. No deployment requires a paid model call.

Gate: offline fixture API tests, contract validation, and a browser-ready fixture endpoint. A real database is not required to test fixture mode. Do not spend the entire hackathon on extraction before proving frontend connectivity.

### B. Sources, context audit and first real snapshot

Implement selected adapters and CLI replay, strict domain schemas, immutable canonical text and Unicode spans. Audit source-justified Mondo contexts and exact HPO mappings before bulk ingestion. Record the audit in `config/context_mapping_audit.json`; retain null baselines for unmapped grouping classes. Do not narrow a source to a convenient leaf only to obtain annotations. Check separately scoped disease entries rather than merging gene-level conditions.

Use the entire pinned HPO label/alternate-ID/typed-synonym index. Exact Mondo mappings only; union multiple annotated IDs with original row provenance. Do not convert broad/related mappings into identity. HPO NOT remains negative evidence.

Implement review artifacts, dependency hashes and catalog freeze. Early mechanism contexts are allowed only with source-spanned manual imports and accepted review. Otherwise build the hour-8 journey with unknown mechanism. Add exact-disease community resources early, with source-supported ownership and access facts.

Implement migrations, transactional publisher and read-only API storage. Build a small real reviewed snapshot before pursuing cross-community scoring. Non-expert source-fidelity review must not be labeled scientific validation. The coding agent must not record human acceptance or invent reviewer identities on its own.

Gate: exact-resource journey, source/version/paragraph evidence, reproducible publish, unchanged prior snapshot on failure, read-only service role.

### C. Literature, checks and explainable analytics

Use one configurable OpenAI client for the track-required extraction/checking path; other model providers and JEV are deferred. Implement extraction and contextual checking stages, candidate linking, unknown/ambiguous queues, claim scope, supporting/opposing evidence and explicit disposition. Semantic classification cannot be a keyword heuristic. Ordinary text retrieval and alias matching are permitted; keyword presence cannot decide causal validity, direction, assertion status or truth. This application reports sourced relations; it does not establish causal effects from documents.

Require configured provider credentials and a positive shared paid-run budget; otherwise use recorded fixtures. One durable budget ledger covers extraction, checking and retries. Cache completed calls and record uncertain outcomes. Never assume repeat attempts are free. Human reviewer supplies acceptance; model agreement is insufficient.

The biological cap is 40 reviewed assertions, with an advisory allocation of 18 subgroup phenotypes, 12 baseline phenotypes, 8 mechanism/variant and 2 other relations. Operational asset/contact assertions are reviewed separately. These are priorities, not quotas: publish fewer if evidence is missing. Freeze selected catalog definitions/aliases by hour 8 and report affected reviews on later changes.

Implement the master plan's deterministic phenotype calculation separately at disease and subgroup levels. Subgroup phenotypes require accepted, scoped literature `has_phenotype` claims; disease-wide HPO never supplies subgroup differentiation. OMIM is the pinned IC reference. If any direct term lacks reference support, return null and no ranking at that level, even when ancestors have weights. Missing profiles and zero-weight denominators are null, not zero. Do not compute a selectively assessed subset without an agreed new algorithm.

Report direct terms, distinct publication families and coverage; ancestors do not inflate evidence counts. Same-parent disease baseline can be 1.0 while the subgroup score remains null. Mechanism comparisons retain tissue/assay/species uncertainty and never become an invented composite confidence score.

Add asset opportunities and scoped gaps using reviewed dependencies. Exact assets appear first. A cross-context lead is normally a request to investigate compatibility, not reuse permission. Explanations are deterministic individual-record descriptions or reviewed synthesis, never live generated advice.

Gate: master T01-T19 and T26-T36; evidence and explanation dependencies resolve; unsupported scientific narrative is withheld.

### D. Publication, real integration and freeze

Complete remaining master acceptance tests, deploy with the private-schema read role, configure actual Lovable origins, publish the reviewed real snapshot, and provide the backend handoff. Run the browser journey with the frontend agent/human. Test missing snapshot readiness, common error statuses, cold starts and non-cold response timing. Keep UI and server contract versions aligned.

Freeze the snapshot and deployment for judging. Report measured coverage, limitations, model cost and the difference between fixture tests and real scientific evaluation. An honest-gap demo is allowed if the desired biology cannot be supported. Do not generate the expected conclusion to make the demo succeed.

## API-specific acceptance

- Exact response shapes and required nullable fields match `contracts/openapi.json`; all seven routes validate against shared schemas using actual HTTP responses.
- Serve `/openapi.json` describing the implemented public routes. Compare generated documentation with the committed contract; no undocumented field/status drift. Extra operational metadata is acceptable only outside product DTOs.
- Framework validation errors become the shared 422 envelope, not FastAPI's default detail shape. Unexpected failures expose a request ID, not stack traces, credentials or SQL.
- Search is bounded to 200 characters; blank/whitespace-only is invalid. Limit search results to 20, contexts per returned entity to 20, comparisons to three ranked neighbors plus explicitly labeled counterexamples. Record truncation warnings. Graph is optional and bounded at 60 nodes/100 links.
- API reads never trigger paid calls, downloads, publication or writes. Rate limiting is a bounded in-process safeguard for a single demo instance or a configured platform control; it is not claimed as distributed protection. Return 429 in the shared envelope when applied.
- IDs resolve within one pinned snapshot. No mandatory request snapshot parameter or 409 protocol in contract 1.0.0. An optional pin extension requires an agreed contract amendment.
- Assertion DTO projection retains exact source versions, source metadata, scope, statuses, accepted-review summary/hash, opposing evidence and permitted context. Multiple distant spans may use an encompassing permitted context window; otherwise represent separate evidence views, each with a source/version reference and truthful original offsets.
- The source metadata contract includes license, preprint version, published DOI and publication-family ID. Preserve cohort IDs and independence status. Do not count a preprint and article as independent replication.
- `reviewed_content_sha256` is the hash of the reviewed substantive internal record/dependencies, not a hash of the public DTO or private review reasoning. Explain that distinction in code/docs.
- In real mode readiness requires DB access and complete validation of the pinned publication. If that fails, `/readyz` is 503 and product routes are unavailable; liveness may remain 200. No fallback to an arbitrary newest or fixture snapshot.

## Test plan and completion report

Implement the master tests T01-T38 with relevant offline, database and browser boundaries. Fixtures supplied here test the public interface; they are not the biological evaluation set. Use separate challenging semantic cases and source replay tests from the master plan. A source adapter should retain recorded, redistributable fixtures without requiring live network in normal tests.

For T31, use the provided medRxiv smoke manifest as a known route example and construct a small synthetic response fixture containing a pinned version, `license`, `published: "NA"` and a later article link. Normalize NA to null. Do not copy a restricted abstract into the public test repository. The live route was checked; that is not a guarantee of future uptime or scientific validity.

README must provide fresh-checkout offline instructions, model-budget setup, review/publish steps, local API startup, deployment, source limitations and “exploratory clustering through explainable similarity neighborhoods.” Report exactly which real integrations and browser checks remain blocked by external setup. No fabricated pass claims.

## Prompt to paste into Codex or Claude

```text
Implement a new standalone Rare Disease Atlas backend using the attached master plan revision 2.2, BACKEND spec, API COMMUNICATION CONTRACT, OpenAPI 1.0.0, fixtures and deployment notes. Do not use or modify Jacq source. These documents are the implementation specification; do not redesign the scientific scope or replace it with a generic graph platform.

Work through phases A-D in the backend spec. Begin with strict models, fixture replay and the seven-route API so Lovable can integrate immediately. Then implement pinned source adapters, contextual/versioned evidence, source-justified contexts, human review gates, deterministic two-level phenotype analytics, immutable publication and Supabase storage. Finish deployment configuration for Render-hosted FastAPI and the shared handoff. Complete useful offline work even when real credentials or hosting access are missing.

Use the exact shared wire schema and fixtures. Keep the browser/API/pipeline credentials separated. Do not run ingestion or model calls on API startup or HTTP requests. Paid calls require a positive configured budget and credentials; human acceptance requires an actual human decision. Never invent data, reviewer decisions, mechanism context, subgroup phenotypes, source mappings or reuse permission. Do not implement semantic judgments with keywords.

Validate master tests T01-T38 at their proper boundaries and report fixture checks separately from real-source, database, live-model and browser results. Read the source and hosting documentation linked in the plan before relying on external API details. Propose and explain genuine contract/spec contradictions; do not quietly drop requirements. Produce working code, reproducible commands, migrations, deployment configuration, a readiness handoff and an honest completion report. Do not stop at another plan or claim deployed resources that have not been created.
```
