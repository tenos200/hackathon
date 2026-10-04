# Real data status (uploaded captures, 2026-10-03 run `20261003T192150Z_898c47`)

`python -m atlas real-ingest` registers every file in `hackathon-claude-repo/data/manifest.jsonl` as a recorded capture
(all 72 SHA-256 values match) and runs the loaders offline. Everything produced is **pending human review**; nothing is
public yet.

## What was ingested

| Source | Release / scope | Result |
|---|---|---|
| HPO `hp.json` + `phenotype.hpoa` | hp 2026-09-01, annotations 2026-09-02 | 20,482 terms (full alias index); IC reference of 8,465 OMIM diseases; 711 disease-phenotype rows for the selected diseases |
| Mondo `mondo.obo` + SSSOM | releases/2026-09-01 | 38 selected diseases with typed mappings |
| HGNC | 7 genes | SCN1A, SCN2A, KCNQ2, STXBP1 + stretch SCN8A, SNAP25, STX1B |
| GenCC submissions | 82 rows for the genes | classifications kept verbatim (incl. Limited) |
| Orphadata product 6 | 2026-06-23 | gene–disorder associations; 3 source-stated gain-of-function mechanisms (SCN2A early-infantile DEE; SCN8A in two epilepsies) |
| GO `goa_human.gaf` | current | 96 biological-process annotations (labels unavailable, see below) |
| ClinicalTrials.gov | 6 query pages, 133 studies | 76 studies linked by exact disease identity or a registered gene term |
| NIH RePORTER | 4 gene searches, 336 projects | projects, applicant organizations, funders, investigators (NIH profile IDs) |
| PubMed EFetch | 105 abstracts | captured, `link_only`; claims need OpenAI extraction |
| medRxiv | 31 preprints | latest recorded version pinned; claims need extraction; document role unset |
| FamilieSCN2A page | 1 page | drafted organization, DRAGONFLY registry and CTRS resources with exact quotes |

Drafts produced for review: `config/disease_selection.json` (reasons cite source records),
`config/context_mapping_audit.json` (41 rows), 41 research contexts (38 disease-level, 3 mechanism subgroups), and
`data/drafts/{communities,assets}.jsonl`. 2,500 pending assertions; 0 structural failures.

**Dry run** (`python scripts/real_dry_run.py`, simulated acceptance in a discarded scratch copy): all 41 contexts and
2,500 assertions assemble; 2,848 route payloads validate against contract 1.0.0; 87 comparison cards (63 ranked);
cluster facets for every gene, the recorded gain-of-function group and a phenotype neighborhood; network overlaps
between unconnected communities through shared NIH investigators. See `reports/real_dry_run.json`. This proves the
backend handles the data; it is **not** a reviewed snapshot.

## Findings that need a person

1. **Dravet syndrome has no disease baseline under the strict mapping rule.** In this Mondo release the exact
   `Orphanet:33069` mapping sits on the obsolete MONDO:0011794, and `OMIM:607208` ("Dravet syndrome") maps exactly to
   DEE6A (MONDO:0100079). The current Dravet term MONDO:0100135 has no exact annotated mapping, so its baseline is null
   and it has no ranked cards; DEE6A carries the OMIM Dravet annotations. Options: accept this (honest), or record a
   reviewed explicit mapping decision (a plan change that should be written down).
2. GenCC's MONDO:0011794 was resolved to its single source-provided replacement MONDO:0100135; the submitted ID is kept
   in the record (`submitted_mondo_curie`).
3. ORPHA:716903 (SNAP25 congenital myasthenic syndrome) has no exact Mondo mapping and was left out.
4. The phenotype neighborhood facet links 21 of the disease contexts into one component (epilepsy phenotypes overlap
   broadly, as the plan anticipated). It is labelled exploratory; ranked cards and broad-overlap warnings carry the detail.
5. Grouping classes (e.g. "intellectual disability", "complex neurodevelopmental disorder") are included because GenCC
   lists them; their audit rows mark the granularity. Consider excluding them from the demo journey.

## Missing data / decisions (please add)

| Needed | Why | How to provide |
|---|---|---|
| **OpenAI API key + approved budget** | Literature claims (subgroup phenotypes, mechanisms, publication–claim–investigator edges) and the track requirement | Set `OPENAI_API_KEY` in a pipeline environment that can reach api.openai.com, fill `config/models.json` (model, verified pricing) and `config/budget.json` caps |
| **A named human reviewer** | Nothing publishes without accepted review | Run `python -m atlas review-batch ... --reviewer "<name>" --reason "<why>" --apply`, or tell me explicitly which decision to record under which name |
| GO ontology (`go-basic.obo` or `go.json`) | Process names instead of bare GO IDs | Upload with a manifest row like the others |
| More community pages | Only FamilieSCN2A is captured; the journey needs partners for other diseases | Approved pages (HTML) for e.g. Dravet Syndrome Foundation, KCNQ2 Cure Alliance, STXBP1 Foundation, SCN8A Alliance, NORD/Global Genes listings |
| Demo packet + release metadata | The journey, user question, counterexample and gap case are human choices | `config/demo_packet.json`, `config/release.json` |
| PubMed text policy per PMID | Abstract text is `link_only` until redistribution is confirmed | `public_text_policy` per PMID |
| medRxiv document roles | Protocol vs research report affects allowed statuses | `document_role` per DOI |
| Dravet mapping decision | See finding 1 | A written decision |
| Optional: ClinVar variant IDs, ORCID IDs for key investigators, more RePORTER pages (SCN2A had 189 matches, 100 retrieved) | Variant-level mechanisms; stronger person identity; complete funding coverage | Add to the run config and re-capture |
| Hosting | Deployment | Supabase project + roles, Render service, Lovable origins |

The duplicate `data/orphadata/en_product6.xml` at the repository root is identical to the one under
`hackathon-claude-repo/data/` and is not used.

## Release 2026-10-04 (served snapshot)

`snapshots/snap_21b5b209…fd142.json` (10.7 MB): 41 contexts, 1,017 entities, 2,500 assertions, 87 comparison cards,
174 calculations, 0 withheld; 2,848 route payloads validated. Accepted by the project owner in one blanket decision
without per-record inspection (`scripts/build_release.py`). Not in this release: literature-extracted claims (needs an
OpenAI key and budget, then context assignment), so the three gain-of-function subgroups have no subgroup phenotype
profile and only unranked cards; opportunities, gaps and explanations (need human-written imports). The FamilieSCN2A
organization and its two resources (CTRS, DRAGONFLY registry) are covered by the same acceptance and appear in
`/contexts/ctx:MONDO:1060245/actions` as exact-disease assets. 11 contexts have no comparable phenotype profile and show no similarity link.


## Release update (NORD organizations)

`snapshots/snap_cf01baf8…6ef0.json`: adds the Dravet Syndrome Foundation and the Congenital Myasthenic Syndrome Association (linked to "congenital myasthenic syndrome", the Mondo class above SNAP25's CMS 18, through a cited `disease_subclass_of` link); the 11 records of the "benign familial neonatal-infantile seizures" NORD search were all unrelated and were not linked (NORD organization directory export, serves
Dravet syndrome, MONDO:0100135). One NORD record was not linked because its disease field ("Dravet Syndrome
Foundation,") names no disease. The export's capture time was not recorded; `fetched_at` is the time it was received.
Accepted under the same blanket decision by the project owner.
