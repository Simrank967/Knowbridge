"""Stages 2-5: extraction, entities, graph, evidence and the Knowledge Debt report."""
import logging
import tempfile
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from ..config import DATA
from ..db import DB
from ..debt import build_report
from ..deps import get_db
from ..extraction import get_extractor
from ..graph import export, graph_to_json, knowledge_nodes
from ..pipeline import build, extract_all
from ..schemas import (DebtReport, EntityList, ErrorResponse, EvidenceResponse, ExportFormat, ExtractorChoice,
                       ExtractResponse, GraphResponse, KnowledgeSummary, Severity, EntityType, VerificationStatus)
from ..narrative import debt_markdown, debt_text, narrate, summary_markdown
from ..search import search
from ..services import knowledge_summary, trace_entity

log = logging.getLogger("knowbridge.api.knowledge")
router = APIRouter(prefix="/knowledge")

ENTITY_FIELDS = ("id", "type", "name", "description", "status", "attr_status", "attrs", "source_documents",
                 "timestamp", "conflicts", "stale_reasons")


@router.post(
    "/extract", tags=["2 · Extraction"], response_model=ExtractResponse,
    summary="Run NLP knowledge extraction on all uploaded documents",
    responses={409: {"model": ErrorResponse, "description": "No documents uploaded yet."}},
)
def extract(
    extractor: ExtractorChoice = Query("auto", description="`auto` uses the configured LLM provider when credentials exist and falls back "
                                                           "to rules per chunk on any error. `rules` forces the offline rule-based extractor."),
    db: DB = Depends(get_db),
):
    """Extract structured claims from every stored chunk.

    Each claim is `{kind, fields, evidence, certainty}` where `evidence` is text copied verbatim from the source chunk and
    `certainty` is `explicit` or `hedged`. Extracted: **people & roles, tasks, ownership, decisions, rationale, dependencies,
    risks & blockers, deadlines & milestones, unresolved questions, resources**. Nothing is invented: anything not present
    in the text is simply left out and later reported as *MISSING* by the Knowledge Debt detector.
    """
    docs = db.documents()
    if not docs:
        raise HTTPException(409, detail="no documents uploaded yet")
    claims = extract_all(db, get_extractor("rules" if extractor == "rules" else None, sink=db.log_llm))
    return {"claims_extracted": len(claims), "by_kind": dict(Counter(c["kind"] for c in claims)),
            "extractor": claims[0]["extractor"] if claims else None,
            "documents_processed": len(docs), "chunks_processed": len(db.chunks())}


@router.get("/summary", tags=["Demo & Operations"], response_model=KnowledgeSummary, summary="Executive overview (counts only)")
def summary(format: str = Query("json", pattern="^(json|markdown)$", description="`json` for apps, `markdown` for people."), db: DB = Depends(get_db)):
    """Single-call overview for dashboards: entity counts, verification-label breakdown, relationship counts, Knowledge Debt totals
    and a counts-based handover-readiness verdict. Intended for the UI module to consume."""
    sm = knowledge_summary(db)
    return PlainTextResponse(summary_markdown(sm), media_type="text/markdown") if format == "markdown" else sm


@router.get("/entities", tags=["2 · Extraction"], response_model=EntityList, summary="List extracted entities")
def entities(
    type: Optional[EntityType] = Query(None, description="Only return entities of this type."),
    status: Optional[VerificationStatus] = Query(None, description="Only return entities with this verification label."),
    q: Optional[str] = Query(None, min_length=2, description="Case-insensitive text search in entity names."),
    db: DB = Depends(get_db),
):
    """People, tasks, decisions, risks, milestones, resources and open questions, each with a verification label,
    per-attribute labels (e.g. `owner: MISSING`), any detected conflicts and the number of supporting evidence items."""
    items = []
    for d in knowledge_nodes(build(db)):
        if type and d["type"] != type or status and d["status"] != status or q and q.lower() not in d["name"].lower():
            continue
        items.append({k: d[k] for k in ENTITY_FIELDS} | {"evidence_count": len(d["evidence"])})
    return {"count": len(items), "entities": items}


@router.get("/graph", tags=["3 · Knowledge Graph"], response_model=GraphResponse, summary="Get the Knowledge Continuity Graph")
def graph(export_files: bool = Query(False, description="Also write `knowledge_graph.json` and `.graphml` to `data/exports/`."),
          db: DB = Depends(get_db)):
    """Nodes (people, tasks, decisions, risks, milestones, resources, questions, evidence) and typed relationships
    (`ASSIGNED_TO`, `DEPENDS_ON`, `BLOCKS`, `DECIDED_BY`, `SUPPORTED_BY`, `RELATED_TO`, `SUPERSEDES`).
    Every node and edge carries its ID, type, description, source documents, supporting text, timestamp and verification status."""
    G = build(db)
    out = graph_to_json(G)
    if export_files:
        out["exported"] = export(G, DATA / "exports")
    return out


@router.get("/graph/download", tags=["3 · Knowledge Graph"], summary="Download the graph as a JSON or GraphML file",
            response_class=FileResponse, responses={200: {"content": {"application/json": {}, "application/xml": {}}}})
def graph_download(format: ExportFormat = Query("json", description="`json` for apps, `graphml` for Gephi / yEd / Cytoscape."),
                   db: DB = Depends(get_db)):
    folder = DATA / "exports"
    paths = export(build(db), folder)
    path = paths[format]
    return FileResponse(path, filename=f"knowbridge_graph.{format}",
                        media_type="application/json" if format == "json" else "application/xml")


