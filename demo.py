"""Runs the full pipeline on sample_data/ and prints findings. Usage: python demo.py"""
import tempfile, logging
from pathlib import Path
from app.db import DB
from app.pipeline import run_folder
from app.graph import knowledge_nodes, stats, export
logging.basicConfig(level=logging.WARNING)
root = Path(__file__).parent; db = DB(Path(tempfile.mkdtemp()) / "demo.db")
r = run_folder(db, root / "sample_data"); G = r["graph"]
print("\n=== DOCUMENTS (synthetic demo data) ===")
for d in r["documents"]: print(f"  {d['id']}  {d['filename']:<24} {d['source_type']:<9} chunks={d['n_chunks']} date={d['doc_date']}")
print("\n=== EXTRACTED ENTITIES ===")
for t in ("person", "task", "decision", "risk", "question", "milestone", "resource"):
    for d in [n for n in knowledge_nodes(G) if n["type"] == t]:
        extra = {k: v for k, v in d["attr_status"].items() if v != "VERIFIED"}
        print(f"  [{t:<9}] {d['status']:<11} {d['name'][:70]}" + (f"   attrs: {extra}" if extra else ""))
print("\n=== IMPORTANT RELATIONSHIPS ===")
for u, v, k, d in G.edges(keys=True, data=True):
    if k != "SUPPORTED_BY": print(f"  {G.nodes[u]['name'][:34]:<34} --{k}--> {G.nodes[v]['name'][:34]:<34} [{d['status']}]")
print("\n=== GRAPH STATISTICS ==="); [print(f"  {k}: {v}") for k, v in stats(G).items()]
print("  exported:", export(G, root / "data" / "exports"))
print("\n=== EVIDENCE-LINKED FACTS (sample) ===")
for nid in ("decision:use-postgresql-for-the-sensor-database", "task:t-103", "task:t-105"):
    d = G.nodes[nid]; print(f"  {d['name']}  [{d['status']}]")
    for e in d["evidence"][:2]: print(f"      <- {e['doc_name']} ({e['chunk_id']}, {e['timestamp']}): \"{e['text'][:90]}\"")
print("\n=== KNOWLEDGE DEBT FINDINGS ===")
rep = r["report"]; print("  summary:", rep["summary"])
for i in rep["issues"]: print(f"  {i['id']} [{i['severity']:<6}] {i['category']:<22} {i['title'][:75]}")
