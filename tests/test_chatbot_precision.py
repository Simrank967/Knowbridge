from app.chatbot.service import _expanded_query, _grounded_llm


def test_short_rl_query_expands_to_algorithm_terms():
    expanded = _expanded_query("what RL algorithm does this project use?")
    assert "reinforcement learning" in expanded
    assert "PPO" in expanded
    assert "DQN" in expanded


def test_grounded_llm_is_optional_without_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)
    assert _grounded_llm("What algorithm is used?", [{"text": "PPO"}]) is None


def test_rl_algorithm_answer_is_synthesised_without_llm():
    from app.chatbot.service import _deterministic_technical_answer
    evidence = [{"source": "README.md", "text": "PPO-based reinforcement learning. Stable-Baselines3 integration. Hybrid AI combining PPO decisions and tactical combat priorities. Scripted fallback when a trained checkpoint is unavailable."}]
    answer = _deterministic_technical_answer("what rl algorithm does this project use", evidence)
    assert "Proximal Policy Optimization (PPO)" in answer
    assert "Stable-Baselines3" in answer
    assert "Random Forest" in answer
    assert "README.md" in answer
    assert "not the reinforcement-learning algorithm" in answer
