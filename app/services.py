"""Small service helpers shared by routers (kept out of the routers so they stay thin)."""
from collections import Counter

from .debt import build_report
from .graph import knowledge_nodes, stats
from .narrative import readiness
from .pipeline import build


def knowledge_summary(db) -> dict:
    """Counts-only overview of everything the engine currently knows."""
    G = build(db)
    kn = knowledge_nodes(G)
    counts = db.counts()
    return {
        "documents": counts["documents"], "chunks": counts["chunks"], "claims": counts["claims"],
        "entities_by_type": dict(Counter(d["type"] for d in kn)),
        "entities_by_verification": dict(Counter(d["status"] for d in kn)),
        "relationships_by_type": {k: v for k, v in stats(G)["edges_by_type"].items() if k != "SUPPORTED_BY"},
        "knowledge_debt": (rep := build_report(G))["summary"], "readiness": readiness(rep),
        "note": "Counts only. KNOWBRIDGE deliberately reports no confidence percentages.",
    }


def trace_entity(G, db, entity_id):
    """Fact -> chunk -> document -> exact character span, plus a check that the quote really is in the stored source text."""
    d = G.nodes[entity_id]; chunks = {c["id"]: c for c in db.chunks()}; docs = {x["id"]: x for x in db.documents()}; trail = []
    for e in d["evidence"]:
        c = chunks.get(e["chunk_id"]); lines = [l.strip() for l in e["text"].splitlines() if l.strip()]
        start = c["text"].find(lines[0]) if c and lines else -1
        trail.append({"document": e["doc_name"], "doc_id": e["doc_id"], "source_type": (docs.get(e["doc_id"]) or {}).get("source_type"),
                      "source_url": ((c or {}).get("meta") or {}).get("source_url"), "chunk_id": e["chunk_id"], "page": (c or {}).get("page"),
                      "section": (c or {}).get("section"), "quote": e["text"], "certainty": e["certainty"], "timestamp": e["timestamp"],
                      "char_start": start if start >= 0 else None, "char_end": start + len(lines[0]) if start >= 0 else None,
                      "verbatim_in_source": bool(c) and all(l in c["text"] for l in lines)})
    return {"entity": {k: d[k] for k in ("id", "type", "name", "status", "attr_status")}, "trail": trail,
            "relationships": [{"type": k, "target": v, "target_name": G.nodes[v]["name"], "status": e["status"]}
                              for _, v, k, e in G.out_edges(entity_id, keys=True, data=True) if k != "SUPPORTED_BY"]}
