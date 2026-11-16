import json
from app.ingestion import ingest_file
from app.chatbot import service
from app.db import DB


def test_json_and_source_code_are_ingested_with_content(tmp_path):
    payload = {"service": "payments", "retry_limit": 4}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    result = ingest_file(p)
    assert result["doc"]["source_type"] == "json"
    assert "retry_limit" in " ".join(c["text"] for c in result["chunks"])

    code = tmp_path / "worker.py"
    code.write_text("def process_payment(amount):\n    return amount * 2\n", encoding="utf-8")
    result = ingest_file(code)
    assert result["doc"]["source_type"] == "code"
    assert "process_payment" in " ".join(c["text"] for c in result["chunks"])


def test_answer_passes_recent_conversation_to_grounded_generator(tmp_path, monkeypatch):
    db = DB(tmp_path / "chat.db")
    from app.ingestion import ingest_text
    db.add_document(ingest_text("architecture.md", "The billing worker processes invoices using a retry queue.", "markdown"))
    seen = {}

    def fake_llm(question, evidence, history=None):
        seen["question"] = question
        seen["history"] = history
        return "The worker uses a retry queue [E1]."

    monkeypatch.setattr(service, "_grounded_llm", fake_llm)
    result = service.answer(db, "Which queue does it use?", history=[
        {"role": "user", "content": "Explain the billing worker."},
        {"role": "assistant", "content": "It processes invoices."},
    ])
    assert "retry queue" in result["answer"]
    assert seen["history"][-1]["content"] == "It processes invoices."
    assert seen["question"] == "Which queue does it use?"
    db.close()


def test_uploaded_zip_ingests_nested_supported_files(tmp_path, monkeypatch):
    import io
    import zipfile
    from app.sources import ingest_uploaded_archive
    from app.config import DATA

    monkeypatch.setattr("app.sources.DATA", tmp_path)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("src/worker.py", "def run_job():\n    return 'ok'\n")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("project/nested.zip", inner.getvalue())
        z.writestr("project/README.md", "# Project\nA worker service.")
    results, skipped = ingest_uploaded_archive(outer.getvalue(), "project.zip")
    names = [r["doc"]["filename"] for r in results]
    assert any("worker.py" in name for name in names)
    assert any("README.md" in name for name in names)
    assert skipped == 0


def test_structured_claim_retrieval_uses_source_evidence():
    from app.chatbot.service import _structured_evidence
    claims = [{"doc_id": "d1", "chunk_id": "c1", "kind": "decision",
               "fields": {"decision": "Use Redis queue"}, "evidence": "The team selected Redis for background jobs.",
               "certainty": "VERIFIED"}]
    chunks = [{"id": "c1", "doc_id": "d1", "section": "Architecture", "page": 2, "text": "Redis queue"}]
    result = _structured_evidence(claims, chunks, "Which queue was selected?")
    assert result and result[0]["chunk_id"] == "c1"
    assert "Redis" in result[0]["text"]
