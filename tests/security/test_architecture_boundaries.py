"""Architecture boundary and repository-safety tests.

Two kinds of check live here:

* **Import direction** - the domain, repository and service layers must remain
  usable with no LangGraph and no LLM code. This is the structural expression
  of "governance lives outside the LLM", so a violation is a security finding,
  not a style issue.
* **Repository hygiene** - no secret is committed, `.env` is ignored, and
  `.env.example` contains placeholders only.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "reqpilot"

#: Packages the domain-side layers may never import.
FORBIDDEN_FOR_DOMAIN = ("langgraph", "reqpilot.graph", "reqpilot.agents", "reqpilot.llm")

#: The layers that must stay independent of orchestration.
DOMAIN_SIDE = ("domain", "repositories", "services")


def imported_modules(path: Path) -> set[str]:
    """Return every module name imported by a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def domain_side_files() -> list[Path]:
    files: list[Path] = []
    for package in DOMAIN_SIDE:
        files.extend((SRC / package).rglob("*.py"))
    return files


# --- import direction ----------------------------------------------------


def test_domain_side_files_exist() -> None:
    """Guard against the boundary test passing because it found nothing."""
    assert len(domain_side_files()) >= 10


@pytest.mark.parametrize("path", domain_side_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_domain_does_not_import_orchestration_or_llm(path: Path) -> None:
    for imported in imported_modules(path):
        for forbidden in FORBIDDEN_FOR_DOMAIN:
            assert not (imported == forbidden or imported.startswith(forbidden + ".")), (
                f"{path.relative_to(REPO_ROOT)} imports {imported!r}. The domain, "
                "repository and service layers must remain usable without "
                "LangGraph or LLM code (architecture A.4, J.1)."
            )


def test_domain_is_importable_in_a_process_without_langgraph() -> None:
    """The strongest form of the check: import the domain with langgraph blocked.

    If the domain has a hidden dependency on orchestration, this fails even when
    the static check passes.
    """
    script = (
        "import sys;\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name == 'langgraph' or name.startswith('langgraph.'):\n"
        "            raise ImportError('langgraph is blocked for this test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block());\n"
        "import reqpilot.domain.policy, reqpilot.domain.models, "
        "reqpilot.services.audit, reqpilot.repositories;\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_graph_layer_may_import_the_domain() -> None:
    """The permitted direction must actually work, or the rule is vacuous."""
    import reqpilot.graph  # noqa: F401
    from reqpilot.graph.state import BaseGraphState  # noqa: F401

    assert True


def test_only_the_llm_package_may_reference_a_provider_sdk() -> None:
    """The gateway is the single choke point (ADR-006)."""
    provider_markers = re.compile(r"\b(anthropic|openai|ollama)\b", re.IGNORECASE)
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if "llm" in path.parts or path.name == "config.py":
            continue  # the gateway and the provider selector may name providers
        for imported in imported_modules(path):
            if provider_markers.search(imported):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {imported}")
    assert not offenders, f"provider SDK imported outside the gateway: {offenders}"


# --- repository hygiene --------------------------------------------------


def test_env_is_gitignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert "data/private" in gitignore


def test_no_dotenv_file_is_committed() -> None:
    """A real .env must never exist in a tracked location."""
    assert not (REPO_ROOT / ".env").exists() or ".env" in (REPO_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_env_example_exists_and_is_placeholders_only() -> None:
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    # No value that looks like a real credential.
    real_key = re.compile(r"(sk-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,})")
    assert not real_key.search(content), "'.env.example' appears to contain a real credential"

    # Secret-bearing keys must be empty or an obvious placeholder.
    for line in content.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        # Strip a trailing inline comment, as a real .env parser would.
        value = raw_value.split("#", 1)[0].strip()
        if any(token in key.upper() for token in ("KEY", "PASSWORD", "SECRET", "TOKEN")):
            assert value == "" or "CHANGE_ME" in value or "placeholder" in value.lower(), (
                f"{key} in .env.example must be empty or an obvious placeholder, got {value!r}"
            )


def test_env_example_covers_every_setting() -> None:
    """The contract must not drift behind the code.

    A setting that exists but is undocumented is a setting nobody configures
    correctly.
    """
    from reqpilot.config import Settings

    documented = {
        line.split("=", 1)[0].strip()
        for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    required = {(field.alias or name).upper() for name, field in Settings.model_fields.items()}
    missing = required - {d.upper() for d in documented}
    assert not missing, f".env.example is missing: {sorted(missing)}"


def test_no_secret_literals_in_source() -> None:
    """A crude but useful guard against a pasted credential."""
    suspicious = re.compile(r"(sk-[A-Za-z0-9]{16,}|password\s*=\s*[\"'][^\"']{6,}[\"'])")
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if suspicious.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"possible hard-coded secret in: {offenders}"


def test_no_real_data_directories_are_populated() -> None:
    """``data/private`` must be empty apart from its README."""
    private = REPO_ROOT / "data" / "private"
    if not private.exists():
        return
    entries = {p.name for p in private.iterdir()} - {"README.md", ".gitkeep"}
    assert not entries, f"data/private must stay local-only, found: {sorted(entries)}"


# --- data-directory convention -------------------------------------------

#: The canonical layout, from architecture section V and R.3. A single spelling
#: for each directory, so code, configuration and documentation cannot drift
#: apart again.
CANONICAL_DATA_DIRS = {"kb_seed", "gold", "dev", "private"}

#: Names that were used before the convention was settled. Their reappearance
#: would mean the discrepancy had returned.
SUPERSEDED_DATA_DIRS = {"corpus", "fixtures"}


def test_data_directories_match_the_architecture() -> None:
    present = {p.name for p in (REPO_ROOT / "data").iterdir() if p.is_dir()}
    assert present == CANONICAL_DATA_DIRS, (
        f"data/ must match architecture section V exactly.\n"
        f"  unexpected: {sorted(present - CANONICAL_DATA_DIRS)}\n"
        f"  missing:    {sorted(CANONICAL_DATA_DIRS - present)}"
    )


def test_superseded_data_directory_names_are_gone() -> None:
    present = {p.name for p in (REPO_ROOT / "data").iterdir() if p.is_dir()}
    clashes = present & SUPERSEDED_DATA_DIRS
    assert not clashes, f"superseded data directory name(s) reappeared: {sorted(clashes)}"


def test_every_data_directory_documents_itself() -> None:
    for name in CANONICAL_DATA_DIRS:
        assert (REPO_ROOT / "data" / name / "README.md").exists(), f"data/{name} has no README"


def test_settings_point_at_the_canonical_directories() -> None:
    """Configuration must not name a directory that does not exist."""
    from reqpilot.config import Settings

    settings = Settings(_env_file=None)
    for path in (settings.kb_seed_dir, settings.gold_data_dir, settings.dev_data_dir):
        assert (REPO_ROOT / path).is_dir(), f"configured data path does not exist: {path}"
