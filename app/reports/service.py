"""Dynamic evidence-grounded report generation from the current KNOWBRIDGE corpus."""
import uuid, re
from datetime import datetime
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from ..config import DATA
from ..debt import build_report
from ..graph import knowledge_nodes
from ..pipeline import build
from ..search import search

def generate(db, request, conversation_id=None, fmt="pdf"):
    rid=uuid.uuid4().hex[:12]; title=request.strip().strip('"') or "Project Knowledge Report"
    low=title.lower(); rep=build_report(build(db)); issues=rep.get("issues",[]); nodes=knowledge_nodes(build(db)); chunks=db.chunks()
    terms=re.findall(r"[a-z0-9_+-]{3,}",low)
    hits,_=search(chunks,title,12)
    if any(x in low for x in ("risk","blocker")): issues=[i for i in issues if any(w in (i.get('category','')+i.get('title','')).lower() for w in ('risk','block','mitigat'))]
    elif any(x in low for x in ("owner","task")): issues=[i for i in issues if any(w in (i.get('category','')+i.get('title','')).lower() for w in ('owner','task'))]
    elif any(x in low for x in ("decision",)): nodes=[n for n in nodes if n.get('type')=='decision']
    elif any(x in low for x in ("dependency", "depend")): nodes=[n for n in nodes if n.get('type') in ('task','risk')]
    # Custom requests retain retrieved context and full knowledge-debt section rather than fabricate a template answer.
    path=DATA/"reports"/f"{rid}.pdf"; path.parent.mkdir(parents=True,exist_ok=True)
    _pdf(path,title,db,issues,nodes,hits,rep)
    md=_markdown(title,db,issues,nodes,hits,rep)
    db.save_report(rid,conversation_id,title,"pdf",str(path),{"issues":len(issues),"evidence":len(hits)})
    return {"report_id":rid,"title":title,"format":"pdf","download_url":f"/reports/{rid}/download","markdown":md,"summary":f"Generated from {len(db.documents())} source documents, with {len(issues)} relevant knowledge-debt findings and {len(hits)} retrieved evidence passages."}

def _markdown(title,db,issues,nodes,hits,rep):
    out=[f"# {title}",f"Generated: {datetime.now().astimezone().strftime('%d %b %Y, %H:%M %Z')}",f"Sources: {len(db.documents())} documents · {len(db.chunks())} chunks · {len(db.claims())} claims","","## Knowledge debt findings"]
    out += [f"- **{i.get('severity')} — {i.get('title')}**: {i.get('description')}" for i in issues] or ["No matching findings were recorded."]
    out += ["","## Relevant project entities"]+[f"- **{n.get('type')} — {n.get('name')}** ({n.get('status','unlabelled')})" for n in nodes[:40]]
    out += ["","## Supporting source passages"]+[f"- `{h.get('chunk_id')}` · {h.get('section') or 'Source'}: {h.get('text','')[:500]}" for h in hits] or ["- No relevant passages retrieved."]
    out += ["","## Limitations","This report reflects only the currently connected and successfully ingested sources. Missing evidence is not proof that an activity or decision does not exist."]
    return "\n".join(out)

def _pdf(path,title,db,issues,nodes,hits,rep):
    styles=getSampleStyleSheet(); styles.add(ParagraphStyle(name='Small',parent=styles['BodyText'],fontSize=8,leading=11,textColor=colors.HexColor('#475569')))
    doc=SimpleDocTemplate(str(path),pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm)
    story=[Paragraph('KNOWBRIDGE · PROJECT INTELLIGENCE',styles['Small']),Paragraph(title,styles['Title']),Paragraph(f"Generated {datetime.now().astimezone().strftime('%d %b %Y, %H:%M %Z')}",styles['Small']),Spacer(1,8)]
    story += [Paragraph('Corpus snapshot',styles['Heading2']),Paragraph(f"{len(db.documents())} documents · {len(db.chunks())} chunks · {len(db.claims())} extracted claims",styles['BodyText']),Spacer(1,8)]
    story.append(Paragraph('Knowledge debt findings',styles['Heading2']))
    if issues:
        data=[['Severity','Finding','Description']]+[[i.get('severity',''),i.get('title',''),i.get('description','')] for i in issues[:80]]
        t=Table([[Paragraph(str(c),styles['Small']) for c in row] for row in data],colWidths=[22*mm,48*mm,105*mm],repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#172554')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.35,colors.HexColor('#cbd5e1')),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f1f5f9')]),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)])); story += [t,Spacer(1,10)]
    else: story.append(Paragraph('No matching knowledge-debt findings were recorded in the current corpus.',styles['BodyText']))
    story.append(Paragraph('Relevant project entities',styles['Heading2']))
    for n in nodes[:50]: story.append(Paragraph(f"<b>{n.get('type','').title()}: {n.get('name','')}</b> — {n.get('status','')} · {n.get('description','')}",styles['Small']))
    story.append(Paragraph('Supporting evidence',styles['Heading2']))
    for h in hits:
        story.append(Paragraph(f"<b>{h.get('section') or h.get('chunk_id')}</b> · Source chunk {h.get('chunk_id')}: {h.get('text','')[:1000]}",styles['Small'])); story.append(Spacer(1,4))
    story += [Spacer(1,8),Paragraph('Scope and limitations',styles['Heading2']),Paragraph('This report reflects only the currently connected and successfully ingested sources. Findings are derived from available records; missing evidence is not proof that an activity or decision does not exist. Verification labels describe source support, not a probability.',styles['Small'])]
    def footer(canvas,doc):
        canvas.saveState(); canvas.setFont('Helvetica',8); canvas.setFillColor(colors.HexColor('#64748b')); canvas.drawString(18*mm,10*mm,'KNOWBRIDGE · Evidence-linked project knowledge'); canvas.drawRightString(A4[0]-18*mm,10*mm,f'Page {doc.page}'); canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
