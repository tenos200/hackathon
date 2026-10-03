---
title: Rare Disease Atlas - implementation plan v2.2
revision: '2.2'
date: 2026-10-03
status: ready-for-implementation
supersedes: Rare Disease Atlas implementation plan.md, 2026-10-03
timebox: 24 hours
implementation: standalone; no Jacq code reuse
---

# Rare Disease Atlas: implementation plan v2.2

Revision 2.2 incorporates the third review and the deployable frontend/backend handoff. Previous revisions are archived. In the handoff package this file is `01_MASTER_PLAN_V2.2.md`. Use it with `04_API_COMMUNICATION_CONTRACT.md`, `contracts/openapi.json` and the role-specific specifications. Section 16 records the changes.

## 1. Objective and release boundary

Build one complete, evidence-backed journey for a patient organization: find its disease community, inspect a qualified connection to another community, discover an existing research resource, and leave with a concrete question or collaboration proposal. If the evidence does not support a connection, explain the search coverage and the next question to investigate.

This is a research-navigation prototype. It does not diagnose an individual, recommend treatment, determine trial eligibility, estimate intervention effects, or simulate counterfactual outcomes. Every biological assertion is scoped and traceable. A computed connection is a suggestion to investigate, not clinical proof.

The challenge explicitly values one complete journey in 24 hours. Using OpenAI models or tools is necessary for track-prize eligibility. This release demonstrates actual OpenAI extraction and contextual checking on real documents. Explanations are generated offline and reviewed; the deployed app makes no model calls.

This document replaces the previous plan as the build contract. It includes the community-import format, evidence rules, API, component ownership and acceptance tests. No missing scraper specification, diagram, prior conversation or Jacq repository is required. This plan governs internal domain rules; the supplied OpenAPI contract governs exact public HTTP response shapes. Public DTOs deliberately summarize internal records without exposing private review data. Both frontend and backend implement contract version 1.0.0.

### Initial scope

| Area | Required for release | Deferred |
| --- | --- | --- |
| Biology | 3-4 genes and a few explicitly selected disease/mechanism contexts | Broad disease coverage |
| Starting candidates | SCN1A, SCN2A, KCNQ2, STXBP1; select the actual journey from sources | Forced SCN2A-to-Dravet bridge or forced STXBP1 exclusion |
| Literature | Target 30-60 abstracts, with a smaller reviewed subset published | Hundreds of abstracts |
| Public claims | Start with at most 40 substantive biological assertions to keep review feasible | Publishing unreviewed model output |
| Communities/assets | 3-5 verified organizations; 2-3 actual resources; one named professional collaborator where verified | Automated community discovery and broad grant mining |
| Variant specificity | One or two sourced variant examples if needed to explain the chosen mechanism | Full ClinVar ingestion |
| Analytics | Transparent phenotype comparison, mechanism comparison cards and computed neighborhoods | Louvain, centrality, combined confidence score |
| Interface | Search, journey page, evidence drawer, action/gap card, source/about view | Separate scout and researcher products |
| Storage/API | Supabase Postgres, FastAPI, one frozen snapshot | Live refresh, general graph database, arbitrary path queries |
| Explanation | Reviewed offline text and safe templates | Live generative endpoint |
| Other AI | OpenAI behind one client | JEV comparison or additional model providers |

Allocate the initial 40 biological-assertion review slots explicitly: 18 scoped subgroup phenotype assertions, 12 disease-baseline phenotype assertions, 8 mechanism/variant assertions and 2 other biological assertions. These are adjustable workload allocations, not quotas or evidence thresholds. Reallocate unsupported slots rather than inventing claims; drop optional treatment claims first. Operational ownership/contact assertions are reviewed separately. Count direct phenotype terms and distinct publications per subgroup in the UI; propagated ancestors do not increase the evidence count.

The counts are work limits, not scientific thresholds or claims of sufficient coverage. Fewer sources are acceptable if the journey is complete and limitations are explicit. A failed scientific premise must change the journey, never the data or scoring rules.

## 2. First gate: establish a real demonstration

During hours 0-2, the product/review owner creates `config/demo_packet.json`, using real source records. The developers can build schemas, fixtures, API mocks and the basic app while this happens.

Required fields:

| Field | Meaning |
| --- | --- |
| `starting_context_id` | A sourced disease/research subgroup, or a disease with mechanism explicitly unknown |
| `user_question` | A specific research task, such as identifying an existing outcome-measure resource and its owner |
| `existing_exact_disease_assets` | Known resources for the starting disease, shown before cross-community suggestions |
| `unmet_need` | A precisely bounded question or limitation, with evidence or an explicit unverified-need label |
| `candidate_partner_ids` | Verified organizations or professionals, not guessed names |
| `candidate_asset_ids` | Specific registry, protocol, questionnaire, model or study resource |
| `supporting_source_ids` | Sources for the proposed journey, including relevant disagreements |
| `compatibility_question` | What must be checked before the asset could help this context |
| `counterexample` | A sourced limitation or an apparently similar pair that cannot support the same inference |
| `gap_case` | A real bounded search without a supported route; incomplete coverage is allowed if labeled |
| `reviewer`, `reviewed_at` | Who inspected the packet and when |

