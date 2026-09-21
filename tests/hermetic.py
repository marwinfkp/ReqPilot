"""Keeps the default test suite hermetic (ADR-012, ET-10). Imported first by conftest.

A developer's ``.env`` - or shell - may select a real provider and hold a real
API key. The default suite must never inherit either: that would turn offline
tests into billable, networked calls and put a personal key within reach of
every test. So, before any test runs:

* every ``LLM_*`` variable is removed from this process's environment and set
  aside in :data:`SHELL_LLM_ENVIRONMENT`;
* ``REQPILOT_DOTENV=off`` stops settings from reading ``.env`` at all.

Only the opt-in live tests (``tests/llm``, ``pytest -m llm``) put the developer's
configuration back, explicitly, for the duration of one test.
"""

from __future__ import annotations

import os

#: The developer's own ``LLM_*`` shell settings, removed from the test process.
SHELL_LLM_ENVIRONMENT: dict[str, str] = {
    name: os.environ.pop(name) for name in list(os.environ) if name.upper().startswith("LLM_")
}

os.environ["REQPILOT_DOTENV"] = "off"
