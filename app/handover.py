"""Evidence-grounded handover drafts and auditable approval workflow.
Prototype security: admin actions require KNOWBRIDGE_ADMIN_TOKEN; public corpus only.
"""
import json, os, uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from .deps import get_db
from .pipeline import build
from .graph import knowledge_nodes
from .debt import build_report
from .config import DATA
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from xml.sax.saxutils import escape
from fastapi.responses import FileResponse

router = APIRouter(prefix='/handover', tags=['Handover'])
DDL = '''CREATE TABLE IF NOT EXISTS handovers(id TEXT PRIMARY KEY, project_id TEXT, recipient TEXT, role TEXT, perspective TEXT, status TEXT, payload TEXT, version INTEGER, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS handover_events(id INTEGER PRIMARY KEY AUTOINCREMENT, handover_id TEXT, actor TEXT, action TEXT, version INTEGER, snapshot TEXT, comment TEXT, created TEXT);'''

def init(db): db.c.executescript(DDL)
def now(): return datetime.now(timezone.utc).isoformat()
def admin(token):
    configured=os.getenv('KNOWBRIDGE_ADMIN_TOKEN')
    if not configured or not token or not __import__('hmac').compare_digest(token,configured):
        raise HTTPException(403,'Administrator token required. Configure KNOWBRIDGE_ADMIN_TOKEN.')

def get(db, hid):
    init(db); r=db.c.execute('SELECT * FROM handovers WHERE id=?',(hid,)).fetchone()
    if not r: raise HTTPException(404,'Handover not found')
    d=dict(r); d['payload']=json.loads(d['payload']); return d

def event(db,hid,actor,action,version,payload,comment=''):
    db.c.execute('INSERT INTO handover_events(handover_id,actor,action,version,snapshot,comment,created) VALUES(?,?,?,?,?,?,?)',(hid,actor,action,version,json.dumps(payload),comment,now()))

def accessible(db):
    # Existing ingestors do not track source ACLs. Fail closed if restricted metadata appears.
    chunks=db.chunks()
    if any((c.get('meta') or {}).get('visibility','public') != 'public' for c in chunks):
        raise HTTPException(403,'Restricted sources present; source ACL enforcement must be configured before handover generation.')

ROLE_PROFILES = {
    'backend developer': ['backend','api','server','database','integration','authentication','payment','endpoint','schema','architecture','algorithm','model','yolo','yolov8','cbam','implementation','technical','training','inference'],
    'frontend developer': ['frontend','ui','ux','interface','web','react','component','screen','accessibility'],
    'full-stack developer': ['frontend','backend','api','database','integration','ui','server','web'],
    'qa engineer / tester': ['test','qa','quality','bug','defect','validation','acceptance','regression','coverage'],
    'project manager': ['project','milestone','owner','status','schedule','risk','dependency','blocker','approval','coordination','team','responsibility','stakeholder','priority','unresolved'],
    'project lead': ['project','milestone','decision','risk','owner','dependency','blocker','approval','coordination','team','responsibility','stakeholder','priority','status','unresolved'],
    'ui/ux designer': ['ui','ux','design','prototype','screen','interface','user','feedback','usability'],
    'devops engineer': ['devops','deploy','deployment','infrastructure','ci/cd','pipeline','environment','server','monitoring'],
    'data engineer': ['data','dataset','pipeline','etl','schema','warehouse','quality','integration'],
    'data scientist': ['data','dataset','analysis','experiment','metric','model','prediction','evaluation'],
    'ml engineer': ['machine learning','ml','model','training','inference','deployment','dataset','evaluation','pipeline'],
    'ai/genai engineer': ['ai','genai','llm','prompt','rag','embedding','model','inference','evaluation'],
    'security engineer': ['security','vulnerability','authentication','authorization','permission','access','encryption','risk'],
    'business analyst': ['requirement','stakeholder','business','acceptance','decision','process','question','approval'],
    'product manager': ['product','roadmap','feature','user','requirement','priority','milestone','feedback'],
    'technical writer': ['documentation','document','guide','readme','api','technical','knowledge','resource'],
    'researcher': ['research','experiment','hypothesis','literature','finding','evaluation','model','data'],
    'system architect': ['architecture','system','design','database','api','integration','dependency','technology'],
    'project administrator': ['project','owner','approval','risk','status','handover','permission','milestone'],
}

