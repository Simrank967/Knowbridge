"""A. Document ingestion: text extraction + chunking with provenance (doc id, chunk id, section, page)."""
import hashlib, logging, re
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
log = logging.getLogger("knowbridge.ingest")
SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".csv": "csv", ".md": "markdown"}
MAX_CHARS = 900
DATE_RE = re.compile(r"(?:date|last updated|updated)\s*:\s*(\d{4}-\d{2}-\d{2})", re.I)

class IngestError(Exception):
    """Raised for empty, corrupted or unsupported files (handled gracefully by callers)."""

def _chunk_lines(lines, section, page):
    out, cur, n = [], [], 0
    for ln in lines:
        if n + len(ln) > MAX_CHARS and cur:
            out.append(("\n".join(cur), section, page)); cur, n = [], 0
        cur.append(ln); n += len(ln) + 1
    if cur: out.append(("\n".join(cur), section, page))
    return out

def _chunk_text(text, page=None):
    """Split on markdown headings (kept as section metadata), then by size."""
    out, sec, lines = [], None, []
    for ln in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", ln)
        if m:
            out += _chunk_lines(lines, sec, page); lines, sec = [], m.group(1).strip(); continue
        if ln.strip(): lines.append(ln.rstrip())
    return out + _chunk_lines(lines, sec, page)

def _read_pdf(p):
    try: import pymupdf as fitz
    except ImportError:
        try: import fitz
        except ImportError: raise IngestError("PDF support needs PyMuPDF (pip install pymupdf)")
    try:
        with fitz.open(str(p)) as pdf:
            return [(i + 1, pg.get_text()) for i, pg in enumerate(pdf)]
    except Exception as e:
        raise IngestError(f"corrupted or unreadable PDF: {e}")

def _read_docx(p):
    import docx
    try:
        d = docx.Document(str(p))
    except Exception as e:
        raise IngestError(f"corrupted or unreadable DOCX: {e}")
    lines = []
    for para in d.paragraphs:
        t = para.text.strip()
        if t: lines.append("# " + t if para.style.name.lower().startswith(("heading", "title")) else t)
    for tb in d.tables:
        for row in tb.rows: lines.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(lines)

def ingest_file(path):
    p = Path(path); ext = p.suffix.lower()
    if ext not in SUPPORTED: raise IngestError(f"unsupported file type '{ext}' ({p.name})")
    if not p.exists(): raise IngestError(f"file not found: {p.name}")
    raw = p.read_bytes()
    if not raw.strip(): raise IngestError(f"empty file: {p.name}")
    doc_id = "doc-" + hashlib.sha1(raw).hexdigest()[:8]
    parts, row_dates = [], []
    if ext == ".pdf":
        pages = _read_pdf(p); full = "\n".join(t for _, t in pages)
        for pg, t in pages: parts += [(a, b, c, None) for a, b, c in _chunk_text(t, pg)]
    elif ext == ".csv":
        try: df = pd.read_csv(p, dtype=str, keep_default_na=False)
        except Exception as e: raise IngestError(f"unreadable CSV: {e}")
        if df.empty: raise IngestError(f"CSV has no data rows: {p.name}")
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        for i, r in df.iterrows():
            row = {k: str(v).strip() for k, v in r.items()}
            parts.append((" | ".join(f"{k}: {v}" for k, v in row.items() if v), f"row {i + 2}", None, {"row": row}))
            row_dates += [v for k, v in row.items() if k in ("updated", "last_updated") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)]
        full = "\n".join(x[0] for x in parts)
    else:
        full = _read_docx(p) if ext == ".docx" else raw.decode("utf-8", errors="replace")
        parts = [(a, b, c, None) for a, b, c in _chunk_text(full)]
    if not full.strip() or not parts: raise IngestError(f"no extractable text in {p.name} (scanned or blank?)")
    m = DATE_RE.search(full)
    doc = {"id": doc_id, "filename": p.name, "source_type": SUPPORTED[ext],
           "upload_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "doc_date": max(row_dates) if row_dates else (m.group(1) if m else None), "n_chunks": len(parts)}
    chunks = [{"id": f"{doc_id}-c{i:03d}", "doc_id": doc_id, "idx": i, "section": s, "page": pg, "text": t, "meta": meta}
              for i, (t, s, pg, meta) in enumerate(parts)]
    log.info("ingested %s -> %s (%d chunks)", p.name, doc_id, len(chunks))
    return {"doc": doc, "chunks": chunks}

def ingest_text(name, text, source_type, doc_date=None, meta=None):
    """Ingest already-fetched text (web page, GitHub README/issues...) with the same IDs/chunks/provenance as files."""
    if not text or not text.strip(): raise IngestError(f"no extractable text in {name}")
    parts = _chunk_text(text)
    if not parts: raise IngestError(f"no extractable text in {name}")
    doc_id = "doc-" + hashlib.sha1((name + text).encode()).hexdigest()[:8]; m = DATE_RE.search(text)
    doc = {"id": doc_id, "filename": name, "source_type": source_type, "upload_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "doc_date": doc_date or (m.group(1) if m else None), "n_chunks": len(parts)}
    chunks = [{"id": f"{doc_id}-c{i:03d}", "doc_id": doc_id, "idx": i, "section": s, "page": pg, "text": t, "meta": dict(meta or {})}
              for i, (t, s, pg) in enumerate(parts)]
    log.info("ingested %s -> %s (%d chunks)", name, doc_id, len(chunks)); return {"doc": doc, "chunks": chunks}

def ingest_folder(folder):
    ok, errors = [], []
    for p in sorted(Path(folder).iterdir()):
        if p.is_file() and not p.name.startswith(".") and p.name != "generate.py":
            try: ok.append(ingest_file(p))
            except IngestError as e: log.warning("skipped %s: %s", p.name, e); errors.append({"file": p.name, "error": str(e)})
    return ok, errors
