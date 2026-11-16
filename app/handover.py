"""Evidence-grounded handover drafts and auditable approval workflow.
Prototype security: admin actions require KNOWBRIDGE_ADMIN_TOKEN; public corpus only.
"""
from typing import Optional
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
    repository: Optional[str] = None
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
# Explicit responsibility vocabularies keep role assignment explainable and prevent
# the previous broad technical profile from sending the same work to every engineer.
ROLE_TASK_PROFILES = {
 'backend developer': {'terms':['api','endpoint','backend','server','database','schema','authentication','authorization','service','route','request','response','integration','query','migration'], 'why':'Owns server-side logic, APIs, persistence and service integration.'},
 'frontend developer': {'terms':['frontend','ui','ux','component','screen','browser','react','javascript','typescript','css','html','accessibility','chat interface'], 'why':'Owns browser-facing components, interaction flows and presentation.'},
 'full-stack developer': {'terms':['api','endpoint','backend','frontend','ui','database','integration','component','server','web'], 'why':'Owns work spanning the application interface and server-side integration.'},
 'ml engineer': {'terms':['machine learning','random forest','classifier','training','inference','model evaluation','feature','pytorch','tensorflow','scikit-learn','dataset','checkpoint','ppo'], 'why':'Owns model development, training, evaluation and inference integration.'},
 'ai/genai engineer': {'terms':['llm','rag','retrieval','embedding','prompt','generation','reranking','vector','language model','chatbot','inference'], 'why':'Owns language-model integration, retrieval and grounded answer generation.'},
 'data engineer': {'terms':['data pipeline','ingestion','etl','database','schema','dataset','data quality','normalization','storage','migration'], 'why':'Owns data ingestion, transformation, storage and quality workflows.'},
 'data scientist': {'terms':['dataset','analysis','experiment','metric','prediction','feature','statistical','evaluation'], 'why':'Owns data analysis, experimentation and model-performance interpretation.'},
 'devops engineer': {'terms':['deployment','deploy','ci/cd','pipeline','docker','container','environment','monitoring','render','infrastructure','release'], 'why':'Owns build, release, runtime configuration and operational reliability.'},
 'qa engineer / tester': {'terms':['test','testing','regression','integration test','validation','coverage','acceptance','quality','bug'], 'why':'Owns verification, regression coverage and acceptance testing.'},
 'security engineer': {'terms':['security','authentication','authorization','permission','secret','token','encryption','vulnerability','access control'], 'why':'Owns security controls, access boundaries and vulnerability remediation.'},
 'technical lead': {'terms':['architecture','design decision','dependency','integration','technical risk','system design','trade-off'], 'why':'Coordinates cross-component technical decisions and architectural dependencies.'},
}

def _role_task_match(role_key, title, description=''):
    profile = ROLE_TASK_PROFILES.get(role_key)
    if not profile:
        # Fall back to the role's existing curated terms, but require a real task node.
        terms = STRICT.get(role_key, {}).get('terms', [])
        why = 'Matched the task evidence to this role profile.'
    else:
        terms, why = profile['terms'], profile['why']
    text = (str(title) + ' ' + str(description)).lower()
    hits = [term for term in terms if re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text)]
    return (len(hits), why, hits)

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

# Repository-component profiles complement explicit TASK matching. They operate on
# already-ingested chunks and document metadata, so no new provider or schema is needed.
ROLE_COMPONENT_PROFILES = {
 'backend developer': ['api','endpoint','route','fastapi','flask','django','server','backend','database','sql','sqlite','schema','authentication','authorization','middleware','service','request','response','webhook','integration','repository','controller'],
 'frontend developer': ['frontend','user interface','interface','ui','ux','html','css','javascript','typescript','react','vue','component','browser','form','button','modal','screen','accessibility','responsive','pygame','rendering','animation'],
 'full-stack developer': ['api','endpoint','fastapi','backend','database','sqlite','html','css','javascript','typescript','react','frontend','interface','integration','request','response'],
 'ml engineer': ['machine learning','random forest','classifier','classification','training','inference','pytorch','tensorflow','scikit-learn','sklearn','model','checkpoint','feature extraction','preprocessing','dataset','evaluation','accuracy','loss','ppo','stable-baselines'],
 'ai/genai engineer': ['llm','large language model','genai','generative ai','rag','retrieval','embedding','prompt','generation','reranking','vector search','chatbot','agent','ppo','reinforcement learning','policy','decision-making','inference'],
 'data engineer': ['data pipeline','ingestion','etl','dataset','data quality','normalization','transformation','database','schema','storage','migration','csv','parquet','warehouse'],
 'data scientist': ['dataset','analysis','experiment','metric','prediction','feature','statistical','evaluation','visualization','pandas','numpy','scipy','regression','classification','forecast'],
 'devops engineer': ['deployment','deploy','ci/cd','github actions','docker','container','environment variable','requirements.txt','render','infrastructure','monitoring','logging','release','build','server configuration'],
 'qa engineer / tester': ['test','testing','pytest','unittest','regression','integration test','validation','coverage','acceptance','assert','fixture','mock','quality'],
 'security engineer': ['security','authentication','authorization','permission','secret','token','encryption','vulnerability','access control','cors','csrf','injection','sanitization','hmac'],
 'technical lead': ['architecture','system design','design decision','dependency','integration','technical risk','trade-off','module','interface contract','data flow'],
}

