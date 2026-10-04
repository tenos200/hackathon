"""Canonicalization happens exactly once, before any offsets are computed.

After canonicalization the text is immutable: offsets are zero-based Unicode
code-point positions, half-open [start, end), and quote matching requires
`canonical_text[start:end] == quote`. Nothing normalizes text afterwards.
Each canonicalizer has a version string that participates in source identity.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from atlas.models.records import Section, Span

TEXT_CANONICALIZER = "text-1"
HTML_CANONICALIZER = "html-text-1"
MARKDOWN_CANONICALIZER = "markdown-1"


def normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonicalize_text(text: str) -> str:
    """Plain text/Markdown: line endings only. Whitespace and Unicode are preserved."""
    return normalize_line_endings(text)


_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
               "section", "article", "header", "footer", "main", "nav", "blockquote", "pre", "dt", "dd", "hr"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def canonicalize_html(markup: str) -> str:
    """Deterministic HTML-to-text (version html-text-1).

    Drops script/style, turns block boundaries into line breaks, collapses
    horizontal whitespace within a line, trims lines and collapses runs of
    blank lines. The result is then immutable.
    """
    parser = _TextExtractor()
    parser.feed(normalize_line_endings(markup))
    parser.close()
    raw = html.unescape("".join(parser.parts)).replace(" ", " ")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in raw.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    return text + "\n" if text else ""


def paragraph_sections(text: str, label_prefix: str = "paragraph") -> list[Section]:
    """Sections for text without native structure: blank-line separated paragraphs."""
    sections: list[Section] = []
    for index, match in enumerate(re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text), start=1):
        sections.append(Section(label=f"{label_prefix} {index}", start=match.start(), end=match.end()))
    return sections


class SpanError(ValueError):
    pass


def verify_span(text: str, span: Span) -> None:
    if not (0 <= span.start <= span.end <= len(text)):
        raise SpanError(f"span [{span.start},{span.end}) is outside the canonical text")
    if text[span.start:span.end] != span.quote:
        raise SpanError(f"quote does not match canonical text at [{span.start},{span.end})")


def locate_quote(text: str, quote: str, sections: list[Section], section_label: str | None) -> Span:
    """Find an exact quote. Ambiguous repeats require a disambiguating section label.

    Raises SpanError when the quote is absent or cannot be placed uniquely; the
    caller then records `needs_context` instead of guessing.
    """
    if not quote:
        raise SpanError("empty quote")
    starts = [m.start() for m in re.finditer(re.escape(quote), text)]
    if section_label is not None:
        bounds = [(s.start, s.end) for s in sections if s.label == section_label]
        starts = [st for st in starts if any(a <= st and st + len(quote) <= b for a, b in bounds)]
    if not starts:
        raise SpanError("quote not found verbatim in the canonical text" +
                        (f" within section {section_label!r}" if section_label else ""))
    if len(starts) > 1:
        raise SpanError(f"quote occurs {len(starts)} times; a section label is needed to place it")
    start = starts[0]
    label = section_label
    if label is None:
        label = next((s.label for s in sections if s.start <= start and start + len(quote) <= s.end), None)
    return Span(start=start, end=start + len(quote), quote=quote, section_label=label)
