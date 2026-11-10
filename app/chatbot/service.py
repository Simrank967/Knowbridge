"""Evidence-first conversational interface over KNOWBRIDGE's existing knowledge store."""
import re, uuid
from ..search import search
from ..debt import build_report
from ..graph import knowledge_nodes
from ..pipeline import build
from ..services import knowledge_summary

REPORT_WORDS = ("report", "pdf", "export", "document")

def answer(db, question: str, project_id=None):
    q=question.strip(); low=q.lower(); docs=db.documents(); chunks=db.chunks(); claims=db.claims()
    if not docs:
        return {"answer":"I don't have any project sources yet. Upload documents or connect a GitHub repository, then ask me again.","evidence":[],"verification":"MISSING","suggestions":["Connect a GitHub repository","Upload project documents"]}
    if any(x in low for x in ("knowledge gap","knowledge debt","missing owner","conflict","conflicting","risk","blocker","unresolved")):
        rep=build_report(build(db)); issues=rep.get("issues",[])
        filtered=[i for i in issues if any(t in (i.get("category","")+" "+i.get("title","")+" "+i.get("description","")).lower() for t in re.findall(r"[a-z]{4,}",low))]
        shown=filtered or issues
        lines=[f"I found **{len(shown)}** relevant knowledge-debt finding(s)."]
        for i in shown[:12]:
            lines.append(f"- **{i.get('severity','?')} · {i.get('title','Finding')}** — {i.get('description','')}")
        evidence=[]
        for i in shown[:12]:
            for e in i.get("evidence",[])[:2]: evidence.append(e)
        return {"answer":"\n".join(lines) if shown else "No matching knowledge-debt findings were detected in the currently ingested sources.","evidence":_dedupe_evidence(evidence),"verification":"CONFLICTING" if any(i.get("category")=="Conflict" for i in shown) else "VERIFIED","suggestions":["Generate a knowledge debt report PDF","Show all open tasks","Summarise project decisions"]}
    if any(x in low for x in ("summari", "overview", "what do you know", "project about", "architecture", "technologies", "components")):
        sm=knowledge_summary(db)
        hits,_=search(chunks, q+" project overview architecture components technologies decisions tasks", 7)
        intro=f"Your connected project currently has **{sm['documents']} source documents**, **{sm['chunks']} text chunks** and **{sm['claims']} extracted claims**."
        snippets=[f"- **{h.get('section') or h['chunk_id']}**: {_clip(h['text'],360)}" for h in hits[:5]]
        return {"answer":intro+("\n\nRelevant project context:\n"+"\n".join(snippets) if snippets else "\n\nI could not retrieve sufficiently relevant passages for a deeper summary."),"evidence":hits,"verification":"VERIFIED" if hits else "MISSING","suggestions":["What are the biggest risks?","Which tasks have no owner?","Generate a project overview PDF"]}
    hits,method=search(chunks,q,7)
    if not hits:
        return {"answer":"I couldn't find sufficient evidence in the connected project sources to answer this confidently. Try different wording or connect more project documents.","evidence":[],"verification":"MISSING","retrieval_method":method,"suggestions":["Show project overview","List available sources","Find knowledge gaps"]}
    # Only quote/restate what retrieval returned. Do not infer an unsupported answer.
    lines=["I found the following relevant evidence in your project sources:"]
    for h in hits[:5]: lines.append(f"- {_clip(h['text'],420)}")
    return {"answer":"\n\n".join(lines),"evidence":hits,"verification":"VERIFIED","retrieval_method":method,"suggestions":["Explain this in more detail","Find related decisions","Generate a report from these findings"]}

def _clip(s,n): return re.sub(r"\s+"," ",s).strip()[:n]+("…" if len(s)>n else "")
def _dedupe_evidence(items):
    out=[]; seen=set()
    for e in items:
        key=(e.get("chunk_id"),e.get("text"))
        if key not in seen: seen.add(key); out.append(e)
    return out[:20]