def _component_label(doc, chunk):
    filename = str(doc.get('filename') or 'source document')
    section = str(chunk.get('section') or '').strip()
    stem = filename.rsplit('/', 1)[-1]
    return f"{stem}" + (f" · {section}" if section else '')

def _role_component_matches(db, role_key, req):
    """Rank real indexed passages by role relevance and preserve source provenance."""
    terms = list(ROLE_COMPONENT_PROFILES.get(role_key, []))
    if req.role_description or req.responsibilities:
        custom = re.findall(r'[a-z][a-z0-9+#./-]{2,}', (req.role_description+' '+req.responsibilities).lower())
        terms.extend(t for t in custom if t not in {'the','and','for','with','from','this','that','role','work','team'})
    if not terms:
        terms = list(_role_terms(req))
    docs = {d['id']: d for d in db.documents()}
    matches=[]
    for ch in db.chunks():
        doc=docs.get(ch.get('doc_id'), {})
        text=(str(doc.get('filename',''))+' '+str(ch.get('section') or '')+' '+str(ch.get('text') or '')).lower()
        hits=[]
        for term in terms:
            if re.search(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])', text):
                hits.append(term)
        if not hits: continue
        # Multi-term evidence is preferred; file names and headings contribute naturally.
        score=len(hits)*2 + min(len(set(hits)), 5)
        excerpt=re.sub(r'\s+',' ',str(ch.get('text') or '')).strip()
        if len(excerpt)>700: excerpt=excerpt[:697]+'...'
        matches.append({'component':_component_label(doc,ch),'source_file':doc.get('filename') or 'Unknown source',
                        'chunk_id':ch.get('id'),'section':ch.get('section'),'relevance_score':score,
                        'matched_terms':hits[:8],'evidence':excerpt,'verification':'SOURCE_EVIDENCE'})
    matches.sort(key=lambda x:(-x['relevance_score'], x['source_file'].lower(), str(x.get('section') or '').lower()))
    # Keep the strongest distinct source sections rather than flooding every role with all files.
    selected=[]; seen=set()
    for item in matches:
        key=(item['source_file'],item.get('section') or item['chunk_id'])
        if key in seen: continue
        seen.add(key); selected.append(item)
        if len(selected)>=8: break
    return selected

def _build_role_guidance(role, components):
    """Create cautious onboarding responsibilities from source-backed components."""
    responsibilities=[]; suggestions=[]
    for c in components:
        label=c['component']; terms=', '.join(c['matched_terms'][:4])
        responsibilities.append({
            'title':f"Understand {label}",
            'description':f"Review this indexed project component and understand how it supports the wider system. Relevant evidence includes: {terms}.",
            'why_this_role':f"The source contains implementation evidence matching the {role} responsibility profile.",
            'related_component':label,'source_files':[c['source_file']],
            'evidence':[{'document':c['source_file'],'chunk_id':c['chunk_id'],'quote':c['evidence']}],
            'expected_outcome':'Be able to explain the component, its interfaces and its role in the project.',
            'status':'EXISTING_COMPONENT_TO_UNDERSTAND'
        })
        suggestions.append({
            'title':f"Review and validate {label}",
            'description':f"Inspect the documented implementation in {c['source_file']} and identify correctness, maintainability or integration concerns relevant to this role. This is a recommendation, not a confirmed backlog item.",
            'why_this_role':f"The component contains source evidence relevant to {role}.",
            'related_component':label,'source_files':[c['source_file']],
            'evidence':[{'document':c['source_file'],'chunk_id':c['chunk_id'],'quote':c['evidence']}],
            'expected_outcome':'Produce a review finding or confirm that no change is needed.',
            'priority':'Unprioritised (suggestion)','status':'SUGGESTED_NOT_CONFIRMED'
        })
    return responsibilities, suggestions

