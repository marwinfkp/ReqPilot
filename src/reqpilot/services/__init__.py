"""Application services - deterministic domain operations.

Services own the guarantees the architecture refuses to delegate to a model:
approval gates, baselines, audit, evaluation. They must remain callable without
LangGraph, which import-linter enforces.
"""
