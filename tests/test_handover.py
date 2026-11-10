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