class Generate(BaseModel):
    project_id: str = 'default'
    recipient: str = Field(min_length=1,max_length=120)
    role: str = Field(min_length=1,max_length=120)
    perspective: str = 'incoming'
    role_description: str = Field(default='',max_length=1000)
    responsibilities: str = Field(default='',max_length=1500)
class Edit(BaseModel):
    sections: list[dict]
    actions: list[dict]
    comment: str = ''
class Review(BaseModel):
    comment: str = ''

def _role_terms(req):
    role = req.role.strip().lower()
    terms = set(ROLE_PROFILES.get(role, []))
    # Alias matching keeps common shortened titles useful.
    aliases = {'qa engineer':['qa','test','quality'], 'tester':['test','qa','bug'],
               'frontend':['frontend','ui','interface'], 'backend':['backend','api','server'],
               'devops':['deploy','infrastructure','pipeline'], 'administrator':['approval','owner','permission']}
    for alias, words in aliases.items():
        if alias in role: terms.update(words)
    custom_text = f'{req.role_description} {req.responsibilities}'.lower()
    if custom_text.strip():
        import re
        stop = {'with','from','that','this','will','their','have','into','your','about','role','work','team','and','the','for'}
        terms.update(w for w in re.findall(r'[a-z][a-z0-9+#./-]{2,}', custom_text) if w not in stop)
    if not terms:
        import re
        stop = {'with','from','that','this','will','their','have','into','your','about','role','work','team','and','the','for'}
        terms.update(w for w in re.findall(r'[a-z][a-z0-9+#./-]{2,}', role) if w not in stop)
    return terms

def _node_text(n):
    return (str(n.get('name',''))+' '+str(n.get('description',''))+' '+json.dumps(n.get('attrs',{}),default=str)+' '+json.dumps(n.get('observations',{}),default=str)).lower()

def _role_family(role, perspective):
    r = role.lower()
    if perspective == 'administrator' or 'administrator' in r: return 'administrator'
    if any(x in r for x in ('backend','frontend','full-stack','devops','developer','engineer','architect','ml','ai/genai','data engineer')): return 'technical'
    if any(x in r for x in ('qa','tester','quality')): return 'quality'
    if any(x in r for x in ('project lead','project manager','product manager','manager')): return 'leadership'
    if any(x in r for x in ('business analyst','product')): return 'business'
    if any(x in r for x in ('designer','ui/ux')): return 'design'
    if any(x in r for x in ('research','scientist')): return 'research'
    if 'security' in r: return 'security'
    return 'custom'

def _question_action(role_family, question):
    q = question.strip().rstrip('?')
    if role_family == 'technical':
        return f'Locate the technical rationale for "{q}" and document its implementation implications.'
    if role_family == 'leadership':
        return f'Identify the decision-maker for "{q}" and coordinate documentation of the approved rationale.'
    if role_family == 'quality':
        return f'Confirm whether "{q}" affects test strategy, acceptance criteria or validation requirements.'
    if role_family == 'design':
        return f'Confirm whether "{q}" affects user experience or interface decisions and record the agreed direction.'
    if role_family == 'research':
        return f'Locate the experiment, evidence or evaluation that answers "{q}" and document the findings.'
    if role_family == 'security':
        return f'Check whether "{q}" has security implications and locate the documented security decision.'
    if role_family == 'business':
        return f'Confirm the stakeholder decision behind "{q}" and record its business rationale.'
    return f'Find a source-supported answer to "{q}" and record who can confirm it.'

import re
ID_RE=r'[A-Z]{1,4}-\d+'
def _dedupe_decisions(nodes):
    """Drop stale/duplicate decisions whose wording is covered by a current decision."""
    tok=lambda n: {t for t in re.findall(r'[a-z0-9]+',n['name'].lower()) if len(t)>3 and t not in ('instead','using','adopt','with','that','this','from')}
    dec=[n for n in nodes if n['type']=='decision']; drop=set()
    for n in dec:
        for o in dec:
            if o is n or o['id'] in drop: continue
            a,b=tok(n),tok(o)
            if a and a<=b and (n.get('status')=='STALE' or a<b or n['id']>o['id']) and (o.get('status')!='STALE' or n.get('status')=='STALE'):
                drop.add(n['id']); break
    return [n for n in nodes if n['id'] not in drop]

