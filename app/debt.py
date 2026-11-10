"""E. Knowledge Debt Detector -> structured report derived only from the verified graph."""
from collections import Counter
SEV = {"High": 0, "Medium": 1, "Low": 2}

def build_report(G):
    issues = []; nodes = lambda t: [(i, d) for i, d in G.nodes(data=True) if d["type"] == t]
    def add(title, cat, desc, sev, nid, verify, evid=None):
        d = G.nodes[nid]
        ev = evid if evid is not None else [{"document": e["doc_name"], "chunk_id": e["chunk_id"], "text": e["text"], "timestamp": e["timestamp"]} for e in d["evidence"][:2]]
        issues.append({"id": f"KD-{len(issues) + 1:03d}", "title": title, "category": cat, "description": desc, "severity": sev,
                       "related_entity": {"id": nid, "type": d["type"], "name": d["name"]}, "supporting_evidence": ev, "recommended_verification": verify})
    dependents = lambda t: [u for u, _, k in G.in_edges(t, keys=True) if k == "DEPENDS_ON"]
    blocked = lambda t: any(k == "BLOCKS" for _, _, k in G.in_edges(t, keys=True))
    for tid, d in nodes("task"):
        if d["attr_status"].get("definition") == "MISSING": continue
        if d["attr_status"].get("owner") == "MISSING":
            crit = bool(dependents(tid)) or blocked(tid)
            why = ("it " + ("blocks/is depended on by other work" if dependents(tid) else "is currently blocked")) if crit else "no one is recorded as accountable"
            add(f"No owner for {d['attrs'].get('ref', '')} {d['name']}".replace("  ", " "), "Missing owner", f"No source names an owner for this task; {why}.",
                "High" if crit else "Medium", tid, "Confirm who owns this task and record it in the task board.")
        if d["attr_status"].get("status") == "MISSING":
            add(f"Status not recorded for {d['name']}", "Insufficient evidence", "No source states the current status of this task.", "Low", tid, "Ask the departing owner or tracker for the current status.")
    for did, d in nodes("decision"):
        if d["attr_status"].get("rationale") == "MISSING":
            rel = any(k == "RELATED_TO" for _, _, k in G.out_edges(did, keys=True))
            add(f"Decision without rationale: {d['name']}", "Missing rationale", "The decision is documented but no source explains why it was made.",
                "High" if rel else "Medium", did, "Ask the decision maker for the reasoning and alternatives considered.")
    for u, v, k in G.edges(keys=True):
        if k != "DEPENDS_ON": continue
        t = G.nodes[v]; probs = []
        if t["attr_status"].get("definition") == "MISSING": probs.append(f"{t['name']} is referenced but never defined in any source")
        elif t["attr_status"].get("owner") == "MISSING": probs.append(f"{t['name']} has no recorded owner")
        if probs: add(f"Dependency with missing information: {G.nodes[u]['name']} → {t['name']}", "Dependency gap", "; ".join(probs) + ".", "Medium", u,
                      "Identify the dependency's owner, scope and expected completion date.", [{"document": e["doc_name"], "chunk_id": e["chunk_id"], "text": e["text"], "timestamp": e["timestamp"]} for e in G.edges[u, v, k]["evidence"][:2]])
    for rid, d in nodes("risk"):
        if d["attr_status"].get("mitigation") == "MISSING":
            add(f"{'Blocker' if d['attrs'].get('blocker') else 'Risk'} without mitigation: {d['name']}", "Missing mitigation", "No source describes a mitigation or response plan.",
                "High" if d["attrs"].get("blocker") else "Medium", rid, "Document who is handling this and the mitigation plan.")
    for qid, d in nodes("question"):
        add(f"Unresolved question: {d['name']}", "Unresolved question", "Raised in the sources with no recorded answer.", "Medium", qid, "Find the answer or assign an owner to resolve it.")
    for nid, d in G.nodes(data=True):
        for c in d["conflicts"]:
            vals = "; ".join(f"'{o['value']}' ({o['document']}, {o['timestamp']})" for o in c["observations"])
            add(f"Conflicting {c['attribute']} for {d['name']}", "Conflict", f"Sources disagree: {vals}. Not resolved automatically.", "High", nid,
                f"Confirm the true {c['attribute']} with the owner and correct the outdated source.", [{"document": o["document"], "chunk_id": o["chunk_id"], "text": o["text"], "timestamp": o["timestamp"]} for o in c["observations"]])
        if d["type"] != "evidence" and d["stale_reasons"] and not d["conflicts"]:
            sup = any("superseded" in r for r in d["stale_reasons"])
            add(f"Outdated information: {d['name']}", "Outdated information", "; ".join(d["stale_reasons"]).capitalize() + ".", "Low" if sup else "Medium", nid,
                "Confirm whether anything still relies on this, and update or retire it." if sup else "Re-confirm this item is still current.")
        if d["type"] != "evidence" and d["status"] == "INFERRED":
            add(f"Insufficient evidence: {d['name']}", "Insufficient evidence", "Only hedged or indirect statements support this item.", "Low", nid, "Confirm with a source that states it explicitly.")
    issues.sort(key=lambda i: (SEV[i["severity"]], i["category"]))
    return {"summary": {"total": len(issues), "by_severity": dict(Counter(i["severity"] for i in issues)), "by_category": dict(Counter(i["category"] for i in issues))}, "issues": issues}
