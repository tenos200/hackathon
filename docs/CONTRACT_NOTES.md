# Contract and specification notes

Contract 1.0.0 is implemented unchanged: `contracts/openapi.json` and `contracts/fixtures.json` are byte-identical to the
handoff (SHA-256 `3eedc557…b845c` and `ca02ddde…55f34355`, checked at API startup in fixture mode). No field, enum, path
or requiredness was changed. The items below are genuine tensions found while implementing, each with what the code
does now and a proposal. None requires a contract amendment for the hackathon; items 1–3 deserve a decision by the
backend owner and the human before the real snapshot is frozen.

## Proposed clarifications (need a human decision)

1. **Operational timestamps vs content-addressed snapshot IDs.** `CoverageRecord.searched_at` is a required public field,
   while the master plan requires content hashes to exclude run/fetch timestamps so that identical inputs reproduce the
   same snapshot ID (T19). *Implemented:* a single canonical hash projection (`atlas/assembly/package.py`,
   `OPERATIONAL_FIELDS`) excludes `coverage[].searched_at` from the hash; the value is still served. Re-publishing the
   same ID is a no-op that keeps the first stored rows, as the plan specifies. *Consequence:* two packages that differ
   only in `searched_at` share one ID, and the first published wins. *Proposal:* accept this; list further operational
   fields here if added.

2. **`MetaData.published_at`.** A wall-clock publication time would also break reproducibility. *Implemented:* it is the
   human-set `config/release.json` `published_at` (part of the content hash); the database keeps the actual insert time
   separately in `atlas.snapshots.inserted_at`, outside the public DTO. *Proposal:* document that this field is the
   release date of record.

3. **Who assigns a literature claim to a research context.** The master plan requires subgroup phenotypes to come from
   accepted `has_phenotype` assertions whose `context_id` names the subgroup, but it also forbids the model from choosing a
   context the source does not establish. *Implemented:* the model never sets `context_id`; a person writes
   `review/context_assignments.jsonl` (candidate → context, plus study design/cohort IDs/patient count) before promotion,
   and the assertion review then covers that assignment. Unassigned literature phenotypes enter neither profile level.
   *Proposal:* confirm this as the D1/P workflow.

## Interpretations recorded (no change needed)

4. **Review of supports.** Rule 4 requires an accepted review of "at least one relevant support record and the
   assertion". The assertion fingerprint covers its full support set and each support's evidence/source hashes, so an
   accepted assertion review dispositions all of its supports (including opposing ones) at once; separate support reviews
   are supported but not required.

5. **Context review.** Every published context (including disease-level, unknown-mechanism contexts) needs an accepted
   definition review, following "review context definitions before scoring". This is stricter than "non-null mechanism
   and narrowed subgroup definitions require accepted evidence".

6. **Potential disagreements.** Code only *flags* a subject/predicate/object bucket for disposition when it contains
   different polarities (positive `reported_result`/`listed_record` vs `negated` vs `inconclusive`) or different effect
   directions; planned/proposed/background statements are not treated as opposition. Flagged buckets are withheld until
   a reviewed `Conflict` exists. `different_context` keeps both records usable; `comparable`/`unclear` publish them with a
   conflict badge and exclude them from analytics. Code never decides that two statements contradict.

7. **Unresolved HPO positive/NOT rows** for the same disease and term are withheld as a flagged bucket rather than either
   row being chosen; the profile layer additionally nulls the score if such a pair is ever accepted as `comparable`.

8. **Free-text mechanism dimensions.** Cell/tissue and assay/species texts are compared only for exact equality (`same`);
   otherwise `not_comparable` (needs expert judgment) or `unknown`. No string-similarity or keyword judgment.

9. **Publication counts.** `publication_counts`/`distinct_publication_count` count publication families after merging
   families that share a study/cohort ID (T10), so a preprint, its article and repeated cohort reports count once.

10. **Sparse-profile warning threshold.** The plan requires sparse-evidence disclosure but gives no number. The frozen
    heuristic is `SPARSE_PROFILE` when either side has fewer than 5 direct terms (`SPARSE_DIRECT_TERMS`), recorded in each
    calculation's parameters. It affects wording only, never ranking.

