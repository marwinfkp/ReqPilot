"""The opt-in ``llm`` test category (ADR-012).

This category is excluded by default via ``addopts`` in ``pyproject.toml`` and
must never run in CI. It exists in P0 so the boundary is established and the
mark is registered; no test here makes a network call, because P0 implements no
provider.

Run explicitly with::

    pytest -m llm
"""

from __future__ import annotations

import pytest

from reqpilot.config import Settings

pytestmark = pytest.mark.llm


def test_llm_category_is_opt_in() -> None:
    """A placeholder proving the category is wired and excluded by default.

    When a real provider is implemented in a later roadmap phase, live
    behaviour tests belong here - never in the default suite.
    """
    settings = Settings(_env_file=None)
    assert settings.llm_provider.value == "stub"
