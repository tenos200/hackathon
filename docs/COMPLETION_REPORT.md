# Completion report — Rare Disease Atlas backend

Date: 2026-10-03 · Branch: `claude/pensive-noether-bhf0g8` · Contract 1.0.0 (unchanged; checksums verified)

## Summary

Phases A–D of the backend spec are implemented as working code with reproducible commands, migrations, deployment
configuration and tests. Phase A (the fixture API Lovable needs) was pushed first. **What is not done is the real
scientific snapshot and every deployment.** The build environment's network policy blocked all scientific sources,
OpenAI, Render, Supabase and Bright Data, and the human gates (demo packet, hour-one context audit, review decisions)
belong to people. No data, reviewer decisions, mechanism contexts, subgroup phenotypes, source mappings or reuse
permissions were invented. The real-pipeline config is committed with deliberate nulls. Run against it, every stage
records failed coverage, and assembly refuses until a person sets the release metadata.

## What was built

| Area | Where | Notes |
|---|---|---|
| Public DTOs, seven routes, health, errors, CORS, rate limit | `atlas/api/` | Drift test: DTO schemas equal the committed contract field-for-field |
| Fixture mode | `atlas/api/fixture_store.py` | Unchanged fixtures, checksum-verified; optional scenario overlays |
| Real mode | `atlas/api/real_store.py` | Pinned snapshot; hash recomputed; every route payload schema-validated before ready |
| Strict domain records, predicate contract | `atlas/models/` | Unknown fields rejected; content-derived IDs; per-type entity properties |
| Sources | `atlas/sources/` | Approved-URL caching fetcher (direct / Bright Data / recorded import), bounded retries, rate limits, canonical text + Unicode spans, adapters for HPO, phenotype.hpoa, Mondo OBO + SSSOM, HGNC, PubMed EFetch, medRxiv details, ClinicalTrials.gov v2, GenCC, GO GAF, web pages; section 8 imports |
| Linking | `atlas/linking/` | Exact ID → identity aliases; ambiguity returns all; non-identity synonyms only retrieve |
| Extraction / checking | `atlas/extraction/`, `atlas/checking/` | One configurable OpenAI client (Responses API, strict JSON schema), recorded replay, shared ledger; structural checks only (no keyword semantics) |
| Review gates | `atlas/review/` | Fingerprints over content and dependencies; queue HTML + template; import rejects untouched, stale, structural-override and reasonless disagreements; catalog freeze/impact |
| Analytics | `atlas/analytics/` | Two separate profile levels, OMIM IC reference, coverage withholding, mechanism cards, neighborhoods, counterexamples |
| Assembly / package | `atlas/assembly/` | Section 6 rules, conflicts, opportunities, gaps, explanations; canonical content-addressed package |
| Storage | `migrations/`, `atlas/storage/` | Private schema, immutable tables, least-privilege roles, RLS, transactional publisher, read-only reader |
| CLI | `python -m atlas …` | `sources, backbone, extract, check, review-export, review-import, catalog-freeze, catalog-impact, assemble, validate, publish, serve, migrate, budget-status, fixture-check` |
| Deployment | `render.yaml`, `.python-version`, `requirements*.txt`, `.env.example` | API runtime excludes the OpenAI SDK and fetch client |
| Handoff | `handoff/backend_ready.json` | Explicitly NOT DEPLOYED; only checks actually run |

## Verification, by boundary

Generated matrix: [`TEST_MATRIX.md`](TEST_MATRIX.md) (from `reports/master_tests.json`): 86 tests, 98 recorded master-test
executions, 0 non-passing.

