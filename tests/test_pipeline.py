import json, re
import networkx as nx
import pytest
from fastapi.testclient import TestClient
from app.extraction import RuleExtractor
from app.evidence import is_conflict
from app.graph import export, knowledge_nodes
from app.ingestion import IngestError, ingest_file
from tests.conftest import SAMPLE

# ---- ingestion / text extraction
def test_all_sample_formats_ingest_with_ids_and_metadata():
    for f in ("meeting_notes.md", "task_allocation.csv", "technical_decision.docx", "progress_report.pdf"):
        r = ingest_file(SAMPLE / f); ids = [c["id"] for c in r["chunks"]]
        assert r["doc"]["id"].startswith("doc-") and len(set(ids)) == len(ids) and r["doc"]["doc_date"]
        assert all(c["id"].startswith(r["doc"]["id"]) and c["text"].strip() for c in r["chunks"])
    assert ingest_file(SAMPLE / "progress_report.pdf")["chunks"][0]["page"] == 1
    assert ingest_file(SAMPLE / "meeting_notes.md")["chunks"][0]["section"] == "Sprint Planning Meeting Notes"
    assert ingest_file(SAMPLE / "task_allocation.csv")["chunks"][0]["meta"]["row"]["task_id"] == "T-101"

def test_txt_and_long_document_chunking(tmp_path):
    p = tmp_path / "a.txt"; p.write_text("\n".join(f"Line number {i} " + "x" * 80 for i in range(40)))
    r = ingest_file(p); assert len(r["chunks"]) > 1 and all(len(c["text"]) <= 1000 for c in r["chunks"])

def test_bad_files_handled_gracefully(tmp_path):
    for name, data in (("e.txt", b"  \n"), ("x.xyz", b"hi"), ("bad.pdf", b"not a pdf"), ("bad.docx", b"not a docx"), ("e.csv", b"a,b\n")):
        (tmp_path / name).write_bytes(data)
        with pytest.raises(IngestError): ingest_file(tmp_path / name)

# ---- extraction
def test_rule_extractor_unit_and_no_invention():
    c = {"text": "Action T-9: Write docs (owner: Jane Doe) due 2026-01-01\nDecision: Use Redis because it is fast.", "meta": None}
    k = {x["kind"]: x for x in RuleExtractor().extract(c)}
    assert k["task"]["fields"] == {"ref": "T-9", "title": "Write docs", "owner": "Jane Doe", "due": "2026-01-01"}
    assert k["decision"]["fields"]["rationale"] == "it is fast"
    assert RuleExtractor().extract({"text": "Lorem ipsum dolor sit amet.", "meta": None}) == []

def test_entities_extracted_from_documents(kb):
    names = {(d["type"], d["name"]) for d in knowledge_nodes(kb["graph"])}
    assert ("person", "Rohan Mehta") in names and ("milestone", "Beta release") in names
    types = {t for t, _ in names}; assert {"person", "task", "decision", "risk", "question", "milestone", "resource"} <= types
    assert kb["graph"].nodes["person:rohan-mehta"]["attrs"]["role"] == "Backend Developer"

# ---- graph
def test_graph_creation_relationships_and_exports(kb, tmp_path):
    G = kb["graph"]; kinds = {k for _, _, k in G.edges(keys=True)}
    assert {"ASSIGNED_TO", "DEPENDS_ON", "BLOCKS", "DECIDED_BY", "SUPPORTED_BY", "RELATED_TO", "SUPERSEDES"} - kinds <= {"RELATED_TO"}
    assert G.has_edge("task:t-102", "task:t-101", "DEPENDS_ON") and G.has_edge("task:t-103", "task:t-104", "BLOCKS")
    assert G.has_edge("decision:adopt-xgboost-for-energy-forecasting-instead-of-the-lstm", "decision:use-an-lstm-model-for-energy-forecasting-experiments", "SUPERSEDES") or any(k == "SUPERSEDES" for *_, k in G.edges(keys=True))
    out = export(G, tmp_path); assert json.load(open(out["json"]))["stats"]["knowledge_nodes"] > 10
    assert nx.read_graphml(out["graphml"]).number_of_nodes() == G.number_of_nodes()

