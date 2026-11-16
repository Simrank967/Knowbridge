"""Evidence retrieval with keyword + optional semantic search.

Scores are ranking signals, never confidence. Semantic model and document vectors are
cached in-process; deterministic TF-IDF remains available when embeddings are disabled
or unavailable.
"""
import hashlib
import math
import os
import re
from collections import Counter

STOP = {"the", "and", "for", "that", "this", "with", "are", "was", "who", "what", "when", "how", "does", "from"}
_MODEL = None
_EMBED_CACHE = {}


def _tok(s):
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 2 and t not in STOP]


def _tfidf(chunks, q):
    docs = [_tok(c["text"] + " " + (c.get("section") or "")) for c in chunks]
    N = len(docs)
    df = Counter(t for d in docs for t in set(d))
    idf = lambda t: math.log((N + 1) / (df.get(t, 0) + 1)) + 1
    vec = lambda toks: {t: n * idf(t) for t, n in Counter(toks).items()}
    qv = vec(_tok(q))
    qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    out = []
    for d in docs:
        dv = vec(d)
        dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
        out.append(sum(v * dv.get(t, 0) for t, v in qv.items()) / (qn * dn))
    return out


def _embed(chunks, q):
    global _MODEL
    from sentence_transformers import SentenceTransformer
    if _MODEL is None:
        _MODEL = SentenceTransformer(os.getenv("KNOWBRIDGE_EMBED_MODEL", "all-MiniLM-L6-v2"))
    texts = [c["text"] for c in chunks]
    fingerprint = hashlib.sha256("\\n".join(c["id"] + ":" + c["text"] for c in chunks).encode()).hexdigest()
    vectors = _EMBED_CACHE.get(fingerprint)
    if vectors is None:
        vectors = _MODEL.encode(texts, normalize_embeddings=True)
        _EMBED_CACHE[fingerprint] = vectors
        # Bound memory in long-running deployments.
        while len(_EMBED_CACHE) > 4:
            _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
    qv = _MODEL.encode([q], normalize_embeddings=True)[0]
    return [float(x) for x in vectors @ qv]


def _rank_fusion(score_lists, limit):
    """Reciprocal-rank fusion prevents incomparable model scores dominating."""
    fused = Counter()
    for scores in score_lists:
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        for rank, idx in enumerate(order[:limit * 4], 1):
            fused[idx] += 1.0 / (60 + rank)
    return fused


def search(chunks, q, k=5):
    if not chunks or not _tok(q):
        return [], "tfidf"
    keyword = _tfidf(chunks, q)
    has_lexical_match = any(score > 0 for score in keyword)
    method = "hybrid-keyword"
    score_lists = [keyword]
    semantic_scores = None
    if os.getenv("KNOWBRIDGE_EMBEDDINGS") == "1":
        try:
            semantic_scores = _embed(chunks, q)
            method = "hybrid"
        except Exception:
            # Keep the dependency-free retrieval path operational.
            method = "hybrid-keyword-fallback"

    # Reciprocal-rank fusion always assigns a rank, even when every candidate is
    # irrelevant. Require actual lexical overlap or a meaningful semantic score
    # before allowing candidates into the fused ranking.
    if not has_lexical_match:
        semantic_threshold = float(os.getenv("KNOWBRIDGE_SEMANTIC_MIN_SCORE", "0.35"))
        if semantic_scores is None or not semantic_scores or max(semantic_scores) < semantic_threshold:
            return [], method
        # For semantic-only retrieval, rank by similarity directly. RRF would
        # otherwise turn weak similarities into apparently valid results.
        score_lists = [semantic_scores]
    elif semantic_scores is not None:
        # True hybrid retrieval: combine both ranked candidate lists when
        # lexical overlap exists. Previously the semantic model was evaluated
        # but its ranking was not included in fusion on this path.
        score_lists.append(semantic_scores)
    fused = _rank_fusion(score_lists, k)
    qtokens = set(_tok(q))
    ranked = []
    for idx, base in fused.items():
        c = chunks[idx]
        body = set(_tok(c["text"]))
        section = set(_tok(c.get("section") or ""))
        overlap = len(qtokens & body) / max(1, len(qtokens))
        section_match = 0.04 if qtokens & section else 0.0
        # Small, transparent lexical rerank on top of fused retrieval.
        rerank = base + 0.12 * overlap + section_match
        ranked.append((rerank, idx))
    ranked.sort(reverse=True)
    top = []
    for score, idx in ranked[:k]:
        if score <= 0:
            continue
        c = chunks[idx]
        top.append({
            "chunk_id": c["id"], "doc_id": c["doc_id"],
            "section": c.get("section"), "page": c.get("page"),
            "relevance": round(score, 4), "text": c["text"],
        })
    return top, method
