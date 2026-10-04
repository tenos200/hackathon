# Lovable integration brief (paste into Lovable)

Backend: Rare Disease Atlas API on Render. Read-only, public, JSON over HTTPS. No auth, no cookies, no Supabase in the browser.

## 1. Configuration

```
VITE_ATLAS_API_BASE_URL = https://hackathon-claude-repo.onrender.com/v1
VITE_ATLAS_DATA_MODE    = live        # "mock" = use the uploaded fixture files only
```

- Build URLs as `${BASE}/meta`, `${BASE}/contexts/${encodeURIComponent(id)}`. Do not use a root-relative URL constructor that drops `/v1`.
- Every request: `fetch(url, { headers: { Accept: "application/json" }, credentials: "omit", signal })` with an AbortController timeout of ~60 s (a free Render instance can take ~50 s to wake), then 15 s once warm.
- IDs contain colons (e.g. `ctx:alpha-loss`, `HGNC:10588`); encode each path segment once.
- Keep all API calls in one data module. Cache responses keyed by `(base URL, snapshot_id, route)` only.
- Contracts to upload to Lovable: `contracts/openapi.json` (1.0.0), `contracts/fixtures.json`, `contracts/extension-1.1.0/openapi-extension.json`, `contracts/extension-1.1.0/fixtures.json`.

## 2. Every response

Success envelope (all `/v1` routes):
```json
{ "contract_version": "1.0.0", "snapshot_id": "snap_…", "data_mode": "real" | "synthetic_fixture",
  "data": { … }, "warnings": [ { "code": "…", "message": "…" } ] }
```
Error envelope (HTTP 404, 422, 429, 500, 503):
```json
{ "contract_version": "1.0.0", "snapshot_id": "snap_…" | null,
  "error": { "code": "NOT_FOUND|INVALID_REQUEST|RATE_LIMITED|INTERNAL_ERROR|SNAPSHOT_UNAVAILABLE", "message": "…", "request_id": "…", "retryable": true|false } }
```
- Show `error.message` and `request_id`; offer "retry" when `retryable`. 503 = "data is not ready", 429 = wait `Retry-After` seconds.
- **Footer on every page:** `snapshot_id` (shortened) + `data_mode`. If `data_mode == "synthetic_fixture"` show a persistent banner: "Synthetic interface data — not research findings". Never fall back to mock data silently.
- Show each `warnings[].message` near the content it concerns.

## 3. Endpoints and the screen each one powers

| Screen (challenge brief) | Call | Key fields |
|---|---|---|
| App start / About | `GET /meta` | `example_contexts[]` (starting points), `counts`, `source_versions[]`, `algorithm_version`, `limitations[]`, `capabilities.graph` |
| **One global search** (disease, gene, symptom, group, mechanism) | `GET /search?q=<1–200 chars>` | `matches[]`: `label`, `entity_type`, `matched_alias`, `ambiguous` (show "several matches — choose one"), `contexts[]` (each `id`, `label`, `profile_level`, `mechanism_known`). User picks a context explicitly; "mechanism unknown" is a valid choice. |
| **Journey summary** (summary first) | `GET /contexts/{id}` | `label`, `scope_text`, `disease`, `gene_ids`, `mechanism` (null = unknown), `summary[]` sentences, `disease_profile` and `subgroup_profile` (terms, `direct_term_count`, `distinct_publication_count`), `limitations[]` |
| **Connections / "who shares our characteristics"** | `GET /contexts/{id}/connections` | `comparisons[]` (≤3 cards), `counterexamples[]`, `graph` (small, optional) |
| **Patient action view** (assets, partners, next step, gaps) | `GET /contexts/{id}/actions` | `exact_disease_assets[]` (show FIRST), `opportunities[]`, `gaps[]` |
| **Explain every edge** (evidence drawer) | `GET /assertions/{id}` | subject–`predicate`→object, `scope_text`, `statement_status`, `badges[]`, `review`, `evidence[]` (text or structured record), `conflicts[]` |
| Computed comparison drawer | `GET /calculations/{id}` | `algorithm_version`, `parameters[]`, `result` (same shape as a score), `input_assertion_ids`, `explanation[]`, `limitations[]` |
| **Knowledge graph view** (extension) | `GET /graph?focus=<id>&depth=1\|2` | `nodes[]` (`id`, `label`, `type`, `is_focus`), `links[]` (`relation`, `assertion_id`, `calculation_id`, `computed`), `truncated`, `legend[]` |
| **Whole-atlas map** (landing graph; extension) | `GET /atlas-map` | every context + its genes/diseases/mechanisms, `nodes[]`, `links[]` (same link shape as `/graph`), `contexts_without_similarity[]` (show as "no comparable phenotype profile", not as different), `legend[]`, `limitations[]` |
| **Trace a connection** (extension) | `GET /paths?from=<id>&to=<id>&max_length=4` | `paths[]` each `{length, nodes[], links[]}` in order from → to; `warnings` `NO_PATH` = "no connection within N steps in the published records" (coverage, not absence) |
| **Mechanism / cluster view** (extension) | `GET /clusters` | `clusters[]` (`label`, `basis`, `members[]`, `explanation`, `assertion_ids`, `calculation_ids`), `unclustered[]` |
| Any node page (gene, group, person, study…) (extension) | `GET /entities/{id}` | `label`, `entity_type`, `aliases`, `properties[]`, `contexts[]`, `assertions[]` (each opens `/assertions/{id}`) |
| **Network overlap / collaborators** (extension) | `GET /contexts/{id}/network` | `community[]` (people/orgs around this context), `overlaps[]` (other, otherwise unconnected contexts and the shared people/orgs) |
| Health (not for UI) | `GET /healthz`, `GET /readyz` (outside `/v1`) | `status`, `snapshot_id`, `data_mode` |