Check existing exact-disease resources first. In particular, do not assume SCN2A lacks registries: its foundation lists DRAGONFLY and other research resources. Confirm the selected subgroup and task rather than generalizing from an incomplete import. [FamilieSCN2A research resources](https://www.scn2a.org/research/clinical-trials-and-research-opportunities/).

**Hour-one context audit:** create `config/context_mapping_audit.json` with intended population, chosen Mondo ID, source-supported disease granularity, exact mapped IDs, matching HPO IDs/row counts and baseline availability. Choose the most specific disease justified by the source, not merely a leaf with more annotations. Grouping classes may legitimately have no exact annotated mapping. Do not map a broad SCN2A cohort to a DEE subtype just to obtain a score. Check relevant SCN1A disease concepts separately, including DEE6A and DEE6B where applicable; use the pinned ontology and evidence, not a gene-to-one-disease shortcut. A null baseline is a valid recorded outcome.

The proposed cross-community relation must identify what might transfer: infrastructure, measurement practice, experimental method, or biological knowledge. These are different propositions. A shared molecular-effect label is not enough to approve any of them.

**If no defensible bridge is found by hour 2:** retain the small gene slice, demonstrate exact-community resources and one explicitly unconfirmed collaboration question. The full journey may end in a justified gap and an outreach question. Do not block engineering on manufacturing a positive result.

No patient records are needed. Search operates on diseases, genes and research contexts. It must not infer an individual child's functional mechanism from a gene name, symptoms or age.

## 3. Team and architecture

The schedule assumes two developers and one product/review teammate. If there are fewer people, reduce the corpus and polished graph view first.

| Owner | Responsibilities |
| --- | --- |
| D1: data and AI | Source capture, selected records, extraction, linking, checker, source replay |
| D2: contracts and serving | Shared schemas, analytics, assembly, snapshot publication, API, integration tests |
| P: product and review | Demo packet, community/asset research, factual review queue, Lovable, story and submission |

One-way data flow:

```text
Public sources / approved manual imports
    -> versioned raw cache + canonical source documents
    -> candidate assertions + entity-link candidates
    -> structural checks + contextual model check + human review
    -> published assertions + evidence + derived comparisons/actions/gaps
    -> immutable snapshot in Postgres
    -> FastAPI (snapshot pinned at startup; no model key)
    -> Lovable frontend
```

Use a separate `atlas` backend repository and a Lovable-managed `atlas-web` frontend repository. Both READMEs link to the other and to this specification. Do not copy Jacq source, fixtures, private data or credentials. Knowledge and design lessons may be applied through fresh implementations.

Backend defaults: Python 3.12, Pydantic 2, FastAPI, pytest, a Postgres driver and the official OpenAI Python client. Pin exact dependency versions after the initial compatibility smoke test. Generate the OpenAPI document from the response models. The frontend uses its generated types or the same checked-in response fixtures.

**Deployment decision:** Lovable hosts the React frontend; a Render Python web service hosts FastAPI; a separate team-owned Supabase project stores Postgres snapshots. The frontend calls the Render HTTPS API directly and has no Supabase credentials. The pipeline runs as an explicit local/controlled build job, not on API startup. Supabase Edge Functions are not the host for this Python process. See `05_DEPLOYMENT_AND_HANDOFF.md` for setup, role grants, connection choice and readiness checks.

No graph database is required. Build bounded neighborhoods from published assertion records; networkx is optional, not a prerequisite. There is no generic shortest-path endpoint.

## 4. Source policy and minimum ingestion

### Required source paths

| Source | Minimum implementation | Allowed result |
| --- | --- | --- |
| HGNC and Mondo | Import only selected IDs, labels and mappings from pinned source records | Canonical identity and typed mappings |
| HPO | Pinned ontology plus selected disease annotations; full reference annotations for scoring where feasible | Positive/negative phenotype annotations and ontology structure |
| PubMed | E-utilities client to fetch chosen PMIDs and record the exact query/selection; metadata and canonical abstract text | Literature candidates, never automatically established findings |
| medRxiv, optional | Selected-DOI API import only; pin the returned manuscript version, abstract, license and any journal-publication link | Preprint candidates with explicit publication status; no broad feed ingestion |
| Primary studies | Read full text for essential claims not supported by the abstract; otherwise omit the claim | Scoped evidence spans from the accessible source |
| Group/asset websites | Approved URL capture through direct HTTP or optional Bright Data Web Unlocker, followed by manual structured import; no automated discovery dependency | Verified organization/resource existence and contact page |
| ClinicalTrials.gov | Small selected NCT import using API v2; generic discovery optional | Registered study design/status, not efficacy or eligibility advice |
| Orphadata / GenCC / GO | Selected records only when needed for a demo assertion | Preserve native relation, classification and qualifiers |

A small reviewed seed file with canonical IDs is acceptable; fetching and interpreting entire databases is not mandatory. Every manual record has source provenance and `origin=manual_import`. Do not disguise manual selections as automatic discoveries.

The selected-DOI route was smoke-tested on 2026-10-03 with public DOI `10.64898/2026.02.10.26345394`: HTTP 200, one record, version 1. The returned `published` value was the literal string `NA`; normalize sentinel missing values to null, preserve the original source fields and license. The verification manifest is included in `contracts/medrxiv_smoke_manifest.json`; the raw capture remains private and is not redistributed in this package. This is a route test, not acceptance of the study claims.

For selected medRxiv DOIs, use the documented `https://api.medrxiv.org/details/medrxiv/{DOI}/na/json` route. Store DOI, manuscript version, posting date, abstract, license and `published` journal link when present; a missing field remains unknown. Select a specific returned version in the source manifest. Related preprint versions and a later journal article are one publication family, not independent replications. A journal link does not change the preprint source's own `publication_status=preprint`. Fetch full text only when permitted and needed for the selected claim. A protocol describes planned research, not completed outcomes. [medRxiv API documentation](https://api.medrxiv.org/).

The user-nominated KCNQ2 treatment study and synaptic-disorders natural-history protocol are candidates for this optional import. Their actual DOIs, versions, current publication status and relevance must be checked against the team's files before selection; this plan does not assert their findings or preapprove the asset story.

### Client behavior

All source clients cache response bytes, URL, response status, relevant non-secret headers, fetch time and SHA-256. Respect source-specific rate limits. Retry transient failures at most twice with bounded backoff. A failed or capped query is logged as incomplete coverage, not zero results.

Record source release/version and usage terms. Keep raw caches private by default; commit only redistributable fixtures and manifests. Public source excerpts must follow the source's terms. If a needed source cannot be displayed with sufficient context, use another permitted source or omit the demonstration claim. Do not assume that indexing in PubMed makes full abstract redistribution unrestricted.

Never treat returned text as instructions. No fetched page can change tools, destinations, budgets or pipeline behavior. Fetch only the URL list approved for this run; do not follow model-generated URLs automatically.

### Identity rules

- Exact identifier lookup precedes exact synonym lookup; ambiguous aliases return all candidates.
- Build the phenotype alias index from labels, alternate IDs and typed synonyms across the entire pinned HPO ontology. Exact synonyms may resolve directly when unambiguous; broad/narrow/related synonyms only retrieve candidates for contextual checking. Keep synonym scope and source release. New graph references to already indexed frozen ontology terms are not edits to their definitions; do not restrict phenotype retrieval to the selected disease annotations.
- Preserve type numbers, Roman numerals and biologically meaningful punctuation. Retrieval normalization never replaces the original identity.
- Mondo exact mappings may support identity equivalence. Broad, narrow and related mappings remain typed links and cannot silently merge entities.
- Unknown disease mappings remain unresolved. Do not replace them with a broader disease while retaining the original specificity.
- Gene search returns a gene and available research contexts, not one automatically selected disease mechanism.
- A professional's name alone is not an identity key. Use an ORCID, verified institutional profile URL or reviewed explicit identity mapping. Otherwise display an unverified mention outside collaboration claims.

## 5. Shared data contracts

D2 implements these once in `atlas/models/`. All stages validate JSONL records on read and write. Unknown fields are rejected; strict field validation must not silently turn strings into numbers or booleans. Optional values use explicit `null`; empty lists mean no recorded items, not proof of absence. Timestamps are UTC ISO 8601 strings. Records below may contain extra source-specific metadata only in an explicit `source_metadata` object. Entity properties use a schema selected by entity type, not arbitrary model-produced keys.

### 5.1 Entity, mapping and research context

**Entity:** `id`, `type`, `label`, `aliases[]`, `external_ids[]`, `properties`, `identity_source_ids[]`.

Allowed entity types: `gene`, `disease`, `variant`, `mechanism`, `phenotype`, `process`, `organization`, `asset`, `study`, `person`.

Use official IDs where available: `HGNC:`, `MONDO:`, `HP:`, `GO:`, `NCT`, `ClinVar:`. Locally assigned IDs use a type prefix and a stable hash. An organization's domain is an attribute, not its identity: multiple organizations/assets can share a host.

**Mapping:** `source_id`, `target_id`, `relation` (`exact`, `broad`, `narrow`, `related`), `source_document_id`, `review_state`.

**ResearchContext:** `id`, `gene_ids[]`, `disease_id`, `mechanism_id|null`, `profile_level` (`disease`, `subgroup`), `label`, `scope`, `definition_evidence_ids[]`, `definition_review_state`.

A context is a browsing/research grouping, not a diagnosis or a new canonical disease. Non-null mechanism and narrowed subgroup definitions require accepted evidence. Include an explicit unknown-mechanism context. An unspecified qualifier remains unknown, not universal.

Set `profile_level=subgroup` when the context selects a mechanism or another narrower population. It does not inherit a subgroup phenotype profile from its parent disease. A disease-wide unknown-mechanism browsing context uses `profile_level=disease`. Review context definitions before scoring; do not infer this field from a display label.

**Mechanism properties:** gene ID, functional-effect category (`loss`, `gain`, `dominant_negative`, `mixed`, `context_dependent`, `unknown`), process IDs, cell/tissue text, assay/species text and definition evidence IDs. Preserve these qualifiers in the mechanism identity. Same effect across different genes is never an identity merge.

### 5.2 SourceDocument and Evidence

**SourceDocument:** `id`, `source_kind`, `external_ref`, `title`, `url`, `release_or_version|null`, `published_at|null`, `fetched_at`, `raw_sha256`, `canonical_text`, `canonical_sha256`, `canonicalizer_version`, `sections[]`, `public_text_policy` (`full`, `excerpt`, `link_only`), `origin` (`download`, `api`, `manual_import`), `source_metadata`.

`source_kind`: `ontology`, `gene_registry`, `curation_database`, `literature`, `trial_registry`, `organization_website`, `institutional_website`. SourceDocument identity includes external reference, content hash and canonicalizer version, so a changed source is a new version, not an overwrite.

Source metadata records capture backend/transformation and original target URL where relevant. For preprints, it records server, DOI, pinned manuscript version, license, journal DOI/link if supplied, and a publication-family identifier. Evidence retains its own publication status and links to this versioned source. These fields affect source identity/fingerprints where they alter interpretation or captured content; they must not be discarded during public-source projection.

Each section has `label`, `start`, `end`. Text offsets are zero-based Unicode code-point positions into immutable `canonical_text`, half-open `[start,end)`. Normalize line endings during canonicalization; preserve subsequent whitespace and Unicode. The extractor sees exactly this text. Quote matching requires `canonical_text[start:end] == quote`. Never normalize after computing offsets.

**Evidence:** `id`, `source_document_id`, `kind` (`text_spans`, `structured_record`), `spans[]`, `record_locator|null`, `record_payload|null`, `study_design`, `species`, `patient_count|null`, `study_or_cohort_ids[]`, `independence` (`confirmed`, `shared_data`, `unknown`), `publication_status` (`published`, `preprint`, `unknown`), `source_native_validity|null`, `source_metadata`.

Each span has `start`, `end`, `quote`, `section_label|null`. Multiple spans are allowed. Store sentence highlights, but show the containing paragraph or abstract section and its boundaries. For plain abstracts without sections, the entire permitted abstract is the context. Do not truncate away a reviewer-marked necessary qualifier.

Structured records use an exact source file/row or JSON pointer and the original relevant fields. They do not need invented prose quotes. The evidence drawer has separate text, structured-record and computation presentations.

Paper identity is the SourceDocument's PMID/DOI; no separate paper entity is required. Evidence-to-document joins support paper/source display without graph-path inference.

### 5.3 Assertion and support

**Assertion:** `id`, `subject_id`, `predicate`, `object_id`, `context_id|null`, `scope`, `effect_direction`, `statement_status`, `origin`, `support_ids[]`, `review_state`, `review_id|null`.

**Scope:** `variant_ids[]`, `population_text|null`, `age_text|null`, `species|null`, `cell_or_tissue|null`, `assay_text|null`, `outcome_text|null`, `comparator_text|null`, `timeframe_text|null`, `other_qualifiers[]`. Preserve the source wording as well as linked IDs. Do not infer units, populations or missing bounds.

`effect_direction`: `improves`, `worsens`, `no_detected_effect`, `unknown`, `not_applicable`.

`statement_status`: `reported_result`, `proposed`, `planned`, `ongoing`, `background`, `negated`, `inconclusive`, `listed_record`.

`origin`: `literature_extraction`, `source_adapter`, `manual_import`.

`review_state`: `pending`, `accepted`, `rejected`, `needs_context`.

**Support:** `id`, `assertion_id`, `evidence_id`, `stance` (`supports`, `opposes`, `inconclusive`), `checker_result_id|null`, `review_state`, `review_id|null`.

Assertion IDs hash canonical JSON of subject, predicate, object, context, all scope fields, effect direction and statement status. Sort set-like ID lists before hashing; do not paraphrase scope for deduplication. Exact duplicates can merge support lists. Potential paraphrase duplicates remain separate until explicitly reviewed. Unknown scope does not match a known subgroup as though equivalent.

**Review:** `id`, `target_type`, `target_id`, `target_content_sha256`, `reviewer`, `reviewed_at`, `decision`, `reason`, `dimensions`. Dimensions cover source fidelity, entity identity, direction, statement status, scope and applicability. A changed assertion, evidence text, support set or link invalidates the prior review. A human can disagree with the model check only with a recorded reason; structural failures cannot be overridden.

Review fingerprints exclude review IDs, review states and operational timestamps themselves, preventing a circular hash. They include the substantive target payload and the content hashes of its evidence, entity definitions and support dependencies. Human review decisions are private; published records include only the accepted state, reviewed content hash and an appropriately labeled review summary. Model agreement is never labeled expert review.

**Catalog freeze:** lock the seed entity/context subset before its early manual reviews, and freeze the complete selected catalog, aliases and mappings by hour 8 before bulk literature review. Record the catalog hash in the review manifest. Later entity edits, including aliases, are deliberate change sets: D2 lists affected assertions, checks, calculations and explanations; D1/P re-review affected content before republishing. Do not silently invalidate records and discover the loss only at final assembly. Adding an unrelated new entity does not invalidate existing reviews unless their actual dependencies change. Defer nonessential late catalog edits to the next snapshot. Early reviewed seed records must pass a dependency-hash check at the hour-8 freeze; re-review any affected records before publication.

### 5.4 Predicate/type contract

| Predicate | Subject -> object | Interpretation |
| --- | --- | --- |
| `gene_associated_with_disease` | gene -> disease | Preserve source-native validity; does not imply a mechanism |
| `variant_in_gene` | variant -> gene | Identity/location relation |
| `variant_associated_with_disease` | variant -> disease | Preserve source classification |
| `variant_has_effect` | variant -> mechanism | Requires functional-effect evidence |
| `disease_has_mechanism` | disease -> mechanism | Scoped, supported mechanism attribution |
| `mechanism_causes_disease` | mechanism -> disease | Only when that causal assertion is supported in context |
| `has_phenotype` | disease -> phenotype | Positive or explicitly negated, never silently inverted |
| `gene_involved_in_process` | gene -> process | Membership/involvement, not disruption |
| `mechanism_affects_process` | mechanism -> process | Separately evidenced alteration |
| `responds_to` | disease or mechanism -> asset | Asset must have kind `intervention`; direction and outcome required |
| `studies` | study -> disease or mechanism | Registered design, not demonstrated effect |
| `tests` | study -> asset | Asset kind `intervention`; not efficacy |
| `serves` | organization -> disease | Sourced community remit |
| `owns_or_runs` | organization -> asset or study | Distinguish ownership/operation in source metadata |
| `asset_for_context` | asset -> disease or mechanism | Intended scope, not reuse in another context |
| `professional_at` | person -> organization | Verified public professional affiliation |
| `works_on` | person -> disease or mechanism | Sourced professional work, not name co-occurrence |

No other predicates enter the published graph without an explicit contract change. An association must not be coerced into a causal predicate. Unsupported or out-of-schema claims are retained internally with a rejection/abstention reason.

### 5.5 Conflicts, calculations, opportunities and gaps

**Conflict:** `id`, `assertion_ids[]`, `comparability` (`comparable`, `different_context`, `unclear`), `reason`, `evidence_ids[]`, `review_state`, `review_id|null`.

**Calculation:** `id`, `kind`, `algorithm_version`, `parameters`, `input_assertion_ids[]`, `input_evidence_ids[]`, `reference_source_ids[]`, `result`, `missingness`, `content_sha256`.

**Comparison:** `id`, `context_a`, `context_b`, `disease_baseline`, `subgroup_comparison`, `ranking_basis` (`disease_baseline`, `subgroup`, `unranked`), `mechanism_features[]`, `relevant_differences[]`, `unknowns[]`, `calculation_ids[]`.

Each phenotype comparison result contains `score|null`, `calculation_id|null`, `profile_level`, `same_parent_disease`, `direct_annotation_counts`, `mapped_annotation_ids` (per side), `input_assertion_ids` (per side), `shared_terms[]`, `specific_shared_terms[]`, `missingness[]`, `warnings[]`. Disease-baseline and subgroup values are separate; no endpoint returns an unqualified single phenotype score. A subgroup result with missing scoped evidence returns null even if its disease baseline is complete.

Each shared-term item contains HPO ID, label, `ic|null`, `score_contribution|null`, whether it is directly annotated on each side, and supporting input assertion IDs. When a numerical score exists, `score_contribution = IC(term) / weighted_union`; these contributions sum to the score. `specific_shared_terms` is the subset reaching the fixed descriptive IC cutoff in section 9.3. This makes broad-term dominance visible without another ranking formula. Mapping IDs on subgroup profiles may be empty: their source is scoped literature, not inherited HPO disease rows.

Each mechanism feature is `{dimension, comparison: same|different|unknown|not_comparable, assertion_ids[], explanation}`. It must use accepted assertions. A feature match is descriptive, not mechanistic equivalence.

**Opportunity:** `id`, `starting_context_id`, `partner_entity_ids[]`, `asset_id`, `kind` (`infrastructure`, `measurement`, `experimental_method`, `biological_question`), `readiness` (`discovered`, `investigate_compatibility`, `reuse_confirmed`), `basis_assertion_ids[]`, `comparison_ids[]`, `known_differences[]`, `unknowns[]`, `expert_checks[]`, `action_text`, `contact_url`, `review_state`, `review_id|null`.

**Gap:** `id`, `context_id`, `kind` (`not_found_in_search`, `source_unavailable`, `incomplete_retrieval`, `unresolved_identity`, `insufficient_context`, `conflicting_evidence`, `compatibility_unknown`), `coverage_record_ids[]`, `description`, `evidence_needed`, `next_question`.

**CoverageRecord:** `id`, `source`, `query_or_urls`, `searched_at`, `filters`, `result_cap`, `returned_count`, `inspected_count`, `completion` (`complete_for_query`, `capped`, `failed`, `partial`), `failure_reason|null`.

No gap means “does not exist globally.” `complete_for_query` describes the executed query, not exhaustive world knowledge.

### 5.6 Candidate, checker and explanation records

**CandidateClaim:** `id`, `source_document_id`, `subject_mention`, `subject_type`, `object_mention`, `object_type`, `predicate`, `scope`, `effect_direction`, `statement_status`, `spans[]`, `linked_subject_id|null`, `linked_object_id|null`, `context_id|null`, `resolution_state` (`linked`, `ambiguous`, `unlinked`, `invalid`), `reason|null`, `model_response_id`.

**CheckerResult:** `id`, `candidate_id`, `candidate_content_sha256`, `model_response_id`, `dimensions`. Each of the five dimensions in stage 5 is `{verdict: pass|fail|insufficient_context, reason, evidence_spans: []}`. All dimensions are required. A stale candidate hash invalidates the check.

New entities or mechanism profiles suggested during extraction go to manual identity review before joining the small allowed entity catalog. A model cannot silently mint a canonical ID or choose a disease context that the source does not establish.

**Explanation:** `id`, `context_id`, `sentences[]`, `dependency_hashes`, `origin` (`template`, `model_then_reviewed`, `human_authored`), `model_response_id|null`, `prompt_version|null`, `policy_version`, `review_state`, `review_id|null`. Sentences follow the API sentence contract in section 10. Templates may report individual records using their exact scope and status; synthesis or a suggested action requires accepted human review. Neither public explanations nor templates may assert an individual's mechanism.

**PublicSourceView:** `id` (original source-version ID), public bibliographic fields, raw/canonical hashes, permitted `context_windows[]`, and permitted structured-record fields. Each window has text and its original canonical start/end offsets. This is what goes into the public `sources` table; private full source documents and raw caches do not automatically enter that table. Validate excerpts and offsets against private originals before publishing.

## 6. Publication and evidence rules

There is no single strength ladder called curated/replicated/observed. Return independent badges: source type, native validity, study design, human/animal/cell scope, statement status, review status and conflict. Source-native classifications are displayed verbatim with attribution.

For this small release, a substantive assertion may enter public views only when:

1. Entity and predicate/type checks pass.
2. Every cited evidence record resolves to its exact source version; all text spans verify.
3. Required scope and direction are preserved; failed qualifiers were not silently deleted.
4. At least one relevant support record and the assertion have accepted human review tied to their current hashes.
5. All retrieved potentially opposing records in its comparison bucket have been dispositioned as accepted, rejected or unresolved; any unresolved relevant opposition blocks an unqualified public assertion.

Accepted negative or contested evidence can be shown. Acceptance means the record faithfully represents its source, not that the biological proposition is true. Rejected and pending candidates remain in the private review output. Sources containing limited, disputed or refuted classifications must retain those classifications and must not be used as positive established evidence.

Identity labels and ontology structure may be published after deterministic adapter checks and a batch review of the pinned source subset. That exception does not apply to biological claims, asset availability or reuse judgments.

Report “one study reports” or “reported in N publications” as appropriate. Never infer independent replication from author surnames. Do not use “established” or “proven” as automatic labels.

For HPO, `NOT` annotations remain negative evidence and do not enter positive similarity sets. Preserve frequency, onset, reference and source evidence code. Disease-wide phenotypes are used only in the separately labeled disease-baseline comparison, with `applicability=disease_wide_not_subgroup_specific`; they are never copied into a subgroup profile or presented as characteristics of an individual. Subgroup phenotypes come from accepted, explicitly scoped `has_phenotype` assertions as defined in section 9.1.

Positive analytics inputs must have accepted supporting evidence and statement status `reported_result` or a positive `listed_record`. Exclude negated, proposed, planned, ongoing, background-only and inconclusive assertions, source-native refuted/disputed claims, and unresolved relevant conflicts. Such records can still appear in evidence/uncertainty views with faithful labels. Mechanism-property comparisons must cite accepted assertions/evidence supporting the properties; entity metadata alone is not a biological evidence source.

GO involvement does not become disease disruption. A registered trial does not establish benefit. A grant/paper mention does not establish a collaborator's specialty. A website reference to a registry does not establish ownership or reusable material availability.

## 7. Pipeline and review workflow

All stages support cached/offline execution. Content artifacts are deterministic for the same frozen inputs and recorded model responses. Operational logs and timestamps are separate from content hashes.

### Stage 1: sources and identities

Read selected IDs and URLs from config. Capture sources, canonicalize once, resolve exact identities and preserve typed non-equivalent mappings. Produce source documents, entities, aliases, mappings and coverage records. An unresolved mapping produces a review item, not a guessed edge.

### Stage 2: backbone and community imports

Build candidate assertions from selected structured records. Each adapter has a fixture proving its source-to-predicate mapping. Import manually reviewed communities/assets using section 8. This stage includes the small real group/study/asset dataset needed by the hour-8 journey; it is not biology-only.

During hours 2-6, P may also prepare one or two mechanism-context definitions from the demo packet's primary sources, with D1 checking the import. Use `data/imports/mechanism_contexts.jsonl`: entity/context definitions plus the same Assertion, Evidence, Support and Review records used elsewhere. Include source version, exact spans and scoped interpretation; `origin=manual_import` must remain visible. An early subgroup phenotype comparison additionally requires separate scoped `has_phenotype` assertions. A mechanism definition alone does not supply those phenotypes. Early manual literature imports may use `checker_result_id=null` with an accepted human review; they still require every structural, scope and provenance check.

Each line of that import is `{context, entities: [], assertions: [], evidence: [], supports: [], reviews: []}` using the shared schemas. Evidence references SourceDocuments already captured by the source stage. Import order and validation are handled by the backbone stage; do not create a second evidence format for early examples.

If these imports are absent or not accepted, the hour-8 journey uses disease-wide unknown-mechanism contexts, existing assets and an action/gap. The mechanism split becomes available only after accepted manual definitions or the later literature run. The hour-8 gate does not require that split.

### Stage 3: literature extraction

One OpenAI extraction per abstract, cached by canonical text hash, prompt/schema version, model identifier and generation settings. Request a typed result containing candidate claims or an abstention reason. Each candidate supplies subject/object mentions and types, predicate, statement status, scope, effect direction and exact support spans.

The prompt asks what the source supports in context, including association, negation, background attribution, proposals and unknowns. It must never complete a claim from external model knowledge. Do not classify by lists of causal or hedge words. A constrained relation schema limits syntax, not scientific correctness.

Keep actual API usage and raw responses privately. Make a source/model capability smoke test before the larger batch; model names and supported settings are configuration, not assumptions in the pipeline. Budget reservations include extraction, linking, checking and retries. No paid run starts without a positive team-configured budget and credentials. Use configurable per-call output limits and bounded concurrency; start at two concurrent requests.

### Stage 4: link and structurally validate

Use exact typed aliases first. For unresolved mentions, create up to five candidates with conservative lexical retrieval; an optional model call may choose one candidate or abstain. Embeddings are deferred unless exact/lexical retrieval proves inadequate. Do not silently resolve ambiguous exact aliases.

Validate quoted spans, predicate/types and IDs. Retain all qualifiers. A missing context, unsupported number or ambiguous entity produces `needs_context`, not a broader claim. Save the failed candidate and reason for evaluation.

### Stage 5: contextual model check

For each candidate considered for publication, the checker sees the full canonical abstract or relevant source section, all supporting spans, the proposed assertion and linked entity definitions. It returns `pass`, `fail` or `insufficient_context` separately for:

- Whether the text supports this exact assertion.
- Whether the relationship type and direction are faithful.
- Whether statement status and attribution are faithful.
- Whether subgroup, species, outcome and other scope are preserved.
- Whether linked entities are correct.

The checker is an aid to human review, not independent scientific validation. No agreement count or probability becomes evidence strength. All five dimensions must be addressed; absent results remain pending.

### Stage 6: human review and conflict disposition

Generate `review/queue.html` plus `review/decisions.jsonl`. The HTML can be a simple local table with source context and model findings; a team member records decisions through the JSONL import or a small local form. A separate web review product is out of scope.

Before bulk review, require the hour-8 catalog manifest and validate all early review fingerprints against it. New mechanisms or aliases discovered during extraction are deferred or processed through the explicit catalog change/re-review procedure; they are not silently inserted while reviews are in progress.

P reviews proposed public claims; D1 helps with source interpretation. Inspect every biological assertion on the journey, the actual contact/asset pages and every proposed action. “Accepted” denotes a faithful sourced statement; lack of relevant scientific expertise is recorded and compatibility remains unconfirmed.

Group potential disagreements by subject/predicate/object for review, then compare full scope. Opposite results in different populations are `different_context`; they remain separate assertions. An unclear comparison is not silently declared contradiction. A non-significant result is not automatically evidence of absence. Shared cohort IDs prevent duplicate study counts.

### Stage 7: calculations, actions and explanations

Calculate comparisons only from published eligible assertions. Produce opportunities from reviewed source-backed inputs, not from an arbitrary graph path. Generate or template explanatory sentences offline and review them against the full qualified inputs. Each sentence cites assertion, calculation or opportunity IDs.

Citation membership checks run in code. Semantic fidelity is a review requirement. A forbidden-word list is not the acceptance criterion. Bind explanations to the exact dependency hashes and snapshot content. Any changed dependency requires regeneration/review or a deterministic fallback that only describes individual records.

### Stage 8: assemble and publish

Run referential, evidence, review and snapshot checks. Package only published records, calculations, coverage summaries and permitted source context. Keep pending/rejected candidates and raw paid responses outside the public database. Publish the immutable snapshot and serve it under a pinned ID.

## 8. Self-contained community, asset and study import

Use `data/imports/communities.jsonl`, `assets.jsonl`, `studies.jsonl` and optional `people.jsonl`. URLs are reviewed manually and captured through the same source client. Bright Data is an allowed optional fetch backend for this approved URL list, using the available credits; it is not a discovery or release dependency. Direct HTTP or an imported recorded capture remains the fallback.

Set `fetch_backend=direct|brightdata` per approved URL. The Bright Data adapter uses Web Unlocker's documented `POST https://api.brightdata.com/request` with zone and target URL. HTML can be fetched with `format=raw`; Markdown is an explicit `data_format=markdown` transformation, not a substitute value for `format`. Store the target URL, backend, requested transformation, received response hash and canonicalizer version. Existing team CLI captures can be imported when they include those fields and capture time; no undocumented earlier commands are required by this plan. Validate the target response, not just the proxy's HTTP status. [Bright Data API reference](https://docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website).

Use `BRIGHTDATA_API_KEY` and `BRIGHTDATA_UNLOCKER_ZONE` only in the pipeline, with a configured provider budget. Do not log credentials. No SERP query or autonomous link expansion is added. A backend failure must produce a capture/coverage failure, not an empty source or a guessed record.

| Record | Required fields |
| --- | --- |
| Organization | Stable local ID, name, official URL, public contact URL, served disease IDs, source document IDs, source spans/record locators, reviewer and date |
| Asset | ID, name, kind, owner organization ID or null, URL, target context IDs, source references, accessibility fields below, reviewer and date |
| Study | NCT ID or sourced local ID, title, design, status as reported, status date, conditions, intervention records if any, eligibility text/reference, source document ID |
| Person | ID, name, public institutional/profile URL, organization, sourced work context, source document IDs, reviewer and date |

Asset kinds: `registry`, `natural_history_resource`, `protocol`, `questionnaire`, `model`, `assay`, `biobank`, `intervention`, `other`.

Accessibility fields: `materials_url|null`, `access_terms_text|null`, `population_scope`, `outcome_measures[]`, `age_scope|null`, `genotype_scope|null`, `reuse_permission` (`explicit`, `restricted`, `unknown`), `last_checked_at`.

Normalize these into Entity, Assertion, Evidence and Support records; imports do not bypass publication checks. For example, organization ownership and intended disease scope are distinct assertions with distinct support. A study record can represent a resource without asserting that its protocol is available to reuse.

Flag observational records as natural-history candidates only when the design supports that interpretation; a reviewer confirms the asset type. Do not infer natural-history status merely from “observational.” Contact links point to official organization or institutional pages. Do not publish guessed identities, scraped personal contact details or implied endorsement.

`reuse_confirmed` requires explicit evidence of relevant materials/access permission and a recorded qualified compatibility review. The expected hackathon state is usually `investigate_compatibility`, not confirmed reuse. The user action can be an outreach question asking the owner what can be shared.

## 9. Analytics that can be implemented and explained

### 9.1 Phenotype comparison

Build the HPO ancestor DAG using `is_a` edges, including each term itself; support multiple parents and guard against cycles. Resolve obsolete IDs only through an unambiguous source-provided replacement. Exclude negated terms, the root and non-phenotypic aspects from positive matching.

**Map disease identities before assembling profiles.** For a context's Mondo disease ID, collect only source IDs with explicit exact-equivalence mappings in the pinned Mondo mapping product: OMIM, ORPHA/Orphanet and DECIPHER IDs where available. A bare untyped cross-reference or a broad/narrow/related mapping is not enough. Normalize namespace spellings, not disease granularity. Match those IDs to the HPO annotation file; if several match, union their positive annotations and deduplicate terms while retaining every originating ID, row, reference and mapping source. Record the matched IDs per profile and calculation. An exact mapping not present in the annotation file contributes no rows. No exact annotated mapping means an unavailable disease baseline, not a broader-disease fallback. Surface unresolved positive/negative disagreements across imported rows for review instead of silently choosing one.

**Keep two profile levels separate.**

- **Disease baseline:** the mapped HPO disease annotations and any accepted literature annotations that explicitly concern the whole disease. Deduplicate repeated terms but retain provenance. Cache this once per disease ID. Subgroups of the same Mondo disease therefore share a baseline, which can legitimately score 1.0; label it as the same parent-disease dataset, never as evidence that the subgroups match.
- **Subgroup profile:** only accepted, scoped literature `has_phenotype` assertions whose `context_id` names that subgroup and whose qualifiers match its reviewed definition. Manually imported literature assertions qualify under the same rule. Do not add parent-disease HPO rows or fill gaps from another subgroup. Missing subgroup data remains missing. Different subgroup phenotype descriptions in the demo must come from these scoped assertions, not HPO inheritance.

The `has_phenotype` subject remains the Mondo disease entity; `context_id` and `scope` restrict it to the subgroup. Store profile level and all input assertion IDs in each calculation. Group-level mechanism evidence by itself is not phenotype evidence. Missing mention of a phenotype in one subgroup is not proof that subgroup lacks it; report observed annotation differences and coverage, not clinical absence.

Use a fixed reference population for information content: distinct OMIM disease IDs with positive phenotypic annotations in the pinned HPO annotation file. This is a scoring reference, not direct access to or redistribution of OMIM content. Store its release/hash and disease count N. For a term t, count each reference disease once if it is annotated to t or a descendant. Define `IC(t) = -ln(count(t)/N)` for positive counts; use zero weight and an unknown-reference flag for zero counts, rather than infinite information content.

For a comparison at one profile level, form A and B from that level's eligible positive annotations and their ancestors, carrying applicability flags and input assertion IDs. Compare disease baseline to disease baseline or subgroup to subgroup; never mix the two or union them to compute one score. Use weighted Jaccard:

```text
score = sum(IC(t) for t in A intersection B)
        / sum(IC(t) for t in A union B)
```

Return null for that level if either side lacks a usable positive profile, any direct term lacks IC-reference support, or the weighted union is zero. Zero means disjoint observed annotations under this calculation, not a proven biological difference; null means unavailable. If either subgroup lacks scoped literature phenotypes, `subgroup_comparison.score=null` even when `disease_baseline.score=1`. Return direct annotation counts, reference coverage, exact mapping IDs, top shared informative terms, input assertion IDs and profile-level warnings. Independently observed subgroup profiles may themselves score 1.0; never force them apart to fit a demonstration.

If the full reference cannot be loaded, omit the numerical score and show exact shared terms with coverage. Do not silently switch algorithms or make up IC values. A scoreless comparison is an allowed fallback and is disclosed in the snapshot.

**Reference coverage rule:** the IC population remains OMIM-only, while source profiles may use exact-mapped ORPHA/DECIPHER annotations. Some direct terms therefore have no positive reference count. Zero weighting such terms is expected but can hide real recorded differences and inflate overlap. Report per side the number of distinct direct terms, terms represented in the reference, fraction covered, and unassessed IDs. For the initial release, if any included direct term lacks reference support, withhold the numerical score and exclude the pair from numerical ranking at that level; still show qualitative shared terms, missingness and the independent other profile level. Do not silently score only the assessed subset. Ancestor support does not make an unsupported direct term assessed.

**Sparse profiles:** display direct-term and distinct-publication counts next to every subgroup score. Never count propagated ancestors as independent observations. A score of 1.0 on two observed terms describes those records only; do not label it strong evidence or a complete phenotype match. Do not fill a term allocation just to produce a number.

This score is a browsing heuristic in [0,1], not a probability, causal distance or clinical match. Freeze it before the final evaluation.

### 9.2 Mechanism comparison

Display dimensions separately: functional effect, gene family where sourced, affected process, cell/tissue context and assay/species context. For each, return same/different/unknown/not-comparable with supporting assertions. No combined mechanism score and no bonus for matching treatment-response direction in this release.

Same effect alone produces a descriptive match, never a shared-mechanism claim. Unknown data must remain unknown. Broader process overlap may identify a question to investigate but does not erase cell/context differences.

### 9.3 Computed neighborhoods

For a disease-level starting context, rank other disease-level contexts by available disease-baseline score descending, then context ID. For a subgroup starting context, rank other subgroup contexts only by available subgroup score. Missing subgroup scores must not be substituted with disease-baseline scores. Show separately labeled unranked candidates where shared baseline terms or sourced mechanism features provide a reason to inspect them, including the reason subgroup ranking is unavailable. Do not mix these candidates into the ranked score list. Show at most three comparison cards in total: ranked candidates first, then unranked candidates in stable ID order. Exclude the starting context itself.

For a phenotype-derived candidate require at least one shared non-root term with positive reference weight at the relevant level; scoreless fallback candidates require an explicit shared non-root term. This is a permissive browsing filter, not evidence of a meaningful biological match. In an epilepsy slice it may admit most pairs. Show how much of the score comes from broad versus more specific terms. Define the descriptive flag `broad_overlap_only` when no shared term with known IC reaches `ln(10)` (present in at most 10 percent of the reference diseases); unknown IC is labeled unassessed. This fixed heuristic affects the explanation, not ranking or clinical eligibility. Record it with algorithm parameters and freeze it before evaluation. Show the highest-IC shared terms and a warning if the overlap is broad; do not tune weights or term cutoffs until the desired neighbors appear. The three-card cap is a presentation limit, not a validated threshold.

Treat the center plus these candidates as an overlapping computed neighborhood, not a validated disease class. Pairwise calculations and mechanism differences accompany every connection. Describe this in the README and pitch as exploratory clustering through explainable similarity neighborhoods, and immediately state the actual top-neighbor method. It is not a disjoint community partition or validated mechanistic clustering. This supplies an inspectable small-scale grouping view without a second community-detection algorithm. A dissimilar or context-incompatible candidate can remain visible as a counterexample. Do not tune the rules to recover a predetermined gene grouping.

### 9.4 Opportunities and gap generation

Show exact-disease assets first. For each other candidate community, list sourced resources; create an opportunity draft only when at least one specific artifact and owner/contact are known. A team member must review the stated relevance, differences and checks before publication.

Operational resources can be relevant despite different biology. Biological transfer claims require their own qualified evidence and remain questions where unresolved. Neither neighborhood membership nor asset absence automatically approves an action.

Templates describe coverage failures precisely: “No registry was found in these inspected sources,” not “No registry exists.” Preserve separate cards for missing sourcing, incomplete retrieval, unresolved entity identity and actual conflicting evidence.

## 10. Storage, snapshot consistency and API

### Storage

Tables: `snapshots`, `entities`, `contexts`, `mappings`, `sources`, `evidence`, `assertions`, `supports`, `conflicts`, `calculations`, `comparisons`, `opportunities`, `gaps`, `coverage`, `explanations`. Store typed payloads as validated JSONB initially; add only the lookup indexes used by the API. Do not build a generic ORM abstraction.

Every content table uses primary key `(snapshot_id, id)` and snapshot-aware references. Sources and records are immutable after publication. Content hashes exclude run/fetch/review timestamps while retaining source content versions, review decisions and calculation inputs. Raw model responses remain private; public content retains their hashes/version metadata where relevant. Keep operational timestamps in run manifests for audit. Define one canonical content projection used both for hashing and for idempotency comparisons; timestamp-only differences create a new run manifest, not a modified published snapshot.

Build the complete canonical package first. Derive `snapshot_id = snap_<sha256-of-canonical-package>` with the `snapshot_id` fields excluded from that hash. Canonical JSON uses sorted object keys and deterministic record ordering. The complete hash is recorded; shortening is for display only.

The publisher computes the snapshot ID itself from the canonical package; a genuine content change naturally gets a different ID. Re-publishing the same ID is a no-op and preserves the original published metadata. Check package and stored checksums for corruption or an implementation error; this is an integrity assertion, not a normal same-ID update/conflict workflow. Insert entities/contexts/sources transactionally before dependent records, using deferred constraints where cross-references require them; validate the complete transaction before commit. Do not update a global pointer behind a running API. Set `ATLAS_SNAPSHOT_ID` at startup, verify the complete snapshot, then mark the service ready. All reads remain pinned to it. Restart/redeploy to serve a new one. Freeze publication during judging.

Only the publisher has write credentials. The API uses a read-only role. Disable anonymous direct database reads; the browser accesses only the API. API deployment contains no OpenAI key.

### Seven public endpoints

Deploy these seven logical routes under `/v1`. The exact HTTP contract is supplied as `contracts/openapi.json`; its public DTOs project these internal records. All success envelopes add `contract_version` and `data_mode` to the fields below. Operational endpoints `/healthz` and `/readyz`, and `/openapi.json`, are separate from the seven product routes. Use `/readyz` for deployment readiness.

All successful product responses use `{contract_version, snapshot_id, data_mode, data, warnings: []}`. `data_mode` is `real` or `synthetic_fixture`; warnings have a code and human-readable message. The common error envelope and all response DTOs are in the shared contract. The default frontend does not send a snapshot parameter or implement a 409 restart state machine. Display the served snapshot ID in the footer and retain it with each response. Each response must be internally consistent with the server's pinned snapshot. Freeze deployments during judging; after a deliberate redeploy, reload the app and clear frontend data caches. Continuous consistency across a mid-session redeploy is not a promise of the minimum release.

Optional hardening: accept a `snapshot_id` query parameter; if supplied and different from the server's snapshot, return 409 `SNAPSHOT_CHANGED`. Missing parameters are always allowed. If the team implements this option or a response-ID change detector, cover it with its own test; neither is required for the initial Lovable integration.

| Endpoint | Exact minimum response data |
| --- | --- |
| `GET /meta` | Content hash, date, source releases, coverage summary, corpus counts, reviewed/published counts, algorithm versions, limitations |
| `GET /search?q=...` | Up to 20 typed matches: ID, label, matched alias, ambiguity flag, available context IDs; no inferred patient mechanism |
| `GET /contexts/{id}` | Context definition, gene/disease/mechanism entities, scoped summary sentences, assertion IDs, relevant phenotype records, limitations |
| `GET /contexts/{id}/connections` | Up to three comparison cards with separate disease-baseline/subgroup results and ranking basis, counterexamples, computed neighborhood nodes/links, explanation sentence references |
| `GET /assertions/{id}` | Qualified assertion, badges, accepted supporting/opposing evidence, source context, conflicts, review summary |
| `GET /calculations/{id}` | Algorithm, parameters, inputs, result, missingness, source references and inspectable assertion IDs |
| `GET /contexts/{id}/actions` | Exact-disease assets, reviewed opportunities, official contacts, outreach draft if reviewed, gaps and coverage |

Every substantive summary/explanation sentence is `{text, assertion_ids: [], calculation_ids: [], opportunity_ids: []}` with at least one appropriate dependency. General UI labels and navigation text do not require scientific citations. A sentence synthesizing multiple assertions must have a reviewed explanation record. The API never generates new prose.

Context pages return enough source-backed information for the first journey without the frontend chaining generic graph traversal. Evidence endpoint embeds the allowed paragraph/section plus quote offsets relative to the returned context and the original source coordinates. Frontend highlights must account for Unicode code points; test a non-BMP character before a quote.

Public API excludes pending/rejected claims and internal review deliberations. Unknown IDs return 404; malformed requests 422; optional explicit snapshot-pin mismatch returns 409 if that feature is implemented. Bound query length at 200 characters, response items and graph size at 60 nodes/100 links. No raw SQL/filter expression from clients. Set CORS to the frontend origin; use basic hosting rate limits for availability. CORS is not authentication.

Target p95 under 500 ms for non-cold requests on the demo snapshot, measured separately from hosting cold starts. Cached in-memory reads are acceptable only when tied to the pinned snapshot. Do not let performance polish delay evidence completeness.

## 11. Frontend contract for Lovable

Build against one committed fixture per API response before the real data exists. Use public configuration `VITE_ATLAS_API_BASE_URL` (including `/v1`) and `VITE_ATLAS_DATA_MODE=mock|live`. Mock mode uses only the supplied synthetic fixtures, with a persistent synthetic-data banner. A production judging build must use live mode against a reviewed real snapshot; it must never silently fall back to mocks. Production displays a persistent snapshot/coverage footer and does not silently fall back to synthetic fixtures.

Required views can be panels on one journey page:

1. **Search:** typed results and explicit context selection; unknown mechanism is a valid choice.
2. **Journey summary:** source-backed facts, existing exact-disease resources, and limitations first.
3. **Connections:** separate disease-baseline and subgroup comparison panels, top shared terms, mechanism differences/unknowns, and a small optional graph. A shared parent baseline must not look like a measured match between subgroups. Missing subgroup evidence says so, even when the disease baseline scores 1.0. Display ranking basis and broad-overlap warnings. Computed links are visually and verbally distinct.
4. **Evidence drawer:** complete assertion scope, source/date, original context, highlight, native classification and relevant opposing evidence. Structured records and calculations have their own presentations.
5. **Action or gap:** owner/contact, what might help, differences, required checks and one concrete question to ask. Never imply confirmed reuse from discovered availability.
6. **About:** sources, versions, AI/manual contribution, evaluation, limitations and reproduction instructions.

Contradiction warnings appear on the summary/card itself; full supporting and opposing context is in the drawer. Status is not encoded by color alone. Use plain language, keyboard-accessible controls and a mobile-width check. Keep research caveats attached to their claims; a generic footer cannot correct an overstated sentence.

The graph is optional presentation, not a release dependency: comparison cards can complete the journey. If shown, traversing a link opens the assertion or calculation that generated it. No personalized medical advice or patient-data upload form.

Suggested first Lovable prompt:

> Build a research-navigation app for a rare-disease patient organization. Use the supplied API fixtures and contracts. Start with typed search, explicit research-context selection, a journey page, evidence drawer and action/gap panel. Distinguish sourced statements from computed comparisons. Show existing exact-disease assets before cross-community suggestions. Never infer a person's mechanism, eligibility or treatment. Do not invent biological content; render only provided records and reviewed sentences. Keep API access in one data module and show the snapshot and coverage. Build the comparison list before adding a graph.

## 12. Repository layout and bounded work packages

```text
atlas/
  config/                    targets, demo packet, source pins, budgets, prompts
  atlas/models/              record and API schemas
  atlas/clients/             source and model clients, private response cache
  atlas/stages/              sources, backbone, extract, check, review, assemble
  atlas/analytics/           phenotype, comparisons, opportunities, gaps
  atlas/api/                 seven endpoints, snapshot pinning
  atlas/cli.py               command entry point
  data/imports/              source-backed selected records
  tests/fixtures/            redistributable synthetic and recorded examples
  tests/                     unit, contract, integration, browser tests
  migrations/                snapshot-aware Postgres tables and roles
  docs/                      this plan, API fixtures, evaluation, source terms
  review/                    local review artifacts; private by default
  README.md
```

| Package | Owner and file scope | Deliverable and acceptance |
| --- | --- | --- |
| A: contracts/fixtures | D2: `atlas/models`, `tests/fixtures`, `docs/api`, `config` | Typed records and seven API fixtures validate; fixture distinguishes unknown from negative and scoped from broad claims |
| B: sources/backbone | D1: `atlas/clients`, source/backbone stages, `data/imports`, source tests | Canonical sources, IDs, selected biology plus groups/assets/studies; provenance for each assertion; no silent identity merges |
| C: extraction/review | D1: extract/check/review stages, prompts, review tests | Real extraction, cached replay, full-context checking, human decisions bound to content hashes; no pending claims published |
| D: assembly/analytics/API | D2: analytics, assembler, migrations, API, related tests | Deterministic calculations and snapshot; seven endpoints; all evidence and review integrity checks; deployment |
| E: frontend/demo/evaluation | P: `atlas-web`, demo packet, evaluation/docs with D1 | Complete journey on real API; source-based review; locked evaluation, walkthrough and submission |

Schema changes are owned by D2 and must update all affected fixtures before dependent work continues. Do not ask an agent to implement the whole system in one pass. Assign a package with this document and its acceptance criteria. These instructions govern future implementation; this plan revision itself does not start that work.

### CLI contract to implement

```text
python -m atlas sources --config config/targets.json
python -m atlas backbone --cached
python -m atlas extract --cached-sources --budget-usd <approved-cap>
python -m atlas check --cached-sources --budget-usd <approved-remaining-cap>
python -m atlas review-export
python -m atlas review-import review/decisions.jsonl
python -m atlas assemble --offline
python -m atlas validate --snapshot <id>
python -m atlas publish --snapshot <id>
python -m atlas serve --snapshot <id>
```

Each command returns a nonzero exit code on validation failure. `--offline` forbids network calls and fails on missing inputs rather than fabricating records. Paid commands share one durable run budget ledger so separate stages cannot each spend the full cap. Resume uses completed cached responses; uncertain network outcomes are recorded and never promised to be bill-free retries.

Environment: `OPENAI_API_KEY` (pipeline only), optional `NCBI_API_KEY` and contact email, optional `BRIGHTDATA_API_KEY` and `BRIGHTDATA_UNLOCKER_ZONE` (capture only), publisher database URL (publisher only), read-only database URL and `ATLAS_SNAPSHOT_ID` (API), `FRONTEND_ORIGIN`, frontend API base URL. Provider-specific spending caps apply to both OpenAI and Bright Data, including available credits; medRxiv selected DOIs and pinned versions live in config. Do not pass credentials in prompts, logs or committed config. Model IDs, pricing assumptions and prompt versions live in non-secret config and are verified before the paid run.

## 13. Tests and measurable acceptance criteria

Offline tests use recorded model responses to test pipeline behavior. They do not establish model reasoning quality. Live evaluation is separate and explicitly reported.

| ID | Required test | Pass condition |
| --- | --- | --- |
| T01 | Identity ambiguity | Ambiguous gene/disease alias returns alternatives; type numbers remain distinct; related mapping does not merge |
| T02 | Context selection | Gene-only input produces no individual mechanism claim; unknown mechanism remains usable |
| T03 | Contextual citation | Multiple spans verify; quote displayed inside necessary context; Unicode highlight is correct |
| T04 | Scope identity | Same endpoints with different ages/variants/outcomes remain separate assertions |
| T05 | Semantic fixtures | Association, negation, background, planned work and inconclusive findings retain their status or abstain; no keyword rule determines meaning |
| T06 | Failed qualifier | Invalid age/count qualifier blocks/requeues the assertion; it is not silently removed |
| T07 | Native validity | A refuted/limited database record is displayed as such and cannot become established positive evidence |
| T08 | Review gate | Pending or structurally failed records are not published; changed dependencies invalidate review and are listed before publication, including early manual imports |
| T09 | Conflict scope | Opposite results in different populations are separated; comparable opposition is surfaced; unclear opposition blocks an unqualified summary |
| T10 | Study identity | Repeated reports of one cohort do not count as independent replication |
| T11 | HPO semantics | NOT does not enter positive matching; multiple parents propagate once; missing profile gives null, not zero |
| T12 | Scoring | Within each profile level, weighted overlap is symmetric in [0,1]; identical positive-weight profiles give 1; zero-weight/missing profiles give null; inputs and IC reference resolve |
| T13 | Mechanism comparison | Same functional-effect label with different/unknown cell context never yields automatic mechanistic equivalence |
| T14 | Source predicate fidelity | GO membership is not disruption; trial registration is not efficacy; source mention is not ownership |
| T15 | Asset readiness | Unknown access/compatibility cannot yield reuse_confirmed; exact-disease assets appear first |
| T16 | Coverage/gaps | Failed/capped source query yields incomplete coverage, never a global absence assertion |
| T17 | Derivation/explanation | Every displayed computed link has calculation provenance; explanation dependencies resolve and match reviewed hashes |
| T18 | Publication integrity | Missing evidence, stale review or dangling ID fails publication; previous snapshot is intact |
| T19 | Reproducibility | Cached inputs plus recorded responses yield the same content hash; repeat publish creates no duplicate data |
| T20 | Snapshot consistency | Requests without a snapshot parameter succeed; each response and its dependencies use the server-pinned ID; footer displays it; optional explicit mismatch check is tested only if implemented |
| T21 | API/bundle boundaries | No model key or direct database access in browser; API role cannot write; raw review data is not public |
| T22 | API contracts | All seven responses match fixtures/schemas, including two phenotype result levels; 404/422 errors and any implemented optional 409 follow the contract |
| T23 | Real browser journey | Search -> context -> connection -> evidence -> action/gap works on deployed real data, at mobile width and by keyboard |
| T24 | Budget/resume | Interrupted run reuses completed cached calls; shared ledger stops new paid calls at the configured cap |
| T25 | Fresh checkout | README reproduces fixture build and all offline tests without credentials or external network |
| T26 | Subgroup profile separation | Same-parent contexts with only HPO data have a shared baseline but null subgroup scores; adding scoped literature affects only its subgroup; no baseline fallback or forced split |
| T27 | Mondo-to-HPO mapping | Multiple exact annotated IDs union with row provenance; duplicate terms count once; broad/related/untyped xrefs and missing mappings do not supply a baseline; namespace aliases normalize correctly |
| T28 | Catalog freeze | Alias/definition changes after freeze produce an affected-review list and block stale dependents; unrelated appended entities do not invalidate unchanged dependencies |
| T29 | Early mechanism imports | An accepted source-spanned manual definition enables a mechanism context before extraction; without it the hour-8 journey uses unknown mechanism; no subgroup phenotype is inferred from the definition |
| T30 | Optional capture backend | Recorded direct/Bright Data responses yield canonical sources with backend provenance; missing Bright Data credentials do not block direct/cached sources; target-page errors are not accepted as documents |
| T31 | Selected preprint | Pinned DOI/version imports preserve preprint/license metadata; later article links do not relabel the captured source or count as independent replication; protocol claims remain planned |
| T32 | Neighborhood interpretation | Subgroup ranking never uses a disease-baseline score; broad-only matches carry the fixed warning, specific contributions are visible, null IC is unassessed, and the three-card limit does not imply clinical relevance |
| T33 | Context audit | Each selected context has a source-justified disease scope and exact-mapping/annotation audit; a grouping class with no mapping stays unavailable instead of borrowing a leaf |
| T34 | Reference completeness | A direct ORPHA-derived term missing from the OMIM reference makes that profile-level score unavailable and unranked, even if its ancestors have weights; counts/IDs explain why |
| T35 | Sparse evidence display | UI reports direct terms and distinct publications, not ancestors; two matching terms scoring 1.0 do not generate a strong-evidence claim |
| T36 | HPO aliases | A phenotype absent from selected baseline annotations still resolves through the full pinned ontology; related synonyms retrieve candidates without auto-equivalence |
| T37 | Frontend/backend handshake | Both repositories use contract 1.0.0 and identical fixtures; browser requests Render `/v1` directly, with allowed origins and no credentials; health/readiness and common errors work |
| T38 | Deployment separation | API has read-only DB access and no model key; pipeline never runs on API startup; a failed/missing snapshot fails readiness; public bundle has only public configuration |

### Small real-model evaluation

Before prompt iteration, reserve 10 of the selected abstracts as a locked final set. Use separate development examples. Include difficult source phenomena where available: subgroup differences, association, negation, background attribution, planned work and insufficient context. Artificial stress fixtures must be labeled synthetic and reported separately.

Human review creates a small reference list of relevant assertions and safe abstentions before inspecting final predictions. Record disagreements/unresolved judgments; do not force a binary truth label where the source does not support one. Run one final evaluation after prompts and analytics rules are frozen.

Report numerators and denominators: retrieved documents, candidates, accepted/rejected/needs-context claims, accepted-claim source fidelity, entity-link accuracy, direction/status/scope errors, recall against the small reference list, and cost/runtime. Review all demo claims separately. No headline clinical-accuracy claim from this sample. Do not tune to force the illustrative biology or rerun selectively until the final set looks good.

## 14. Schedule and hard cuts

| Time | D1 | D2 | P | Gate |
| --- | --- | --- | --- | --- |
| 0-2h | Verify source access and selected records; first model smoke test when budget configured | Schemas, fixtures, API skeleton, deployment smoke test | Demo packet, assets/partners, rules/credits; Lovable on fixtures | Source-justified context/mapping audit and frozen initial contract; lock seed definitions before early reviews |
| 2-6h | Selected biology/community/study imports; source replay; optional selected preprints and early mechanism imports | Assembly, pinned snapshot, API | Review imports; optionally source 1-2 mechanism definitions; build core panels | Small real snapshot ready; any known mechanism has accepted source-spanned support |
| 6-8h | Finish selected IDs, aliases and exact HPO mappings; prepare literature batch | Freeze catalog manifest, validate early reviews, deploy snapshot/API | Re-review any changed seed dependencies; verify backbone journey | Working exact-resource journey using unknown mechanism by default or accepted manual contexts; full catalog frozen before bulk review |
| 8-13h | Extract/check small corpus against frozen catalog; export review queue | Separate disease/subgroup calculations, calculation drawer, integrity tests | Review intended public claims and opportunities; scoped subgroup phenotypes where supported | Reviewed literature integrated; credible connection or explicit gap, never a copied HPO subgroup profile |
| 13-16h | Locked evaluation, cost report, source audit | API/browser tests, snapshot consistency, bug fixes | Explain/action wording review and mobile pass | Complete journey; no unreviewed public assertions |
| 16-18h | Freeze source/prompt changes | Freeze snapshot and features; test cold start | Record walkthrough and prepare README/story | Release candidate frozen |
| 18-22h | Help fix release blockers | Reproduction and deployment checks | Team video, one-minute walkthrough, submission | Submit by hour 22 if possible |
| 22-24h | Only submission-blocking fixes | Only submission-blocking fixes | Verify submission and links | Deadline buffer |

If a gate slips, cut in this order: interactive graph, extra genes, extra corpus volume, optional model linking, automatic PubMed discovery. Keep a selected-PMID real extraction, provenance, human review, exact-disease resources, an action/gap and a working deployment/local fallback. Do not cut evidence review to preserve visual features.

If credentials or credits are unavailable, continue offline fixtures and real manually sourced imports, clearly labeled. That keeps engineering moving but does not satisfy the intended actual-OpenAI demonstration until a real run is completed. If hosting fails, the brief permits a prototype easy to run locally; document that route and record the working walkthrough.

## 15. Demo, impact and submission

### One-minute walkthrough

- 0-10s: state the patient organization's specific research question; search and select its context without inferring an individual's mechanism.
- 10-25s: show existing exact-disease resources and one candidate connection, including a meaningful difference/unknown.
- 25-40s: open one qualified assertion and its original context, then a computed comparison's inputs.
- 40-52s: show a real owner/partner, a concrete resource and a sourced outreach question or compatibility checklist.
- 52-60s: show the bounded gap and the milestone that might be accelerated, with remaining validation stated.

### 10x impact discipline

Target preparation of an expert-reviewable collaboration proposal: verified partner/resource, evidence packet and compatibility questions. This is a step toward a shared study or another selected research milestone, not proof of a faster treatment.

Measure the preparation task on comparable questions if time permits. A credible baseline must reflect existing resources, not an invented requirement to rebuild everything. Report measured time savings separately from projected study-level impact. Keep consent, approvals, recruitment, funding and scientific validation in the larger timeline. Without a credible baseline, present explicit scenarios and assumptions rather than claiming an achieved 10x result.

### Release checklist

- [ ] Real demo packet approved; exact-disease assets checked; no predetermined biological outcome forced.
- [ ] Every public substantive assertion and action has current accepted review and inspectable source support.
- [ ] At least one actual OpenAI extraction/check run is documented, including rejected/uncertain outcomes.
- [ ] All applicable T01-T38 tests pass; optional fetch/pinning features are tested with recorded fixtures or explicitly marked not implemented; live-model evaluation reported separately from fixtures.
- [ ] Entity/context catalog and mappings were frozen before bulk review; any later changes have an impact list and completed dependent re-reviews.
- [ ] Disease-baseline and subgroup evidence remain separate in calculations and UI; missing subgroup evidence is shown as unavailable.
- [ ] One complete real journey and one bounded gap work in the browser or documented local fallback.
- [ ] Public database/API contains only published records and permitted source context.
- [ ] Snapshot, prompt/model/source versions, costs, manual contributions and limitations are recorded.
- [ ] Both repositories have reproduction instructions and no secrets or Jacq code.
- [ ] Team video and one-minute walkthrough meet event rules; submission links tested on another device.

## 16. Grounding and differences from v1

This version removes automatic gene-to-mechanism selection, endpoint-only claim merging, keyword-based semantic decisions, the curated-equals-established rule, author-name replication tests, combined mechanism-confidence scoring, the missing scraper dependency, live explanations and moving snapshots during a demo. It makes community resources part of the early backbone, not a later dependency. Source validation remains a real gate with an honest-gap fallback; no scientific conclusion is preapproved by this document.

### Revision 2.1 changes after the second review

1. Disease-level HPO baselines and literature-supported subgroup profiles are separate schema/API results. Identical parent profiles cannot stand in for a measured subgroup match. Genuine scoped evidence may still support identical profiles; no split is forced.
2. The hour-8 baseline explicitly works with unknown mechanism. One or two reviewed manual mechanism definitions may be added earlier, with separate phenotype evidence needed for subgroup scoring.
3. Mondo-to-HPO mapping uses explicit exact equivalence only, unions matched annotated IDs with provenance and returns unavailable when no exact mapping exists.
4. Catalog freeze and deliberate review-impact reporting prevent late alias/definition edits from silently invalidating the demonstration.
5. Bright Data Web Unlocker is allowed for approved URL capture and recorded CLI-import provenance; automatic discovery stays deferred.
6. medRxiv selected-DOI ingestion is optional, versioned and visibly preprint, with publication-family deduplication and protocol/result separation.
7. The default frontend needs no snapshot parameter or 409 restart logic; responses retain pinned IDs. Explicit client pinning is optional hardening.
8. Same-ID content checks are corruption safeguards rather than update logic. Neighborhoods disclose broad-term overlap and the arbitrary presentation cap, with no post hoc threshold tuning.

The API fixtures, tests and schedule must follow this revision; older single-score or mandatory-409 fixtures are superseded.

### Revision 2.2 changes and role handoff

- Adds the hour-one source-scope/mapping audit without forcing all contexts into leaf diseases.
- Reserves review capacity for subgroup phenotype assertions and requires sparse-evidence counts.
- Indexes the full pinned HPO vocabulary with typed synonyms.
- Withholds profile-level numerical rankings when any direct term lacks IC-reference support.
- Records the successful medRxiv route check and `NA` sentinel handling.
- Uses accurate exploratory-clustering wording instead of implying validated mechanistic clusters.
- Chooses Lovable + Render FastAPI + Supabase Postgres and splits ownership, wire DTOs, mock fixtures, deployment and integration handshakes into the accompanying handoff documents.
- Extends the acceptance matrix to T01-T38. Actual implementation tests remain to be written/run by the coding agents; schema/fixture validation of this handoff is reported separately.

References behind the design corrections:

- Challenge: `hackathon5.pdf`, especially pages 3-6, supplied by the team.
- [Mondo mapping semantics](https://mondo.readthedocs.io/en/latest/editors-guide/mappings/): preserve exact versus broad/narrow/related relationships.
- [HPO annotation format](https://hpo-annotation-qc.readthedocs.io/en/latest/annotationFormat.html): retain qualifier, reference, frequency and onset information.
- [GenCC validity definitions](https://thegencc.org/faq): preserve limited/disputed/refuted classifications.
- [GO annotation semantics](https://www.geneontology.org/docs/go-annotations/): involvement and normal function do not automatically assert disease disruption.
- [Ogiwara et al., 2018](https://www.nature.com/articles/s42003-018-0099-2) and [Tan et al., 2026](https://onlinelibrary.wiley.com/doi/10.1002/epi.70100): functional-effect labels require biological context; these papers are examples to inspect, not preapproved demo claims.
- [FamilieSCN2A research resources](https://www.scn2a.org/research/clinical-trials-and-research-opportunities/): check existing infrastructure before asserting an unmet need.
- [Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/) and [FastAPI response models](https://fastapi.tiangolo.com/tutorial/response-model/): implement shared input/output contracts explicitly rather than relying on default coercion or arbitrary response dictionaries.
- [Python version support](https://devguide.python.org/versions/): verify the selected runtime and pinned dependency compatibility at kickoff.
- [medRxiv API](https://api.medrxiv.org/): selected-DOI metadata, manuscript version and publication linkage.
- [Bright Data Web Unlocker API](https://docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website): optional capture of approved URLs with explicit output transformation.

Source selection, access rules, sponsor model availability and hackathon repository requirements must be checked at kickoff. These are operational gates with stated fallbacks; they do not require inventing additional architecture before coding can begin.
