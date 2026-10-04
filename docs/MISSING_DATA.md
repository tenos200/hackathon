# Missing data: exact checklist

Do the items in order; each one is independent. For every item this page says **which website**, **what to search**,
**which page or file to take**, **what it must contain**, and **what to paste into the fetch config**.

**URLs below were written from memory and could not be opened from the build environment. Open each one in a browser
first and copy the real address from the address bar.**

## How to hand data over (same for every item)

1. Paste the snippet into your fetch script's config (the format of `hackathon-claude-repo/data/config_used_*.json`).
2. Run the fetch script. It downloads the files **and** rewrites `hackathon-claude-repo/data/manifest.jsonl` (SHA-256 +
   fetch time). Never edit the manifest or downloaded files by hand.
3. Check the run summary (`summary_*.json`): every item should be `ok`. Note any `failed`.
4. Push `hackathon-claude-repo/data/` to `main` and tell me. I rebuild the snapshot and give you the new
   `ATLAS_SNAPSHOT_ID` for Render.

**If you captured data another way (a scraper export, a saved page):** send me the file with the URL it came from;
I register it with `scripts/register_manual_upload.py` (copied into `data/manual/` with its SHA-256 in
`data/manual_manifest.jsonl`, which the fetch script never overwrites). NORD organization-directory exports
(`organization_name`, `disease_or_gene`, `website_url`, `product_page_url`) are loaded automatically: an organization
is linked to a disease only when `disease_or_gene` equals that disease's name or a synonym.

**If a website blocks the script:** open the page in your browser, *File → Save Page As → "Webpage, HTML only"*, save
it as `hackathon-claude-repo/data/web/<label>.html`, and send me the exact URL and the date you saved it. I will
register it.

---

## ✅ Item 1 — Patient organizations, registries and studies (most important)

**Goal:** for each gene, one patient organization page and one page describing a registry, natural history study,
biobank or model it runs. This fills the patient action view ("existing resources", "who to contact") and the
community network for every gene, not just SCN2A.

### What a page must contain (otherwise skip it)

Written in the page text, not only in images or PDFs:

- [ ] the organization's name,
- [ ] the gene or disease it serves (e.g. "families affected by SCN8A"),
- [ ] the name of a resource and what it is (e.g. "the X Patient Registry", "natural history study"),
- [ ] who runs or funds that resource (e.g. "launched by the Foundation in partnership with …"),
- [ ] a contact, "get involved" or "join the registry" link.

The page that usually has all of this is called **Research**, **Registry**, **Clinical trials**, **Natural history**
or **Get involved**. The home page usually does not.

### Where to look, per gene

| Gene (disease) | Organization(s) to check | Starting address | Page to take |
|---|---|---|---|
| SCN1A (Dravet syndrome) | Dravet Syndrome Foundation | `https://dravetfoundation.org` | Research / Patient registry page |
| SCN1A (Dravet syndrome) | Dravet Syndrome UK | `https://www.dravet.org.uk` | Research page |
| SCN2A | FamilieSCN2A Foundation | already captured | — |
| SCN8A | International SCN8A Alliance | `https://www.scn8a.net` | Research / registry page |
| SCN8A | The Cute Syndrome Foundation | `https://www.thecutesyndrome.com` | Research page |
| KCNQ2 | KCNQ2 Cure Alliance | `https://www.kcnq2cure.org` | Research / natural history page |
| KCNQ2 | Jack Pribaz Foundation | `https://www.jackpribazfoundation.org` | Research page |
| KCNQ2 | RIKEE variant database (a reusable asset) | `https://www.rikee.org` | About page |
| STXBP1 | STXBP1 Foundation | `https://www.stxbp1disorders.org` | Research / natural history page |
| SNAP25, STX1B | none known; search (below) | — | — |
| Several genes | Simons Searchlight (registry covering several of these genes) | `https://www.simonssearchlight.org` | The page listing the genes it covers |
| Several genes | Rare Epilepsy Network (registry) | search "Rare Epilepsy Network registry" | About / participating organizations |

Cross-gene registries (the last two rows) matter most for the brief's "find what can be shared": one registry serving
several communities.

### How to find organizations you don't know (and SNAP25 / STX1B)

1. **NORD:** go to `https://rarediseases.org`, search the disease name (e.g. "Dravet syndrome", "KCNQ2",
   "STXBP1"), open the **Rare Disease Report**, and scroll to **Associated Organizations** / **Resources**. Note each
   organization's website, then open its research or registry page (checklist above).
2. **GARD (NIH):** go to `https://rarediseases.info.nih.gov`, search the disease name, open the disease page, and find
   **Patient Organizations** / **Resources**. Same as above.
