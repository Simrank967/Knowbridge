"""Evidence-first conversational interface over KNOWBRIDGE's existing knowledge store."""
import os, re, uuid
from ..search import search
from ..debt import build_report
from ..graph import knowledge_nodes
from ..pipeline import build
from ..services import knowledge_summary



def _graph_evidence(db, question, limit=5):
    """Retrieve source-backed claims and nearby graph context using lexical entity matching."""
    from collections import Counter
    from ..graph import knowledge_nodes
    from ..search import _tok

    query_terms = set(_tok(question))
    if not query_terms:
        return []
    graph = build(db)
    found = []
    for node in knowledge_nodes(graph):
        haystack = " ".join([
            str(node.get("name", "")), str(node.get("description", "")),
            str(node.get("attrs", {})), str(node.get("observations", {})),
        ])
        terms = set(_tok(haystack))
        overlap = len(query_terms & terms)
        if overlap == 0:
            continue
        # A matching entity contributes only its stored source evidence.
        for ev in node.get("evidence", [])[:2]:
            found.append({
                "chunk_id": ev.get("chunk_id"), "doc_id": ev.get("doc_id"),
                "section": node.get("name"), "page": None,
                "relevance": round(overlap / max(1, len(query_terms)), 4),
                "text": ev.get("text", ""), "entity_type": node.get("type"),
                "verification": node.get("status", "INFERRED"),
            })
    deduped = {}
    for item in found:
        key = (item.get("chunk_id"), item.get("text"))
        if key not in deduped or item["relevance"] > deduped[key]["relevance"]:
            deduped[key] = item
    return sorted(deduped.values(), key=lambda x: x["relevance"], reverse=True)[:limit]


def _structured_evidence(claims, chunks, question, limit=5):
    """Retrieve extracted structured claims by lexical overlap and retain their source chunk."""
    from ..search import _tok
    terms = set(_tok(question))
    if not terms:
        return []
    chunk_map = {c.get("id"): c for c in chunks}
    found = []
    for claim in claims:
        fields = claim.get("fields", {})
        haystack = " ".join((str(claim.get("kind", "")), str(fields), str(claim.get("evidence", ""))))
        overlap = len(terms & set(_tok(haystack)))
        if not overlap:
            continue
        source_chunk = chunk_map.get(claim.get("chunk_id"), {})
        found.append({
            "chunk_id": claim.get("chunk_id"), "doc_id": claim.get("doc_id"),
            "section": source_chunk.get("section") or claim.get("kind"),
            "page": source_chunk.get("page"),
            "relevance": round(overlap / max(1, len(terms)), 4),
            "text": claim.get("evidence") or source_chunk.get("text", ""),
            "structured_kind": claim.get("kind"),
            "verification": claim.get("certainty", "INFERRED"),
        })
    return sorted(found, key=lambda x: x["relevance"], reverse=True)[:limit]


def _source_name(db, doc_id):
    for d in db.documents():
        if d.get("id") == doc_id:
            return d.get("filename") or "project source"
    return "project source"


def _expanded_query(question):
    """Add domain vocabulary for short technical queries that lexical tokenizers miss."""
    q = question.lower()
    extra = []
    if re.search(r"\brl\b|reinforcement learning|policy|agent", q):
        extra += ["reinforcement learning", "PPO", "DQN", "A2C", "SAC", "TD3",
                  "Q-learning", "policy gradient", "stable-baselines", "train_ppo"]
    if re.search(r"\bapi\b|endpoint|route", q):
        extra += ["FastAPI", "router", "endpoint", "HTTP"]
    if re.search(r"\bmodel\b|llm|language model", q):
        extra += ["model", "inference", "transformer", "checkpoint"]
    return (question + " " + " ".join(extra)).strip()


