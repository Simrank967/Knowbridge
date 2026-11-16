"""API-layer tests: OpenAPI quality, demo loader, filters, summary, error handling."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


@pytest.fixture(autouse=True)
def _demo():
    assert c.post("/demo/load").status_code == 200


def test_openapi_is_documented_and_typed():
    spec = c.get("/openapi.json").json()
    assert spec["info"]["title"].startswith("KNOWBRIDGE") and len(spec["info"]["description"]) > 500
    assert {t["name"] for t in spec["tags"]} >= {"1 · Documents", "5 · Knowledge Debt", "Demo & Operations"}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("summary") and op.get("tags"), f"{method} {path} lacks summary/tags"
    assert "DebtReport" in spec["components"]["schemas"] and "GraphResponse" in spec["components"]["schemas"]


def test_docs_page_is_branded_and_root_redirects():
    page = c.get("/docs"); assert page.status_code == 200 and "KNOWBRIDGE" in page.text and "swagger-ui" in page.text
    root = c.get("/", follow_redirects=False)
    assert root.status_code == 200 and "One conversation" in root.text and "KNOWBRIDGE" in root.text


def test_demo_load_runs_pipeline_and_finds_planted_issues():
    r = c.post("/demo/load").json()
    assert len(r["documents"]) == 4 and r["errors"] == [] and r["extraction"]["claims_extracted"] > 20
    cats = r["summary"]["knowledge_debt"]["by_category"]
    for needed in ("Missing owner", "Missing rationale", "Conflict", "Unresolved question"):
        assert cats.get(needed, 0) >= 1, needed


def test_health_reports_loaded_state_and_process_time_header():
    r = c.get("/health"); h = r.json()
    assert h["documents_loaded"] == 4 and h["version"] and "X-Process-Time-ms" in r.headers


def test_entity_filters_and_enum_validation():
    assert all(e["type"] == "task" for e in c.get("/knowledge/entities", params={"type": "task"}).json()["entities"])
    conf = c.get("/knowledge/entities", params={"status": "CONFLICTING"}).json()
    assert conf["count"] >= 1 and all(e["status"] == "CONFLICTING" for e in conf["entities"])
    assert c.get("/knowledge/entities", params={"type": "banana"}).status_code == 422


def test_debt_report_filters_recompute_summary():
    full = c.get("/knowledge/debt-report").json()
    high = c.get("/knowledge/debt-report", params={"severity": "High"}).json()
    assert 0 < high["summary"]["total"] < full["summary"]["total"] and all(i["severity"] == "High" for i in high["issues"])
    assert high["filters_applied"] == {"severity": "High"}
    own = c.get("/knowledge/debt-report", params={"category": "missing owner"}).json()
    assert own["summary"]["total"] >= 2


def test_evidence_modes_and_404():
    assert c.get("/knowledge/evidence", params={"entity_id": "nope"}).status_code == 404
    assert c.get("/knowledge/evidence").json()["count"] > 10
    e = c.get("/knowledge/evidence", params={"entity_id": "task:t-103"}).json()
    assert e["evidence"] and all(x["text"] and x["doc_name"] and x["chunk_id"] for x in e["evidence"])


def test_summary_and_graph_download():
    s = c.get("/knowledge/summary").json(); assert s["entities_by_type"]["task"] >= 6 and "SUPPORTED_BY" not in s["relationships_by_type"]
    g = c.get("/knowledge/graph/download", params={"format": "graphml"}); assert g.status_code == 200 and b"<graphml" in g.content
    assert c.get("/knowledge/graph/download", params={"format": "json"}).json()["nodes"]


def test_upload_validation_and_reset():
    assert c.post("/documents/upload", files={"file": ("virus.exe", b"MZ")}).status_code == 422
    assert c.post("/documents/upload", files={"file": ("e.txt", b"  ")}).status_code == 422
    r = c.delete("/demo/reset").json(); assert r["documents_removed"] == 4
    assert c.get("/documents").json()["count"] == 0 and c.post("/knowledge/extract").status_code == 409


def test_new_endpoints_end_to_end():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app); assert c.post("/demo/load").status_code == 200
    md = c.get("/knowledge/debt-report?format=markdown"); assert md.status_code == 200 and "Knowledge Debt Report" in md.text
    b = c.get("/knowledge/brief").json(); assert b["generated_by"] == "template" and b["readiness"]["verdict"]
    assert c.get("/knowledge/summary?format=markdown").status_code == 200
    t = c.get("/knowledge/trace/task:t-105").json(); assert t["trail"] and t["trail"][0]["verbatim_in_source"]
    assert c.get("/knowledge/trace/nope").status_code == 404
    assert c.get("/knowledge/ask", params={"q": "cloud hosting budget approvals"}).json()["count"] >= 1
    assert c.post("/sources/connect", json={"urls": ["http://127.0.0.1:1/x"]}).status_code == 422
    r = c.post("/projects/ingest", files=[("files", ("n.md", b"Date: 2026-10-01\nAction T-9: Do it (owner: Ann Lee)\n", "text/markdown"))], data={"urls": ""})
    assert r.status_code == 200 and r.json()["extraction"]["claims_extracted"] >= 1
    c.delete("/demo/reset")   # leave a clean DB: test_pipeline expects an empty store (409 on extract)
