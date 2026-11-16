from app.extraction import RuleExtractor
from app.chatbot.service import answer
from app.db import DB
from app.ingestion import ingest_text


def test_code_fragments_are_not_extracted_as_open_questions():
    source = '''unknown = [str(c) for c in self.model.classes_ if str(c) not in ACTION_LABELS]
print(f"WARNING: model classes {unknown} are not game actions {list(ACTION_LABELS)}")
"UNKNOWN ENTITY HAS ENTERED THE CORE"
"UNKNOWN ENTITY CONNECTED..."
if unknown:
Open question: Why is the action mapping validated before gameplay?
'''
    chunk = {"text": source, "meta": {}}
    claims = RuleExtractor().extract(chunk)
    questions = [c for c in claims if c["kind"] == "question"]
    assert len(questions) == 1
    assert "Why is the action mapping" in questions[0]["fields"]["text"]


def test_dataset_question_does_not_return_unrelated_readme_passages(tmp_path):
    db = DB(tmp_path / "dataset.db")
    try:
        db.add_document(ingest_text("README.md", "The project uses MediaPipe, OpenCV, Random Forest and PPO for a fighting game.", "github"))
        result = answer(db, "Which dataset has this project used?")
        assert result["verification"] == "MISSING"
        assert result["answer_mode"] == "insufficient-dataset-evidence"
        assert "does not name the datasets" in result["answer"]
    finally:
        db.close()


def test_project_overview_prefers_readme_and_excludes_raw_code(tmp_path, monkeypatch):
    db = DB(tmp_path / "overview.db")
    try:
        db.add_document(ingest_text("README.md", "# AI Superpowers\n\nAn interactive superhero fighting game controlled through webcam hand gestures. It combines computer vision, supervised gesture recognition and a PPO-based opponent.", "github"))
        db.add_document(ingest_text("game.py", "class CyberpunkGame:\\n    def __init__(self):\\n        pygame.init()\\n        self.music_loaded = False", "github"))
        monkeypatch.delenv("HF_TOKEN", raising=False)
        result = answer(db, "Summarize the project")
        assert result["answer_mode"] == "readable-evidence-overview"
        assert "AI Superpowers" in result["answer"] or "interactive superhero" in result["answer"]
        assert "pygame.init" not in result["answer"]
        assert "## Project overview" in result["answer"]
    finally:
        db.close()


def test_technical_stack_lines_create_resource_entities():
    claims = RuleExtractor().extract({
        "text": "The game uses MediaPipe for hand tracking.\nThe opponent uses PPO-based reinforcement learning.",
        "meta": {},
    })
    resources = [c["fields"].get("name") for c in claims if c["kind"] == "resource"]
    assert "MediaPipe" in resources
    assert "PPO" in resources


def test_project_overview_strips_readme_badges_and_html(tmp_path):
    from app.chatbot.service import _overview_evidence
    db = DB(tmp_path / "readme_markup.db")
    try:
        doc = ingest_text("README.md", "# Project\n\n[![Python](https://img.shields.io/badge/Python-blue)](https://python.org)\n\n<p align=\"center\"><img src=\"badge.svg\"></p>\n\nAI Superpowers is a gesture-controlled fighting game combining computer vision and reinforcement learning.", "github")
        db.add_document(doc)
        evidence = _overview_evidence(db, db.chunks())
        rendered = " ".join(x["text"] for x in evidence)
        assert "AI Superpowers is a gesture-controlled fighting game" in rendered
        assert "img.shields.io" not in rendered
        assert "<p" not in rendered
        assert "badge.svg" not in rendered
    finally:
        db.close()