3. **Orphanet:** go to `https://www.orpha.net`, search the disease, open its page, then **Patient organisations**
   (filter by country if needed).
4. **Global Genes:** search "Global Genes foundation alliance <gene>".

**Disease names to search** (directories are organized by disease, not gene; try the names left to right; older
names starting "early infantile epileptic encephalopathy" are still used on many sites):

| Gene | Search these disease names |
|---|---|
| SCN1A | Dravet syndrome · Generalized epilepsy with febrile seizures plus (GEFS+) · Familial hemiplegic migraine · Lennox-Gastaut syndrome |
| SCN2A | SCN2A-related disorder · Developmental and epileptic encephalopathy 11 (early infantile epileptic encephalopathy 11) · Benign familial neonatal-infantile seizures · Episodic ataxia type 9 |
| SCN8A | SCN8A-related epilepsy · Developmental and epileptic encephalopathy 13 (early infantile epileptic encephalopathy 13) · Benign familial infantile seizures 5 |
| KCNQ2 | KCNQ2-related epilepsy / KCNQ2 encephalopathy · Developmental and epileptic encephalopathy 7 (early infantile epileptic encephalopathy 7) · Benign familial neonatal seizures (epilepsy) |
| STXBP1 | STXBP1 encephalopathy / STXBP1-related disorder · Developmental and epileptic encephalopathy 4 (early infantile epileptic encephalopathy 4) · Ohtahara syndrome |
| SNAP25 | Congenital myasthenic syndrome 18 · Congenital myasthenic syndrome (general) |
| STX1B | Generalized epilepsy with febrile seizures plus type 9 · Generalized epilepsy with febrile seizures plus (GEFS+) |
| All (umbrella) | Developmental and epileptic encephalopathy · Infantile spasms · Malignant migrating partial seizures of infancy |

Many patient groups are named after the gene even when the directory lists them under the disease; on NORD and
GARD a gene search sometimes works too.

Optional: the NORD/GARD disease page itself can also be captured (it proves which organizations serve the disease),
but the organization's own research/registry page is required for the resource.

### Config to add

One line per page in `web.pages` (keep the existing FamilieSCN2A line; each `label` must be unique):

```json
{"label": "dravet_foundation_research", "url": "<exact page URL>", "backend": "direct", "data_format": null},
{"label": "scn8a_alliance_research", "url": "<exact page URL>", "backend": "direct", "data_format": null},
{"label": "kcnq2_cure_research", "url": "<exact page URL>", "backend": "direct", "data_format": null},
{"label": "stxbp1_foundation_research", "url": "<exact page URL>", "backend": "direct", "data_format": null},
{"label": "simons_searchlight_genes", "url": "<exact page URL>", "backend": "direct", "data_format": null}
```

### Natural history studies and registries on ClinicalTrials.gov (same item, no website needed)

Many registries are also listed on ClinicalTrials.gov as observational studies. Add these queries to `ctgov.queries`:

```json
{"term": "SCN1A natural history"}, {"term": "SCN2A natural history"}, {"term": "SCN8A natural history"},
{"term": "KCNQ2 natural history"}, {"term": "STXBP1 natural history"}, {"term": "developmental epileptic encephalopathy registry"}
```

**What I do with it:** organization and resource records, each quoting the page word for word; reuse permission
stays "unknown" unless the page states it. They show in the action view, network view, atlas map and assistant.

---

## ✅ Item 2 — Gene Ontology names

**Goal:** show process names (e.g. "sodium ion transport") instead of IDs like `GO:0006814` in the
"genes sharing a biological process" clusters.

- **Source:** Gene Ontology Consortium.
- **File:** `go-basic.obo` (~30 MB). No search needed.
- **Config** (add to `files`):

```json
{"name": "go_basic_obo", "dest": "go/go-basic.obo", "urls": ["http://purl.obolibrary.org/obo/go/go-basic.obo"]}
```

**What I do with it:** wire it to the GO loader; no other change.

---

## ✅ Item 3 — Research coverage for SCN8A, SNAP25, STX1B

**Goal:** these three genes were never searched in NIH RePORTER (funding, investigators) or ClinicalTrials.gov, so they
have no studies, researchers or trials.

- **Sources:** NIH RePORTER API and ClinicalTrials.gov API (your script already queries both).
- **Searches:** the gene symbol in project title/abstract (RePORTER) and as a search term (ClinicalTrials.gov).
- **Config** (replace the existing `reporter` and `ctgov` blocks; keep the natural-history queries from item 1):

