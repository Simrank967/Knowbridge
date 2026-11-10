from fastapi.testclient import TestClient
from app.main import app


def test_chatbot_and_real_pdf_report(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'chat.db')
    monkeypatch.setattr(config, 'DATA', tmp_path / 'data')
    c = TestClient(app)
    page = c.get('/')
    assert page.status_code == 200 and 'One conversation' in page.text
    assert c.post('/demo/load').status_code == 200
    chat = c.post('/chat', json={'message': 'What do you know about this project?'})
    assert chat.status_code == 200
    data = chat.json()
    assert data['conversation_id'] and data['evidence']
    history = c.get('/conversations/' + data['conversation_id'])
    assert history.status_code == 200 and len(history.json()['messages']) == 2
    report = c.post('/reports/generate', json={'request': 'Generate a project knowledge audit PDF', 'conversation_id': data['conversation_id']})
    assert report.status_code == 200
    pdf = c.get(report.json()['download_url'])
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
    c.delete('/demo/reset')