Request flow: `meta` (+ `atlas-map` for the landing graph) → `search` → user selects a context → `context` → `connections` + `actions` in parallel → `assertions/{id}`, `calculations/{id}`, `entities/{id}`, `graph` on click. Progressive reveal: summary first, depth on click.

## 4. How to render the important parts

**Comparison card** (`comparisons[]`): show two separate panels, "Disease baseline" (`disease_baseline`) and "Subgroup" (`subgroup_comparison`). Never merge them.
- `score: null` → "Unavailable" + the first `missingness` line. Never show 0 for null.
- Always show `direct_term_counts` and `publication_counts` next to a score ("2 vs 3 observed terms, 1 publication").
- `ranking_basis`: `subgroup` / `disease_baseline` = ranked; `unranked` = label "Shown for inspection, not ranked".
- `same_parent_disease: true` + baseline 1.0 → say "same parent-disease data; not evidence the subgroups match".
- `shared_terms[]` sorted by `ic`; highlight `specific_shared_term_ids`; `score_contribution` as a bar. Warning codes: `SPARSE_PROFILE`, `BROAD_OVERLAP_ONLY`, `REFERENCE_INCOMPLETE`.
- `mechanism_features[]`: one row per `dimension` with `same | different | unknown | not_comparable` (text label, not colour only) + `explanation`; click → assertions.
- `relevant_differences[]`, `unknowns[]`, `summary[]`; `counterexamples[]` below the cards.
- Card click → `GET /calculations/{calculation_id}`.

**Sentences** (`summary`, `description`, `explanation`, `action`, `counterexamples`): `{text, assertion_ids, calculation_ids, opportunity_ids}` — render text with small citation chips that open the assertion / calculation / opportunity. Render only provided text.

**Evidence drawer** (`evidence[]`):
- `kind: "text_spans"` → show `context.text` with highlights. Offsets are **Unicode code points**: use `const cps = Array.from(text); cps.slice(start, end).join("")`, never `text.slice` (emoji break it).
- `kind: "structured_record"` → table of `record_fields[]` + `record_locator`.
- `context: null` with `source.public_text_policy: "link_only"` → no text, link to `source.url`.
- Show `stance` (supports / opposes / inconclusive), `source.title`, `source.external_ref`, `source.release_or_version`, `source.publication_status` (preprint badge), `source_native_validity` verbatim, `study_design`, `species`, `patient_count`.
- `review.description`; `expert_validation: false` → "Source-fidelity reviewed, not expert validation". `conflicts[]` shown on the summary too.

**Graph** (`/graph` or `connections.graph`): nodes coloured by `type` (context, gene, disease, phenotype, mechanism, organization, person, study, asset, process). Links:
- `computed: true` → dashed, label "computed similarity", click → `/calculations/{calculation_id}`.
- `assertion_id` present → solid, label = `relation`, click → `/assertions/{assertion_id}`.
- `relation: "mentions"` → faint/dotted, label "named in text — no relationship asserted".
- `relation: "context_scope"` → thin grey "part of this research context".
- `relation` starting `community:` (atlas map) → person/organization tied to that gene/disease through a project or registry record; click → `/entities/{id}`; label "lead to ask about".
- `relation: "phenotype_similarity:unranked"` → dashed and faint, "shown for inspection, not ranked".
- `truncated: true` → "Showing part of the graph — click a node to explore" (re-query with that node as `focus`).