```json
"reporter": {"limit": 100, "terms": ["SCN1A", "SCN2A", "KCNQ2", "STXBP1", "SCN8A", "SNAP25", "STX1B"],
             "search_field": "projecttitle,abstracttext"},
"ctgov": {"page_size": 100, "max_pages_per_query": 5, "queries": [
  {"cond": "Dravet syndrome"}, {"term": "SCN1A"}, {"term": "SCN2A"}, {"term": "KCNQ2"}, {"term": "STXBP1"},
  {"term": "SCN8A"}, {"term": "SNAP25"}, {"term": "STX1B"}]}
```

- **PubMed:** move `SCN8A`, `SNAP25`, `STX1B` from `stretch_genes` into `genes` (the PubMed queries run per gene in
  `genes`), or add them to `pubmed.extra_queries`.

**What I do with it:** nothing beyond the rebuild.

---

## ✅ Item 4 — ClinVar variants

**Goal:** gene → variant → disease edges (the brief's "gene–variant–mechanism"). ClinVar says whether a variant is
pathogenic; it does **not** say gain or loss of function (that needs item 5).

- **Source:** NCBI ClinVar through E-utilities (the bulk file is too big for GitHub).
- **Search, per gene** (7 searches; replace `SCN2A` with each gene):

```
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&term=SCN2A[gene]+AND+(clinsig_pathogenic[prop]+OR+clinsig_likely_pathogenic[prop])&retmax=500&retmode=json&tool=rare_disease_atlas&email=<your email>
```

- **Then:** take the IDs from `esearchresult.idlist` and download summaries in batches of up to 200:

```
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=clinvar&id=<id1,id2,…>&retmode=json&tool=rare_disease_atlas&email=<your email>
```

- **Where:** save as `clinvar/<GENE>_esearch.json` and `clinvar/<GENE>_esummary_<n>.json` through the fetch script (as
  `files` entries or by extending its `clinvar` step; it already has a `clinvar.variation_ids` setting).
- **Size:** a few MB in total.

**What I do with it:** write a ClinVar loader (clinical significance shown word for word, never treated as mechanism).

---

## ✅ Item 5 — Claims from papers (OpenAI): run, not download

**Goal:** the brief's "Extract": genes, variants, symptoms, mechanisms and investigators from the 105 PubMed abstracts
and 31 medRxiv preprints already uploaded. It is the only way the three gain-of-function subgroups get their own symptom
profiles.

I cannot reach OpenAI from my environment, so this runs on your computer:

1. In `config/models.json`: set `extraction.model` to the OpenAI model ID, and add a `pricing` entry for that model
   (input and output USD per 1M tokens from OpenAI's pricing page, `verified_by` = your name, `verified_at` = today).
2. In `config/budget.json`: set `shared_cap_usd` and `providers.openai.cap_usd` to your limit (e.g. `5`).
3. In a terminal, from the repo folder:
   ```
   python -m atlas real-ingest
   export OPENAI_API_KEY=sk-...      # this terminal only; never on Render
   python -m atlas catalog-freeze
   python -m atlas extract --cached-sources --budget-usd 5
   ```
4. Zip `work/stage/literature/` and send it to me (do not commit `work/`).
5. Tell me whether the extracted claims may be assigned to research contexts under the same blanket acceptance as
   before. The spec requires that decision from you.

---

## ✅ Item 6 — Three decisions (just answer in chat)

1. **Dravet syndrome profile:** Mondo's Dravet ID has no exact OMIM/Orphanet mapping, so it has no symptom profile. Use
   the DEE 6A profile (where OMIM's Dravet data sits) for Dravet, labelled as your mapping decision? **Yes / No**
2. **Abstract quotes:** show short quoted passages from PubMed abstracts in the evidence drawer (currently link-only)?
   **Yes / No**
3. **Preprints:** include medRxiv preprints in extraction, with a "preprint" badge? **Yes / No**

---

## Optional (stretch goals in the brief)

| What | Source | Search | Config |
|---|---|---|---|
| Researcher identity across projects | ORCID (`https://orcid.org`) | each key investigator's name (e.g. from the network view) | their iDs in `orcid.ids` |
| Funder calls (where money is going) | NIH Guide (`https://grants.nih.gov/funding/searchguide`) and foundation "grants" pages | gene names, "developmental epileptic encephalopathy" | `web.pages` |
| Mouse models (reusable assets) | Jackson Laboratory (`https://www.jax.org/strain`) | each gene symbol | `web.pages` (model pages) |

---

### Already in place (no action)

Mondo, HPO ontology + annotations, GenCC, Orphanet gene associations, GO annotations, HGNC (7 genes), 105 PubMed
records, 31 medRxiv records, 336 NIH RePORTER projects, 133 ClinicalTrials.gov studies, FamilieSCN2A pages.
