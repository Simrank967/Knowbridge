"""Stage 1: document ingestion."""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..config import DATA, MAX_UPLOAD_MB
from ..db import DB
from ..deps import get_db
from ..ingestion import SUPPORTED, IngestError, ingest_file
from ..schemas import DocumentList, ErrorResponse, UploadResponse

log = logging.getLogger("knowbridge.api.documents")
router = APIRouter(prefix="/documents", tags=["1 · Documents"])


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Upload and chunk a project document",
    responses={413: {"model": ErrorResponse, "description": "File larger than the configured upload limit."},
               422: {"model": ErrorResponse, "description": "File is empty, corrupted or an unsupported type."}},
)
async def upload(file: UploadFile = File(..., description="PDF, DOCX, TXT, CSV or Markdown file."), db: DB = Depends(get_db)):
    """Ingest one document.

    The file is text-extracted (PyMuPDF for PDF, python-docx for DOCX, pandas for CSV), split into chunks that
    keep their **page or section**, and stored with a unique document ID and chunk IDs.
    Uploading the same file again replaces its earlier chunks and claims.

    Supported types: `.pdf` `.docx` `.txt` `.csv` `.md`. Next step: `POST /knowledge/extract`.
    """
    name = Path(file.filename or "upload").name
    if Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(422, detail=f"unsupported file type '{Path(name).suffix}'. Supported: {', '.join(sorted(SUPPORTED))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, detail=f"file exceeds the {MAX_UPLOAD_MB} MB upload limit")
    dest = DATA / "uploads" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    try:
        res = ingest_file(dest)
    except IngestError as e:
        log.warning("rejected %s: %s", name, e)
        raise HTTPException(422, detail=str(e))
    db.add_document(res)
    log.info("ingested %s -> %s (%d chunks)", name, res["doc"]["id"], res["doc"]["n_chunks"])
    return {**res["doc"], "message": "uploaded and chunked; call POST /knowledge/extract next"}


@router.get("", response_model=DocumentList, summary="List ingested documents")
def list_documents(db: DB = Depends(get_db)):
    """Every ingested document with its source type, document date and chunk count."""
    docs = db.documents()
    return {"count": len(docs), "documents": docs}