| Boundary | Result | What it does and does not show |
|---|---|---|
| Shared contract validation | `contracts/validate.py` reproduces the handoff numbers exactly (40 schemas, 44 fixtures, 150 references, 16 quotes, 4 negatives) | The package is intact |
| Fixture API over real HTTP (`offline-fixture`) | All 34 default fixtures served verbatim and schema-valid; scenario overlays; 404/405/422/429/500/503 envelopes; readiness failures | Interface only; invented data |
| Synthetic source-format pipeline (`offline-unit`) | Full pipeline from recorded synthetic captures through review stand-ins, assembly and real-mode projection; every route schema-valid | Pipeline behavior with invented records and **scripted** model output; says nothing about model quality or real biology |
| Local PostgreSQL 16 (`database-local`) | Migrations (idempotent), read-only API login, revoked `anon`/`authenticated`, immutability triggers, idempotent publish, rollback leaves prior snapshot intact, API serving through the read-only role | Not Supabase: exposed-schema settings, pooler, TLS and real custom-role grants still to check |
| Headless Chromium (`browser-local`) | Allowed origin reads success/404/422 and `X-Request-ID`; encoded colon IDs; disallowed origin blocked (`reports/browser_cors_check.json`) | Not Lovable, not deployed, no journey UI |
| Static config | Render blueprint has no secrets and the specified commands; API import graph cannot reach pipeline/model/fetch code | Not a Render deployment |
| Local latency | Warm p95 ≈ 2 ms in both modes (`reports/latency_local.json`) | Not Render, not cold start |
| Fresh checkout (T25) | A fresh clone, pinned install and full suite passed; the offline CLI chain reproduces the in-process snapshot ID | Package install needs an index; tests block external network |
| **Real sources** | **Not run.** NCBI, HGNC, OBO (HPO/Mondo), medRxiv, ClinicalTrials.gov blocked (proxy 403); an online `sources` run recorded each as failed coverage | No real records exist |
| **Live model** | **Not run.** No key, $0 budget, api.openai.com blocked. Model cost: **$0** | The track-required real OpenAI extraction/check run is still outstanding |
| **Supabase / Render / Lovable** | **Not run.** Nothing provisioned or deployed | T23 and the deployed halves of T37/T38 are open |

## Defects the tests caught and fixed

- Canonical ID ordering in the package dropped the ranked-first card order (restored at projection).
- An auditor's identity leaked into published context audits (only defined audit fields are now published).
- Operational `searched_at` timestamps changed the snapshot ID across identical runs (canonical hash projection added).
- Repeated reports of one cohort counted as independent publications (families sharing a cohort now merge).
- The disagreement rule over-flagged positive records with different positive statuses (narrowed to polarity and
  direction).
- Reviews of removed records were treated as stale and blocked assembly (now warnings).
- An accepted explanation citing unpublished assertions caused a dangling reference (now withheld, templates used).
- The NCBI contact email and API key could have entered recorded URLs and public coverage (now never recorded).

## Contradictions and interpretations

See [`CONTRACT_NOTES.md`](CONTRACT_NOTES.md). Three need a human decision before freezing the real snapshot:

1. Excluding operational timestamps from the hash.
2. `published_at` as the release date of record.
3. A person, not the model, assigning literature claims to contexts.

## Remaining work, in the order the plan's gates need it

1. **Human, hour 0–2:** fill `config/demo_packet.json` and `config/context_mapping_audit.json` from real sources. Check
   existing exact-disease resources first (e.g. FamilieSCN2A's listed registries) and pick contexts justified by
   sources (SCN1A DEE6A/6B checked separately).
2. **Network/credentials:** allow the source hosts in the environment's network settings, or run the pipeline on a team
   machine. Pin versioned HPO/Mondo URLs in `config/targets.json`. Re-check the external API details listed in
   `CONTRACT_NOTES.md` item 15 against live documentation.
3. **Sources → backbone → imports → catalog freeze**, with exact quotes from captured pages. Add `expect_text` to each
   approved URL.
4. **OpenAI:** choose and verify the model and pricing (`config/models.json`), set caps (`config/budget.json`), run
   `extract`/`check` on selected PMIDs with a locked 10-abstract evaluation set. Report numerators and denominators
   separately from these fixture tests.
5. **Human review** of every public assertion, context, conflict, opportunity and explanation; then `assemble`,
   `validate`, and `publish` to Supabase with the publisher login.
6. **Deploy:** create the Supabase project and roles, connect the Render Blueprint, set the API env with the exact
   Lovable origins, confirm `/readyz`, measure cold start, update `handoff/backend_ready.json`, and run the joint browser
   journey (T23) with the frontend.
