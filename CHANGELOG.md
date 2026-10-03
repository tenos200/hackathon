# Changelog

## 0.1.0 — 2026-10-03

- Contract 1.0.0 public API: strict DTOs, seven `/v1` routes, `/healthz`, `/readyz`, committed `/openapi.json`, shared
  error envelope for 404/405/422/429/500/503, CORS including error responses, bounded in-process rate limiter.
- Explicit `synthetic_fixture` mode serving the unchanged shared fixtures (checksums verified) with optional scenario
  overlays; `real` mode pinned to one validated snapshot from PostgreSQL or a local package file.
- Pipeline: approved-URL caching fetcher (direct, Bright Data, recorded imports), canonical text and Unicode spans,
  HPO/Mondo/HGNC/PubMed/medRxiv/ClinicalTrials.gov/GenCC/GO adapters, community/asset/study/person imports, typed alias
  index, structural checks, OpenAI client with recorded replay and a shared durable budget ledger, extraction and
  contextual checking stages, review queue/import bound to content fingerprints, catalog freeze and impact reports.
- Analytics: separate disease-baseline and subgroup weighted Jaccard with OMIM IC reference, reference-coverage
  withholding, mechanism comparison cards, neighborhoods with ranked/unranked cards and counterexamples.
- Assembly: publication rules, conflict dispositions, opportunities/gaps/explanations, canonical content-addressed
  package with an operational-timestamp-free hash projection.
- Storage: private `atlas` schema migration, immutable tables, least-privilege roles, transactional publisher and
  read-only reader. Render blueprint without secrets.
- Tests for T01–T38 at offline, local-database, static and local-browser boundaries; generated test matrix.
