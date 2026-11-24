"""Secure handling of uploaded files: validation + text extraction.

* Only TXT, MD, CSV, PDF and DOCX are accepted.
* Size limit (MAX_UPLOAD_MB) and a content ("magic bytes") check, so a renamed
  executable is rejected even if it ends in .pdf.
* Files are processed in memory and never written to disk or executed.
"""
import csv
import io
import re

import docx
import fitz  # PyMuPDF
from fastapi import HTTPException, UploadFile

from app import config

ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".pdf", ".docx"}
_REQUIREMENT_COLUMNS = ("requirement", "statement", "description", "text")
_SOURCE_COLUMNS = ("source", "stakeholder", "owner", "raised by")


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", (name or "upload").split("/")[-1].split("\\")[-1])
    return name[:120] or "upload"


async def read_upload(upload: UploadFile) -> tuple[str, str]:
    """Validate an upload and return (safe filename, extracted text)."""
    name, text, _pages = await read_upload_with_pages(upload)
    return name, text


async def read_upload_with_pages(upload: UploadFile) -> tuple[str, str, list[str] | None]:
    """Like read_upload, plus the text of every page for PDFs (None for other files),
    so knowledge-base chunks can record the page they come from."""
    name = safe_filename(upload.filename)
    extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"{name}: unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    data = await upload.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"{name}: file larger than {config.MAX_UPLOAD_MB} MB")
    if not data:
        raise HTTPException(400, f"{name}: file is empty")
    try:
        text = extract_text(data, extension)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"{name}: could not read file ({type(exc).__name__})") from exc
    if not text.strip():
        raise HTTPException(400, f"{name}: no readable text found (scanned PDFs are not supported)")
    return name, text, (pdf_pages(data) if extension == ".pdf" else None)


def pdf_pages(data: bytes) -> list[str]:
    with fitz.open(stream=data, filetype="pdf") as pdf:
        return [join_wrapped_lines(page.get_text()) for page in pdf]


def extract_text(data: bytes, extension: str) -> str:
    if extension == ".pdf":
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "File content is not a PDF")
        with fitz.open(stream=data, filetype="pdf") as pdf:
            return join_wrapped_lines("\n".join(page.get_text() for page in pdf))
    if extension == ".docx":
        if not data.startswith(b"PK"):
            raise HTTPException(400, "File content is not a DOCX document")
        document = docx.Document(io.BytesIO(data))
        lines = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(lines)
    if b"\x00" in data[:4096]:
        raise HTTPException(400, "Binary content is not allowed in a text file")
    text = data.decode("utf-8-sig", errors="replace")
    return csv_to_lines(text) if extension == ".csv" else text


def csv_to_lines(text: str) -> str:
    """CSV with a 'requirement' column (and optional 'source') -> one tagged line per row."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return text
    headers = {h.lower().strip(): h for h in rows[0].keys() if h}

    def best_column(candidates: tuple[str, ...]) -> str | None:
        # Candidates are in priority order: a "Requirement" column wins over "Description" or "Context text".
        for candidate in candidates:
            for lowered, original in headers.items():
                if candidate in lowered:
                    return original
        return None

    req_col = best_column(_REQUIREMENT_COLUMNS)
    if not req_col:
        return text
    src_col = best_column(_SOURCE_COLUMNS)
    lines = []
    for row in rows:
        statement = (row.get(req_col) or "").strip()
        if statement:
            source = (row.get(src_col) or "").strip() if src_col else ""
            lines.append(f"[{source}] {statement}" if source else statement)
    return "\n".join(lines)


def join_wrapped_lines(text: str) -> str:
    """PDF text breaks sentences across lines; re-join lines that continue a sentence."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if out and out[-1] and line and not re.search(r"[.:;!?]$", out[-1]) and line[0].islower():
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return "\n".join(out)
