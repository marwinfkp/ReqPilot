"""Module M11 - guardrails: masking, injection defence, output filtering.

Architecture sections P and Q. P0 establishes the package boundary only;
authorization policy itself lives in reqpilot.domain.policy because it is domain
logic that must remain importable without any infrastructure."""