STRICT = {
 'data engineer': {'terms':['database','schema','data model','pipeline','etl','data quality','dataset','data','warehouse','migration','storage','postgres','postgresql','sql','ingestion','occupancy'],'types':()},
 'backend developer': {'terms':['api','endpoint','backend','server','integration','graphql','rest','authentication','gateway','payment','database','schema','architecture','service','mqtt','webhook','firebase','provider'],'types':()},
 'project manager': {'terms':['owner','milestone','risk','blocker','dependency','schedule','due','approval','coordination','stakeholder','unavailable','delayed','responsible'],'types':('risk','milestone','question','task')},
}
STRICT['project lead']=STRICT['project manager']
_TS={'not_started':'OPEN','not started':'OPEN','todo':'OPEN','to do':'OPEN','open':'OPEN','in_progress':'IN PROGRESS','in progress':'IN PROGRESS','blocked':'BLOCKED','done':'COMPLETED','completed':'COMPLETED','complete':'COMPLETED'}
def _hits(text, terms): return sum(1 for t in terms if re.search(r'(?<![a-z0-9])'+re.escape(t)+r'(?![a-z0-9])',text))
def _task_status(n):
    """Task status comes only from recorded status observations, never from the evidence label."""
    vals=[]
    for o in n.get('observations',{}).get('status',[]):
        v=_TS.get(str(o['value']).strip().lower().replace('-','_'),_TS.get(str(o['value']).strip().lower()))
        if v: vals.append((o['evidence'].get('timestamp') or '',v))
    if not vals: return 'UNKNOWN',[]
    if n.get('conflicts') and any(c.get('attribute')=='status' for c in n['conflicts']): return 'UNKNOWN',sorted({v for _,v in vals})
    return max(vals)[1],[]

def _merge_actions(actions, nodes, role):
    """Combine overlapping actions (same task ref / entity), keeping all evidence and linked findings."""
    name={n['id']:n['name'] for n in nodes}; groups={}; order=[]
    for a in actions:
        ent=a.get('related_entity'); refs=re.findall(ID_RE,name.get(ent,'')+' '+a['title'])
        key=refs[0] if refs else ent
        if key not in groups: groups[key]=[]; order.append(key)
        groups[key].append(a)
    rank={'High':0,'Medium':1,'Low':2}; out=[]
    for k in order:
        g=sorted(groups[k],key=lambda a:rank.get(a['priority'],3)); m=dict(g[0])
        ev=[]; seen=set()
        for a in g:
            for e in a.get('evidence',[]):
                sig=(e.get('chunk_id'),str(e.get('quote') or e.get('text')))
                if sig not in seen: seen.add(sig); ev.append(e)
        steps=[]; [steps.append(a['next_step']) for a in g if a['next_step'] not in steps]
        m.update(evidence=ev,next_step=' '.join(steps) if len(steps)>1 else steps[0],
                 linked_findings=[{'entity':a.get('related_entity'),'finding':a['title'],'reason':a['reason']} for a in g],
                 role_relevance=f'Linked to an item included in the {role} handover.')
        if len(g)>1: m['merged_from']=len(g); m['reason']=' | '.join(dict.fromkeys(a['reason'] for a in g))
        out.append(m)
    return out

