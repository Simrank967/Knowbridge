"""Project isolation, false-question filtering, role relevance, perspectives, follow-ups, streaming upload, large/nested repos."""
import io, json, zipfile
from types import SimpleNamespace
from fastapi.testclient import TestClient
from test_repo_intelligence import repo, FILES, _zip  # fixtures + synthetic repo
from app import sources, repo_intel, repo_roles
from app.extraction import RuleExtractor
from app.ingestion import ingest_text
from app.pipeline import extract_all
from app.chatbot.service import answer
from app.handover import generate_payload

DEMO = "Action item T-9: Configure MQTT broker for the PostgreSQL energy forecasting demo (owner: Dana)\nOpen question: which PostgreSQL version do we use for forecasting?\n"

def _req(role="ML Engineer", perspective="incoming"):
    return SimpleNamespace(project_id="d", recipient="Sam", role=role, perspective=perspective, role_description="", responsibilities="", repository=None)

def _setup(repo):
    db, scan, _ = repo; scan()
    db.add_document(ingest_text("energy_demo.md", DEMO, "upload", "2026-01-01", {}))                                  # unassociated upload
    db.add_document(ingest_text("github:p/other · notes.md", DEMO, "github", "2026-01-01", {"repository": "p/other"}))  # other repository
    extract_all(db); return db

def test_handover_and_chat_exclude_other_projects(repo):
    db = _setup(repo)
    for persp in ("incoming", "departing", "administrator"):
        blob = json.dumps(generate_payload(db, _req(perspective=persp)), default=str).lower()
        assert not any(w in blob for w in ("mqtt", "postgresql", "forecasting", "dana")), persp
    leak = answer(db, "How do we configure the MQTT broker for PostgreSQL energy forecasting?")
    assert not any(w in json.dumps(leak, default=str).lower() for w in ("energy_demo", "p/other", "mqtt broker for the postgresql"))
    assert all(d["filename"].startswith("github:o/arena") for d in repo_intel.scope(db).documents())

def test_explicit_association_is_included(repo):
    db, scan, _ = repo; scan()
    db.add_document(ingest_text("arena_notes.md", "# Notes\nThe arena renderer uses pygame sprites.\n", "upload", "2026-01-01", {"repository": "o/arena", "associated": True}))
    assert "arena_notes.md" in [d["filename"] for d in repo_intel.scope(db).documents()]

def test_code_fragments_are_not_questions(repo):
    for frag in ('UNKNOWN = 0', 'data.get("name", "unknown")', 'x: int = 5', 'Traceback (most recent call last):', '  File "a.py", line 3, in f', 'name = row["unknown"]', 'timeout: int = 30  # unknown?'):
        assert not [c for c in RuleExtractor().extract({"text": frag, "meta": {}}) if c["kind"] == "question"], frag
    assert [c for c in RuleExtractor().extract({"text": "Open question: who owns deployment?", "meta": {}}) if c["kind"] == "question"]
    db = _setup(repo); code_docs = {d["id"] for d in db.documents() if d["source_type"] == "github_code"}
    assert code_docs and not [c for c in db.claims() if c["doc_id"] in code_docs]

def test_backend_does_not_get_ppo_but_ai_does(repo):
    db, scan, _ = repo; scan(); M = repo_intel.build_model(db)
    be, ai, ml = (repo_roles.role_view(M, r) for r in ("Backend Developer", "AI/GenAI Engineer", "ML Engineer"))
    assert "backend/ai.py" not in be["files"] and "backend/ai.py" in ai["files"] and "backend/game.py" in be["files"]
    assert "ml/train_gesture.py" in ml["files"] and "ml/train_gesture.py" not in be["files"]

def test_recommendations_deduplicated_and_evidence_based(repo):
    db, scan, _ = repo; scan(); M = repo_intel.build_model(db)
    for r in ("QA Engineer", "Technical Lead", "Backend Developer"):
        s = repo_roles.role_view(M, r)["suggestions"]; keys = [(x["related_component"], x["title"]) for x in s]
        assert len(keys) == len(set(keys)) and len([x for x in s if x["title"].startswith("Add tests")]) <= len({x["related_component"] for x in s})
        assert all(x["status"] == "SUGGESTED_NOT_CONFIRMED" and x["evidence"] for x in s)

def test_perspectives_differ_and_never_invent_owners(repo):
    db = _setup(repo); out = {p: generate_payload(db, _req(perspective=p)) for p in ("incoming", "departing", "administrator")}
    titles = {p: [s["title"] for s in v["sections"]] for p, v in out.items()}
    assert len({tuple(t) for t in titles.values()}) == 3
    assert any("Handover instructions" in t for t in titles["departing"]) and any("Component ownership" in t for t in titles["administrator"]) and any("execution flow" in t for t in titles["incoming"])
    assert all(i["evidence"] or i["verification"] in ("MISSING", "INFERRED", "UNVERIFIED", "MANIFEST_DERIVED") for v in out.values() for s in v["sections"] for i in s["items"])
    assert "dana" not in json.dumps(out).lower() and all(t["status"] != "CONFIRMED" for v in out.values() for t in v["suggested_tasks"])

def test_followup_uses_conversation_context(repo):
    db, scan, _ = repo; scan(); q1 = "Explain how the game makes decisions"
    a = answer(db, "and what about its fallback?", role="Backend Developer", history=[{"role": "user", "content": q1}])
    assert a.get("evidence") and "backend/" in a["answer"]
    assert answer(db, "Is there anything about the weather forecast?", history=[]).get("evidence") in (None, [])

def test_large_and_nested_files_reported(repo, monkeypatch):
    db, scan, state = repo; monkeypatch.setattr(sources, "MAX_FILE_BYTES", 300)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("r-main/a/b/c/deep.py", "def deep():\n    return 1\n\ndef deeper():\n    return 2\n"); z.writestr("r-main/big.py", "x = 1\n" * 200); z.writestr("r-main/big/model.bin", b"\0" * 5000)
    state["zip"] = b.getvalue(); scan(); m = repo_intel.manifest(db); by = {f["path"]: f for f in m["files"]}
    assert by["a/b/c/deep.py"]["status"] == "INDEXED" and by["big.py"]["status"] == "METADATA_ONLY" and "oversized" in by["big.py"]["reason"]
    assert by["big/model.bin"]["status"] == "METADATA_ONLY" and m["stats"]["discovered"] == 3 and m["stats"]["indexed"] == 1

def test_streaming_upload_limit_and_cleanup(monkeypatch, tmp_path):
    from app.main import app; import app.routers.documents as D
    from app.deps import get_db; from app.db import DB
    udb = DB(tmp_path / "u.db"); app.dependency_overrides[get_db] = lambda: udb  # isolated DB: never pollute the shared app database
    monkeypatch.setattr(D, "MAX_UPLOAD_MB", 1); monkeypatch.setattr(D, "DATA", tmp_path); c = TestClient(app)
    assert c.post("/documents/upload", files={"file": ("big.txt", b"a" * (2 * 1024 * 1024))}).status_code == 413
    assert not list((tmp_path / "uploads").glob("*.part")) and not (tmp_path / "uploads" / "big.txt").exists()
    assert c.post("/documents/upload?repository=o/arena", files={"file": ("ok.txt", b"hello world notes about arena\n")}).status_code == 200
    app.dependency_overrides.pop(get_db, None); assert udb.counts()["documents"] == 1
