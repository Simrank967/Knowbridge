import os
from fastapi.testclient import TestClient
from app.main import app
from app.db import DB
from app.pipeline import run_folder
from app.config import SAMPLE_DIR

def test_handover_lifecycle(tmp_path,monkeypatch):
    monkeypatch.setenv('KNOWBRIDGE_DB',str(tmp_path/'test.db'))
    monkeypatch.setenv('KNOWBRIDGE_ADMIN_TOKEN','demo-secret')
    # Dependency override ensures isolated database regardless of module-level config.
    from app.deps import get_db
    def dep():
        db=DB(tmp_path/'test.db')
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=dep
    try:
        db=DB(tmp_path/'test.db');run_folder(db,SAMPLE_DIR);db.close()
        c=TestClient(app)
        r=c.post('/handover/generate',json={'recipient':'New engineer','role':'Backend Developer'});assert r.status_code==200,r.text
        h=r.json();hid=h['id'];assert h['status']=='DRAFT';assert h['payload']['sections']
        assert c.post(f'/handover/{hid}/approve',json={}).status_code==403
        assert c.post(f'/handover/{hid}/share',headers={'X-Admin-Token':'demo-secret'},json={}).status_code==409
        headers={'X-Admin-Token':'demo-secret'}
        assert c.post(f'/handover/{hid}/submit',headers=headers,json={}).json()['status']=='PENDING_REVIEW'
        assert c.post(f'/handover/{hid}/approve',headers=headers,json={}).json()['status']=='APPROVED'
        assert c.post(f'/handover/{hid}/share',headers=headers,json={}).json()['status']=='SHARED'
        assert c.get(f'/handover/{hid}/download').content[:4]==b'%PDF'
        assert len(c.get(f'/handover/{hid}/history',headers=headers).json())==4
    finally: app.dependency_overrides.clear()

def test_dynamic_roles_and_custom_role(tmp_path,monkeypatch):
    monkeypatch.setenv('KNOWBRIDGE_DB',str(tmp_path/'roles.db'))
    from app.deps import get_db
    def dep():
        db=DB(tmp_path/'roles.db')
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=dep
    try:
        db=DB(tmp_path/'roles.db');run_folder(db,SAMPLE_DIR);db.close()
        c=TestClient(app)
        available=c.get('/handover/roles').json()['roles']
        assert 'Backend Developer' in available
        assert 'Other / Custom Role' in available
        assert len(available) >= 20
        generated=[]
        for role,extra in [
            ('Backend Developer',{}),('QA Engineer / Tester',{}),
            ('UI/UX Designer',{}),('DevOps Engineer',{}),
            ('Data Scientist',{}),('Project Manager',{}),
            ('Robotics Integration Engineer',{'role_description':'Integrates sensors and campus gate hardware','responsibilities':'Sensor integration, device connectivity'})
        ]:
            r=c.post('/handover/generate',json={'recipient':'Incoming member','role':role,**extra})
            assert r.status_code==200,r.text
            payload=r.json()['payload']
            assert payload['role']==role
            assert isinstance(payload['sections'],list)
            generated.append(payload)
        # Ensure role matching actually changes content for at least several profiles.
        signatures={tuple((s['title'],tuple(i['title'] for i in s['items'])) for s in p['sections']) for p in generated}
        assert len(signatures)>=3
        assert generated[-1]['role_description']=='Integrates sensors and campus gate hardware'
    finally: app.dependency_overrides.clear()

def test_role_specific_questions_and_answer_status(tmp_path,monkeypatch):
    monkeypatch.setenv('KNOWBRIDGE_DB',str(tmp_path/'yolo.db'))
    from app.deps import get_db
    from app.db import DB
    from app.pipeline import run_folder
    source_dir=tmp_path/'yolo_sources'; source_dir.mkdir()
    (source_dir/'technical_notes.md').write_text(
        '# Model design notes\n\n'
        'Open question: Why YOLOv8s?\n'
        'Open question: Why CBAM?\n'
        'The project uses YOLOv8s with CBAM attention.\n', encoding='utf-8')
    def dep():
        db=DB(tmp_path/'yolo.db')
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=dep
    try:
        db=DB(tmp_path/'yolo.db'); run_folder(db,source_dir); db.close()
        c=TestClient(app)
        outputs={}
        for role in ('Backend Developer','Project Lead','QA Engineer / Tester'):
            r=c.post('/handover/generate',json={'recipient':'Incoming member','role':role})
            assert r.status_code==200,r.text
            outputs[role]=r.json()['payload']
        def questions(payload):
            return [i for section in payload['sections'] for i in section['items'] if i.get('state')=='OPEN']
        for payload in outputs.values():
            qs=questions(payload)
            assert qs, payload
            assert all(q['verification']=='MISSING' for q in qs)
            assert all(q['question_status']=='VERIFIED' and q['answer_status']=='MISSING' for q in qs)
            assert all(q['evidence'] for q in qs)
        action_titles={role:[a['title'] for a in payload['actions']] for role,payload in outputs.items()}
        assert len({tuple(v) for v in action_titles.values()}) >= 2
        assert all(a['action_type']=='SUGGESTED_CLARIFICATION' for p in outputs.values() for a in p['actions'])
    finally: app.dependency_overrides.clear()


def test_role_task_mapping_is_specific_and_explainable():
    from app.handover import _role_task_match
    backend = _role_task_match('backend developer', 'Implement FastAPI API endpoint and database schema')
    ml = _role_task_match('ml engineer', 'Train and evaluate the Random Forest gesture classifier')
    frontend = _role_task_match('frontend developer', 'Improve React chat interface and accessible components')
    devops = _role_task_match('devops engineer', 'Configure Render deployment and CI/CD pipeline')
    assert backend[0] > 0 and 'server-side' in backend[1]
    assert ml[0] > 0 and 'model development' in ml[1]
    assert frontend[0] > 0 and 'browser-facing' in frontend[1]
    assert devops[0] > 0 and 'operational reliability' in devops[1]
    assert _role_task_match('backend developer', 'Prepare model training dataset')[0] == 0


