"""Strict structured-output schemas for extraction and contextual checking.

The constrained schema limits syntax only; it does not make a claim correct.
Every property is required and nullable values are explicit, as strict
structured outputs require.
"""

from __future__ import annotations

from atlas.models.predicates import EXTENSION_PREDICATES, PREDICATES

ENTITY_TYPES = ["gene", "disease", "variant", "mechanism", "phenotype", "process", "organization", "asset", "study", "person"]
STATUSES = ["reported_result", "proposed", "planned", "ongoing", "background", "negated", "inconclusive", "listed_record"]
DIRECTIONS = ["improves", "worsens", "no_detected_effect", "unknown", "not_applicable"]
NULLABLE_STRING = {"type": ["string", "null"]}

EXTRACTION_SCHEMA_VERSION = "extraction-schema-1"
CHECK_SCHEMA_VERSION = "check-schema-1"

_SCOPE = {
    "type": "object", "additionalProperties": False,
    "required": ["population_text", "age_text", "species", "cell_or_tissue", "assay_text", "outcome_text",
                 "comparator_text", "timeframe_text", "other_qualifiers", "variant_mentions"],
    "properties": {
        "population_text": NULLABLE_STRING, "age_text": NULLABLE_STRING, "species": NULLABLE_STRING,
        "cell_or_tissue": NULLABLE_STRING, "assay_text": NULLABLE_STRING, "outcome_text": NULLABLE_STRING,
        "comparator_text": NULLABLE_STRING, "timeframe_text": NULLABLE_STRING,
        "other_qualifiers": {"type": "array", "items": {"type": "string"}},
        "variant_mentions": {"type": "array", "items": {"type": "string"}},
    },
}

EXTRACTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["abstained", "abstention_reason", "claims"],
    "properties": {
        "abstained": {"type": "boolean"},
        "abstention_reason": NULLABLE_STRING,
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["subject_mention", "subject_type", "predicate", "object_mention", "object_type",
                         "statement_status", "effect_direction", "patient_count", "scope", "support_quotes"],
            "properties": {
                "subject_mention": {"type": "string"}, "subject_type": {"type": "string", "enum": ENTITY_TYPES},
                "predicate": {"type": "string", "enum": sorted(set(PREDICATES) - EXTENSION_PREDICATES)},
                "object_mention": {"type": "string"}, "object_type": {"type": "string", "enum": ENTITY_TYPES},
                "statement_status": {"type": "string", "enum": STATUSES},
                "effect_direction": {"type": "string", "enum": DIRECTIONS},
                "patient_count": {"type": ["integer", "null"]},
                "scope": _SCOPE,
                "support_quotes": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["quote", "section_label"],
                    "properties": {"quote": {"type": "string"}, "section_label": NULLABLE_STRING}}},
            },
        }},
    },
}

_DIMENSION = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "reason", "quotes"],
    "properties": {"verdict": {"type": "string", "enum": ["pass", "fail", "insufficient_context"]},
                   "reason": {"type": "string"}, "quotes": {"type": "array", "items": {"type": "string"}}},
}
CHECK_DIMENSIONS = ["text_supports_assertion", "relation_and_direction", "status_and_attribution",
                    "scope_preserved", "entities_correct"]
CHECK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": CHECK_DIMENSIONS,
    "properties": {name: _DIMENSION for name in CHECK_DIMENSIONS},
}
