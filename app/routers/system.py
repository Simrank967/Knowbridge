"""Health, one-click synthetic demo and reset."""
import logging
import time
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

from ..config import SAMPLE_DIR, VERSION
from ..db import DB
from ..deps import get_db
from ..extraction import get_extractor
from ..ingestion import ingest_folder
from ..pipeline import extract_all
from ..schemas import DemoLoadResponse, ErrorResponse, HealthResponse, ResetResponse
from ..services import knowledge_summary

log = logging.getLogger("knowbridge.api.system")
router = APIRouter(tags=["Demo & Operations"])
STARTED = time.time()


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health(db: DB = Depends(get_db)):
    """Liveness probe plus a few facts useful when debugging a demo: how many documents/claims are loaded and which extractor is active."""
    c = db.counts()
    return {"status": "ok", "service": "knowbridge-reconstruction-engine", "version": VERSION,
            "extractor_mode": get_extractor().name, "documents_loaded": c["documents"], "claims_extracted": c["claims"],
            "uptime_seconds": round(time.time() - STARTED, 1)}


@router.post(
    "/demo/load", response_model=DemoLoadResponse, summary="Load the synthetic demo project and run the full pipeline",
    responses={500: {"model": ErrorResponse, "description": "Bundled sample data is missing."}},
)
def load_demo(db: DB = Depends(get_db)):
    """One click for judges: clears existing data, ingests the **synthetic** sample project (meeting notes `.md`, task allocation `.csv`,
    technical decision `.docx`, progress report `.pdf`), runs extraction with the rule-based extractor and returns the headline numbers.

    The demo data deliberately contains an unowned task, an undocumented decision, a conflicting task status, a dependency and unresolved
    questions, so the Knowledge Debt detector has real problems to find. The findings are **derived from the files**, not hard-coded.
    """
    if not SAMPLE_DIR.exists():
        raise HTTPException(500, detail="sample_data folder not found")
    db.reset()
    ok, errors = ingest_folder(SAMPLE_DIR)
    for r in ok:
        db.add_document(r)
    claims = extract_all(db, get_extractor("rules"))
    log.info("demo loaded: %d documents, %d claims", len(ok), len(claims))
    return {"message": "Synthetic demo project loaded. Next: GET /knowledge/debt-report", "dataset": "synthetic demo data",
            "documents": [r["doc"] for r in ok], "errors": [e if isinstance(e, dict) else {"error": str(e)} for e in errors],
            "extraction": {"claims_extracted": len(claims), "by_kind": dict(Counter(c["kind"] for c in claims)),
                           "extractor": claims[0]["extractor"] if claims else None,
                           "documents_processed": len(ok), "chunks_processed": len(db.chunks())},
            "summary": knowledge_summary(db)}


@router.delete("/demo/reset", response_model=ResetResponse, summary="Delete all documents, chunks and claims")
def reset(db: DB = Depends(get_db)):
    """Start from a clean slate (e.g. before uploading your own documents)."""
    n = db.reset()
    return {"message": "all documents, chunks and claims removed", "documents_removed": n}