@router.get(
    "/evidence", tags=["4 · Evidence"], response_model=EvidenceResponse,
    summary="Trace facts back to their source evidence",
    responses={404: {"model": ErrorResponse, "description": "Unknown entity ID."}},
)
def evidence(
    entity_id: Optional[str] = Query(None, description="Entity ID from `/knowledge/entities`, e.g. `task:t-103`. Returns that entity's evidence and relationships."),
    q: Optional[str] = Query(None, min_length=2, description="Keyword search across the raw source chunks."),
    db: DB = Depends(get_db),
):
    """Three modes:

    * **`entity_id`**: the verbatim supporting passages (document, chunk, timestamp) for one entity plus the evidence for each of its relationships.
    * **`q`**: keyword search over the original document chunks.
    * *neither*: the evidence-linked fact list for every entity.
    """
    G = build(db)
    if entity_id:
        if entity_id not in G:
            raise HTTPException(404, detail="unknown entity")
        d = G.nodes[entity_id]
        return {"entity": {k: d[k] for k in ("id", "type", "name", "status", "attr_status")}, "evidence": d["evidence"],
                "relationships": [{"type": k, "target": v, "status": e["status"], "evidence": e["evidence"]}
                                  for _, v, k, e in G.out_edges(entity_id, keys=True, data=True) if k != "SUPPORTED_BY"]}
    if q:
        ql = q.lower()
        matches = [{"chunk_id": c["id"], "doc_id": c["doc_id"], "section": c["section"], "page": c["page"], "text": c["text"]}
                   for c in db.chunks() if ql in c["text"].lower()]
        return {"query": q, "count": len(matches), "matches": matches}
    facts = [{"id": d["id"], "type": d["type"], "name": d["name"], "status": d["status"], "evidence": d["evidence"]}
             for d in knowledge_nodes(G)]
    return {"count": len(facts), "facts": facts}


@router.get("/debt-report", tags=["5 · Knowledge Debt"], response_model=DebtReport, summary="Generate the Knowledge Debt Report")
def debt_report(
    severity: Optional[Severity] = Query(None, description="Only return issues of this severity."),
    category: Optional[str] = Query(None, description="Only return issues of this category, e.g. `Missing owner`, `Conflict`, `Missing rationale`."),
    format: str = Query("json", pattern="^(json|markdown|text)$", description="`json` (default) for apps; `markdown`/`text` render the same findings for people."),
    db: DB = Depends(get_db),
):
    """Everything a team should fix **before** a handover: tasks without owners, decisions without rationale, dependencies with
    missing information, risks without mitigation, unresolved questions, conflicting task statuses, outdated information and
    items with insufficient evidence.

    Each issue has a title, category, description, severity (High / Medium / Low), related entity, supporting evidence and the
    specific information a human should verify. Findings are derived from the documents; none are hard-coded.
    """
    rep = build_report(build(db))
    issues = [i for i in rep["issues"] if (not severity or i["severity"] == severity)
              and (not category or i["category"].lower() == category.lower())]
    if format != "json":
        shown = {**rep, "issues": issues, "summary": {**rep["summary"], "total": len(issues)}}
        return PlainTextResponse(debt_markdown(shown) if format == "markdown" else debt_text(shown), media_type="text/markdown" if format == "markdown" else "text/plain")
    if not (severity or category):
        return {**rep, "filters_applied": {}}
    summary_ = {"total": len(issues), "by_severity": dict(Counter(i["severity"] for i in issues)),
                "by_category": dict(Counter(i["category"] for i in issues))}
    return {"summary": summary_, "issues": issues,
            "filters_applied": {k: v for k, v in (("severity", severity), ("category", category)) if v}}


@router.get("/brief", tags=["5 · Knowledge Debt"], summary="Plain-English briefing of the knowledge gaps")
def brief(db: DB = Depends(get_db)):
    """Short readable narrative plus the readable report. Built from the structured findings; if an LLM writes the narrative, every issue ID it cites
    is validated against the report and the template text is used otherwise. Raw LLM JSON is kept in the backend (SQLite), never returned."""
    rep = build_report(build(db)); text, by = narrate(rep, db.log_llm)
    return {"narrative": text, "generated_by": by, "readiness": knowledge_summary(db)["readiness"], "report_markdown": debt_markdown(rep)}


@router.get("/trace/{entity_id}", tags=["4 · Evidence"], summary="Trace one fact to document, passage and exact character span",
            responses={404: {"model": ErrorResponse, "description": "Unknown entity ID."}})
def trace(entity_id: str, db: DB = Depends(get_db)):
    """fact → chunk → document (page/section/source URL) → quote with character offsets, and `verbatim_in_source` confirming the quote exists in the stored text."""
    G = build(db)
    if entity_id not in G: raise HTTPException(404, detail="unknown entity")
    return trace_entity(G, db, entity_id)


@router.get("/ask", tags=["4 · Evidence"], summary="Ask a question; get the supporting passages (no generated answer)")
def ask(q: str = Query(..., min_length=3, description="Natural-language question about the project."), k: int = Query(5, ge=1, le=10), db: DB = Depends(get_db)):
    """Semantic-style retrieval (TF-IDF by default; sentence-transformers if `KNOWBRIDGE_EMBEDDINGS=1`). Returns only passages from the sources;
    when nothing relevant exists it says so instead of guessing. `relevance` is a ranking score, not a confidence."""
    hits, method = search(db.chunks(), q, k)
    return {"question": q, "method": method, "count": len(hits), "passages": hits,
            "message": None if hits else "No supporting evidence found in the project sources."}