def generate_payload(db, req):
    accessible(db)
    G=build(db); nodes=knowledge_nodes(G); debt=build_report(G)['issues']
    if not db.documents(): raise HTTPException(409,'Connect project documents first')
    perspective=req.perspective
    terms=_role_terms(req)
    role_key=req.role.strip().lower()
    family=_role_family(role_key, perspective)
    admin_role = family == 'administrator'
    # Role-specific type priorities ensure that the same corpus is organised for the recipient,
    # while shared risks/questions remain visible where relevant.
    priorities = {
        'technical': {'task':5,'decision':6,'resource':4,'risk':3,'question':5,'milestone':1},
        'leadership': {'risk':6,'question':6,'milestone':5,'task':4,'decision':5,'resource':1},
        'quality': {'task':6,'risk':6,'question':5,'decision':3,'resource':4,'milestone':2},
        'design': {'decision':5,'resource':6,'task':4,'question':4,'risk':3,'milestone':1},
        'research': {'decision':5,'resource':6,'question':5,'risk':3,'task':3,'milestone':1},
        'security': {'risk':6,'decision':5,'task':4,'question':5,'resource':3,'milestone':1},
        'business': {'question':6,'decision':6,'milestone':4,'risk':4,'task':3,'resource':2},
        'administrator': {'risk':6,'question':6,'task':6,'decision':5,'milestone':5,'resource':3},
        'custom': {'task':4,'decision':4,'risk':4,'question':4,'resource':3,'milestone':2},
    }.get(family)
    strict=STRICT.get(role_key); keep=set()
    if strict and not admin_role:
        prim={n['id'] for n in nodes if n['type'] in ('task','decision','risk','question','resource','milestone') and (_hits(_node_text(n),strict['terms'])>0 or n['type'] in strict['types'])}
        keep=set(prim)
        for u,v,k in G.edges(keys=True):   # dependencies justify inclusion of linked items
            if k in ('BLOCKS','DEPENDS_ON','RELATED_TO'):
                for a,b in ((u,v),(v,u)):
                    if a in prim and b in G and G.nodes[b]['type'] in ('task','risk','decision','question'): keep.add(b)
    scored=[]
    for n in nodes:
        if n['type'] not in ('task','decision','risk','question','resource','milestone'): continue
        text=_node_text(n)
        lexical=sum(2 if term in text else 0 for term in terms)
        type_weight=priorities.get(n['type'],0)
        # Questions and risks remain visible but are ranked by role-specific priorities.
        score=lexical+type_weight
        if strict and not admin_role:
            if n['id'] in keep: scored.append((score+3*_hits(text,strict['terms']),n))
        elif admin_role or lexical > 0 or type_weight >= 6:
            scored.append((score,n))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen=_dedupe_decisions([n for _,n in scored])
    chosen_ids={n['id'] for n in chosen}
    sections=[]
    section_titles=[('task','Responsibilities and open tasks'),('decision','Decisions and rationale'),('risk','Risks and blockers'),('question','Role-specific questions requiring clarification' if family in ('technical','quality','design','research','security') else 'Project decisions and questions requiring clarification'),('resource','Relevant resources'),('milestone','Milestones')]
    for kind,title in section_titles:
        entries=[]
        for n in chosen:
            if n['type']!=kind: continue
            ev=[{'document':e.get('doc_name'),'chunk_id':e.get('chunk_id'),'quote':e.get('text'),'timestamp':e.get('timestamp')} for e in n.get('evidence',[])[:3]]
            attrs=n.get('attrs',{}); obs=n.get('observations',{})
            if kind == 'question':
                # The existence of an open question may be evidenced; its answer is not thereby verified.
                verification='MISSING'
                question_status='VERIFIED' if ev else 'INFERRED'
                answer_status='CONFLICTING' if n.get('conflicts') else 'MISSING'
            else:
                verification=n.get('status','INFERRED')
                question_status=None; answer_status=None
            ititle=n['name']
            if kind=='task' and re.fullmatch(ID_RE,ititle.strip()):
                ititle=f'{ititle.strip()} — task description not found in sources'
            item={'id':n['id'],'title':ititle,'verification':verification,'details':attrs,'observations':obs,'evidence':ev,'evidence_status':verification,'needs_confirmation': kind == 'question' or bool(n.get('conflicts')) or any(v=='MISSING' for v in n.get('attr_status',{}).values())}
            if kind == 'task':
                ts,tv=_task_status(n); item['task_status']=ts
                if tv: item['task_status_conflict']=tv
            if kind == 'question':
                item.update({'state':'OPEN','question_status':question_status,'answer_status':answer_status})
            entries.append(item)
        if entries: sections.append({'title':title,'items':entries})
    actions=[]
    # Turn explicit open questions into role-specific clarification actions. These are suggestions,
    # not existing assigned tasks, and remain linked to the question's source evidence.
    for n in chosen:
        if n['type'] != 'question': continue
        ev=[{'document':e.get('doc_name'),'chunk_id':e.get('chunk_id'),'quote':e.get('text'),'timestamp':e.get('timestamp')} for e in n.get('evidence',[])[:3]]
        actions.append({'title':_question_action(family,n['name']),'priority':'Medium','reason':'The source documents an open question but does not provide a supported answer.','next_step':'Find the relevant source or ask an authorised project stakeholder to confirm the answer.','related_entity':n['id'],'evidence':ev,'verification':'MISSING','action_type':'SUGGESTED_CLARIFICATION'})
    for issue in debt:
        entity=issue.get('related_entity',{}); text=(entity.get('name','')+' '+issue.get('title','')+' '+issue.get('description','')).lower()
        relevance=sum(1 for t in terms if t in text)
        if entity.get('id') not in chosen_ids and not admin_role: continue
        if entity.get('type')=='decision' and entity.get('id') not in chosen_ids: continue
        # Avoid duplicating the same question-gap already represented above.
        if any(a.get('related_entity') == entity.get('id') for a in actions): continue
        actions.append({'title':issue['title'],'priority':issue['severity'],'reason':issue['description'],'next_step':issue['recommended_verification'],'related_entity':entity.get('id'),'evidence':issue.get('supporting_evidence',[]),'verification':'NEEDS CONFIRMATION','action_type':'SUGGESTED_CLARIFICATION','relevance_score':relevance})
    actions=_merge_actions(actions,nodes,req.role)
    actions.sort(key=lambda a: ({'High':0,'Medium':1,'Low':2}.get(a['priority'],3),-a.get('relevance_score',0)))
    for action in actions: action.pop('relevance_score',None)
    role_focus={
        'technical':'Prioritises implementation, architecture, algorithms, integrations and technical questions.',
        'leadership':'Prioritises project decisions, coordination, risks, ownership and unresolved approvals.',
        'quality':'Prioritises validation, defects, acceptance criteria and testing dependencies.',
        'design':'Prioritises user experience, design decisions, interfaces and feedback.',
        'research':'Prioritises methods, experiments, evidence, models and evaluation questions.',
        'security':'Prioritises security risks, access decisions and unresolved security requirements.',
        'business':'Prioritises requirements, stakeholder decisions and business-impact questions.',
        'administrator':'Prioritises completeness, risks, ownership gaps and review requirements.',
        'custom':'Prioritises evidence matching the supplied role description and responsibilities.'}[family]
    return {'project_overview':f'Based on {len(db.documents())} connected documents and {len(nodes)} knowledge entities. Only source-supported information is included.','recipient':req.recipient,'role':req.role,'role_description':req.role_description,'responsibilities':req.responsibilities,'perspective':perspective,'role_family':family,'role_focus':role_focus,'sections':sections,'actions':actions,'data_limitations': 'The connected sources contain limited project information; role-specific conclusions may therefore be incomplete.' if len(nodes) <= 3 else '', 'limitations':['Role relevance is not authorisation. This prototype only supports public source material.','Custom-role relevance is inferred from the supplied role description and responsibilities.','Missing evidence does not prove an activity did not occur.','An administrator must review and approve before sharing.']}

