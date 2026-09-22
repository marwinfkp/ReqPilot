"""Platform-independent content hashing for frozen evaluation datasets (architecture R.3, D16).

A frozen benchmark is verified by the sha256 of every file its manifest lists.
That hash must identify the *content*, not the machine that checked it out: Git
stores text with LF line endings, a Windows checkout with ``core.autocrlf=true``
writes CRLF, and a Linux checkout writes LF. Hashing raw working-tree bytes
therefore gave one hash on Windows and another on Linux for the same frozen file.

The canonical form is the one the prompt registry already uses for its locks
(``llm/prompts.file_sha256``): the file decoded as UTF-8, every CRLF replaced by
LF, encoded back as UTF-8. Nothing else is normalised - not a lone CR, trailing
whitespace, a BOM, or Unicode form - so every change to the text other than the
line-ending convention still changes the hash. A file that is not valid UTF-8 is
binary to Git, which never converts it; it is hashed as stored.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def canonical_bytes(data: bytes) -> bytes:
    """``data`` with CRLF line endings as LF when it is UTF-8 text; unchanged otherwise."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return text.replace("\r\n", "\n").encode("utf-8")


def canonical_sha256(data: bytes) -> str:
    """The platform-independent sha256 of file content."""
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def file_canonical_sha256(path: Path) -> str:
    """The platform-independent sha256 of a file - what frozen manifests record."""
    return canonical_sha256(path.read_bytes())
