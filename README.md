# Rare Disease Atlas backend (`atlas`)

Python backend for the Rare Disease Atlas research-navigation demo: a reviewed evidence pipeline, deterministic
phenotype analytics, immutable content-addressed snapshots in PostgreSQL (Supabase), and a read-only FastAPI service
(Render) that implements the shared **contract 1.0.0** for the Lovable frontend (`atlas-web`, separate repository).

It connects a source-bounded research context to inspectable evidence, explainable phenotype neighborhoods, existing
research assets and cautious next questions. It does **not** diagnose, recommend treatment, decide trial eligibility,
infer an individual's mechanism, or generate live prose. Built fresh from the specification in
[`Rare_Disease_Atlas_Backend_V2.2/`](Rare_Disease_Atlas_Backend_V2.2/START_HERE.md) (master plan 2.2, backend spec,
API contract, deployment runbook). No Jacq code is used.

**Status (honest):** code, migrations, deployment configuration and offline/local-database tests are complete. The
team's uploaded structured data (38 diseases, 7 genes, 41 contexts, 2,500 assertions) is built into a servable snapshot
in `snapshots/`, accepted in **one blanket source-fidelity decision by the project owner without per-record
inspection** (disclosed in the release limitations; not expert validation). No literature claims were extracted (no
model calls), so subgroup-level phenotype comparisons are not available yet. See
[`docs/COMPLETION_REPORT.md`](docs/COMPLETION_REPORT.md).

## Real data (uploaded captures)

One command rebuilds the servable snapshot (running it records the named person's acceptance of every structurally
valid record):

```bash
python scripts/build_release.py --reviewer "<name>" --reason "<what this person decided>"
# then commit snapshots/ and set on Render: ATLAS_DATA_MODE=real, ATLAS_SNAPSHOT_BACKEND=file,
# ATLAS_SNAPSHOT_ID=<printed id>, ATLAS_SNAPSHOT_FILE=snapshots/<printed id>.json
```

Step by step:

```bash
python -m atlas real-ingest                 # registers hackathon-claude-repo/data/* (SHA-256 checked) and runs all loaders offline
python -m atlas review-export               # queue for people; then either per-item decisions or:
python -m atlas review-batch --predicate has_phenotype --source HPO:phenotype.hpoa \
    --reviewer "<your name>" --reason "<what you checked>"            # dry run: counts and sample IDs
#   ... add --apply to record that person's decision (bound to current content hashes)
python scripts/real_dry_run.py              # full backend on real data with SIMULATED acceptance in a discarded scratch copy
```

Status and findings: [`docs/DATA_STATUS.md`](docs/DATA_STATUS.md). What to add next: [`docs/MISSING_DATA.md`](docs/MISSING_DATA.md).

## API extension (proposed 1.1.0, additive)

`/v1/entities/{id}`, `/v1/graph?focus=&depth=1|2`, `/v1/clusters`, `/v1/contexts/{id}/network` and
`/openapi-extension.json`: entity pages, a bounded knowledge graph with per-link provenance, explainable cluster facets
and community/researcher network overlap, as the challenge brief asks. 1.0.0 is unchanged. See
[`contracts/extension-1.1.0/PROPOSAL.md`](contracts/extension-1.1.0/PROPOSAL.md).

## Neighborhoods: what they are

The connections view offers **exploratory clustering through explainable similarity neighborhoods**: for a starting
context, the backend ranks other contexts at the *same profile level* by a weighted Jaccard overlap of ancestor-closed
HPO term sets, weighted by information content from a fixed OMIM-only reference (`phenotype-weighted-jaccard-1`). It
shows at most three cards: ranked first, then labelled unranked candidates. Disease baselines and subgroup profiles are
scored separately and never substitute for each other. Every card carries its calculations, shared terms, coverage and
mechanism differences. It is an overlapping, computed browsing aid, not a validated disease class, a mechanistic cluster
or a clinical match.

## Fresh checkout: offline build and tests (no credentials, no external network)

```bash
git clone <this repo> atlas && cd atlas
uv venv -p 3.12 .venv            # or: python3.12 -m venv .venv
uv pip install -p .venv/bin/python -r requirements-dev.txt   # or: .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python contracts/validate.py          # shared contract + fixture validation (as in the handoff)
.venv/bin/python -m pytest -q                   # offline + local-database tests; DB tests skip without PostgreSQL 16 binaries
.venv/bin/python scripts/master_report.py       # regenerate docs/TEST_MATRIX.md from the run
```

