"""Text extraction for curated knowledge material (architecture J.2).

Exactly the parsers J.2 names - plain text (``.txt``, ``.md``), ``pypdf`` and
``python-docx`` - and nothing else. An unsupported format is refused, not
guessed at: this is not a general "upload anything" facility.

The extracted text becomes the knowledge item's canonical text, and every chunk
offset refers to it. Line endings are normalised to ``\\n`` first, so offsets do
not depend on the platform a file was saved on.
"""

from __future__ import annotations

import io
from pathlib import Path

from reqpilot.domain.errors import KnowledgeBaseError

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".txt", ".md", ".pdf", ".docx"})


def normalise_text(text: str) -> str:
    """Normalise line endings and drop a byte-order mark. Offsets refer to the result."""
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p)


def _docx_text(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    paragraphs = [p.text.strip() for p in document.paragraphs]
    return "\n\n".join(p for p in paragraphs if p)


def extract_text_from_bytes(data: bytes, suffix: str) -> str:
    """Extract text from a file's bytes, choosing the parser by suffix."""
    suffix = suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise KnowledgeBaseError(
            f"unsupported source format {suffix!r}; supported: {sorted(SUPPORTED_SUFFIXES)}"
        )
    try:
        if suffix == ".pdf":
            text = _pdf_text(data)
        elif suffix == ".docx":
            text = _docx_text(data)
        else:
            text = data.decode("utf-8")
    except KnowledgeBaseError:
        raise
    except Exception as exc:
        raise KnowledgeBaseError(f"could not extract text from {suffix} content: {exc}") from exc

    text = normalise_text(text)
    if not text.strip():
        raise KnowledgeBaseError("no text could be extracted; the knowledge item would be empty")
    return text


def extract_text(path: str | Path) -> str:
    """Extract the canonical text of a curated source file."""
    path = Path(path)
    if not path.is_file():
        raise KnowledgeBaseError(f"source file not found: {path}")
    return extract_text_from_bytes(path.read_bytes(), path.suffix)