@router.get('/roles')
def roles():
    return {'roles': [r.title().replace('Ui/Ux','UI/UX').replace('Ai/Genai','AI/GenAI').replace('Qa','QA').replace('Ml','ML') for r in ROLE_PROFILES] + ['Other / Custom Role'], 'custom_role_supported': True}

@router.post('/generate')
def generate(req:Generate, db=Depends(get_db)):
    if req.perspective not in ('incoming','departing','administrator'): raise HTTPException(422,'Invalid perspective')
    payload=generate_payload(db,req); init(db); hid=uuid.uuid4().hex[:12]; t=now()
    db.c.execute('INSERT INTO handovers VALUES(?,?,?,?,?,?,?,?,?,?)',(hid,req.project_id,req.recipient,req.role,req.perspective,'DRAFT',json.dumps(payload),1,t,t))
    event(db,hid,'system','GENERATED',1,payload); db.c.commit()
    return get(db,hid)

@router.get('/{hid}')
def detail(hid:str,db=Depends(get_db)): return get(db,hid)

@router.put('/{hid}')
def edit(hid:str,req:Edit,x_admin_token:str|None=Header(default=None),db=Depends(get_db)):
    admin(x_admin_token); d=get(db,hid)
    if d['status'] not in ('DRAFT','REJECTED'): raise HTTPException(409,'Only draft/rejected handovers can be edited')
    payload=d['payload']; payload.update(sections=req.sections,actions=req.actions); ver=d['version']+1
    db.c.execute('UPDATE handovers SET payload=?,version=?,updated=? WHERE id=?',(json.dumps(payload),ver,now(),hid)); event(db,hid,'administrator','EDITED',ver,payload,req.comment); db.c.commit(); return get(db,hid)