Installing packages needs a package index; the tests themselves block every non-loopback connection. Database tests start
a throwaway PostgreSQL 16 cluster (`/usr/lib/postgresql/16/bin`, override with `ATLAS_TEST_PG_BIN`; as root they run
`initdb` through `runuser -u postgres`). Optional real-browser CORS probe (needs Node Playwright and Chromium):
`python scripts/browser_cors_check.py`.

## Run the API locally

```bash
# Explicit synthetic fixture mode: invented interface data, visibly labelled (SYNTHETIC_DATA warning)
ATLAS_DATA_MODE=synthetic_fixture ATLAS_ALLOWED_ORIGINS='["http://localhost:5173"]' \
  .venv/bin/uvicorn atlas.api.main:app --port 8000
curl -s localhost:8000/readyz; curl -s localhost:8000/v1/meta | head -c 300

# Real mode from a validated local package (documented local fallback if hosting fails)
python -m atlas serve --snapshot snap_<sha256>      # uses work/snapshots/<id>.json unless ATLAS_DATABASE_URL is set
```

Routes: `/v1/meta`, `/v1/search?q=`, `/v1/contexts/{id}`, `/v1/contexts/{id}/connections`, `/v1/contexts/{id}/actions`,
`/v1/assertions/{id}`, `/v1/calculations/{id}`, plus `/healthz` (liveness), `/readyz` (validated pinned snapshot) and
`/openapi.json` (the committed contract). `ATLAS_DATA_MODE` must be `real` or `synthetic_fixture`. A missing or unknown
value, a missing or invalid snapshot, an unreachable database, or pipeline credentials in the API environment all keep
`/readyz` at 503 and product routes at 503. There is never a fallback to fixtures or to another snapshot. Optional
`ATLAS_FIXTURE_SCENARIOS=contested,partial_reference,preprint,structured_record` swaps in the isolated fixture scenarios
for UI testing.

## Real pipeline runbook

All stages are explicit commands run in a controlled environment, never on API startup or on HTTP requests. Each exits
nonzero on validation failure, and `--offline`/`--cached` fail on missing inputs instead of fabricating records.

| Step | Command | Human gate |
|---|---|---|
| 0 | Fill `config/demo_packet.json`, `config/context_mapping_audit.json`, `config/targets.json` (versioned HPO/Mondo URLs and releases, audited Mondo IDs, PMIDs with text policy, medRxiv DOI/version/role, NCT IDs), `config/approved_urls.json` | product/review owner; audit before bulk ingestion |
| 1 | `python -m atlas sources --config config/targets.json` | — |
| 2 | Write `data/imports/*.jsonl` (contexts, mechanism contexts, communities, assets, studies, people) per `data/imports/README.md`; `python -m atlas backbone --cached` | manual research with exact quotes |
| 3 | `python -m atlas catalog-freeze` (by hour 8); later edits: `python -m atlas catalog-impact` | re-review listed dependents |
| 4 | Set models/pricing (`config/models.json`, `verified_by/at`) and caps (`config/budget.json`); `export OPENAI_API_KEY=…`; `python -m atlas extract --cached-sources --budget-usd <cap>` | approved budget |
| 5 | Write `review/context_assignments.jsonl`; `python -m atlas check --cached-sources --budget-usd <remaining>` | person assigns contexts |
| 6 | `python -m atlas review-export` → fill `review/decisions.jsonl` → `python -m atlas review-import review/decisions.jsonl` | actual human decisions |
| 7 | Add reviewed `conflicts`, `opportunities`, `gaps`, `explanations` imports; re-review; set `config/release.json` | human |
| 8 | `python -m atlas assemble --offline` → prints `snap_<sha256>`; `python -m atlas validate --snapshot <id>` | — |
| 9 | `ATLAS_PUBLISH_DATABASE_URL=… python -m atlas publish --snapshot <id>` | — |

Without `OPENAI_API_KEY`, or with `--offline`, extraction and checking replay only recorded responses
(`work/model/responses/`). A paid call additionally requires a configured model, team-verified pricing, a positive
shared cap in `config/budget.json` and a positive `--budget-usd`. One durable ledger (`work/budget_ledger.jsonl`) covers
extraction, checking, retries and Bright Data. Uncertain outcomes stay charged, and completed calls are cached and never
re-billed. `python -m atlas budget-status` shows spending. Model agreement is never acceptance.

