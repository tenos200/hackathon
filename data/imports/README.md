# Manual imports (human-researched, source-backed)

All rows are validated strictly (unknown fields rejected). Evidence always cites a
SourceDocument captured by `python -m atlas sources` (an approved URL, PubMed record,
etc.) and locates exact quotes in its canonical text; quotes that cannot be placed
uniquely fail instead of being guessed. Import rows are NOT acceptance: every
generated record still needs a human decision via `review-export` / `review-import`.

| File | Model | Notes |
|---|---|---|
| `contexts.jsonl` | `ResearchContext` | Disease-level browsing contexts (mechanism unknown). Needs an audit row. |
| `mechanism_contexts.jsonl` | `MechanismContextImport` | `{context, entities, assertions, evidence, supports, reviews}` with `origin=manual_import`, exact spans and source version. |
| `communities.jsonl` | `OrganizationImport` | Official URL, contact URL, served disease IDs, `serves_evidence` quotes. |
| `assets.jsonl` | `AssetImport` | Kind, owner + separate ownership evidence, target contexts + scope evidence, accessibility fields. `population_scope` must be quoted verbatim from the scope evidence. |
| `studies.jsonl` | `StudyImport` | Registered design/status as reported; never efficacy. |
| `people.jsonl` | `PersonImport` | Institutional/profile URL required; a name is never an identity key. |
| `conflicts.jsonl` | `Conflict` | Human disposition of a flagged disagreement bucket (`comparable`, `different_context`, `unclear`). |
| `opportunities.jsonl` | `OpportunityRecord` | Reviewed leads; `reuse_confirmed` needs explicit permission, materials URL and a qualified compatibility review. |
| `gaps.jsonl` | `GapRecord` | Coverage-scoped gaps; incomplete coverage cannot support `not_found_in_search`. |
| `explanations.jsonl` | `Explanation` | Reviewed synthesis bound to dependency hashes; otherwise per-record templates are served. |

`review/context_assignments.jsonl` (private) holds a person's assignment of linked
literature candidates to a research context, plus study design / cohort IDs / patient
count. The model never chooses a context.

Nothing has been imported yet for the real snapshot: the source hosts were unreachable
from the build environment and the demo packet/context audit are human tasks.
