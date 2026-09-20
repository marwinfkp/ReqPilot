"""Domain layer - pure business types, policy and state rules.

Must not import LangGraph, any LLM code, or the API/web layers. This is the
structural expression of "governance lives outside the LLM" (architecture J.1),
enforced in CI by import-linter.
"""
