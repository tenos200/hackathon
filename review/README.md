# Review workspace (private)

Everything here except this README is gitignored: the queue, decision template,
decisions, imported reviews and context assignments contain private deliberations.

1. `python -m atlas review-export` writes `queue.html` (source context, structural
   problems, model check as an aid) and `decisions.template.jsonl` (null decisions).
2. A person copies lines to `decisions.jsonl` and fills `reviewer`, `reviewed_at`
   (UTC `...Z`), `decision` (`accepted|rejected|needs_context`), `reason` and the
   dimensions. `expert_validation` is true only for an actual qualified expert review.
   Accepting against a failed model check requires `model_check_disagreement`.
3. `python -m atlas review-import review/decisions.jsonl` binds each decision to the
   current content fingerprint. Untouched template lines, stale hashes and attempts
   to accept structural failures are rejected (nonzero exit).

Accepted means "faithful sourced statement", not "true biology". Non-expert
source-fidelity review is never labeled scientific validation.
