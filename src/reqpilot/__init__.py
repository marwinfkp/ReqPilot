"""ReqPilot - agentic requirements engineering and SDLC recommendation assistant.

Authoritative sources, in order:
  1. ``Problem Statement.docx``    - the problem statement
  2. ``docs/01-analysis.md``       - approved Phase 0 analysis and requirements baseline
  3. ``docs/02-architecture.md``   - approved architecture and design
  4. ``docs/03-p0-foundations.md`` - what this roadmap phase (P0) built

The architectural rule that shapes this package layout: the domain, repository
and service layers must remain importable, and testable, with no LangGraph and
no LLM code present. Enforced by the import-linter contracts in ``pyproject.toml``.
"""

__version__ = "0.1.0"
