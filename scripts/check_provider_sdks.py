"""CI guard: no unapproved LLM provider SDK in the installed dependency tree.

    python scripts/check_provider_sdks.py

All model access goes through one gateway (architecture ADR-006), and only the
providers the team has selected may be installed. P0 had none. At P3 closure
the team selected OpenAI (architecture Y): its SDK is the ``openai`` extra and a
``dev`` dependency (the adapter's offline tests use its error types), and it is
imported only by ``reqpilot/llm/openai_provider.py`` (a test enforces that).

Any other known provider SDK in the environment is refused: an accidental
dependency, or a transitive one, that could open a second path to a model.
Adding a provider is a deliberate change to :data:`APPROVED_PROVIDER_SDKS`, with
its gateway adapter - never a silent install.

The guard inspects installed distributions (``importlib.metadata``), not
``import`` - so it neither imports a provider nor calls one. It passes when no
provider SDK at all is installed.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from importlib import metadata

#: Provider SDKs the project has approved, each behind a gateway adapter.
APPROVED_PROVIDER_SDKS: frozenset[str] = frozenset({"openai"})

#: LLM provider SDKs the guard knows. Anything here and not approved fails CI.
KNOWN_PROVIDER_SDKS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "ollama",
        "google-generativeai",
        "google-genai",
        "mistralai",
        "cohere",
        "groq",
        "together",
        "litellm",
        "langchain-openai",
        "langchain-anthropic",
    }
)


def normalise(name: str) -> str:
    """PEP 503 name normalisation (``Google_GenerativeAI`` -> ``google-generativeai``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_distributions() -> set[str]:
    return {normalise(d.metadata["Name"]) for d in metadata.distributions() if d.metadata["Name"]}


def unapproved(installed: Iterable[str]) -> list[str]:
    """Known provider SDKs that are installed and not approved."""
    names = {normalise(n) for n in installed}
    return sorted((names & KNOWN_PROVIDER_SDKS) - APPROVED_PROVIDER_SDKS)


def main(installed: Iterable[str] | None = None) -> int:
    names = installed_distributions() if installed is None else {normalise(n) for n in installed}
    found = sorted(names & KNOWN_PROVIDER_SDKS)
    refused = unapproved(names)
    if refused:
        print(
            "Unapproved provider SDK(s) installed: "
            + ", ".join(refused)
            + f". Approved: {', '.join(sorted(APPROVED_PROVIDER_SDKS))}. "
            "A provider is added deliberately, with its gateway adapter (ADR-006)."
        )
        return 1
    print(f"Provider SDKs installed: {', '.join(found) or 'none'} (all approved).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
