"""Shared pytest setup for the ReqPilot backend.

IMPORTANT: the environment variables below are set *before* any `app` module is
imported, because app/config.py reads them once at import time:

* DATABASE_URL   -> a throw-away SQLite file in a temporary folder (never PostgreSQL;
                    a file, not :memory:, because the pipeline uses its own session),
* OPENAI_API_KEY -> "" so ReqPilot starts in demonstration mode (no OpenAI calls).

Tests that need "OpenAI mode" use the `openai_mode` fixture, which switches
`config.DEMO_MODE` off with monkeypatch (restored automatically) and installs the
fake client from tests/fake_openai.py.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # do not write __pycache__ folders into app/ while testing

_TEST_DIR = Path(tempfile.mkdtemp(prefix="reqpilot_pytest_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'reqpilot_test.db').as_posix()}"
os.environ["OPENAI_API_KEY"] = ""
os.environ["COOKIE_SECURE"] = "false"  # the TestClient talks plain http
os.environ["MAX_UPLOAD_MB"] = "10"
os.environ["MAX_REQUIREMENTS_CHARS"] = "300000"
os.environ["LLM_BATCH_SIZE"] = "20"
os.environ["LLM_CONCURRENCY"] = "4"

import openai  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, llm, pipeline, security  # noqa: E402
from app.database import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402
from tests.fake_openai import FakeOpenAI  # noqa: E402
from tests.helpers import CASES, USERS  # noqa: E402

# Safety nets: fail immediately if the configuration is not the test configuration.
assert config.DATABASE_URL.startswith("sqlite:///"), "tests must never touch PostgreSQL"
assert config.DEMO_MODE, "tests must start in demonstration mode (OPENAI_API_KEY empty)"


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()  # release the SQLite file so the temp folder can be removed (Windows)
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# Autouse fixtures (run for every test)
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _demo_mode_is_restored():
    """After every test (and after monkeypatch has undone its changes) DEMO_MODE must be True again."""
    yield
    assert config.DEMO_MODE is True, "a test left config.DEMO_MODE changed"


@pytest.fixture(autouse=True)
def _no_real_openai(_demo_mode_is_restored, monkeypatch):
    """Make it impossible to create a real OpenAI client during the tests."""

    class ForbiddenOpenAI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Tests must never create a real OpenAI client")

    monkeypatch.setattr(openai, "OpenAI", ForbiddenOpenAI)
    monkeypatch.setattr(llm, "_client", None)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The in-memory rate limiter (600 req/min, 10 logins/min) is shared by all tests."""
    security._hits.clear()
    yield
    security._hits.clear()


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def _seeded_database():
    init_db()
    with SessionLocal() as session:
        seed(session)  # demo users + knowledge_base/*.md (idempotent)


@pytest.fixture
def db(_seeded_database):
    """A SQLAlchemy session on the seeded SQLite test database."""
    session = SessionLocal()
    yield session
    session.close()


# --------------------------------------------------------------------------
# OpenAI mode with a fake client
# --------------------------------------------------------------------------
@pytest.fixture
def openai_mode(monkeypatch):
    """Switch ReqPilot to OpenAI mode, backed by FakeOpenAI (no network, no key)."""
    fake = FakeOpenAI()
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    return fake


# --------------------------------------------------------------------------
# API client and authentication
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def client(_seeded_database):
    """One TestClient for the session. `with` runs the app lifespan (tables, demo users,
    knowledge base). The analysis pipeline runs synchronously inside the request."""
    patch = pytest.MonkeyPatch()
    patch.setattr(pipeline, "start_in_background", lambda analysis_id: pipeline.run_analysis(analysis_id))
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


def login(test_client: TestClient, email: str, password: str | None = None):
    """POST /api/auth/login. Returns (response, session token or None).
    The cookie is removed from the client again so that one client can serve all users."""
    response = test_client.post("/api/auth/login",
                                json={"email": email, "password": password or config.DEMO_USER_PASSWORD})
    token = response.cookies.get(security.SESSION_COOKIE)
    test_client.cookies.clear()
    return response, token


@pytest.fixture(scope="session")
def auth(client):
    """auth("analyst") -> {"Authorization": "Bearer <session token>"} (logged in once per run)."""
    tokens: dict[str, str] = {}

    def headers(user: str) -> dict:
        if user not in tokens:
            security._hits.clear()  # stay below the login rate limit
            response, token = login(client, USERS[user])
            assert response.status_code == 200, response.text
            tokens[user] = token
        return {"Authorization": f"Bearer {tokens[user]}"}

    return headers


@pytest.fixture
def create_analysis(client, auth):
    """create_analysis(case="digital_banking", user="analyst", **form) -> analysis id.

    Posts the benchmark case to POST /api/analyses; the pipeline runs synchronously."""

    def create(case: str = "digital_banking", user: str = "analyst", files=None, **form) -> int:
        data = {**CASES[case]["project"], "requirements_text": CASES[case]["requirements_text"], **form}
        response = client.post("/api/analyses", data=data, files=files, headers=auth(user))
        assert response.status_code == 200, response.text
        return response.json()["analysis_id"]

    return create
