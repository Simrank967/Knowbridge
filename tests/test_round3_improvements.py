from app.extraction import _validated_llm_claims
from app.search import search


def test_llm_claim_validation_requires_verbatim_evidence():
    source = "Action T-9: Write docs (owner: Jane Doe)"
    data = [
        {"kind": "task", "fields": {"ref": "T-9", "title": "Write docs"},
         "evidence": "Action T-9: Write docs (owner: Jane Doe)", "certainty": "explicit"},
        {"kind": "task", "fields": {"ref": "T-10"}, "evidence": "T-10 is assigned to Alex", "certainty": "explicit"},
        {"kind": "unknown", "fields": {"name": "x"}, "evidence": source, "certainty": "explicit"},
        {"kind": "risk", "fields": ["bad"], "evidence": source, "certainty": "explicit"},
    ]
    claims = _validated_llm_claims(data, source)
    assert len(claims) == 1
    assert claims[0]["kind"] == "task"
    assert claims[0]["evidence"] == source


def test_hybrid_keyword_search_is_relevant_and_rejects_unrelated_queries():
    chunks = [
        {"id": "a", "doc_id": "d1", "section": "Budget", "page": None,
         "text": "Who approves the cloud hosting budget?"},
        {"id": "b", "doc_id": "d1", "section": "Models", "page": None,
         "text": "The forecasting model uses an LSTM architecture."},
    ]
    hits, method = search(chunks, "cloud hosting budget approval", 2)
    assert hits and hits[0]["chunk_id"] == "a"
    assert method.startswith("hybrid")
    assert search(chunks, "quantum chromodynamics lattice", 2)[0] == []



def test_audio_question_returns_asset_inventory_not_raw_code():
    from app.chatbot.service import _deterministic_audio_answer
    chunks = [
        {"id": "a", "doc_id": "d1", "text": 'self.main_music_path = os.path.join(project_dir, "Raftaarein Ra One 128 Kbps.mp3")'},
        {"id": "b", "doc_id": "d1", "text": 'self.bonus_music_path = os.path.join(project_dir, "Ra-One-Humming-Ringtone.mp3")'},
        {"id": "c", "doc_id": "d1", "text": 'assets/raone_voice/ and assets/result_audio/ are used for voice and result sounds'},
        {"id": "d", "doc_id": "d1", "text": 'pygame.mixer.music.load(MUSIC_PATH)'},
    ]
    answer = _deterministic_audio_answer("What audios has this project used?", chunks, [])
    assert "Raftaarein Ra One 128 Kbps.mp3" in answer
    assert "Ra-One-Humming-Ringtone.mp3" in answer
    assert "assets/raone_voice/" in answer
    assert "Pygame mixer" in answer
    assert "def " not in answer


def test_semantic_search_rejects_unrelated_low_similarity(monkeypatch):
    import app.search as search_module
    chunks = [
        {"id": "a", "doc_id": "d1", "section": "Budget", "page": None,
         "text": "Who approves the cloud hosting budget?"},
        {"id": "b", "doc_id": "d1", "section": "Models", "page": None,
         "text": "The forecasting model uses an LSTM architecture."},
    ]
    monkeypatch.setenv("KNOWBRIDGE_EMBEDDINGS", "1")
    monkeypatch.setattr(search_module, "_embed", lambda _chunks, _q: [0.12, 0.08])
    hits, method = search_module.search(chunks, "quantum chromodynamics lattice", 2)
    assert hits == []
    assert method == "hybrid"


def test_semantic_search_can_retrieve_strong_semantic_match(monkeypatch):
    import app.search as search_module
    chunks = [
        {"id": "a", "doc_id": "d1", "section": "Budget", "page": None,
         "text": "Who approves the cloud hosting budget?"},
        {"id": "b", "doc_id": "d1", "section": "Models", "page": None,
         "text": "The forecasting model uses an LSTM architecture."},
    ]
    monkeypatch.setenv("KNOWBRIDGE_EMBEDDINGS", "1")
    monkeypatch.setattr(search_module, "_embed", lambda _chunks, _q: [0.82, 0.11])
    hits, method = search_module.search(chunks, "who controls infrastructure spending", 2)
    assert hits and hits[0]["chunk_id"] == "a"
    assert method == "hybrid"
