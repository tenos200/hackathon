"""Minimal OBO 1.4 reader for the pinned HPO and Mondo releases.

Reads [Term] stanzas: id, name, alt_id, synonym (text and scope), is_a,
is_obsolete, replaced_by, xref (with trailing qualifiers kept verbatim) and the
header `data-version`. It does not interpret anything beyond these tags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SYNONYM_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"\s+(EXACT|BROAD|NARROW|RELATED)\b')
_XREF_RE = re.compile(r"^(\S+)(?:\s+(\{.*\}))?")


@dataclass
class OboTerm:
    id: str
    name: str | None = None
    alt_ids: list[str] = field(default_factory=list)
    synonyms: list[tuple[str, str]] = field(default_factory=list)  # (text, EXACT|BROAD|NARROW|RELATED)
    is_a: list[str] = field(default_factory=list)
    is_obsolete: bool = False
    replaced_by: list[str] = field(default_factory=list)
    xrefs: list[tuple[str, str | None]] = field(default_factory=list)  # (id, qualifier block)
    subsets: list[str] = field(default_factory=list)


@dataclass
class OboDocument:
    data_version: str | None
    terms: dict[str, OboTerm]


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def parse_obo(text: str) -> OboDocument:
    data_version: str | None = None
    terms: dict[str, OboTerm] = {}
    current: OboTerm | None = None
    in_term = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("!"):
            continue
        if line.startswith("["):
            if current is not None:
                terms[current.id] = current
            current = None
            in_term = line == "[Term]"
            continue
        if ":" not in line:
            continue
        tag, _, value = line.partition(":")
        value = value.strip()
        if " ! " in value and tag in ("is_a", "replaced_by", "alt_id"):
            value = value.split(" ! ", 1)[0].strip()
        if not in_term and current is None and tag == "data-version":
            data_version = value
            continue
        if not in_term:
            continue
        if tag == "id":
            current = OboTerm(id=value)
            continue
        if current is None:
            continue
        if tag == "name":
            current.name = value
        elif tag == "alt_id":
            current.alt_ids.append(value)
        elif tag == "synonym":
            match = _SYNONYM_RE.match(value)
            if match:
                current.synonyms.append((_unescape(match.group(1)), match.group(2)))
        elif tag == "is_a":
            current.is_a.append(value.split()[0])
        elif tag == "is_obsolete":
            current.is_obsolete = value == "true"
        elif tag == "replaced_by":
            current.replaced_by.append(value.split()[0])
        elif tag == "subset":
            current.subsets.append(value.split()[0])
        elif tag == "xref":
            match = _XREF_RE.match(value)
            if match:
                current.xrefs.append((match.group(1), match.group(2)))
    if current is not None:
        terms[current.id] = current
    return OboDocument(data_version=data_version, terms=terms)
