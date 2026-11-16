"""Synthetic-repository regression tests: recursive discovery, manifest accounting, structure, role-aware answers/handover, incremental scan."""
import io, struct, wave, zipfile
import pytest
from app import sources, repo_intel
from app.db import DB
from app.chatbot.service import answer

def _wav():
    b = io.BytesIO()
    with wave.open(b, "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * 800)
    return b.getvalue()

FILES = {
    "README.md": "# Arena\nA gesture-controlled fighting game.\n\n## Features\n- Gesture recognition with a Random Forest\n- PPO opponent\n",
    "backend/game.py": "from backend.ai import choose_action\nimport pygame\n\nclass Game:\n    def step(self, g):\n        try:\n            return choose_action(g)\n        except Exception:\n            return 'idle'  # fallback\n",
    "backend/ai.py": "import os\nfrom stable_baselines3 import PPO\n\ndef load_policy(p='models/ppo.zip'):\n    if not os.path.exists(p):\n        return None\n    return PPO.load(p)\n\ndef choose_action(obs):\n    policy = load_policy()\n    return policy.predict(obs)[0] if policy else 'idle'\n",
    "ml/train_gesture.py": "import pandas as pd\nfrom sklearn.ensemble import RandomForestClassifier\n\ndef train():\n    df = pd.read_csv('data/gestures.csv')\n    clf = RandomForestClassifier(n_estimators=50)\n    return clf.fit(df.drop(columns=['label']), df['label'])\n",
    "ui/render.py": "import pygame\n\ndef draw_character(screen, sprite):\n    screen.blit(sprite, (0, 0))\n\ndef animate_hit(screen):\n    pass\n",
    "data/gestures.csv": "a,b,label\n1,2,punch\n3,4,kick\n",
    "tests/test_game.py": "from backend.game import Game\n\ndef test_step():\n    assert Game().step(None)\n",
    "requirements.txt": "pygame\nstable-baselines3\nscikit-learn\npandas\n",
    "assets/hit.wav": _wav(), "assets/logo.png": b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 4, 4) + b"\0" * 20,
    "node_modules/x/index.js": "x", "broken/bad.py": "def (:\n",
}
def _zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for p, d in FILES.items(): z.writestr("arena-main/" + p, d)
    return b.getvalue()

@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "DATA", tmp_path)
    monkeypatch.setattr(sources, "_gh", lambda p: {"default_branch": "main", "pushed_at": "2026-01-01T00:00:00Z", "sha": "a" * 40} if p.count("/") <= 3 or "commits" in p else [])
    state = {"zip": _zip()}
    monkeypatch.setattr(sources, "_http_get_archive", lambda u, h=None: state["zip"])
    db = DB(tmp_path / "t.db")
    def scan():
        ok, errs, notes = sources.connect(["https://github.com/o/arena"], db)
        for r in ok: db.add_document(r)
        for s in list(sources.LAST_SCANS): repo_intel.persist_scan(db, s["repo"], s["branch"], s["commit"], s["source"], s["truncated"], s["stats"], s["rows"], s["notes"])
        return ok, notes
    return db, scan, state

def test_manifest_accounting(repo):
    db, scan, _ = repo; scan(); m = repo_intel.manifest(db); st = m["stats"]; by = {f["path"]: f for f in m["files"]}
    assert st["discovered"] == len(FILES) and st["discovered"] == len(m["files"])
    assert by["node_modules/x/index.js"]["status"] == "EXCLUDED" and by["assets/hit.wav"]["status"] == "METADATA_ONLY" and by["assets/hit.wav"]["meta"]["sample_rate"] == 8000
    assert by["backend/ai.py"]["status"] == "INDEXED" and repo_intel.prior_structure(db, "o/arena", "backend/ai.py")["functions"]
    assert by["broken/bad.py"]["indexed"] == 1 and "structure unavailable" in by["broken/bad.py"]["reason"]
    assert st["metadata_only"] == 2 and st["failed"] == 0 and m["commit"] == "a" * 40

def test_incremental_reuse(repo):
    db, scan, state = repo; scan(); n = db.counts()["documents"]
    _, notes = scan(); assert "unchanged, reused" in notes[0] and db.counts()["documents"] == n

def test_role_answers_differ_but_cite_source(repo):
    db, scan, _ = repo; scan(); q = "Explain how the game makes decisions"
    be, ml, fe = (answer(db, q, role=r) for r in ("Backend Developer", "ML Engineer", "Frontend Developer"))
    assert all("backend/ai.py" in a["answer"] for a in (be, ml)) and be["answer"] != ml["answer"] != fe["answer"]
    assert be["evidence"] and be["role_lens"] == "Backend Developer"
    assert "hit.wav" in answer(db, "Which audio files are present?")["answer"]

def test_unrelated_query_and_dataset(repo):
    db, scan, _ = repo; scan()
    assert not answer(db, "What is the capital of Mongolia?").get("evidence")
    assert "gestures.csv" in answer(db, "What dataset was used to train the gesture model?")["answer"]

def test_handover_is_source_backed(repo):
    from types import SimpleNamespace
    from app.handover import generate_payload
    db, scan, _ = repo; scan()
    p = generate_payload(db, SimpleNamespace(project_id="d", recipient="Sam", role="ML Engineer", perspective="incoming", role_description="", responsibilities=""))
    assert p["repo_context"]["repository"]["commit"] == "a" * 40 and p["role_components"]
    assert all(s.get("status") != "CONFIRMED" for s in p["suggested_tasks"])
