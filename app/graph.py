"""C. Knowledge Continuity Graph (NetworkX MultiDiGraph). Every node/edge keeps id, type, description,
source documents, supporting text (evidence), timestamp and a verification status (set by evidence.verify)."""
import json, re
from collections import Counter
from pathlib import Path
import networkx as nx
from .extraction import ID

def slug(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:70] or "x"
_STOP = {"the", "that", "this", "with", "for", "and", "use", "model", "prototype", "approach", "from"}
def _toks(s): return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 3 and t not in _STOP}

def build_graph(claims, docs, chunks):
    G = nx.MultiDiGraph(); dn = {d["id"]: d["filename"] for d in docs}; cm = {c["id"]: c for c in chunks}
    first = {}
    for c in claims:                                   # resolve "Rohan" -> "Rohan Mehta" only if unambiguous
        f = c["fields"]
        for nm in (f.get("name") if c["kind"] == "person" else None, f.get("owner"), f.get("decided_by")):
            if nm and " " in nm: first.setdefault(nm.split()[0].lower(), set()).add(nm)
    def canon(nm):
        nm = re.sub(r"\s+", " ", nm).strip(); cand = first.get(nm.lower()) if " " not in nm else None
        return next(iter(cand)) if cand and len(cand) == 1 else nm
    def ev(c): return {"doc_id": c["doc_id"], "doc_name": dn.get(c["doc_id"], ""), "chunk_id": c["chunk_id"], "text": c["evidence"],
                       "timestamp": c["timestamp"], "certainty": c["certainty"]}
    def add_ev(lst, e):
        if not any(x["chunk_id"] == e["chunk_id"] and x["text"] == e["text"] for x in lst): lst.append(e)
    def node(t, key, c, name=None):
        nid = f"{t}:{slug(key)}"
        if nid not in G:
            G.add_node(nid, id=nid, type=t, name=name or key, description="", evidence=[], source_documents=[], observations={},
                       attrs={}, status="VERIFIED", attr_status={}, conflicts=[], stale_reasons=[], timestamp=None)
        add_ev(G.nodes[nid]["evidence"], ev(c)); return nid
    def observe(nid, attr, value, c, ts=None):
        e = ev(c); e["timestamp"] = ts or e["timestamp"]
        G.nodes[nid]["observations"].setdefault(attr, []).append({"value": value, "evidence": e})
    def edge(a, b, et, e, desc):
        if not a or not b or a == b: return
        if G.has_edge(a, b, et): add_ev(G.edges[a, b, et]["evidence"], e)
        else: G.add_edge(a, b, key=et, id=f"{et}:{a}->{b}", type=et, description=desc, evidence=[e], source_documents=[], status="VERIFIED", timestamp=None)
    def task(c, ref=None, title=None):
        key = ref or title
        if not key: return None
        tid = node("task", key, c, name=title or ref); n = G.nodes[tid]
        if ref: n["attrs"].setdefault("ref", ref)
        if title and "title" not in n["attrs"]: n["attrs"]["title"] = title; n["name"] = title
        return tid
    def person(nm, c): nm = canon(nm); return node("person", nm, c, name=nm)

    for c in claims:
        k, f = c["kind"], c["fields"]
        if k == "person":
            pid = person(f["name"], c)
            if f.get("role"): G.nodes[pid]["attrs"].setdefault("role", f["role"])
        elif k == "task":
            tid = task(c, f.get("ref"), f.get("title"))
            if f.get("owner"):
                pid = person(f["owner"], c); observe(tid, "owner", G.nodes[pid]["name"], c)
                edge(tid, pid, "ASSIGNED_TO", ev(c), f"{G.nodes[tid]['name']} is assigned to {G.nodes[pid]['name']}")
            if f.get("due"): observe(tid, "due", f["due"], c)
        elif k == "status":
            tid = task(c, f.get("ref"), None if f.get("ref") else f.get("title"))
            if tid and f.get("status"): observe(tid, "status", f["status"], c)
        elif k == "decision":
            did = node("decision", f["text"], c)
            if f.get("rationale"): observe(did, "rationale", f["rationale"], c)
            if f.get("supersedes"): G.nodes[did]["attrs"].setdefault("supersedes_text", f["supersedes"])
            if f.get("decided_by"):
                pid = person(f["decided_by"], c); edge(did, pid, "DECIDED_BY", ev(c), f"Decision decided by {G.nodes[pid]['name']}")
        elif k == "dependency":
            a, b = task(c, f["task"]), task(c, f["depends_on"])
            edge(a, b, "DEPENDS_ON", ev(c), f"{f['task']} depends on {f['depends_on']}")
            if f.get("via") == "blocked by": edge(b, a, "BLOCKS", ev(c), f"{f['depends_on']} blocks {f['task']}")
        elif k == "risk":
            rid = node("risk", f["text"], c)
            if f.get("blocker"): G.nodes[rid]["attrs"]["blocker"] = True
            if f.get("mitigation"): observe(rid, "mitigation", f["mitigation"], c)
            for ref in f.get("blocks", []): edge(rid, task(c, ref), "BLOCKS", ev(c), f"Blocker blocks {ref}")
        elif k == "milestone":
            mid = node("milestone", f["name"], c)
            if f.get("date"): observe(mid, "date", f["date"], c)
        elif k in ("question", "resource"):
            node(k, f.get("text") or f.get("name"), c)

    for did, d in [(i, d) for i, d in G.nodes(data=True) if d["type"] == "decision" and d["attrs"].get("supersedes_text")]:
        tk = _toks(d["attrs"]["supersedes_text"]); best = None
        for oid, o in G.nodes(data=True):
            if o["type"] == "decision" and oid != did and tk & _toks(o["name"]) and (not best or len(tk & _toks(o["name"])) > best[0]):
                best = (len(tk & _toks(o["name"])), oid)
        if best: edge(did, best[1], "SUPERSEDES", d["evidence"][0], f"'{d['name']}' supersedes '{G.nodes[best[1]]['name']}'")
    for nid, d in [(i, d) for i, d in G.nodes(data=True) if d["type"] in ("decision", "risk", "question")]:
        for r in set(re.findall(ID, d["name"])):
            tid = f"task:{slug(r)}"
            if tid in G and not G.has_edge(nid, tid, "BLOCKS"):
                edge(nid, tid, "RELATED_TO", {**d["evidence"][0], "certainty": "inferred"}, f"{d['type']} mentions {r} (not an explicit relationship)")

    for nid, d in [(i, d) for i, d in G.nodes(data=True)]:          # evidence nodes + SUPPORTED_BY
        by_chunk = {}
        for e in d["evidence"]: by_chunk.setdefault(e["chunk_id"], []).append(e)
        for ch, es in by_chunk.items():
            eid = f"evidence:{ch}"; c = cm.get(ch, {})
            if eid not in G:
                label = c.get("section") or (f"page {c['page']}" if c.get("page") else ch)
                G.add_node(eid, id=eid, type="evidence", name=f"{es[0]['doc_name']} · {label}", description=(c.get("text") or "")[:300],
                           source_documents=[es[0]["doc_name"]], evidence=[{**es[0], "text": c.get("text", "")}], observations={}, attrs={"chunk_id": ch, "section": c.get("section"), "page": c.get("page")},
                           status="VERIFIED", attr_status={}, conflicts=[], stale_reasons=[], timestamp=es[0]["timestamp"])
            G.add_edge(nid, eid, key="SUPPORTED_BY", id=f"SUPPORTED_BY:{nid}->{eid}", type="SUPPORTED_BY", description="claim supported by source chunk",
                       evidence=es, source_documents=[es[0]["doc_name"]], status="VERIFIED", timestamp=es[0]["timestamp"])
    for _, _, d in G.edges(data=True):
        d["source_documents"] = sorted({e["doc_name"] for e in d["evidence"]}); d["timestamp"] = max((e["timestamp"] or "" for e in d["evidence"]), default=None) or None
    return G

