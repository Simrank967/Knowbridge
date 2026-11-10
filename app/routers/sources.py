"""Stage 1 (continued): connect GitHub repos / web pages / file links, alone or together with uploaded files."""
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..config import DATA, MAX_UPLOAD_MB
from ..db import DB
from ..deps import get_db
from ..extraction import get_extractor
from ..ingestion import SUPPORTED, IngestError, ingest_file
from ..pipeline import extract_all
from ..services import knowledge_summary
from ..sources import connect

log = logging.getLogger("knowbridge.api.sources")
router = APIRouter(tags=["1 · Documents"])


class ConnectRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=10, description="GitHub repo URLs, web pages or direct links to PDF/DOCX/CSV/MD/TXT files.",
                            examples=[["https://github.com/owner/repo", "https://example.com/project-plan.pdf"]])
    extract: bool = Field(True, description="Also run extraction so the debt report is ready immediately.")


def _finish(db, ok, errors, notes, extract, extractor="auto"):
    for r in ok: db.add_document(r)
    ext = None
    if extract and db.documents():
        claims = extract_all(db, get_extractor("rules" if extractor == "rules" else None, sink=db.log_llm))
        ext = {"claims_extracted": len(claims), "by_kind": dict(Counter(c["kind"] for c in claims)), "extractor": claims[0]["extractor"] if claims else None}
    return {"documents": [r["doc"] for r in ok], "errors": errors, "notes": notes, "extraction": ext,
            "summary": knowledge_summary(db) if ext else None,
            "message": "ingested" + (" and extracted. Next: GET /knowledge/brief or /knowledge/debt-report" if ext else "; call POST /knowledge/extract next")}


@router.post("/sources/connect", summary="Connect GitHub / web / file links as project sources")
def connect_sources(body: ConnectRequest, db: DB = Depends(get_db)):
    """Fetches each link (**GitHub**: README, `docs/*.md`, issues & PRs with assignees, state and `depends on #n` links; **web page**: main text;
    **file link**: PDF/DOCX/CSV/MD/TXT) and stores it with the same document/chunk IDs and provenance as uploads, plus the source URL.
    Private/internal addresses are blocked. Set `GITHUB_TOKEN` for private repos or higher rate limits. A failing link is reported in `errors` and never blocks the others."""
    ok, errors, notes = connect(body.urls)
    if not ok and errors: raise HTTPException(422, detail="; ".join(f"{e['url']}: {e['error']}" for e in errors))
    return _finish(db, ok, errors, notes, body.extract)


@router.post("/projects/ingest", summary="Step 1 in one call: files and/or links, then extraction")
async def ingest_project(files: list[UploadFile] = File(default=[], description="PDF, DOCX, TXT, CSV or Markdown files."),
                         urls: str = Form("", description="Links separated by new lines or commas."),
                         extract: bool = Form(True), db: DB = Depends(get_db)):
    ok, errors = [], []
    for f in files:
        name = Path(f.filename or "upload").name; data = await f.read()
        if Path(name).suffix.lower() not in SUPPORTED: errors.append({"url": name, "error": "unsupported file type"}); continue
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024: errors.append({"url": name, "error": f"exceeds {MAX_UPLOAD_MB} MB"}); continue
        dest = DATA / "uploads" / name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
        try: ok.append(ingest_file(dest))
        except IngestError as e: errors.append({"url": name, "error": str(e)})
    links = [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]
    notes = []
    if links:
        o2, e2, notes = connect(links[:10]); ok += o2; errors += e2
    if not ok and not errors: raise HTTPException(422, detail="provide at least one file or link")
    if not ok: raise HTTPException(422, detail="; ".join(f"{e['url']}: {e['error']}" for e in errors))
    return _finish(db, ok, errors, notes, extract)