# ---- evidence traceability
def test_every_fact_traces_to_source_text(kb):
    G, db = kb["graph"], kb["db"]; chunks = {c["id"]: c["text"] for c in db.chunks()}; docs = {d["id"] for d in db.documents()}
    items = [d for d in knowledge_nodes(G)] + [d for *_, d in G.edges(data=True) if d["type"] != "SUPPORTED_BY"]
    for d in items:
        assert d["evidence"], d
        for e in d["evidence"]:
            assert e["doc_id"] in docs and e["chunk_id"] in chunks
            assert all(l in chunks[e["chunk_id"]] for l in e["text"].splitlines()), (d["id"], e["text"])

# ---- verification labels / conflicts
def test_conflict_detected_not_silently_resolved(kb):
    t = kb["graph"].nodes["task:t-103"]; c = t["conflicts"][0]
    assert t["status"] == "CONFLICTING" and c["attribute"] == "status"
    assert {o["value"] for o in c["observations"]} == {"done", "in_progress"} and len({o["document"] for o in c["observations"]}) >= 2
    assert "resolved" not in t["attrs"]

def test_progression_is_not_conflict_but_regression_is(kb):
    assert kb["graph"].nodes["task:t-101"]["attr_status"]["status"] == "VERIFIED"       # in progress -> completed weeks later
    ev = lambda v, ts: {"value": v, "evidence": {"timestamp": ts}}
    assert is_conflict("status", [ev("done", "2026-10-01"), ev("in_progress", "2026-10-20")])
    assert not is_conflict("status", [ev("in_progress", "2026-10-01"), ev("done", "2026-10-20")])

def test_missing_and_stale_labels(kb):
    G = kb["graph"]
    assert G.nodes["task:t-105"]["attr_status"]["owner"] == "MISSING" and G.nodes["task:t-106"]["attr_status"]["owner"] == "MISSING"
    assert G.nodes["task:t-106"]["status"] == "STALE"
    assert any(d["status"] == "STALE" and "superseded" in d["stale_reasons"][0] for d in knowledge_nodes(G) if d["type"] == "decision")

# ---- knowledge debt
def test_missing_owner_and_rationale_detection(kb):
    iss = kb["report"]["issues"]
    owners = {i["related_entity"]["id"]: i for i in iss if i["category"] == "Missing owner"}
    assert set(owners) == {"task:t-105", "task:t-106"} and owners["task:t-105"]["severity"] == "High"
    rat = [i["related_entity"]["name"] for i in iss if i["category"] == "Missing rationale"]
    assert len(rat) == 1 and "MQTT" in rat[0]

def test_debt_report_structure_and_categories(kb):
    rep = kb["report"]; need = {"title", "category", "description", "severity", "related_entity", "supporting_evidence", "recommended_verification"}
    assert all(need <= set(i) and i["severity"] in ("High", "Medium", "Low") for i in rep["issues"])
    cats = set(rep["summary"]["by_category"])
    assert {"Missing owner", "Missing rationale", "Dependency gap", "Missing mitigation", "Unresolved question", "Conflict", "Outdated information"} <= cats
    assert rep["summary"]["total"] == len(rep["issues"])

# ---- API
def test_api_end_to_end():
    from app.main import app
    c = TestClient(app); assert c.get("/health").json()["status"] == "ok"
    assert c.post("/knowledge/extract").status_code == 409
    for f in ("meeting_notes.md", "task_allocation.csv", "technical_decision.docx", "progress_report.pdf"):
        assert c.post("/documents/upload", files={"file": (f, (SAMPLE / f).read_bytes())}).status_code == 200
    assert c.post("/documents/upload", files={"file": ("bad.pdf", b"nope")}).status_code == 422
    assert len(c.get("/documents").json()["documents"]) == 4
    assert c.post("/knowledge/extract").json()["claims_extracted"] > 20
    assert c.get("/knowledge/entities", params={"type": "task"}).json()["count"] >= 6
    g = c.get("/knowledge/graph", params={"export_files": True}).json(); assert g["stats"]["edges"] > 0 and "graphml" in g["exported"]
    e = c.get("/knowledge/evidence", params={"entity_id": "task:t-103"}).json(); assert e["entity"]["status"] == "CONFLICTING" and e["evidence"]
    assert c.get("/knowledge/evidence", params={"q": "MQTT"}).json()["matches"]
    assert c.get("/knowledge/debt-report").json()["summary"]["total"] > 5