def knowledge_nodes(G): return [d for _, d in G.nodes(data=True) if d["type"] != "evidence"]
def stats(G):
    kn = knowledge_nodes(G)
    return {"knowledge_nodes": len(kn), "evidence_nodes": G.number_of_nodes() - len(kn), "edges": G.number_of_edges(),
            "nodes_by_type": dict(Counter(d["type"] for d in kn)), "edges_by_type": dict(Counter(k for _, _, k in G.edges(keys=True))),
            "nodes_by_status": dict(Counter(d["status"] for d in kn))}
def graph_to_json(G):
    return {"nodes": [dict(d) for _, d in G.nodes(data=True)],
            "edges": [{"source": u, "target": v, **d} for u, v, d in G.edges(data=True)], "stats": stats(G)}
def export(G, folder):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    (folder / "knowledge_graph.json").write_text(json.dumps(graph_to_json(G), indent=2))
    H = nx.MultiDiGraph(); flat = lambda d: {k: (v if isinstance(v, (str, int, float, bool)) else json.dumps(v)) for k, v in d.items() if v is not None}
    for n, d in G.nodes(data=True): H.add_node(n, **flat(d))
    for u, v, k, d in G.edges(keys=True, data=True): H.add_edge(u, v, key=k, **flat(d))
    nx.write_graphml(H, folder / "knowledge_graph.graphml")
    return {"json": str(folder / "knowledge_graph.json"), "graphml": str(folder / "knowledge_graph.graphml")}
