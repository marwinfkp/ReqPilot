"""The shape of the retrieval SQL (architecture J.4) - **generated SQL only**.

These tests compile the statements for the PostgreSQL dialect and inspect them.
They prove the allowlist is a join inside the query and that every restriction
precedes ``LIMIT``; they do **not** prove runtime behaviour. That is proved
against a live PostgreSQL in ``tests/integration/test_p2_postgres_retrieval.py``.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from reqpilot.domain.enums import NormativeSourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.knowledge import KnowledgeChunk
from reqpilot.repositories.knowledge import RetrievalScope, scoped_chunks

pytestmark = pytest.mark.unit

PROJECT = ProjectId(uuid.UUID("11111111-1111-1111-1111-111111111111"))


def scope(**overrides: object) -> RetrievalScope:
    fields: dict = {
        "project_id": PROJECT,
        "jurisdictions": ("IN", "INTL"),
        "kb_version_pin": None,
        "kb_version": 5,
        "as_of": dt.date(2026, 6, 1),
        "allowlist_size": 2,
    }
    fields.update(overrides)
    return RetrievalScope(**fields)


def sql(stmt) -> str:
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return " ".join(str(compiled).split())


def test_the_allowlist_is_an_inner_join_on_this_project() -> None:
    text = sql(scoped_chunks(select(KnowledgeChunk.id), scope()).limit(8))
    assert re.search(
        r"JOIN source_allowlist ON source_allowlist\.normative_source_id = normative_source\.id "
        r"AND source_allowlist\.project_id = '11111111-?1111-?1111-?1111-?111111111111'",
        text,
    ), text
    assert "LEFT OUTER JOIN" not in text, "an outer join would let non-allowlisted rows through"


def test_every_restriction_is_a_predicate_before_limit() -> None:
    text = sql(scoped_chunks(select(KnowledgeChunk.id), scope()).limit(8))
    where, _, tail = text.partition(" LIMIT ")
    assert tail.strip() == "8"
    assert "normative_source.jurisdiction IN ('IN', 'INTL')" in where
    assert (
        "normative_source.effective_date IS NULL OR normative_source.effective_date <= '2026-06-01'"
        in where
    )
    assert "knowledge_item.status = 'ACTIVE'" in where


def test_a_pinned_project_filters_by_kb_version_instead_of_status() -> None:
    text = sql(scoped_chunks(select(KnowledgeChunk.id), scope(kb_version_pin=3)))
    assert "knowledge_item.kb_version <= 3" in text
    superseded = "knowledge_item.superseded_in_kb_version"
    assert f"{superseded} IS NULL OR {superseded} > 3" in text
    assert "knowledge_item.status" not in text


def test_no_jurisdiction_scope_means_no_rows() -> None:
    text = sql(scoped_chunks(select(KnowledgeChunk.id), scope(jurisdictions=())))
    assert "false" in text.lower()


def test_classification_adds_predicates_and_never_removes_the_join() -> None:
    plain = sql(scoped_chunks(select(KnowledgeChunk.id), scope()))
    narrowed = sql(
        scoped_chunks(
            select(KnowledgeChunk.id),
            scope(),
            source_types=frozenset({NormativeSourceType.ORG_POLICY}),
            applicability=frozenset({"privacy"}),
            embedding_model="BAAI/bge-small-en-v1.5",
        )
    )
    assert "JOIN source_allowlist" in narrowed
    assert "normative_source.source_type IN ('ORG_POLICY')" in narrowed
    assert "&&" in narrowed, "applicability is an array-overlap predicate"
    assert "knowledge_chunk.embedding_model = 'BAAI/bge-small-en-v1.5'" in narrowed
    # Everything in the plain statement's WHERE clause survives narrowing.
    plain_where = plain.partition(" WHERE ")[2]
    for predicate in plain_where.split(" AND "):
        assert predicate.strip("() ") in narrowed
