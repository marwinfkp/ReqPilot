"""Markdown rendering of a structured artefact (ADR-007: Jinja2 -> Markdown, canonical).

The layout is a versioned Jinja2 template (``templates/document-<version>.md.j2``)
rendered in a **sandboxed** environment with ``StrictUndefined``. The template is
package data written by the team; the document is passed to it only as *data*.
Project content is never compiled as template source, so a requirement that
contains ``{{ ... }}`` or ``{% ... %}`` is printed, not evaluated.

Every value passes through :func:`md_text` (or :func:`md_cell` in a table), which
neutralises what Markdown would otherwise interpret: raw HTML (``<script>``),
link and image syntax, code spans, a line that would start a heading, quote or
list, and table pipes. The output is deterministic - the same document renders
to the same bytes - with stable headings and stable section ids.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from reqpilot.artifacts.model import Document, Fields, Items, Notice, Paragraph, Section, Table

MARKDOWN_LAYOUT_VERSION = "1.0.0"
_TEMPLATES = Path(__file__).resolve().parent / "templates"

#: XML 1.0-illegal control characters (also stripped for DOCX). Tab, LF, CR stay.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_START = re.compile(r"^(\s*)(#|>|-|\+|\*|=|\d+[.)])")


def _escape(value: object) -> str:
    text = CONTROL_CHARS.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\", "\\\\")
    for char in ("`", "[", "]", "|"):
        text = text.replace(char, "\\" + char)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md_text(value: object, *, block: bool = False) -> str:
    """Escape one value for Markdown. Data stays data.

    Raw HTML, link and image syntax, code spans and table pipes are always
    neutralised. A line that would open a heading, quote, list or rule is escaped
    wherever it starts a Markdown line: every line of a ``block`` value, and every
    line after the first of an inline value (the first line of an inline value
    follows a list marker or a label, where it opens nothing).
    """
    lines = []
    for index, line in enumerate(_escape(value).split("\n")):
        line = line.rstrip()
        if block or index > 0:
            line = _LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), line)
        lines.append(line)
    return "  \n".join(lines).strip()


def md_cell(value: object) -> str:
    """Escape one value for a Markdown table cell: one line, pipes escaped."""
    return " ".join(_escape(value).split()) or " "


def md_anchor(key: object) -> str:
    """A stable heading id from the section key (Pandoc attribute syntax)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(key).lower()).strip("-") or "section"
    return "{#" + slug + "}"


def _fields(pairs: object) -> str:
    return "\n".join(f"- **{md_text(label)}:** {md_text(value)}" for label, value in pairs)  # type: ignore[attr-defined]


def _heading(section: Section) -> str:
    level = min(max(int(section.level), 2), 4)
    return (
        f"{'#' * level} {md_cell(section.number)} {md_cell(section.title)} {md_anchor(section.key)}"
    )


def _traces(citations: object) -> str:
    labels = ", ".join(c.label for c in citations)  # type: ignore[attr-defined]
    return f"*Traces to: {md_text(labels)}*"


def _block(block: object) -> str:
    if isinstance(block, Paragraph):
        return md_text(block.text, block=True)
    if isinstance(block, Notice):
        return "> " + md_text(block.text).replace("  \n", "  \n> ")
    if isinstance(block, Items):
        return "\n".join(
            f"{index}. {md_text(item)}" if block.ordered else f"- {md_text(item)}"
            for index, item in enumerate(block.items, start=1)
        )
    if isinstance(block, Fields):
        return _fields(block.pairs)
    if isinstance(block, Table):
        head = "| " + " | ".join(md_cell(c) for c in block.columns) + " |"
        rule = "|" + "---|" * len(block.columns)
        rows = ["| " + " | ".join(md_cell(v) for v in row) + " |" for row in block.rows]
        return "\n".join([head, rule, *rows])
    raise TypeError(f"unknown block {type(block).__name__}")  # pragma: no cover


@lru_cache(maxsize=4)
def _environment() -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["md"] = md_text
    env.filters["fields"] = _fields
    env.filters["heading"] = _heading
    env.filters["traces"] = _traces
    env.filters["render_block"] = _block
    return env


def render_markdown(document: Document, *, layout_version: str = MARKDOWN_LAYOUT_VERSION) -> str:
    """Render the canonical Markdown of one document."""
    template = _environment().get_template(f"document-{layout_version}.md.j2")
    text = template.render(document=document)
    # Collapse runs of blank lines so the output is stable whatever a block ends with.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