def generate_payload(db, req):
    accessible(db)
    from . import repo_intel, repo_roles, repo_handover
    db=repo_intel.scope(db, getattr(req, 'repository', None))  # strict isolation: only the selected repository's evidence is used
    G=build(db); nodes=knowledge_nodes(G); debt=build_report(G)['issues']
    if not db.documents(): raise HTTPException(409,'Connect project documents first')
    perspective=req.perspective
    terms=_role_terms(req)
    role_key=req.role.strip().lower()
    family=_role_family(role_key, perspective)
    role_components=_role_component_matches(db, role_key, req)
    role_responsibilities, suggested_tasks=_build_role_guidance(req.role, role_components)
    repo_ctx=None; rv=None
    if repo_intel.has_repo(db):  # source-derived components/responsibilities replace keyword matching when a repository is connected
        try:
            M=repo_intel.build_model(db); rv=repo_roles.role_view(M, req.role, req.role_description, req.responsibilities)
            if rv['components'] or rv['responsibilities']:
                role_components, role_responsibilities, suggested_tasks = rv['components'], rv['responsibilities'], rv['suggestions']
                repo_ctx={'repository':{'name':M['repo'],'branch':M['branch'],'commit':M['commit'],'stats':M['stats']},'collaboration':rv['collaboration'],'knowledge_gaps':rv['gaps'],'relevant_files':rv['files'],'project_summary':(repo_intel.project_summary(db) or {}).get('sections',[])}
        except Exception: repo_ctx=None
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
    from .extraction import _looks_like_code
    for n in nodes:
        if n['type'] not in ('task','decision','risk','question','resource','milestone'): continue
        if n['type'] == 'question':
            evidence_rows = n.get('evidence', [])
            if _looks_like_code(n.get('name', '')) or (evidence_rows and all(_looks_like_code(e.get('text', '')) for e in evidence_rows)):
                continue
        text=_node_text(n)
        lexical=sum(2 if term in text else 0 for term in terms)
        type_weight=priorities.get(n['type'],0)
        # Questions and risks remain visible but are ranked by role-specific priorities.
        score=lexical+type_weight
        if strict and not admin_role:
            # Open questions are cross-functional handover risks: keep them visible even
            # when their wording does not match a role's strict keyword profile.
            if n['id'] in keep or n['type'] == 'question':
                scored.append((score+3*_hits(text,strict['terms']),n))
        elif admin_role or lexical > 0 or type_weight >= 6 or n['type'] == 'question':
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
                description = n.get('description', '')
                match_count, ownership_reason, matched_terms = _role_task_match(role_key, ititle, description)
                if match_count:
                    item['assignment'] = {
                        'primary_owner': req.role,
                        'supporting_roles': [],
                        'reviewer': 'Technical Lead' if role_key != 'technical lead' else 'Project Lead',
                        'ownership_reason': ownership_reason,
                        'matched_responsibility_evidence': matched_terms,
                    }
                    item['priority'] = 'High' if ts in ('BLOCKED', 'IN PROGRESS') else 'Unprioritised'
                    item['priority_reason'] = ('Recorded task status indicates active work or a blocker.' if item['priority'] == 'High' else 'No source-backed priority was recorded; confirm scheduling with the project owner.')
                    item['expected_outcome'] = 'Complete or advance the source-documented task and update its status with linked evidence.'
                    item['dependencies'] = [
                        {'task': G.nodes[v].get('name'), 'relationship': k}
                        for u, v, k in G.edges(keys=True)
                        if u == n['id'] and k == 'DEPENDS_ON'
                    ]
            if kind == 'question':
                item.update({'state':'OPEN','question_status':question_status,'answer_status':answer_status})
            entries.append(item)
        if entries: sections.append({'title':title,'items':entries})
    actions=[]
    # Turn explicit open questions into role-specific clarification actions. These are suggestions,
    # not existing assigned tasks, and remain linked to the question's source evidence.
    for n in chosen:
        if n['type'] != 'question': continue
        # Older indexed corpora may contain code-derived false questions. Do not turn
        # assignments, guards, debug strings or code fragments into stakeholder actions.
        from .extraction import _looks_like_code
        evidence_rows = n.get('evidence', [])
        if evidence_rows and all(_looks_like_code(e.get('text', '')) for e in evidence_rows):
            continue
        if _looks_like_code(n.get('name', '')):
            continue
        ev=[{'document':e.get('doc_name'),'chunk_id':e.get('chunk_id'),'quote':e.get('text'),'timestamp':e.get('timestamp')} for e in evidence_rows[:3]]
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
    role_tasks = []
    for section in sections:
        if section['title'] != 'Responsibilities and open tasks':
            continue
        for item in section['items']:
            if item.get('assignment'):
                role_tasks.append({k: item.get(k) for k in ('id','title','task_status','priority','priority_reason','assignment','dependencies','expected_outcome','evidence','verification')})
    role_tasks.sort(key=lambda x: ({'High':0,'Medium':1,'Low':2}.get(x.get('priority'),3), x.get('title','').lower()))
    if repo_ctx and rv:
        try: sections=repo_handover.build_sections(perspective, M, rv, req.role, nodes, role_tasks) or sections
        except Exception: pass
    _out={'project_overview':f'Based on {len(db.documents())} connected documents and {len(nodes)} knowledge entities. Only source-supported information is included.','recipient':req.recipient,'role':req.role,'role_description':req.role_description,'responsibilities':req.responsibilities,'perspective':perspective,'role_family':family,'role_focus':role_focus,'sections':sections,'actions':actions,'role_task_plan':role_tasks,'role_task_summary':{'assigned_tasks':len(role_tasks),'message':('Confirmed task records matched to this role are listed separately.' if role_tasks else 'No explicit, source-backed task records matched this role. Component-based responsibilities and suggestions are shown separately where source evidence exists.')},'role_components':role_components,'role_responsibilities':role_responsibilities,'suggested_tasks':suggested_tasks,'confirmed_tasks':role_tasks,'data_limitations': 'The connected sources contain limited project information; role-specific conclusions may therefore be incomplete.' if len(nodes) <= 3 else '', 'limitations':['Role relevance is not authorisation. This prototype only supports public source material.','Task ownership is inferred from task wording and the selected role profile; confirm assignments with the project owner.','Recommended improvements are not represented as existing project tasks.','An administrator must review and approve before sharing.']}
    if repo_ctx:
        r=repo_ctx['repository']; _out['repo_context']=repo_ctx; _out['project_overview']+=f" Repository {r['name']}@{(r['commit'] or 'unknown')[:10]} ({r['branch']}): {r['stats']['indexed']} of {r['stats']['discovered']} files indexed, {r['stats']['metadata_only']} metadata-only, {r['stats']['excluded']} excluded, {r['stats']['failed']} failed."
    return _out

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
    if p.get('role_components'):
        story.append(Paragraph('Project components relevant to this role',styles['Heading2']))
        for c in p['role_components']:
            story.append(Paragraph(escape(c['component']+' · '+c['source_file']+' · matched: '+', '.join(c['matched_terms'])),styles['BodyText']))
            story.append(Paragraph(escape(c['evidence'][:500]),styles['Italic']))
    if p.get('role_responsibilities'):
        story.append(Paragraph('Role-specific responsibilities',styles['Heading2']))
        for item in p['role_responsibilities']:
            story.append(Paragraph(escape(item['title']+' — '+item['description']),styles['BodyText']))
            story.append(Paragraph(escape('Why this role: '+item['why_this_role']+' · Source: '+', '.join(item['source_files'])),styles['Italic']))
    if p.get('suggested_tasks'):
        story.append(Paragraph('Suggested improvements — not confirmed tasks',styles['Heading2']))
        for item in p['suggested_tasks']:
            story.append(Paragraph(escape(item['title']+' — '+item['description']),styles['BodyText']))
            story.append(Paragraph(escape('Suggested only · Source: '+', '.join(item['source_files'])),styles['Italic']))
    if p.get('confirmed_tasks'):
        story.append(Paragraph('Confirmed source-backed tasks',styles['Heading2']))
        for item in p['confirmed_tasks']:
            story.append(Paragraph(escape(item['title']+' · '+str(item.get('task_status','UNKNOWN'))),styles['BodyText']))
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
