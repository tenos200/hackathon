# Demo script: two complete journeys on the real data

Snapshot `snap_cf01baf8…6ef0`. Every step below was checked against that snapshot. API base:
`https://hackathon-claude-repo.onrender.com/v1`. Each step lists the screen, the call behind it and what to point out.

---

## Journey 1: Maria, SCN2A gain-of-function patient group (mechanism first)

*"Who shares our disease characteristics? What already exists? What should we do together next?"*

| # | Screen | Call | What the judges see |
|---|---|---|---|
| 1 | Search "SCN2A" | `GET /search?q=SCN2A` | One gene node opening 16 research contexts, plus diseases named after SCN2A by synonym (e.g. "SCN2A early infantile epileptic encephalopathy" → DEE 11) |
| 2 | Context summary | `GET /contexts/ctx:MONDO:0800491:SCN2A-gain` | "early-infantile DEE, SCN2A gain-of-function subgroup": mechanism **recorded** (Orphanet association type, cited), disease-level symptom profile, empty subgroup profile shown honestly |
| 3 | Connections | `GET /contexts/ctx:MONDO:0800491:SCN2A-gain/connections` | Closest contexts are the two **SCN8A gain-of-function** subgroups: same recorded effect, different gene. Cards are "shown for inspection, not ranked" because no subgroup symptom data exists yet (the gap is stated) |
| 4 | Clusters | `GET /clusters` | "Recorded gain-of-function contexts" (SCN2A + 2 × SCN8A) and "Genes sharing a biological process: SCN1A, SCN2A, SCN8A" (3 shared GO processes): **different gene names, same pathway**, the brief's key insight |
| 5 | Trace | `GET /paths?from=HGNC:10596&to=org:familiescn2a` | SCN8A → benign familial infantile epilepsy → SCN2A → SCN2A-related disorder → **FamilieSCN2A Foundation**: every arrow opens its GenCC / Orphanet / organization record |
| 6 | Actions | `GET /contexts/ctx:MONDO:1060245/actions` | Existing resources: **DRAGONFLY global patient registry** (run by FamilieSCN2A with NORD) and the **SCN2A Clinical Trial Readiness Study**, quoted from the organization's page; reuse "unknown, ask the owner" |
| 7 | Network | `GET /contexts/ctx:MONDO:0800491:SCN2A-gain/network` | NIH-funded investigators (e.g. Ingo Helbig, Evangelos Kiskinis, Christopher Makinson) who also appear around otherwise unconnected contexts: "leads to ask about" |
| 8 | Ask the atlas | `POST {assistant}/v1/assistant/chat` | *"We lead an SCN2A gain-of-function group. Which communities share our mechanism, what could we reuse, and what must be checked before we reach out?"* |

**Story to tell:** the SCN2A group finds the SCN8A gain-of-function communities through a shared *recorded
mechanism* and shared sodium-channel processes, not through the disease name. An existing registry (DRAGONFLY) and a
readiness study already exist. What is unknown: subgroup-level symptoms (no extracted literature yet). Next step:
ask FamilieSCN2A whether DRAGONFLY's data model could cover SCN8A gain-of-function families, and test it by
comparing subgroup symptom profiles once the literature is extracted.

---

## Journey 2: Devon, newly diagnosed family (symptom and gene first, finding a community)

*"Here is the patient group for your exact diagnosis. If none exists, here are the closest related communities."*

| # | Screen | Call | What the judges see |
|---|---|---|---|
| 1 | Search a symptom: "febrile seizure" | `GET /search?q=febrile%20seizure` | HPO term "Febrile seizure" opening 8 research contexts: **one search box for symptoms too** |
| 2 | Context | `GET /contexts/ctx:MONDO:0100079` | "developmental and epileptic encephalopathy, 6A" (where OMIM's Dravet data sits): symptom profile with sources |
| 3 | Connections | `GET /contexts/ctx:MONDO:0100079/connections` | Computed neighbors: DEE 13 (SCN8A) 0.38, GEFS+ type 2 0.30, malignant migrating partial seizures 0.27, each with shared and differing symptoms; "computed similarity, not a diagnosis" |
| 4 | Exact community | `GET /paths?from=HGNC:10585&to=org:dravetfoundation-org` | SCN1A → Dravet syndrome (GenCC "Definitive") → **Dravet Syndrome Foundation** (NORD directory) |
| 5 | Honest gap | `GET /contexts/ctx:MONDO:0100135` | Dravet syndrome: mechanism not recorded, no symptom profile (no exact OMIM/Orphanet mapping in this Mondo release); the atlas says so instead of guessing |
| 6 | No exact group | `GET /paths?from=HGNC:11132&to=org:asmic-es` | SNAP25 → congenital myasthenic syndrome 18 → (Mondo classification) congenital myasthenic syndrome → **Congenital Myasthenic Syndrome Association (Spain)**: no SNAP25 group is recorded, so the closest community is shown with the classification step cited |
| 7 | Ask the atlas | assistant | *"My child was just diagnosed with a SNAP25-related disorder. Is there a patient group, and if not, who is closest?"* |

**Story to tell:** a parent with a symptom or a gene name reaches an exact community when one exists (SCN1A → Dravet
Syndrome Foundation), and an explicitly labelled *closest* community when none does (SNAP25 → congenital myasthenic
syndrome association), with every step's source one click away.

---

## Backup demos

- `GET /atlas-map`: whole atlas (all 41 contexts, genes, diseases, bridging researchers) for the opening shot.
- `GET /paths?from=HGNC:6296&to=org:familiescn2a`: KCNQ2 has no patient group in the data; the nearest community is
  FamilieSCN2A through shared diseases (e.g. malignant migrating partial seizures of infancy). Good for "what is
  missing and how to build it".
- `GET /paths?from=HP:0002373&to=org:dravetfoundation-org`: symptom → disease → gene → Dravet syndrome → foundation
  in four steps (shortest paths; open each step's evidence).

## Say it honestly

- Records were accepted in one blanket source-fidelity decision, not expert review (shown under About).
- Similarity scores compare recorded symptom profiles; they are not diagnoses or validated disease classes.
- Researchers and organizations are leads to ask, not endorsements.
