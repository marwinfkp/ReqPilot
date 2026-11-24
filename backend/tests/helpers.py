"""Small helpers shared by the test modules (plain functions, no pytest magic)."""
import io
import json

import docx
import fitz  # PyMuPDF

from app import config, pipeline

BENCHMARK_CASES: list[dict] = json.loads(
    (config.EVALUATION_DIR / "benchmark.json").read_text(encoding="utf-8"))["cases"]
CASES: dict[str, dict] = {case["key"]: case for case in BENCHMARK_CASES}

USERS = {
    "admin": "admin@reqpilot.local",
    "analyst": "analyst@reqpilot.local",
    "compliance": "compliance@reqpilot.local",
    "security": "security@reqpilot.local",
    "pm": "pm@reqpilot.local",
    "auditor": "auditor@reqpilot.local",
}

AGENT_NAMES = [stage[0] for stage in pipeline.STAGES]


def project(domain: str = "digital_banking", **overrides) -> dict:
    """A project dict in the shape the pipeline uses."""
    data = {"name": "Test Project", "domain": domain, "description": "A test project.", "jurisdiction": "India",
            "scale": "medium", "budget": "", "schedule": "", "stakeholder_availability": ""}
    data.update(overrides)
    return data


def new_ctx(db, text: str, domain: str = "digital_banking", **project_fields) -> dict:
    """A fresh analysis context for `text` (exactly what the pipeline would build)."""
    return pipeline.new_context(db, project(domain, **project_fields), [{"name": "Test input", "text": text}])


def run_until(db, text: str, last_agent: str, domain: str = "digital_banking", **project_fields) -> dict:
    """Run the pipeline's agents in order up to and including `last_agent`.

    `last_agent` is a (part of an) agent name from pipeline.STAGES, e.g. "Quality" or "SDLC Selection".
    """
    ctx = new_ctx(db, text, domain, **project_fields)
    for agent, _stage, function, _critical, _needs in pipeline.STAGES:
        status, _message = function(ctx)
        ctx["agent_runs"].append({"agent": agent, "status": status, "llm_calls": []})
        if agent == "Requirement Extraction Agent":
            ctx["requirement_ids"] = {r["req_id"] for r in ctx["requirements"]}
        if last_agent in agent:
            return ctx
    raise ValueError(f"No agent called {last_agent!r}")


def run_case(db, case: dict) -> dict:
    """Run the complete pipeline on a benchmark case (as the evaluation module does)."""
    ctx = pipeline.new_context(db, dict(case["project"]),
                               [{"name": "Benchmark requirements", "text": case["requirements_text"]}])
    return pipeline.run_pipeline(ctx)


def req_by_text(ctx: dict, fragment: str) -> dict:
    """The (single) requirement whose statement contains `fragment`."""
    matches = [r for r in ctx["requirements"] if fragment.lower() in r["statement"].lower()]
    assert len(matches) == 1, f"expected one requirement containing {fragment!r}, found {len(matches)}"
    return matches[0]


def findings_of(ctx: dict, issue_type: str | None = None, kind: str | None = None) -> list[dict]:
    return [f for f in ctx["findings"]
            if (issue_type is None or f["issue_type"] == issue_type) and (kind is None or f["kind"] == kind)]


def make_pdf(text: str) -> bytes:
    """A real one-page PDF built in memory."""
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    return pdf.tobytes()


def make_docx(*paragraphs: str, table_rows: list[list[str]] | None = None) -> bytes:
    """A real Word document built in memory with python-docx (paragraphs + an optional table)."""
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table_rows:
        table = document.add_table(rows=0, cols=len(table_rows[0]))
        for row in table_rows:
            for cell, value in zip(table.add_row().cells, row):
                cell.text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