**Actions**: `exact_disease_assets[]` first ("Existing resources for this disease"), then `opportunities[]` ("Possible leads"): `asset`, `partners[]` (`contact_url` as button), `readiness` (`investigate_compatibility` = "needs checking", never "reusable"), `known_differences`, `unknowns`, `expert_checks`, `action` sentence, `outreach_draft` (copyable draft, "nothing is sent"). `access_status: "unknown"` = "access unknown — ask the owner". `gaps[]`: `description`, `coverage[]` (show `completion`; `failed`/`partial` = "search incomplete", never "does not exist"), `evidence_needed`, `next_question`.

**Clusters**: one section per `basis` — "Recorded mechanism" (`functional_effect`), "Shared gene" (`shared_gene`), "Similar symptom patterns" (`phenotype_neighborhood`); always show `explanation`; members click → context page.

**Network**: `community[]` and `overlaps[]` with `role`; label "Lead to ask about — not an endorsement or confirmed collaboration". Click an entity → `/entities/{id}`; assertion IDs → drawer.

Accessibility: status never colour-only; keyboard-operable drawers and graph alternatives (the cards list must work without the graph); check at mobile width.

## 5. IDs to test with

Real snapshot (`data_mode: "real"`): start from `meta.example_contexts` — `ctx:MONDO:0800491:SCN2A-gain`,
`ctx:MONDO:1060245` (SCN2A-related disorder), `ctx:MONDO:0018614:SCN8A-gain`, `ctx:MONDO:0100079` (DEE 6A); search
`q=SCN2A`, `q=Dravet`, `q=seizure`; graph `focus=HGNC:10588&depth=1`; `/atlas-map`; `/clusters`;
`/contexts/ctx:MONDO:0800491:SCN2A-gain/network`. The release `limitations` (shown on About) must stay visible.

Synthetic fixture mode (`synthetic_fixture`):

1.0.0 routes (fixture world): contexts `ctx:alpha-loss`, `ctx:alpha-gain`, `ctx:beta-loss`, `ctx:gap`; search `q=alpha` (ambiguous match) and `q=unknown-fixture` (empty); assertions `assert:alpha-loss-A`, `assert:alpha-loss-mechanism`, `assert:alpha-baseline-A`, `assert:beta-asset-owner`; calculations `calc:alpha-beta-subgroup`, `calc:same-disease-baseline`, `calc:same-disease-subgroup`; error case `/contexts/missing` (404), `/search?q=%20` (422).

Extension routes (separate synthetic examples, different IDs): `/atlas-map`, `/entities/HGNC:900001`, `/graph?focus=ctx:syn-a-loss&depth=1` (and `depth=2`), `/clusters`, `/contexts/ctx:syn-a-unknown/network`. Other IDs return 404 in fixture mode.

Once the reviewed real snapshot is published, the same routes return `data_mode: "real"`; take IDs from `/meta`, `/search` and links in responses, never hard-code them.

## 6. Research assistant (separate service)

Base: `https://<assistant service>.onrender.com` (not the read API). `POST /v1/assistant/chat` with
`Content-Type: application/json` and body `{"messages":[{"role":"user","content":"..."}, ...], "focus_id": "<id or omit>"}`
(1–12 messages, last from the user, each ≤4000 chars; send prior turns back as `assistant` text). Response:
`{snapshot_id, data_mode, data:{blocks[], follow_up_questions[], tools_used[], usage}, warnings:[AI_GENERATED]}`.
Each block: `kind` (`sourced` | `computed` | `unknown` | `guidance` | `unsupported`), `text`, `assertion_ids[]`,
`calculation_ids[]`, `node_ids[]`, `grounded`. Render citations as chips opening the drawers; `unsupported` blocks get a
visible "not backed by the atlas" label; show the AI_GENERATED warning under every answer. Errors use the shared error
envelope: 503 `ASSISTANT_UNAVAILABLE` / `BUDGET_EXHAUSTED`, 429 `RATE_LIMITED`, 502 `ASSISTANT_FAILED` (retry).
Answers take 5–40 s; show progress. `GET /readyz` reports model and remaining daily budget.

## 7. Do not

Infer an individual's mechanism, diagnosis, eligibility or treatment; invent text; blend baseline and subgroup scores; show null as zero; present a lead as confirmed reuse; hide the synthetic banner; call any API other than this one.
