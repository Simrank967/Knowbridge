"""Orchestrates: ingest -> chunk -> extract -> graph -> verify -> debt report. Each stage consumes the previous stage's output."""
import logging
from .db import DB
from .extraction import get_extractor
from .graph import build_graph
from .evidence import verify
from .debt import build_report
from .ingestion import ingest_folder
log = logging.getLogger("knowbridge.pipeline")

def extract_all(db, extractor=None):
    ext = extractor or get_extractor(sink=db.log_llm); docs = {d["id"]: d for d in db.documents()}; out = []
    for ch in db.chunks():
        d = docs[ch["doc_id"]]; ts = d["doc_date"] or d["upload_date"][:10]
        try: claims = ext.extract(ch)
        except Exception as e: log.warning("extraction failed for %s: %s", ch["id"], e); claims = []
        for c in claims:
            c.update(doc_id=ch["doc_id"], chunk_id=ch["id"], timestamp=c["fields"].get("as_of") or ts, extractor=ext.name); out.append(c)
    db.replace_claims(out); log.info("extracted %d claims with %s", len(out), ext.name); return out

def build(db):
    G = build_graph(db.claims(), db.documents(), db.chunks()); return verify(G)

def run_folder(db, folder, extractor=None):
    ok, errors = ingest_folder(folder)
    for r in ok: db.add_document(r)
    claims = extract_all(db, extractor); G = build(db)
    return {"documents": [r["doc"] for r in ok], "errors": errors, "claims": claims, "graph": G, "report": build_report(G)}