def test_handover_payload_exposes_role_task_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('KNOWBRIDGE_DB', str(tmp_path/'role-plan.db'))
    from app.deps import get_db
    from app.db import DB
    from app.pipeline import run_folder
    source_dir = tmp_path/'role_plan_sources'; source_dir.mkdir()
    (source_dir/'tasks.md').write_text(
        '# Project tasks\n\n'
        '- Task: Implement FastAPI API endpoint and database schema.\n'
        '- Task: Train and evaluate the Random Forest gesture classifier.\n', encoding='utf-8')
    def dep():
        db = DB(tmp_path/'role-plan.db')
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db] = dep
    try:
        db=DB(tmp_path/'role-plan.db'); run_folder(db, source_dir); db.close()
        c=TestClient(app)
        backend=c.post('/handover/generate',json={'recipient':'Backend joiner','role':'Backend Developer'})
        ml=c.post('/handover/generate',json={'recipient':'ML joiner','role':'ML Engineer'})
        assert backend.status_code == 200, backend.text
        assert ml.status_code == 200, ml.text
        bp=backend.json()['payload']; mp=ml.json()['payload']
        assert 'role_task_plan' in bp and 'role_task_summary' in bp
        assert all(t.get('assignment',{}).get('primary_owner') == 'Backend Developer' for t in bp['role_task_plan'])
        assert all(t.get('assignment',{}).get('primary_owner') == 'ML Engineer' for t in mp['role_task_plan'])
        assert all(t.get('evidence') for t in bp['role_task_plan'] + mp['role_task_plan'])
    finally:
        app.dependency_overrides.clear()


def test_component_based_role_handover_uses_source_files(tmp_path, monkeypatch):
    monkeypatch.setenv('KNOWBRIDGE_DB', str(tmp_path/'component-roles.db'))
    from app.deps import get_db
    source_dir = tmp_path/'component_sources'; source_dir.mkdir()
    (source_dir/'api_service.py').write_text(
        'FastAPI backend service exposes REST API endpoints and stores records in SQLite database.\n'
        'Authentication middleware validates bearer tokens and returns JSON responses.\n', encoding='utf-8')
    (source_dir/'gesture_model.py').write_text(
        'The machine learning pipeline preprocesses gesture features and trains a Random Forest classifier.\n'
        'The classifier predicts fist, palm and peace gestures using scikit-learn.\n', encoding='utf-8')
    (source_dir/'raone_agent.py').write_text(
        'The AI opponent uses reinforcement learning with a PPO policy and tactical decision-making.\n'
        'Stable-Baselines3 loads a trained checkpoint for inference with a scripted fallback.\n', encoding='utf-8')
    (source_dir/'game_ui.md').write_text(
        '# Frontend game interface\n\nPygame renders the user interface, character animations, menus and interaction screens.\n', encoding='utf-8')
    def dep():
        db=DB(tmp_path/'component-roles.db')
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=dep
    try:
        db=DB(tmp_path/'component-roles.db'); run_folder(db, source_dir); db.close()
        c=TestClient(app); outputs={}
        for role in ('Backend Developer','ML Engineer','AI/GenAI Engineer','Frontend Developer'):
            response=c.post('/handover/generate', json={'recipient':'New member','role':role})
            assert response.status_code==200, response.text
            outputs[role]=response.json()['payload']
        def files(payload):
            return {x['source_file'] for x in payload['role_components']}
        assert any('api_service.py' in f for f in files(outputs['Backend Developer']))
        assert any('gesture_model.py' in f for f in files(outputs['ML Engineer']))
        assert any('raone_agent.py' in f for f in files(outputs['AI/GenAI Engineer']))
        assert any('game_ui.md' in f for f in files(outputs['Frontend Developer']))
        assert files(outputs['Backend Developer']) != files(outputs['ML Engineer'])
        assert outputs['ML Engineer']['role_responsibilities']
        assert outputs['AI/GenAI Engineer']['suggested_tasks']
        assert outputs['ML Engineer']['confirmed_tasks'] == []
        assert all(x['evidence'] and x['source_files'] for x in outputs['ML Engineer']['suggested_tasks'])
        assert all(x['status']=='SUGGESTED_NOT_CONFIRMED' for x in outputs['ML Engineer']['suggested_tasks'])
    finally:
        app.dependency_overrides.clear()


def test_component_handover_empty_repository_evidence_is_honest(tmp_path, monkeypatch):
    monkeypatch.setenv('KNOWBRIDGE_DB', str(tmp_path/'empty-components.db'))
    from app.deps import get_db
    source_dir=tmp_path/'empty_components'; source_dir.mkdir()
    (source_dir/'general.md').write_text('Welcome to the project workspace.\n', encoding='utf-8')
    def dep():
        db=DB(tmp_path/'empty-components.db')
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=dep
    try:
        db=DB(tmp_path/'empty-components.db'); run_folder(db, source_dir); db.close()
        response=TestClient(app).post('/handover/generate', json={'recipient':'Member','role':'ML Engineer'})
        assert response.status_code==200, response.text
        payload=response.json()['payload']
        assert payload['role_components']==[]
        assert payload['role_responsibilities']==[]
        assert payload['suggested_tasks']==[]
        assert payload['confirmed_tasks']==[]
    finally:
        app.dependency_overrides.clear()