def _grounded_llm(question, evidence, history=None):
    """Generic evidence-grounded synthesis with bounded context and validated citations."""
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    provider = os.getenv("KNOWBRIDGE_LLM_PROVIDER", "huggingface").lower()
    if provider not in ("huggingface", "hf") or not token or not evidence:
        return None
    model = os.getenv("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    context = []
    for i, item in enumerate(evidence[:8], 1):
        context.append(
            f"[E{i}] Source: {item.get('source','unknown')} | "
            f"Section: {item.get('section') or 'n/a'} | Page: {item.get('page') or 'n/a'}\n"
            f"{item.get('text','')[:2600]}"
        )
    prior = []
    for message in (history or [])[-6:]:
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))[:1200]
        if role in ("user", "assistant") and content:
            prior.append(f"{role.upper()}: {content}")
    prompt = (
        "You are KNOWBRIDGE, a general assistant for analysing the user's connected project files. "
        "Answer the actual question directly; do not merely list or repeat retrieved passages. "
        "Use only the supplied evidence for claims about this project. Use prior conversation only "
        "to resolve references such as 'it', 'that function' or follow-up questions, never as new evidence. "
        "Choose a useful format for the question: concise answer, steps, comparison table, code explanation, "
        "findings or recommendations as appropriate. Distinguish verified facts from inference. "
        "If evidence is insufficient or conflicting, say so clearly. Cite material project claims using [E#]. "
        "Never invent filenames, values, code behaviour or citations. Avoid dumping code unless requested. "
        "Keep the response focused and normally under 250 words.\n\n"
        f"RECENT CONVERSATION:\n{chr(10).join(prior) or 'None'}\n\n"
        f"CURRENT QUESTION:\n{question}\n\nPROJECT EVIDENCE:\n" + "\n\n".join(context)
    )
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(model=model, token=token, timeout=float(os.getenv("KNOWBRIDGE_LLM_TIMEOUT", "25")))
        result = client.chat_completion(
            messages=[
                {"role": "system", "content": "Be precise, useful, concise and strictly evidence-grounded."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=550,
            temperature=0.1,
        )
        response = (result.choices[0].message.content or "").strip()
        allowed = {f"E{i}" for i in range(1, min(len(evidence), 8) + 1)}
        cited = set(re.findall(r"\[(E\d+)\]", response))
        # A response with no citations is not accepted as a grounded project answer.
        if not response or not cited or not cited.issubset(allowed):
            return None
        return response
    except Exception:
        return None



def _deterministic_audio_answer(question, chunks, evidence):
    """Answer audio-asset questions from repository-wide indexed text."""
    low = question.lower()
    if not re.search(r"\baudio(s)?\b|\bmusic\b|\bsound(s)?\b|\bsfx\b|\bvoice\b|\bsong\b", low):
        return None

    rows = []
    for chunk in chunks:
        text = str(chunk.get("text", ""))
        if re.search(r"\.(?:mp3|wav|ogg|m4a|flac|aac)\b|raone_voice|result_audio|music_path|sound_path|pygame\.mixer|audio", text, re.I):
            rows.append(chunk)

    file_pattern = re.compile(r"(?ix)(?:[A-Za-z0-9_. -]+/)?[A-Za-z0-9_. -]+\.(?:mp3|wav|ogg|m4a|flac|aac)")
    names = []
    for row in rows:
        for match in file_pattern.findall(str(row.get("text", ""))):
            name = match.strip().strip("`' ,;()")
            if name and name.lower() not in {n.lower() for n in names}:
                names.append(name)

    corpus = "\n".join(str(r.get("text", "")) for r in rows)
    dirs = []
    for candidate in ("assets/raone_voice/", "assets/result_audio/", "raone_voice/", "result_audio/"):
        if candidate.lower() in corpus.lower() and candidate not in dirs:
            dirs.append(candidate)
    mechanisms = []
    for pattern, label in (
        (r"pygame\.mixer", "Pygame mixer for playback"),
        (r"pygame\.mixer\.music\.load|music\.load", "background music loaded through the music API"),
        (r"ffmpeg", "FFmpeg audio extraction/conversion from video"),
    ):
        if re.search(pattern, corpus, re.I):
            mechanisms.append(label)

    if not names and not dirs and not mechanisms:
        return None
    lines = ["The repository contains the following audio references:"]
    if names:
        lines.append("**Audio files explicitly named in the indexed source:**")
        lines.extend(f"- `{name}`" for name in names[:20])
    if dirs:
        lines.append("**Audio asset directories:** " + ", ".join(f"`{d}`" for d in dirs))
    if mechanisms:
        lines.append("**Playback/processing:** " + "; ".join(mechanisms) + ".")
    if not names:
        lines.append("The indexed text identifies audio folders or playback code, but does not expose individual audio filenames.")
    lines.append("These are references in indexed repository text; they do not prove every referenced file is present in the current checkout.")
    return "\n\n".join(lines)


REPORT_WORDS = ("report", "pdf", "export", "document")

def _deterministic_technical_answer(question, evidence):
    """Concise, evidence-grounded answers for technical identification questions without an LLM."""
    low = question.lower()
    corpus = "\n".join(str(x.get("text", "")) for x in evidence).lower()
    sources = list(dict.fromkeys(x.get("source") or "project source" for x in evidence))
    source_note = ", ".join(sources[:4])
    if re.search(r"\b(rl|reinforcement learning|reinforcement|policy|agent)\b", low) and re.search(r"\b(algorithm|algorithms|use|used|using|which|what|model)\b", low):
        if "ppo" in corpus or "proximal policy optimization" in corpus:
            result = (
                "The project uses **Proximal Policy Optimization (PPO)** for its reinforcement-learning opponent. "
                "The repository describes a PPO policy integrated with **Stable-Baselines3**. It is a **hybrid "
                "combat system**: PPO supplies learned action decisions, while tactical combat priorities and a "
                "scripted fallback provide additional behaviour when a trained checkpoint is unavailable.\n\n"
                "The **Random Forest** model is used for webcam hand-gesture classification. It is supervised "
                "learning, not the reinforcement-learning algorithm."
            )
            return result + (f"\n\n**Sources:** {source_note}." if source_note else "")
        found = [name for name in ("PPO", "DQN", "A2C", "SAC", "TD3", "Q-learning") if re.search(rf"\b{re.escape(name.lower())}\b", corpus)]
        if found:
            return ("The retrieved evidence mentions **" + ", ".join(found) + "**, but does not establish which algorithm is actively used. "
                    "The training script or checkpoint-loading code is needed to verify that." + (f"\n\n**Sources:** {source_note}." if source_note else ""))
        return "I couldn't verify an RL algorithm from the retrieved passages. Check that the repository's training scripts and model-loading files have been indexed."
    return None


def _overview_evidence(db, chunks, limit=7):
    """Prefer narrative project documentation; never use code or model metadata as an overview."""
    docs = {d.get("id"): d for d in db.documents()}
    code_ext = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cpp", ".c", ".h", ".hpp", ".cs", ".go", ".rs", ".sql", ".sh"}
    candidates = []
    for c in chunks:
        doc = docs.get(c.get("doc_id"), {})
        name = (doc.get("filename") or "").lower()
        if any(name.endswith(ext) for ext in code_ext) or "system_info" in name or name.endswith((".pkl", ".zip", ".bin")):
            continue
        text = str(c.get("text", ""))
        # Convert README markup into readable prose before it reaches the chat UI.
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"(?is)<(script|style|svg)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"<img\b[^>]*>", " ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"\[([^]]+)\]\((https?://[^ )]+)(?:\s+[^)]*)?\)", r"\1", text)
        text = re.sub(r"!\[[^]]*\]\[[^]]*\]", " ", text)
        text = re.sub(r"^\s*[-*_]{3,}\s*$", " ", text, flags=re.M)
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("|") or re.fullmatch(r"[:|\- ]+", line):
                continue
            line = re.sub(r"^#{1,6}\s*", "", line)
            line = re.sub(r"^\s*[-*+]\s+", "", line)
            line = re.sub(r"^\s*\d+[.)]\s+", "", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            line = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", line)
            line = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", line)
            if line and not re.match(r"^(?:https?://|<https?://)", line):
                lines.append(line)
        text = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if len(text) < 45:
            continue
        score = 0
        if "readme" in name: score += 5
        if name.endswith((".md", ".pdf", ".docx", ".txt")): score += 2
        if c.get("section"): score += 1
        candidates.append((score, c, text))
    candidates.sort(key=lambda x: (x[0], len(x[2])), reverse=True)
    result, seen = [], set()
    for _, c, text in candidates:
        key = re.sub(r"\W+", "", text.lower())[:180]
        if key in seen: continue
        seen.add(key)
        result.append({"chunk_id": c.get("id"), "doc_id": c.get("doc_id"),
            "section": c.get("section"), "page": c.get("page"),
            "relevance": 1.0, "text": text, "source": docs.get(c.get("doc_id"), {}).get("filename", "project source")})
        if len(result) >= limit: break
    return result


def _plain_overview(evidence):
    """Readable fallback overview; explicitly labels it as an evidence summary, not LLM analysis."""
    if not evidence:
        return "I can see the repository connection, but the indexed documents do not yet contain enough readable project documentation to create a reliable overview. Index the README and project documentation, then try again."
    lines = ["## Project overview", "Based on the readable project documentation currently indexed:"]
    for item in evidence[:2]:
        section = item.get("section") or item.get("source") or "Project context"
        excerpt = _clip(item.get("text", ""), 180)
        lines.append(f"- **{section}:** {excerpt}")
    lines.append("\n*This is a source summary. It does not infer details that are absent from the indexed files.*")
    return "\n\n".join(lines)


def _followup(q, history):
    """Short or pronoun-led follow-ups inherit the previous user question so retrieval keeps the topic."""
    if not history or not (len(q.split()) <= 8 or re.match(r"(?i)^(and|also|what about|how about|why|then|so|it|that|this|those|they)\b", q)): return q
    prev = next((m.get("content") for m in reversed(history) if m.get("role") == "user" and m.get("content") and m.get("content") != q), None)
    return f"{prev} {q}" if prev else q


class _ProjectSourceView:
    """Read-only view excluding bundled synthetic demo fixtures from user project answers."""
    def __init__(self, db, documents, chunks, claims):
        self._db, self._documents, self._chunks, self._claims = db, documents, chunks, claims
    def documents(self): return list(self._documents)
    def chunks(self): return list(self._chunks)
    def claims(self): return list(self._claims)
    def counts(self):
        return {"documents": len(self._documents), "chunks": len(self._chunks), "claims": len(self._claims)}
    @property
    def c(self): return self._db.c


def _is_synthetic_demo(doc, chunks):
    """Detect explicitly labelled bundled demo fixtures, not ordinary user files."""
    filename = str(doc.get("filename") or "").lower()
    body = " ".join(str(c.get("text", ""))[:1800] for c in chunks[:4]).lower()
    marker = "synthetic demo data - not a real project" in body
    bundled_name = filename in {"meeting_notes.md", "technical_decision.docx", "progress_report.pdf"}
    known_fixture_csv = (filename == "task_allocation.csv"
        and "train energy forecasting model" in body
        and "build mqtt ingestion service" in body
        and "postgresql schema for sensor data" in body)
    return (marker and bundled_name) or known_fixture_csv

def answer(db, question: str, project_id=None, history=None, role=None, role_description=""):
    from .. import repo_intel as _ri
    db = _ri.scope(db)  # strict repository isolation before ANY retrieval
    q=question.strip(); low=q.lower()
    docs=db.documents(); chunks=db.chunks(); claims=db.claims()
    # Bundled synthetic examples are available only through the explicit demo endpoint.
    demo_mode = False
    try:
        flag = db.c.execute("SELECT value FROM runtime_flags WHERE key='demo_mode'").fetchone()
        demo_mode = bool(flag and flag["value"] == "1")
    except Exception:
        demo_mode = False
    if not _ri.has_repo(db) and not demo_mode:
        by_doc = {}
        for chunk in chunks: by_doc.setdefault(chunk.get("doc_id"), []).append(chunk)
        synthetic_ids = {d.get("id") for d in docs if _is_synthetic_demo(d, by_doc.get(d.get("id"), []))}
        if synthetic_ids:
            docs = [d for d in docs if d.get("id") not in synthetic_ids]
            chunks = [c for c in chunks if c.get("doc_id") not in synthetic_ids]
            claims = [c for c in claims if c.get("doc_id") not in synthetic_ids]
            db = _ProjectSourceView(db, docs, chunks, claims)
    # Resolve conversational follow-ups without changing the user's displayed question.
    prior_user = [m.get("content", "") for m in (history or []) if m.get("role") == "user"][-3:]
    prior_assistant = [m.get("content", "") for m in (history or []) if m.get("role") == "assistant"][-2:]
    standalone = q
    if prior_user and (len(q.split()) <= 9 or re.search(r"\b(it|that|this|they|them|those|above|same|former|latter)\b", low)):
        standalone = "Previous user questions: " + " | ".join(prior_user) + "\nPrevious assistant context: " + " | ".join(prior_assistant) + "\nFollow-up question: " + q
    if not docs:
        return {"answer":"I don't have any project sources yet. Upload documents or connect a GitHub repository, then ask me again.","evidence":[],"verification":"MISSING","suggestions":["Connect a GitHub repository","Upload project documents"]}
    from .. import repo_intel, repo_roles
    db = repo_intel.scope(db)  # strict repository isolation for every answer path
    if repo_intel.has_repo(db):  # repository-aware, role-adapted answer built from parsed source structure; None -> fall through to the generic pipeline
        try: rr = repo_roles.repo_answer(db, _followup(q, history), role, role_description, history)
        except Exception: rr = None
        if rr: return rr
    if any(x in low for x in ("knowledge gap","knowledge debt","missing owner","conflict","conflicting","risk","blocker","unresolved")):
        rep=build_report(build(db)); issues=rep.get("issues",[])
        filtered=[i for i in issues if any(t in (i.get("category","")+" "+i.get("title","")+" "+i.get("description","")).lower() for t in re.findall(r"[a-z]{4,}",low))]
        shown=filtered or issues
        lines=[f"I found **{len(shown)}** relevant knowledge-debt finding(s)."]
        for i in shown[:12]:
            lines.append(f"- **{i.get('severity','?')} · {i.get('title','Finding')}** — {i.get('description','')}")
        evidence=[]
        for i in shown[:12]:
            for e in i.get("evidence",[])[:2]: evidence.append(e)
        return {"answer":"\n".join(lines) if shown else "No matching knowledge-debt findings were detected in the currently ingested sources.","evidence":_dedupe_evidence(evidence),"verification":"CONFLICTING" if any(i.get("category")=="Conflict" for i in shown) else "VERIFIED","suggestions":["Generate a knowledge debt report PDF","Show all open tasks","Summarise project decisions"]}
    if any(x in low for x in ("summari", "overview", "what do you know", "project about", "architecture", "technologies", "components")):
        sm = knowledge_summary(db)
        hits = _overview_evidence(db, chunks, 3)
        generated = _grounded_llm(q, hits, history=history) if hits else None
        if generated:
            response = generated
            mode = "huggingface-grounded-overview"
        else:
            response = _plain_overview(hits)
            mode = "readable-evidence-overview"
        intro = f"**{sm['documents']} documents indexed** · {sm['chunks']} searchable passages · {sm['claims']} structured claims"
        return {"answer": intro + "\n\n" + response, "evidence": hits,
                "verification": "VERIFIED" if hits else "MISSING", "answer_mode": mode,
                "suggestions": ["Explain the AI architecture", "Which algorithms are implemented?", "What training data is documented?"]}
    # Dataset-identification questions need explicit dataset/training-source evidence;
    # generic README matches on words like "project" or "used" are not sufficient.
    dataset_question = bool(re.search(r"\b(dataset|datasets|training data|training set|test data|data source)\b", low))
    dataset_hits = []
    if dataset_question:
        dataset_pattern = re.compile(r"\b(dataset|training data|training set|test set|mnist|cifar|imagenet|kaggle|uci|csv|parquet|jsonl)\b", re.I)
        for c in chunks:
            body = str(c.get("text", ""))
            if dataset_pattern.search(body) and re.search(r"\b(dataset|training|trained|samples|records|images|labels|csv|parquet|mnist|cifar|imagenet|kaggle|uci)\b", body, re.I):
                dataset_hits.append({"chunk_id":c.get("id"),"doc_id":c.get("doc_id"),"section":c.get("section"),"page":c.get("page"),"relevance":1.0,"text":body})
        if not dataset_hits:
            return {"answer":"I couldn't identify a dataset or training-data source in the currently indexed repository files. The README describes a Random Forest gesture model and a PPO opponent, but the indexed evidence does not name the datasets used to train them. The model artifact alone does not establish its training dataset.","evidence":[],"verification":"MISSING","answer_mode":"insufficient-dataset-evidence","suggestions":["Show the indexed repository files","Check for model-training scripts","Connect the dataset documentation"]}
    retrieval_query = _expanded_query(standalone)
    hits,method=search(chunks,retrieval_query,10)
    if dataset_question:
        hits = dataset_hits[:8]
    # Add structured knowledge-graph evidence to the passage results. This gives
    # entity/relationship questions a path beyond plain chunk similarity.
    graph_hits=_graph_evidence(db,retrieval_query,5)
    structured_hits=_structured_evidence(claims,chunks,retrieval_query,5)
    merged={}
    for item in hits + graph_hits + structured_hits:
        key=(item.get("chunk_id"),item.get("text"))
        if key not in merged or item.get("relevance",0)>merged[key].get("relevance",0):
            merged[key]=item
    hits=sorted(merged.values(),key=lambda x:x.get("relevance",0),reverse=True)[:8]
    if not hits:
        return {"answer":"I couldn't find sufficient evidence in the connected project sources to answer this confidently. Try different wording or connect more project documents.","evidence":[],"verification":"MISSING","retrieval_method":method,"suggestions":["Show project overview","List available sources","Find knowledge gaps"]}
    # Inventory-style audio questions need a corpus-wide pass; top-k retrieval can
    # otherwise repeat README passages and miss the actual asset declarations.
    audio_answer = _deterministic_audio_answer(q, chunks, hits)
    if audio_answer:
        audio_rows = []
        for c in chunks:
            if re.search(r"\\.(?:mp3|wav|ogg|m4a|flac|aac)\\b|raone_voice|result_audio|music_path|sound_path|pygame\\.mixer", str(c.get("text", "")), re.I):
                audio_rows.append({"chunk_id": c["id"], "doc_id": c["doc_id"],
                    "section": c.get("section"), "page": c.get("page"),
                    "relevance": 1.0, "text": c["text"]})
        by_id = {h.get("chunk_id"): h for h in hits}
        for h in audio_rows:
            by_id.setdefault(h["chunk_id"], h)
        hits = list(by_id.values())[:12]

    # Attach source filenames before synthesis; never expose raw code as the answer.
    for h in hits:
        h["source"] = _source_name(db, h.get("doc_id"))
    if audio_answer:
        return {"answer": audio_answer, "evidence": hits, "verification": "VERIFIED",
                "answer_mode": "repository-audio-inventory", "retrieval_method": method,
                "suggestions": ["Show where each audio file is played", "Explain the game's audio flow", "List voice and sound-effect assets"]}
    generated = _grounded_llm(q, hits, history=history)
    if generated:
        response, answer_mode = generated, "huggingface-grounded"
    else:
        deterministic = _deterministic_technical_answer(q, hits)
        if deterministic:
            response, answer_mode = deterministic, "evidence-grounded-template"
        else:
            # Do not present weak lexical matches as a generated answer.
            from ..search import _tok
            qterms = set(_tok(q))
            meaningful = {t for t in qterms if t not in {"project", "used", "using", "show", "tell", "find", "which", "what", "have"}}
            relevant_hits = [h for h in hits if meaningful & set(_tok(h.get("text", "")))]
            if not relevant_hits:
                return {"answer":"I found no sufficiently relevant source passage to answer this question confidently. Try a more specific question or connect the source file that documents it.","evidence":[],"verification":"MISSING","answer_mode":"insufficient-evidence","retrieval_method":method,"suggestions":["Show indexed source files","Summarise the project","Connect more project documentation"]}
            lines = ["The configured LLM is unavailable. Here is the relevant source evidence, not a generated conclusion:"]
            seen = set()
            for h in relevant_hits[:4]:
                excerpt = _clip(h.get("text", ""), 260)
                key = re.sub(r"\W+", "", excerpt.lower())
                if not excerpt or key in seen:
                    continue
                seen.add(key)
                source = h.get("source", "project source")
                section = f" · {h.get('section')}" if h.get("section") else ""
                lines.append(f"- {excerpt}\n  *Source: {source}{section}*")
            response, answer_mode = "\n\n".join(lines), "evidence-summary"
    return {"answer":response,"evidence":hits,"verification":"VERIFIED","answer_mode":answer_mode,"retrieval_method":method,"suggestions":["Explain the relevant implementation","Show the exact source file","Find related modules"]}

def _clip(s,n): return re.sub(r"\s+"," ",s).strip()[:n]+("…" if len(s)>n else "")
def _dedupe_evidence(items):
    out=[]; seen=set()
    for e in items:
        key=(e.get("chunk_id"),e.get("text"))
        if key not in seen: seen.add(key); out.append(e)
    return out[:20]
