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
from ..sources import connect, ingest_uploaded_archive, SourceError, LAST_SCANS
from .. import repo_intel

log = logging.getLogger("knowbridge.api.sources")
router = APIRouter(tags=["1 · Documents"])


class ConnectRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=10, description="GitHub repo URLs, web pages or direct links to PDF/DOCX/CSV/MD/TXT files.",
                            examples=[["https://github.com/owner/repo", "https://example.com/project-plan.pdf"]])
    extract: bool = Field(True, description="Also run extraction so the debt report is ready immediately.")


def _finish(db, ok, errors, notes, extract, extractor="auto"):
    for r in ok: db.add_document(r)
    scans = list(LAST_SCANS); LAST_SCANS.clear()
    for sc in scans: repo_intel.persist_scan(db, sc["repo"], sc["branch"], sc["commit"], sc["source"], sc["truncated"], sc["stats"], sc["rows"], sc["notes"])
    failed = sum(sc["stats"]["failed"] for sc in scans)
    ext = None
    if extract and db.documents():
        claims = extract_all(db, get_extractor("rules" if extractor == "rules" else None, sink=db.log_llm))
        ext = {"claims_extracted": len(claims), "by_kind": dict(Counter(c["kind"] for c in claims)), "extractor": claims[0]["extractor"] if claims else None}
    return {"documents": [r["doc"] for r in ok], "errors": errors, "notes": notes, "extraction": ext,
            "summary": knowledge_summary(db) if ext else None,
            "repositories": [{"repository": sc["repo"], "branch": sc["branch"], "commit": sc["commit"], "source": sc["source"], "truncated": sc["truncated"], "stats": sc["stats"]} for sc in scans],
            "message": ("PARTIAL: some repository files failed processing; see GET /repo/manifest?status=FAILED. " if failed else "") + "ingested" + (" and extracted. Next: GET /knowledge/brief or /knowledge/debt-report" if ext else "; call POST /knowledge/extract next")}


@router.post("/sources/connect", summary="Connect GitHub / web / file links as project sources")
def connect_sources(body: ConnectRequest, db: DB = Depends(get_db)):
    """Fetches each link (**GitHub**: README, `docs/*.md`, issues & PRs with assignees, state and `depends on #n` links; **web page**: main text;
    **file link**: PDF/DOCX/CSV/MD/TXT) and stores it with the same document/chunk IDs and provenance as uploads, plus the source URL.
    Private/internal addresses are blocked. Set `GITHUB_TOKEN` for private repos or higher rate limits. A failing link is reported in `errors` and never blocks the others."""
    ok, errors, notes = connect(body.urls, db)
    if not ok and errors: raise HTTPException(422, detail="; ".join(f"{e['url']}: {e['error']}" for e in errors))
    return _finish(db, ok, errors, notes, body.extract)


@router.post("/projects/ingest", summary="Step 1 in one call: files and/or links, then extraction")
async def ingest_project(files: list[UploadFile] = File(default=[], description="PDF, DOCX, TXT, CSV or Markdown files."),
                         urls: str = Form("", description="Links separated by new lines or commas."),
                         extract: bool = Form(True), db: DB = Depends(get_db)):
    ok, errors = [], []
    for f in files:
        name = Path(f.filename or "upload").name; data = await f.read()
        if Path(name).suffix.lower() not in SUPPORTED and Path(name).suffix.lower() != ".zip": errors.append({"url": name, "error": "unsupported file type"}); continue
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024: errors.append({"url": name, "error": f"exceeds {MAX_UPLOAD_MB} MB"}); continue
        if Path(name).suffix.lower() == ".zip":
            try:
                archive_docs, skipped = ingest_uploaded_archive(data, name)
                ok.extend(archive_docs)
                if skipped: errors.append({"url": name, "error": f"{skipped} unsupported, oversized or unreadable archive entries skipped"})
            except SourceError as e: errors.append({"url": name, "error": str(e)})
            continue
        dest = DATA / "uploads" / name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
        try: ok.append(ingest_file(dest))
        except IngestError as e: errors.append({"url": name, "error": str(e)})
    links = [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]
    notes = []
    if links:
        o2, e2, notes = connect(links[:10], db); ok += o2; errors += e2
    if not ok and not errors: raise HTTPException(422, detail="provide at least one file or link")
    if not ok: raise HTTPException(422, detail="; ".join(f"{e['url']}: {e['error']}" for e in errors))
    return _finish(db, ok, errors, notes, extract)


@router.get("/repo/manifest", summary="Per-file ingestion manifest and statistics for the connected repository")
def repo_manifest(repo: Optional[str] = None, status: Optional[str] = None, limit: int = 1000, db: DB = Depends(get_db)):
    m = repo_intel.manifest(db, repo, status, limit)
    if not m: raise HTTPException(404, "no repository has been connected")
    return m

@router.get("/repo/summary", summary="Evidence-backed project understanding summary")
def repo_summary(repo: Optional[str] = None, db: DB = Depends(get_db)):
    s = repo_intel.project_summary(db, repo)
    if not s: raise HTTPException(404, "no repository has been connected")
    return s

@router.get("/repo/file", summary="Open source evidence (file lines) from the indexed repository")
def repo_file(path: str, line_start: int = 1, line_end: Optional[int] = None, db: DB = Depends(get_db)):
    M = repo_intel.load(db)
    if not M or path not in M["files"]: raise HTTPException(404, "file not in the connected repository")
    L = repo_intel.source_lines(M, path); a = max(1, line_start); b = line_end or a + 40
    return {"path": path, "commit": M["commit"], "status": M["files"][path]["status"], "lines": [{"n": n, "text": L[n]} for n in range(a, min(b, a + 200) + 1) if n in L]}
