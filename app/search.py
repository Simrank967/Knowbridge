"""Semantic-style retrieval over chunks. Default: dependency-free TF-IDF cosine. Optional: sentence-transformers
(set KNOWBRIDGE_EMBEDDINGS=1). Returns passages only; `relevance` is a ranking score, NOT a confidence."""
import math, os, re
from collections import Counter
STOP = {"the", "and", "for", "that", "this", "with", "are", "was", "who", "what", "when", "how", "does", "from", "will", "have"}
def _tok(s): return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 2 and t not in STOP]

def _tfidf(chunks, q):
    docs = [_tok(c["text"] + " " + (c["section"] or "")) for c in chunks]; N = len(docs)
    df = Counter(t for d in docs for t in set(d)); idf = lambda t: math.log((N + 1) / (df.get(t, 0) + 1)) + 1
    vec = lambda toks: {t: n * idf(t) for t, n in Counter(toks).items()}
    qv = vec(_tok(q)); qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0; out = []
    for d in docs:
        dv = vec(d); dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
        out.append(sum(v * dv.get(t, 0) for t, v in qv.items()) / (qn * dn))
    return out, 0.08

def _embed(chunks, q):
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(os.getenv("KNOWBRIDGE_EMBED_MODEL", "all-MiniLM-L6-v2"))
    E = m.encode([c["text"] for c in chunks], normalize_embeddings=True); qv = m.encode([q], normalize_embeddings=True)[0]
    return [float(x) for x in E @ qv], 0.25

def search(chunks, q, k=5):
    if not chunks or not _tok(q): return [], "tfidf"
    method = "tfidf"
    try:
        if os.getenv("KNOWBRIDGE_EMBEDDINGS") == "1": scores, floor = _embed(chunks, q); method = "embeddings"
        else: scores, floor = _tfidf(chunks, q)
    except Exception: scores, floor = _tfidf(chunks, q)
    ranked = sorted(zip(scores, chunks), key=lambda x: -x[0])[:k]
    return [{"chunk_id": c["id"], "doc_id": c["doc_id"], "section": c["section"], "page": c["page"], "relevance": round(s, 3), "text": c["text"]}
            for s, c in ranked if s >= floor], method
