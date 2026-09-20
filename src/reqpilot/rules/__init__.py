"""Module M7 - deterministic rule and scoring configuration (architecture DQ-03).

Rule-like configuration lives in versioned YAML data files under ``data/``,
never in the environment, so that changing a rule is a data change rather than
a code change.

P0 provides the loading and versioning mechanism plus one example file. The
actual risk matrix, MCDA weights, SDLC rules and control catalogues arrive with
the roadmap phases that use them - defining them now would be speculative.
"""

from reqpilot.rules.loader import RuleSet, load_all, load_ruleset

__all__ = ["RuleSet", "load_all", "load_ruleset"]
