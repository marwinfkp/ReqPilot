"""The versioned extraction ruleset (DQ-03): loaded, validated, fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from tests.p3_helpers import RULES_DIR

from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.extraction import ExtractionRules, load_extraction_rules
from reqpilot.rules.loader import load_ruleset

pytestmark = pytest.mark.unit


def test_the_ruleset_loads_with_its_version() -> None:
    rules = load_extraction_rules(RULES_DIR)
    assert rules.name == "extraction" and rules.version == "1.0.0"
    assert rules.statement_prefix == "The system shall"
    assert rules.auto_merge == "exact_normalised"
    assert 0 < rules.classification_review_threshold < 1


def _write(tmp_path: Path, **changes) -> Path:
    raw = yaml.safe_load((RULES_DIR / "extraction.yaml").read_text(encoding="utf-8"))
    for dotted, value in changes.items():
        section, key = dotted.split("__")
        raw["rules"][section][key] = value
    path = tmp_path / "extraction.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "changes",
    [
        {"dedupe__auto_merge": "similarity"},
        {"dedupe__review_similarity": 1.5},
        {"classification__review_threshold": -0.1},
        {"extraction__max_segments_per_call": 0},
        {"extraction__statement_prefix": "  "},
    ],
)
def test_an_unsafe_ruleset_is_refused(tmp_path: Path, changes) -> None:
    with pytest.raises(RuleConfigurationError):
        ExtractionRules.from_ruleset(load_ruleset(_write(tmp_path, **changes)))