11. **HTTP 405.** The contract lists no 405 status; non-GET requests to product routes return 405 with the shared error
    envelope (`METHOD_NOT_ALLOWED`). The read API defines no mutation verbs, so the frontend never sees it.

12. **Evidence view IDs.** When one evidence record needs disjoint permitted windows, views get stable IDs
    `<evidence id>#window<n>` (contract: "backend may project separate stable view IDs"). These IDs are not routable.

13. **Native-validity vocabulary.** GenCC classifications `Refuted Evidence`, `Disputed Evidence`, `Limited`,
    `No Known Disease Relationship` and `Animal Model Only` are shown verbatim and never treated as positive evidence. The
    list is a controlled-vocabulary mapping in `atlas/sources/adapters.py`, to be re-checked against the pinned GenCC
    release.

14. **Bright Data target validation.** The plan requires validating the target response, not just the proxy status. The
    documentation available to me (via search snippets; docs.brightdata.com was blocked) did not specify a target-error
    header, so a capture is accepted only with HTTP 200, a non-empty body, and (when configured) a reviewer-supplied
    `expect_text` marker found in the page. Recommend setting `expect_text` for every approved page.

15. **External API details verified only through search snippets.** The build environment blocked api.medrxiv.org,
    docs.brightdata.com, render.com, supabase.com, NCBI, HGNC, OBO and OpenAI. Field names and routes used for medRxiv
    (`details/medrxiv/{DOI}/na/json`, `collection[].doi/version/license/published/server`), Bright Data
    (`POST /request`, `zone`, `url`, `format=raw`, `data_format=markdown`, Bearer auth), Render (`PYTHON_VERSION`,
    `.python-version`, `healthCheckPath`), Supabase (session pooler 5432/IPv4, transaction pooler without prepared
    statements, direct connection IPv6) and NCBI (3 req/s without key, 10 with key; `tool`/`email`) match the plan and
    those snippets, but must be re-checked against the live documentation before the first real run. The OpenAI call
    uses `client.responses.create(..., text={"format": {"type": "json_schema", "strict": true, ...}})`, inspected in the
    installed SDK (openai 3.24.0), not exercised against the live API.

## Added with the real-data ingest

16. **Additive extension (proposed 1.1.0).** Entity, graph, cluster and network routes plus three source-only predicates
    (`mentions`, `investigator_on`, `funds`, `disease_subclass_of`) are proposed in `contracts/extension-1.1.0/PROPOSAL.md`. They answer the
    challenge brief's graph, clustering and network-overlap requirements without changing any 1.0.0 shape.

17. **Large reference files** (Mondo, HPO JSON, GenCC, GO, Orphadata) are source documents identified by raw SHA-256
    through a short descriptor (`bulk-descriptor-1`) and cited record-by-record with exact locators and original fields.
    `phenotype.hpoa` keeps full text so annotation rows are verified line by line.

18. **Drafted catalog.** The disease set, research contexts, audit rows and mechanism subgroups are derived by fixed rules
    from GenCC, Orphanet and Mondo records (`atlas/real_data.py`), and Orphanet's controlled association type is the only
    source of the three mechanism subgroups. They remain drafts until a person accepts them; `review-batch` records a
    named person's decision over a filtered set and never runs automatically.

## Added with the research assistant

19. **Model calls at request time.** The master plan forbids model calls on API HTTP requests; the challenge brief asks
    for OpenAI to "Explain: turn a graph path into plain language". *Implemented:* the read API is unchanged and never
    calls a model. A separate service (`atlas/agent`, Render service `atlas-assistant`) holds the only model key
    (`ATLAS_AGENT_OPENAI_API_KEY`, which the read API refuses), reads the same published snapshot read-only, refuses to
    run without a positive daily budget and prices, and answers only through read-only tools. Every cited ID must have
    been returned by a tool in that conversation and must resolve in the snapshot; otherwise it is removed and the
    block is labelled `unsupported`. Answers carry an `AI_GENERATED` warning and are never stored as records.
    *Proposal:* accept this as the "Explain" layer; keep extraction offline as specified.

20. **Shared-process clusters.** `/v1/clusters` adds basis `shared_process`: genes annotated (GO GAF) to the same
    biological process, grouped by gene set. Labels are GO IDs until the GO ontology is uploaded.

