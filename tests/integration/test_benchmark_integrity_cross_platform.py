"""Frozen-benchmark integrity is platform-independent, and no weaker (R.3, D16).

The same frozen benchmark must produce the same hashes on Windows (CRLF working
tree under ``core.autocrlf=true``) and Linux (LF), while any change to the
content - other than the line-ending convention - is still refused.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from tests.p3_helpers import REPO_ROOT

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.domain.integrity import canonical_bytes, canonical_sha256, file_canonical_sha256
from reqpilot.services.evaluation.extraction_eval import load_gold_set
from reqpilot.services.evaluation.quality_eval import load_quality_benchmark

pytestmark = pytest.mark.integration

E1 = REPO_ROOT / "data" / "gold" / "e1_synthetic_v1"
P5 = REPO_ROOT / "data" / "gold" / "p5_quality_conflict_synthetic_v1"
#: Canonical (UTF-8, LF) manifest hashes - identical on every platform.
E1_MANIFEST_SHA256 = "dfc8d21b6d0fa229d4589338a65d52f90940018d599ecff7346d82ed75286b4b"
P5_MANIFEST_SHA256 = "5dd8fd66a2300a87ec2fe8ff0de81da2ed687f974872ab1058153d6569fd62b0"


# --- the canonical hash ----------------------------------------------------------------------


def test_lf_and_crlf_are_the_same_content() -> None:
    lf, crlf = b"line1\nline2\n", b"line1\r\nline2\r\n"
    assert canonical_sha256(lf) == canonical_sha256(crlf) == hashlib.sha256(lf).hexdigest()
    mixed = b"line1\r\nline2\n"
    assert canonical_sha256(mixed) == canonical_sha256(lf)


@pytest.mark.parametrize(
    "changed",
    [
        b"line1\nline3\n",  # a word
        b"line1\nline2",  # the final newline
        b"line1\n\nline2\n",  # an added blank line
        b"line1\rline2\n",  # a lone CR is not a line-ending convention: it is content
        b"line1 \nline2\n",  # trailing whitespace
        "\ufeffline1\nline2\n".encode(),  # a BOM
    ],
)
def test_any_other_change_still_changes_the_hash(changed: bytes) -> None:
    assert canonical_sha256(changed) != canonical_sha256(b"line1\nline2\n")


def test_non_utf8_content_is_hashed_as_stored() -> None:
    binary = b"\xff\xfe\r\n\x00\x01"
    assert canonical_bytes(binary) == binary
    assert canonical_sha256(binary) == hashlib.sha256(binary).hexdigest()


# --- the real frozen sets, as each platform checks them out ------------------------------------


def _checkout(source: Path, target: Path, newline: bytes) -> Path:
    """A copy with every text file in one line-ending convention (a platform's checkout)."""
    shutil.copytree(source, target)
    for path in target.rglob("*"):
        if path.is_file():
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", newline))
    return target


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["linux-lf", "windows-crlf"])
def test_the_e1_benchmark_verifies_on_either_platform(tmp_path: Path, newline: bytes) -> None:
    gold = load_gold_set(_checkout(E1, tmp_path / "e1", newline))
    assert gold.manifest_sha256 == E1_MANIFEST_SHA256
    assert len(gold.requirements) == 60


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["linux-lf", "windows-crlf"])
def test_the_p5_benchmark_verifies_on_either_platform(tmp_path: Path, newline: bytes) -> None:
    benchmark = load_quality_benchmark(_checkout(P5, tmp_path / "p5", newline))
    assert benchmark.manifest_sha256 == P5_MANIFEST_SHA256
    assert len(benchmark.conflict_pairs) == 12 and len(benchmark.ambiguous_ids) == 20


def test_the_working_tree_sets_verify_with_their_canonical_manifest_hashes() -> None:
    assert load_gold_set(E1).manifest_sha256 == E1_MANIFEST_SHA256
    assert load_quality_benchmark(P5).manifest_sha256 == P5_MANIFEST_SHA256
    assert file_canonical_sha256(E1 / "manifest.json") == E1_MANIFEST_SHA256


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["linux-lf", "windows-crlf"])
def test_a_content_change_is_still_refused_on_either_platform(
    tmp_path: Path, newline: bytes
) -> None:
    e1 = _checkout(E1, tmp_path / "e1", newline)
    benchmark = e1 / "BENCHMARK.md"
    benchmark.write_bytes(benchmark.read_bytes().replace(b"60", b"61", 1))
    with pytest.raises(GoldSetIntegrityError, match=r"BENCHMARK.md does not match its frozen hash"):
        load_gold_set(e1)

    p5 = _checkout(P5, tmp_path / "p5", newline)
    conflicts = p5 / "conflicts.jsonl"
    conflicts.write_bytes(conflicts.read_bytes().replace(b'"conflict"', b'"no_conflict"', 1))
    with pytest.raises(GoldSetIntegrityError, match=r"conflicts.jsonl does not match"):
        load_quality_benchmark(p5)


def test_a_lone_carriage_return_is_refused(tmp_path: Path) -> None:
    """Only CRLF -> LF is forgiven; a stray CR is an edit, and it is caught."""
    p5 = _checkout(P5, tmp_path / "p5", b"\n")
    benchmark = p5 / "BENCHMARK.md"
    benchmark.write_bytes(benchmark.read_bytes().replace(b"\n", b"\r", 1))
    with pytest.raises(GoldSetIntegrityError, match=r"BENCHMARK.md does not match"):
        load_quality_benchmark(p5)
