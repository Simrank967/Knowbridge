"""D. Evidence mapping & verification. Labels: VERIFIED / INFERRED / CONFLICTING / MISSING / STALE (no confidence %).
Conflicts are flagged with every observation kept; no value is ever silently chosen."""
from datetime import datetime
from .config import STALE_DAYS, CONFLICT_WINDOW_DAYS
RANK = {"not_started": 0, "in_progress": 1, "blocked": 1, "done": 2}
REQUIRED = {"task": ["owner", "status"], "decision": ["rationale"], "risk": ["mitigation"]}
def _d(s):
    try: return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception: return None

def is_conflict(attr, items):
    """items sorted oldest->newest. Status: differing values within the conflict window, or a regression
    (e.g. done -> in_progress), conflict; a forward progression over time is normal history. Other attrs: any disagreement."""
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["value"] == b["value"]: continue
            if attr != "status": return True
            da, db = _d(a["evidence"]["timestamp"] or ""), _d(b["evidence"]["timestamp"] or "")
            if da is None or db is None or abs((db - da).days) <= CONFLICT_WINDOW_DAYS: return True
            if RANK.get(b["value"], 0) < RANK.get(a["value"], 0): return True
    return False

def _label(obs): return "VERIFIED" if any(o["evidence"]["certainty"] == "explicit" for o in obs) else "INFERRED"

def verify(G, stale_days=STALE_DAYS):
    stamps = [e["timestamp"] for _, d in G.nodes(data=True) if d["type"] != "evidence" for e in d["evidence"] if e.get("timestamp")]
    ref = max((_d(s) for s in stamps if _d(s)), default=None)
    superseded = {v: u for u, v, k in G.edges(keys=True) if k == "SUPERSEDES"}
    for nid, d in G.nodes(data=True):
        if d["type"] == "evidence": continue
        d["timestamp"] = max((e["timestamp"] or "" for e in d["evidence"]), default=None) or None
        d["source_documents"] = sorted({e["doc_name"] for e in d["evidence"]})
        d["description"] = d["description"] or f"{d['type']}: {d['name']}"
        for attr in set(REQUIRED.get(d["type"], [])) | set(d["observations"]):
            obs = d["observations"].get(attr, [])
            if not obs: d["attr_status"][attr] = "MISSING"; continue
            d["attr_status"][attr] = _label(obs)
            items = sorted(obs, key=lambda o: o["evidence"]["timestamp"] or "")
            if is_conflict(attr, items):
                d["attr_status"][attr] = "CONFLICTING"
                d["conflicts"].append({"attribute": attr, "note": "Sources disagree; KNOWBRIDGE flags this and does not choose a value.",
                    "observations": [{"value": o["value"], "document": o["evidence"]["doc_name"], "chunk_id": o["evidence"]["chunk_id"],
                                      "timestamp": o["evidence"]["timestamp"], "text": o["evidence"]["text"]} for o in items]})
        if d["type"] == "task" and not d["attrs"].get("title"): d["attr_status"]["definition"] = "MISSING"
        if nid in superseded: d["stale_reasons"].append(f"superseded by decision '{G.nodes[superseded[nid]]['name']}'")
        if d["type"] in ("task", "risk", "question") and ref and d["timestamp"] and _d(d["timestamp"]):
            st = sorted(d["observations"].get("status", []), key=lambda o: o["evidence"]["timestamp"] or "")
            age = (ref - _d(d["timestamp"])).days
            if not (st and st[-1]["value"] == "done") and age > stale_days:
                d["stale_reasons"].append(f"newest evidence is {age} days older than the newest project document")
        d["status"] = "VERIFIED" if any(e["certainty"] == "explicit" for e in d["evidence"]) else "INFERRED"
        if d["stale_reasons"]: d["status"] = "STALE"
        if d["conflicts"]: d["status"] = "CONFLICTING"
    for _, _, d in G.edges(data=True):
        if d["type"] != "SUPPORTED_BY":
            d["status"] = "VERIFIED" if any(e["certainty"] == "explicit" for e in d["evidence"]) else "INFERRED"
    return G
