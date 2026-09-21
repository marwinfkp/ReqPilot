"""Seed the knowledge base from a curated manifest (architecture V: ``scripts/seed_kb``).

Usage::

    python scripts/seed_kb.py --manifest data/dev/kb_synthetic/manifest.yaml --actor <user id>

The actor must be a real user holding the Knowledge-Base Administrator role in at
least one project; their roles are read from the database exactly as the API
reads them, so this script cannot do anything the API would refuse. Every source
and item goes through the same service as the API - licence, taxonomy and dedupe
rules included - and every addition is audited, in one transaction.

Seeding is idempotent: re-running a manifest adds only what is new.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from reqpilot.api.dependencies import load_actor
from reqpilot.config import get_settings
from reqpilot.repositories.database import session_scope
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.embeddings import build_embedding_provider
from reqpilot.retrieval.rules import load_retrieval_rules
from reqpilot.services.knowledge import KnowledgeAdminService, seed_from_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--actor",
        required=True,
        type=uuid.UUID,
        help="user id of a Knowledge-Base Administrator (human role, Phase 0 F.1)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    embedder = build_embedding_provider(settings)
    rules = load_retrieval_rules(settings.rules_dir)
    with session_scope(settings) as session:
        actor = load_actor(session, args.actor)
        admin = KnowledgeAdminService(session, actor, embedder=embedder, rules=rules)
        summary = seed_from_manifest(args.manifest, admin, KnowledgeBaseRepository(session, actor))
    print(
        f"sources added {summary.sources_added}, reused {summary.sources_reused}; "
        f"items added {summary.items_added}, skipped {summary.items_skipped}; "
        f"embedding model {embedder.model_id}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