Publication rules enforced by `assemble`: structural checks (types, IDs, verbatim spans and qualifiers, protocol
documents cannot support results), an accepted human review bound to the current fingerprint, reviewed dispositions for
flagged disagreements, an audit row for every context, no stale reviews, no dangling IDs, permitted context windows only,
and `reuse_confirmed` only with explicit permission plus a qualified compatibility review. Pending, rejected,
structurally failed and private records never enter the package. `reviewed_content_sha256` in the API is the review
fingerprint: the hash of the reviewed internal record plus its evidence, entity and support dependencies. It is not a
hash of the public DTO or of private review reasoning.

## Database (Supabase Postgres)

```bash
ATLAS_ADMIN_DATABASE_URL=… python -m atlas migrate          # migrations/0001_atlas_schema.sql (idempotent)
psql "$ATLAS_ADMIN_DATABASE_URL" -v api_password=… -v publisher_password=… -f scripts/create_login_roles.sql
```

The migration creates a private `atlas` schema (keep it out of Supabase's exposed Data API schemas) and 15 snapshot-aware
tables with `(snapshot_id, id)` keys, deferred snapshot-aware foreign keys and JSONB payloads. It adds triggers that
forbid UPDATE, DELETE and TRUNCATE, revokes access from PUBLIC/`anon`/`authenticated`, and creates two group roles:
`atlas_api` (SELECT only) and `atlas_publisher` (SELECT + INSERT). RLS is enabled with policies only for those roles. Use
the connection strings from the Supabase dashboard for the custom login roles: the session pooler for an IPv4 host, or a
direct connection where IPv6 works. Avoid transaction pooling, which conflicts with prepared statements. Never point the
API at an owner/admin role.

## Deploy to Render

`render.yaml` defines one Python web service:

- Build: `pip install -r requirements.txt` (API runtime only; the OpenAI SDK and HTTP fetch client are not installed).
- Start: `uvicorn atlas.api.main:app --host 0.0.0.0 --port $PORT`.
- Health check: `/readyz`.
- Python: `PYTHON_VERSION=3.12.3` (also `.python-version`), the version the tests ran on.

Enter `ATLAS_DATA_MODE`, `ATLAS_SNAPSHOT_ID`, `ATLAS_DATABASE_URL` (read-only login) and `ATLAS_ALLOWED_ORIGINS` (exact
frontend origins, JSON array) in the dashboard. Never add `OPENAI_API_KEY`, Bright Data keys or the publisher URL there:
their presence makes `/readyz` fail. The free plan sleeps when idle; decide on a plan for judging explicitly (nothing
here buys one) and measure cold starts on the real service. To roll back, redeploy a known-good commit with a previously
validated snapshot ID. Never modify a published snapshot. After a real check, fill `handoff/backend_ready.json`.

## Source limitations

- PubMed abstracts default to `link_only`: no text is redistributed unless a person sets `excerpt`/`full` for a PMID
  whose terms permit it. medRxiv captures pin one version; `published: "NA"` becomes null with the original kept, and a
  later article link never relabels the preprint or counts as independent.
- HPO disease baselines use only explicit `skos:exactMatch` Mondo mappings. A grouping class without an exact annotated
  mapping has no baseline rather than borrowing a leaf. NOT rows stay negative evidence. A direct term missing from the
  OMIM IC reference withholds the numerical score at that level.
- Coverage records describe executed queries only. A failed or partial retrieval is never presented as absence.
- GO involvement is not disruption, a registered trial is not efficacy, a website mention is not ownership, and
  limited/disputed/refuted classifications are shown verbatim and never used as positive evidence.

## Layout

`atlas/models` (strict records, predicate contract) · `atlas/sources` (fetcher, canonicalization, adapters, imports) ·
`atlas/linking` · `atlas/extraction` (model client, schemas, stages) · `atlas/checking` (structural checks) ·
`atlas/review` (fingerprints, queue/import, catalog freeze) · `atlas/analytics` · `atlas/assembly` (rules, canonical
package) · `atlas/storage` (publisher/reader) · `atlas/api` (DTOs, seven routes, fixture and real stores) ·
`atlas/cli.py` · `config/` · `contracts/` · `migrations/` · `scripts/` · `handoff/` · `docs/` · `tests/`.

Contract notes and proposed clarifications: [`docs/CONTRACT_NOTES.md`](docs/CONTRACT_NOTES.md). Test matrix:
[`docs/TEST_MATRIX.md`](docs/TEST_MATRIX.md).