def transition(hid,status,allowed,comment,token,db):
    admin(token); d=get(db,hid)
    if d['status'] not in allowed: raise HTTPException(409,f'Cannot change {d["status"]} to {status}')
    ver=d['version']+1
    db.c.execute('UPDATE handovers SET status=?,version=?,updated=? WHERE id=?',(status,ver,now(),hid)); event(db,hid,'administrator',status,ver,d['payload'],comment); db.c.commit(); return get(db,hid)
@router.post('/{hid}/submit')
def submit(hid:str,req:Review,x_admin_token:str|None=Header(default=None),db=Depends(get_db)): return transition(hid,'PENDING_REVIEW',('DRAFT','REJECTED'),req.comment,x_admin_token,db)
@router.post('/{hid}/approve')
def approve(hid:str,req:Review,x_admin_token:str|None=Header(default=None),db=Depends(get_db)): return transition(hid,'APPROVED',('PENDING_REVIEW',),req.comment,x_admin_token,db)
@router.post('/{hid}/reject')
def reject(hid:str,req:Review,x_admin_token:str|None=Header(default=None),db=Depends(get_db)): return transition(hid,'REJECTED',('PENDING_REVIEW',),req.comment,x_admin_token,db)
@router.post('/{hid}/share')
def share(hid:str,req:Review,x_admin_token:str|None=Header(default=None),db=Depends(get_db)): return transition(hid,'SHARED',('APPROVED',),req.comment,x_admin_token,db)
@router.get('/{hid}/history')
def history(hid:str,x_admin_token:str|None=Header(default=None),db=Depends(get_db)):
    admin(x_admin_token); get(db,hid)
    return [dict(r) for r in db.c.execute('SELECT actor,action,version,comment,created FROM handover_events WHERE handover_id=? ORDER BY id',(hid,))]
@router.get('/{hid}/download')
def download(hid:str,x_admin_token:str|None=Header(default=None),db=Depends(get_db)):
    d=get(db,hid)
    # Draft PDFs are available to administrators only; approved/shared are releasable in public-data prototype.
    if d['status'] not in ('APPROVED','SHARED'): admin(x_admin_token)
    path=DATA/'reports'/f'handover-{hid}.pdf'; path.parent.mkdir(parents=True,exist_ok=True)
    styles=getSampleStyleSheet(); p=d['payload']; story=[Paragraph('KNOWBRIDGE · PROJECT CONTINUITY PACK',styles['Heading2']),Paragraph(escape(p['recipient']+' — '+p['role']),styles['Title']),Paragraph('Status: '+d['status'],styles['BodyText']),Spacer(1,12),Paragraph(escape(p['project_overview']),styles['BodyText'])]
    if p.get('role_focus'): story.append(Paragraph(escape('Role focus: '+p['role_focus']),styles['BodyText']))
    if p.get('data_limitations'): story.append(Paragraph(escape(p['data_limitations']),styles['Italic']))
    for s in p['sections']:
        story.append(Paragraph(escape(s['title']),styles['Heading2']))
        for item in s['items']:
            story.append(Paragraph(escape(item['title']+' ['+item['verification']+']'),styles['BodyText']))
            if item.get('task_status'): story.append(Paragraph(escape('Task status: '+item['task_status']+' · Evidence status: '+item['verification']),styles['Italic']))
            if item.get('state') == 'OPEN': story.append(Paragraph(escape('Question status: '+str(item.get('question_status'))+' · Answer status: '+str(item.get('answer_status'))),styles['Italic']))
            for e in item['evidence'][:2]: story.append(Paragraph(escape(str(e.get('document'))+' · '+str(e.get('chunk_id'))+': '+str(e.get('quote',''))[:300]),styles['Italic']))
    story.append(Paragraph('Prioritised continuation plan',styles['Heading2']))
    for a in p['actions']: story.append(Paragraph(escape(a['priority']+' · '+a['title']+' — '+a['next_step']+' ['+a.get('action_type','ACTION')+']'),styles['BodyText']))
    story.append(Paragraph('Limitations and review',styles['Heading2']))
    for line in p['limitations']: story.append(Paragraph(escape(line),styles['BodyText']))
    SimpleDocTemplate(str(path),pagesize=A4).build(story)
    return FileResponse(str(path),media_type='application/pdf',filename=f'knowbridge-handover-{hid}.pdf')
