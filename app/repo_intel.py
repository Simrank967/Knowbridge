"""Repository intelligence: manifest persistence + incremental ingestion helpers, evidence-backed project model (NetworkX graph over the
per-file structure stored in SQLite) and a source-traceable project summary. Relationships carry basis = extracted | inferred."""
import json, re, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
import networkx as nx
from .repo_scan import file_kind, is_test_path, AUDIO_EXT

STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
ALIAS = {"sklearn": "scikit-learn", "cv2": "opencv-python", "pil": "pillow", "yaml": "pyyaml", "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "skimage": "scikit-image", "stable_baselines3": "stable-baselines3", "pygame": "pygame"}

# ------------------------------------------------------------ persistence
def init_tables(db): pass  # tables are created by db.SCHEMA (additive, IF NOT EXISTS)

def prior_hashes(db, repo): return {r["path"]: r["sha256"] for r in db.c.execute("SELECT path, sha256 FROM repo_files WHERE repo=? AND indexed=1", (repo,))}
def doc_id_for(db, repo, path):
    r = db.c.execute("SELECT id FROM documents WHERE filename=?", (f"github:{repo} · {path}",)).fetchone(); return r[0] if r else None
def doc_exists(db, repo, path): return doc_id_for(db, repo, path) is not None
def prior_structure(db, repo, path):
    r = db.c.execute("SELECT structure FROM repo_files WHERE repo=? AND path=?", (repo, path)).fetchone(); return json.loads(r[0]) if r and r[0] else None

def manifest_stats(rows):
    c = lambda f: sum(1 for r in rows if f(r))
    return {"discovered": len(rows), "downloaded": c(lambda r: r["sha256"] is not None), "parsed": c(lambda r: r["text_extracted"] or r["indexed"]), "indexed": c(lambda r: r["indexed"]),
            "binary": c(lambda r: r["kind"] in ("audio", "image", "video", "model", "data", "archive", "binary")), "metadata_only": c(lambda r: r["status"] == "METADATA_ONLY"),
            "unsupported": c(lambda r: r["status"] == "METADATA_ONLY" and r["kind"] == "binary"), "oversized": c(lambda r: (r["reason"] or "").startswith("oversized")),
            "excluded": c(lambda r: r["status"] == "EXCLUDED"), "failed": c(lambda r: r["status"] == "FAILED"),
            "by_status": {s: c(lambda r, s=s: r["status"] == s) for s in ("DISCOVERED", "DOWNLOADED", "PARSED", "INDEXED", "METADATA_ONLY", "EXCLUDED", "FAILED") if c(lambda r, s=s: r["status"] == s)}}

def stats_sentence(rid, br, commit, source, s, reused=0):
    msg = (f"{rid}@{(commit or 'unknown')[:10]} ({br}, via {source}): recursively inspected {s['discovered']} files; downloaded {s['downloaded']}; parsed {s['parsed']}; indexed {s['indexed']}"
           f"{f' ({reused} unchanged, reused)' if reused else ''}; metadata-only {s['metadata_only']} (binary {s['binary']}, unsupported {s['unsupported']}, oversized {s['oversized']}); excluded {s['excluded']}; failed {s['failed']}")
    return msg + ("  ⚠ PARTIAL: some files failed processing" if s["failed"] else "")

def persist_scan(db, repo, branch, commit, source, truncated, stats, rows, notes):
    old = {r["path"]: r["doc_id"] for r in db.c.execute("SELECT path, doc_id FROM repo_files WHERE repo=?", (repo,))}
    now_paths = {r["path"] for r in rows}
    for p, did in old.items():  # files removed from the repo, or re-indexed under a new content hash, must not linger
        new = next((r["doc_id"] for r in rows if r["path"] == p), None)
        if did and (p not in now_paths or (new and new != did)): db.delete_document(did)
    db.c.execute("DELETE FROM repo_files WHERE repo=?", (repo,))
    db.c.executemany("INSERT INTO repo_files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [(repo, r["path"], commit, r["ext"], r["kind"], r["size"], r["sha256"], r["status"], r["reason"], r["text_extracted"], r["indexed"], r["doc_id"],
        json.dumps(r["structure"]) if r["structure"] is not None else None, json.dumps(r["meta"] or {})) for r in rows])
    db.c.execute("INSERT OR REPLACE INTO repo_scans VALUES(?,?,?,?,?,?,?,?)", (repo, branch, commit, source, int(truncated), json.dumps(stats), json.dumps(notes), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    db.c.commit()

def has_repo(db): return db.c.execute("SELECT COUNT(*) FROM repo_scans").fetchone()[0] > 0

def manifest(db, repo=None, status=None, limit=None):
    sc = db.c.execute("SELECT * FROM repo_scans" + (" WHERE repo=?" if repo else " ORDER BY scanned_at DESC LIMIT 1"), (repo,) if repo else ()).fetchone()
    if not sc: return None
    q = "SELECT path,ext,kind,size,sha256,status,reason,text_extracted,indexed,doc_id,meta,commit_sha FROM repo_files WHERE repo=?" + (" AND status=?" if status else "") + " ORDER BY path"
    rows = [dict(r) for r in db.c.execute(q, (sc["repo"], status) if status else (sc["repo"],))]
    for r in rows: r["meta"] = json.loads(r["meta"] or "{}")
    return {"repository": sc["repo"], "branch": sc["branch"], "commit": sc["commit_sha"], "source": sc["source"], "truncated": bool(sc["truncated"]), "scanned_at": sc["scanned_at"],
            "stats": json.loads(sc["stats"]), "notes": json.loads(sc["notes"] or "[]"), "files": rows[:limit] if limit else rows}

# ------------------------------------------------------------ facets
UI_LIBS = {"pygame", "tkinter", "kivy", "pyqt5", "pyqt6", "pyside2", "pyside6", "wx", "arcade", "pyglet", "turtle", "react", "react-dom", "vue", "svelte", "angular", "jquery", "three", "phaser", "streamlit", "gradio", "dash", "customtkinter", "curses", "pixi.js"}
API_LIBS = {"flask", "fastapi", "django", "aiohttp", "starlette", "tornado", "bottle", "sanic", "express", "koa", "fastify", "uvicorn", "gunicorn", "grpc", "requests", "httpx", "axios", "socket", "socketserver", "websockets", "http", "urllib"}
DB_LIBS = {"sqlite3", "sqlalchemy", "pymongo", "psycopg2", "psycopg", "mysql", "peewee", "redis", "mongoose", "sequelize", "typeorm", "knex", "prisma"}
ML_LIBS = {"sklearn", "torch", "tensorflow", "keras", "xgboost", "lightgbm", "catboost", "joblib", "scipy", "cv2", "mediapipe", "onnxruntime", "jax", "statsmodels", "torchvision", "torchaudio", "librosa", "optuna", "imblearn"}
RL_LIBS = {"stable_baselines3", "sb3_contrib", "gym", "gymnasium", "ray", "tianshou", "pettingzoo", "tf_agents"}
LLM_LIBS = {"openai", "anthropic", "transformers", "langchain", "langchain_core", "llama_index", "huggingface_hub", "sentence_transformers", "cohere", "ollama", "groq", "litellm", "chromadb", "faiss", "pinecone", "tiktoken"}
DATA_LIBS = {"pandas", "csv", "pyarrow", "polars", "dask", "pyspark", "airflow", "luigi", "prefect", "openpyxl", "xlrd", "h5py"}
SEC_LIBS = {"hashlib", "hmac", "secrets", "jwt", "bcrypt", "passlib", "cryptography", "ssl", "authlib", "itsdangerous", "oauthlib"}
N = lambda rx: re.compile(rx, re.I)
UI_SYM = N(r"(draw|render|display|animate|animation|sprite|screen|window|button|menu|hud|cutscene|cinematic|panel|widget|scene|ui_|_ui\b|overlay|camera|effect)")
ML_SYM = N(r"(feature|preprocess|normali[sz]e|train|predict|classif|infer|evaluat|metric|dataset|extract_landmarks|landmark|embedding)")
DEC_SYM = N(r"(policy|observation|\bobs\b|reward|decide|decision|choose_action|select_action|agent|controller|tactic|strategy|opponent|\bai\b|_ai\b|ai_)")
SEC_SYM = N(r"(auth|token|password|permission|sanitiz|encrypt|decrypt|login|csrf|secret)")
DATA_SYM = N(r"(load_data|read_csv|etl|clean|transform|ingest|pipeline|schema|migrat)")
INFRA_PATH = N(r"(^|/)(dockerfile|docker-compose[\w.]*|\.github/workflows/.+|\.gitlab-ci\.yml|jenkinsfile|makefile|procfile|render\.yaml|requirements[\w-]*\.txt|package\.json|pyproject\.toml|setup\.py|setup\.cfg|\.env\.example|nginx\.conf|[\w.-]+\.tf|tox\.ini)$")

def _mods(S):
    out = {}
    for im in S.get("imports") or []:
        m = (im["module"] or "").lstrip("./")
        top = m.split(".")[0].split("/")[0] if not m.startswith("@") else "/".join(m.split("/")[:2])
        if top: out.setdefault(top.lower(), im["line"])
        for n in im.get("names") or []: out.setdefault(n.lower(), im["line"])
    return out

def file_facets(path, kind, S):
    """facet -> {count, evidence[{line, what}]} derived from source structure (imports, symbols, calls), not README words."""
    F = defaultdict(lambda: {"count": 0, "evidence": []})
    def add(f, line, what, w=1):
        F[f]["count"] += w
        if len(F[f]["evidence"]) < 6: F[f]["evidence"].append({"line": line, "what": what})
    S = S or {}
    if is_test_path(path): add("test", 1, f"test module ({len(S.get('tests') or [])} test functions)", 3)
    if INFRA_PATH.search(path) or (kind == "source" and path.endswith((".sh", ".tf"))): add("infra", 1, f"build/deploy/config file {PurePosixPath(path).name}", 3)
    ext = PurePosixPath(path).suffix.lower()
    if ext in (".html", ".htm", ".css", ".scss", ".jsx", ".tsx", ".vue", ".svelte"): add("ui", 1, f"{ext} markup/style/component file", 3)
    if ext == ".sql" or S.get("schemas"): add("persistence", (S.get("schemas") or [{"line": 1}])[0]["line"], "defines database schema/tables", 2)
    mods = _mods(S)
    for lib_set, facet, label, w in ((UI_LIBS, "ui", "UI library", 2), (API_LIBS, "api", "service/network library", 1), (DB_LIBS, "persistence", "database library", 2), (ML_LIBS, "ml", "ML library", 2),
                                     (RL_LIBS, "rl", "reinforcement-learning library", 3), (LLM_LIBS, "llm", "LLM/GenAI library", 3), (DATA_LIBS, "data", "data library", 2), (SEC_LIBS, "security", "security/crypto library", 2)):
        for m, ln in mods.items():
            if m in lib_set: add(facet, ln, f"imports {label} `{m}`", w)
    for e in S.get("endpoints") or []: add("api", e["line"], f"endpoint {e['method']} {e['path']}", 3)
    for c in S.get("model_ctors") or []:
        add("rl" if c["name"] in ("PPO", "DQN", "A2C", "SAC", "TD3", "DDPG") else "ml", c["line"], f"constructs model `{c['name']}`", 3)
    for c in S.get("fit_calls") or []: add("ml", c["line"], f"training call `{c['call']}`", 3)
    for c in S.get("predict_calls") or []: add("ml", c["line"], f"inference call `{c['call']}`", 2)
    for c in S.get("model_loads") or []: add("model_io", c["line"], f"loads model via `{c['call']}`" + (f" from '{c['path_arg']}'" if c.get("path_arg") else ""), 3)
    for t in S.get("try_blocks") or []: add("errors", t["start"], "try/except: " + ", ".join(h["type"] for h in t["handlers"])[:60], 1)
    for d in S.get("data_refs") or []: add("data", d["line"], f"references data file '{d['value']}'", 1)
    for a in S.get("audio_refs") or []: add("assets", a["line"], f"references audio asset '{a['value']}'", 1)
    for v in S.get("env_vars") or []:
        if re.search(r"KEY|TOKEN|SECRET|PASS", v["name"], re.I): add("security", v["line"], f"reads secret env var {v['name']}", 2)
    syms = [(c["name"], c["line"]) for c in S.get("classes") or []] + [(f["name"], f["line"]) for f in S.get("functions") or []] + [(m["name"], m["line"]) for c in S.get("classes") or [] for m in c.get("methods") or []]
    for name, ln in syms:
        for rx, facet, label in ((UI_SYM, "ui", "rendering/UI symbol"), (ML_SYM, "ml", "ML symbol"), (DEC_SYM, "decision", "decision-logic symbol"), (SEC_SYM, "security", "security symbol"), (DATA_SYM, "data", "data-handling symbol")):
            if rx.search(name): add(facet, ln, f"{label} `{name}`", 1)
    nsym = len(S.get("classes") or []) + len(S.get("functions") or []) + sum(len(c.get("methods") or []) for c in S.get("classes") or [])
    ui_only = "ui" in F and F["ui"]["count"] >= 4 and "api" not in F and not S.get("classes")
    if kind == "source" and not is_test_path(path) and nsym >= 1 and not ui_only:
        add("core_logic", (syms[0][1] if syms else 1), f"{len(S.get('classes') or [])} classes / {len(S.get('functions') or [])} functions of application logic", 2)
    return {k: v for k, v in F.items() if v["count"] > 0}

# ------------------------------------------------------------ model
def _resolve(path, im, paths, lang):
    """Internal import resolution -> repo path or None."""
    mod = im["module"] or ""
    if lang == "python":
        base = PurePosixPath(path).parent.parts
        parts = (list(base[:len(base) - (im["level"] - 1)]) if im["level"] else []) + [p for p in mod.split(".") if p]
        cands = ["/".join(parts) + ".py", "/".join(parts + ["__init__"]) + ".py"] + ["/".join(parts + [n]) + ".py" for n in im.get("names") or []]
        for c in cands:
            if c in paths: return c
            hit = [p for p in paths if p.endswith("/" + c)]
            if len(hit) == 1: return hit[0]
        return None
    if mod.startswith("."):
        norm = PurePosixPath(PurePosixPath(path).parent, mod); parts = []
        for x in norm.parts:
            if x == "..": parts and parts.pop()
            elif x != ".": parts.append(x)
        b = "/".join(parts)
        for c in [b] + [b + e for e in (".js", ".jsx", ".ts", ".tsx", ".vue")] + [b + "/index" + e for e in (".js", ".ts", ".jsx", ".tsx")]:
            if c in paths: return c
    return None

def _component_of(path): parts = path.split("/"); return parts[0] if len(parts) > 1 else path

def load(db, repo=None):
    sc = db.c.execute("SELECT * FROM repo_scans" + (" WHERE repo=?" if repo else " ORDER BY scanned_at DESC LIMIT 1"), (repo,) if repo else ()).fetchone()
    if not sc: return None
    files = {}
    for r in db.c.execute("SELECT * FROM repo_files WHERE repo=?", (sc["repo"],)):
        d = dict(r); d["structure"] = json.loads(d["structure"]) if d["structure"] else None; d["meta"] = json.loads(d["meta"] or "{}"); files[d["path"]] = d
    docs = {d["id"]: d for d in db.documents()}; byp = defaultdict(list)
    for c in db.chunks():
        p = (c.get("meta") or {}).get("path")
        if p and (c.get("meta") or {}).get("repository") == sc["repo"]: byp[p].append(c)
    return {"repo": sc["repo"], "branch": sc["branch"], "commit": sc["commit_sha"], "files": files, "chunks": byp, "docs": docs, "stats": json.loads(sc["stats"]), "truncated": bool(sc["truncated"])}

def source_lines(M, path):
    """{line_no: text} reconstructed from stored chunks (code chunks carry a context header that is not source)."""
    cache = M.setdefault("_lines", {})
    if path in cache: return cache[path]
    out = {}
    for c in sorted(M["chunks"].get(path, []), key=lambda c: c["idx"]):
        ls = (c["meta"] or {}).get("line_start"); body = c["text"].splitlines()
        if ls is None: continue
        if body and body[0].startswith("[") and " :: " in body[0]: body = body[1:]
        if body and body[0].startswith("# imports:"): body = body[1:]
        for i, t in enumerate(body): out[ls + i] = t
    cache[path] = out; return out

def doc_text(M, path): return "\n".join(c["text"] for c in sorted(M["chunks"].get(path, []), key=lambda c: c["idx"]))

def cite(path, a=None, b=None): return f"{path}" + (f":{a}" + (f"-{b}" if b and b != a else "") if a else "")

def evidence_ref(M, path, a=None, b=None):
    ch = next((c for c in M["chunks"].get(path, []) if a and (c["meta"] or {}).get("line_start") and c["meta"]["line_start"] <= a <= (c["meta"].get("line_end") or 0)), None) or (M["chunks"].get(path) or [None])[0]
    q = " ".join(source_lines(M, path).get(i, "").strip() for i in range(a or 0, (b or a or 0) + 1))[:300] if a else ""
    return {"path": path, "line_start": a, "line_end": b or a, "chunk_id": ch["id"] if ch else None, "doc_id": ch["doc_id"] if ch else None, "quote": q or (ch["text"][:200] if ch else ""),
            "document": (M["docs"].get(ch["doc_id"]) or {}).get("filename") if ch else f"github:{M['repo']} · {path}",
            "url": f"https://github.com/{M['repo']}/blob/{M['commit'] or M['branch']}/{path}" + (f"#L{a}" + (f"-L{b}" if b and b != a else "") if a else "")}

ALGOS = {"Random Forest": r"random\s*forest|RandomForest", "PPO (Proximal Policy Optimization)": r"\bPPO\b|proximal\s+policy", "DQN": r"\bDQN\b|deep\s+q", "Q-learning": r"q[- ]learning", "A2C": r"\bA2C\b",
         "CNN": r"\bCNN\b|Conv2d|convolutional", "LSTM/GRU": r"\bLSTM\b|\bGRU\b", "Transformer": r"transformer", "k-NN": r"k-?nn\b|KNeighbors", "SVM": r"\bSVM\b|\bSVC\b|support vector", "Logistic Regression": r"logistic\s*regression|LogisticRegression",
         "Gradient Boosting/XGBoost": r"xgboost|XGB\w+|LightGBM|gradient\s*boost", "K-Means": r"k-?means", "Decision Tree": r"decision\s*tree|DecisionTree", "A* search": r"\bA\*|a_star|astar", "Minimax": r"minimax|alpha[- ]beta",
         "Naive Bayes": r"naive\s*bayes|GaussianNB", "Linear Regression": r"linear\s*regression|LinearRegression", "Hand-landmark tracking (MediaPipe)": r"mediapipe"}
KNOWN_DATASETS = r"\b(mnist|fashion-?mnist|cifar-?10{0,2}|imagenet|coco|iris|kaggle|uci|wine quality|titanic|boston housing|squad|glue|imdb|wikitext|librispeech|common voice|asl alphabet|hagrid)\b"
TECHS = {"pygame", "numpy", "pandas", "scikit-learn", "pytorch", "torch", "tensorflow", "keras", "stable-baselines3", "gymnasium", "gym", "opencv", "mediapipe", "fastapi", "flask", "django", "react", "vue", "express", "sqlite", "postgresql",
         "mongodb", "redis", "docker", "networkx", "matplotlib", "xgboost", "pillow", "pyttsx3", "openai", "transformers", "streamlit", "tkinter", "joblib", "scipy"}

def _requirements(M):
    deps = {}
    for p, f in M["files"].items():
        n = PurePosixPath(p).name.lower(); t = doc_text(M, p) if f["indexed"] else ""
        if n.startswith("requirements") and n.endswith(".txt"):
            for i, ln in enumerate(t.splitlines(), 1):
                m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*(?:\[[^\]]*\])?\s*(==|>=|<=|~=|>|<|!=)?\s*([\w.*-]+)?", ln)
                if m and not ln.lstrip().startswith(("#", "-", "[")) and ln.strip() and not ln.startswith("["): deps[m.group(1).lower()] = {"file": p, "pinned": m.group(2) == "==", "spec": (m.group(2) or "") + (m.group(3) or ""), "line": None}
        elif n == "package.json":
            try:
                j = json.loads(re.sub(r"^\[[^\n]*\n", "", t)) if t.strip().startswith("{") else {}
                for k in ("dependencies", "devDependencies"):
                    for name, v in (j.get(k) or {}).items(): deps[name.lower()] = {"file": p, "pinned": bool(re.match(r"^\d", str(v))), "spec": str(v), "line": None}
            except Exception: pass
        elif n == "pyproject.toml":
            for m in re.finditer(r'"([A-Za-z0-9_.-]+)\s*(?:[<>=~!][^"]*)?"', t.split("dependencies", 1)[-1][:800] if "dependencies" in t else ""): deps.setdefault(m.group(1).lower(), {"file": p, "pinned": False, "spec": "", "line": None})
    return deps

def build_model(db, repo=None):
    M = load(db, repo)
    if not M: return None
    F = M["files"]; paths = set(F); G = nx.MultiDiGraph(); proj = f"project:{M['repo']}"
    G.add_node(proj, type="PROJECT", name=M["repo"])
    def edge(u, v, rel, basis, ev=None):
        if G.has_edge(u, v, key=rel):
            if ev and ev not in G[u][v][rel]["evidence"]: G[u][v][rel]["evidence"].append(ev)
        else: G.add_edge(u, v, key=rel, basis=basis, evidence=[ev] if ev else [])
    facets, comps, internal, external, test_of = {}, defaultdict(list), defaultdict(list), defaultdict(lambda: defaultdict(list)), defaultdict(set)
    for p, f in F.items():
        if f["status"] != "INDEXED" or f["kind"] not in ("source", "config") or f["structure"] is None: continue
        S = f["structure"]; facets[p] = file_facets(p, f["kind"], S); lang = S.get("language")
        if f["kind"] == "source" and not is_test_path(p): comps[_component_of(p)].append(p)
        for im in S.get("imports") or []:
            tgt = _resolve(p, im, paths, lang)
            top = (im["module"] or "").split(".")[0].split("/")[0] if not (im["module"] or "").startswith("@") else "/".join(im["module"].split("/")[:2])
            if tgt and tgt != p:
                internal[p].append((tgt, im["line"]))
                if is_test_path(p): test_of[tgt].add(p)
            elif top and not (im["module"] or "").startswith(".") and top not in STDLIB and top != "": external[p][top].append(im["line"])
    for p in F:
        if F[p]["status"] == "INDEXED" and F[p]["kind"] == "source" and is_test_path(p) and F[p]["structure"]:
            S = F[p]["structure"]; stem = PurePosixPath(p).stem.replace("test_", "").replace("_test", "")
            for q in paths:  # name-based association is inferred, import-based is extracted
                if PurePosixPath(q).stem == stem and not is_test_path(q) and F[q]["kind"] == "source": test_of[q].add(p); edge("file:" + p, "file:" + q, "TESTS", "inferred", {"path": p, "line_start": 1, "note": "matched by file name"})
    for p, ts in test_of.items():
        for t in ts: edge("file:" + t, "file:" + p, "TESTS", "extracted", {"path": t, "line_start": next((l for (x, l) in internal.get(t, []) if x == p), 1)})
    for c, ps in comps.items():
        cid = f"component:{c}"; G.add_node(cid, type="COMPONENT", name=PurePosixPath(c).stem if "/" not in c and "." in c else c); edge(proj, cid, "CONTAINS", "extracted")
        for p in ps:
            G.add_node("file:" + p, type="FILE", name=p); edge(cid, "file:" + p, "IMPLEMENTED_IN", "extracted", {"path": p, "line_start": 1})
            for tgt, ln in internal.get(p, []):
                if tgt in F and F[tgt]["kind"] == "source":
                    G.add_node("file:" + tgt, type="FILE", name=tgt); edge("file:" + p, "file:" + tgt, "IMPORTS", "extracted", {"path": p, "line_start": ln})
                    if not is_test_path(tgt) and _component_of(tgt) != c: edge(cid, f"component:{_component_of(tgt)}", "DEPENDS_ON", "extracted", {"path": p, "line_start": ln, "note": f"imports {tgt}"})
            for mod, lns in external.get(p, {}).items():
                tid = "tech:" + mod.lower(); G.add_node(tid, type="TECHNOLOGY", name=ALIAS.get(mod.lower(), mod)); edge("file:" + p, "module:" + mod, "IMPORTS", "extracted", {"path": p, "line_start": lns[0]}); G.add_node("module:" + mod, type="MODULE", name=mod)
                edge(cid, tid, "USES", "extracted", {"path": p, "line_start": lns[0]})
    M.update(graph=G, facets=facets, comps=dict(comps), internal=dict(internal), external={k: dict(v) for k, v in external.items()}, test_of={k: sorted(v) for k, v in test_of.items()}, deps=_requirements(M))
    _inventories(M); return M

def _inventories(M):
    F = M["files"]; G = M["graph"]; algos, datasets, models, apis, audio, techs, limits = {}, {}, [], [], {"present": [], "referenced": defaultdict(list)}, {}, []
    code = {p: f for p, f in F.items() if f["structure"] and f["kind"] == "source"}
    docs = [p for p, f in F.items() if f["kind"] == "doc" and f["indexed"]]
    base_idx = defaultdict(list)
    for p in F: base_idx[PurePosixPath(p).name.lower()].append(p)
    # algorithms: verified in code (ctors/imports/symbols/chunks) vs documented only
    for name, rx in ALGOS.items():
        R = re.compile(rx, re.I); code_ev, doc_ev = [], []
        for p, f in code.items():
            if is_test_path(p): continue
            for c in M["chunks"].get(p, []):
                m = R.search("\n".join(c["text"].splitlines()[1:]) if c["text"].startswith("[") else c["text"])
                if m: code_ev.append((p, c["meta"].get("line_start"), c["meta"].get("line_end")));  break
        for p in docs:
            for c in M["chunks"].get(p, []):
                if R.search(c["text"]): doc_ev.append((p, None, None)); break
        if code_ev or doc_ev: algos[name] = {"name": name, "status": "verified_in_code" if code_ev else "documented_only", "code_evidence": code_ev[:4], "doc_evidence": doc_ev[:2]}
    M["algorithms"] = algos
    # datasets
    for p, f in F.items():
        if f["status"] in ("INDEXED", "METADATA_ONLY") and (f["ext"] in (".csv", ".npy", ".npz", ".parquet", ".jsonl", ".feather", ".tsv", ".h5", ".mat") or (f["kind"] == "data")) and not is_test_path(p) and "sample" not in p.lower().split("/")[:-1][-1:] :
            datasets.setdefault(p, {"name": p, "status": "present_in_repo", "evidence": [(p, None, None)], "size": f["size"], "meta": f["meta"]})
    for p, f in code.items():
        for d in f["structure"].get("data_refs") or []:
            nm = PurePosixPath(d["value"].replace("\\", "/")).name.lower(); hit = base_idx.get(nm)
            key = hit[0] if hit else d["value"]
            e = datasets.setdefault(key, {"name": key, "status": "present_in_repo" if hit else "referenced_in_code_not_in_repo", "evidence": [], "size": F[hit[0]]["size"] if hit else None, "meta": {}})
            e["evidence"].append((p, d["line"], d["line"]))
    for p in docs:
        for c in M["chunks"].get(p, []):
            for m in re.finditer(KNOWN_DATASETS, c["text"], re.I):
                k = m.group(1).lower(); e = datasets.setdefault("doc:" + k, {"name": m.group(1), "status": "mentioned_in_docs_only", "evidence": [], "size": None, "meta": {}}); e["evidence"].append((p, None, None))
            for ln in c["text"].splitlines():
                if re.search(r"(trained|training)\s+(on|with|using)|dataset\s*(?:is|was|:)|data\s*collected|collected\s+(?:using|by)", ln, re.I):
                    k = "doc-sentence:" + re.sub(r"\W+", " ", ln.lower())[:60]; datasets.setdefault(k, {"name": ln.strip()[:200], "status": "documented_statement", "evidence": [(p, None, None)], "size": None, "meta": {}})
    M["datasets"] = datasets
    # models / checkpoints
    for p, f in F.items():
        if f["kind"] == "model": models.append({"path": p, "kind": "checkpoint/model file", "size": f["size"], "meta": f["meta"], "status": "present_in_repo"})
    loads = []
    for p, f in code.items():
        for ml in f["structure"].get("model_loads") or []:
            nm = PurePosixPath((ml.get("path_arg") or "").replace("\\", "/")).name.lower(); present = bool(nm and base_idx.get(nm)) if nm else None
            loads.append({"file": p, **ml, "present": present})
    M["models"], M["model_loads"] = models, loads
    # APIs and frontend calls
    for p, f in code.items():
        for e in f["structure"].get("endpoints") or []:
            apis.append({"method": e["method"], "path": e["path"], "file": p, "line": e["line"], "handler": e.get("handler")}); G.add_node("api:%s %s" % (e["method"], e["path"]), type="API", name=f"{e['method']} {e['path']}")
            M["graph"].add_edge("api:%s %s" % (e["method"], e["path"]), "file:" + p, key="IMPLEMENTED_IN", basis="extracted", evidence=[{"path": p, "line_start": e["line"]}])
    calls = [{"file": p, "line": c["line"], "url": c["url"]} for p, f in F.items() if f["structure"] for c in f["structure"].get("http_calls") or []]
    for c in calls:
        c["matches"] = [a for a in apis if a["path"].rstrip("/") and (re.sub(r"https?://[^/]+", "", c["url"]).split("?")[0].rstrip("/").endswith(a["path"].rstrip("/")) or a["path"].rstrip("/") in c["url"])]
    M["apis"], M["api_calls"] = apis, calls
    # audio inventory: actual files vs names referenced
    seen = {}
    for p, f in F.items():
        if f["kind"] == "audio": seen.setdefault(f["sha256"] or p, []).append(p)
    audio["present"] = [{"path": v[0], "size": F[v[0]]["size"], "duplicates": v[1:], "meta": F[v[0]]["meta"]} for v in seen.values()]
    for p, f in code.items():
        for a in f["structure"].get("audio_refs") or []: audio["referenced"][PurePosixPath(a["value"].replace("\\", "/")).name.lower()].append((p, a["line"]))
    for p in docs:
        for c in M["chunks"].get(p, []):
            for m in re.findall(r"[\w./-]+\.(?:wav|mp3|ogg|flac|m4a)\b", c["text"], re.I): audio["referenced"][PurePosixPath(m).name.lower()].append((p, None))
    pres = {PurePosixPath(a["path"]).name.lower() for a in audio["present"]} | {PurePosixPath(d).name.lower() for a in audio["present"] for d in a["duplicates"]}
    audio["referenced_missing"] = sorted(n for n in audio["referenced"] if n not in pres); audio["present_unreferenced"] = [a["path"] for a in audio["present"] if PurePosixPath(a["path"]).name.lower() not in audio["referenced"]]
    M["audio"] = audio
    # technologies: manifest (declared) / code imports (verified) / docs (documented)
    for name, d in M["deps"].items(): techs.setdefault(name, {"name": name, "sources": set(), "evidence": []})["sources"].add("declared"); techs[name]["evidence"].append((d["file"], None, None))
    for p, mods in M["external"].items():
        for mod, lns in mods.items():
            k = ALIAS.get(mod.lower(), mod.lower()); t = techs.setdefault(k, {"name": k, "sources": set(), "evidence": []}); t["sources"].add("imported_in_code"); t["evidence"].append((p, lns[0], lns[0]))
    rtext = {p: doc_text(M, p).lower() for p in docs}
    for tname in TECHS:
        for p, tx in rtext.items():
            if re.search(r"(?<![a-z0-9])" + re.escape(tname) + r"(?![a-z0-9])", tx):
                k = ALIAS.get(tname, tname); k = {"pytorch": "torch"}.get(k, k); t = techs.setdefault(k, {"name": k, "sources": set(), "evidence": []}); t["sources"].add("mentioned_in_docs"); t["evidence"].append((p, None, None)); break
    for t in techs.values(): t["status"] = "verified_in_code" if "imported_in_code" in t["sources"] else ("declared_dependency" if "declared" in t["sources"] else "documented_only"); t["sources"] = sorted(t["sources"])
    M["technologies"] = techs
    for k, t in techs.items():
        if t["status"] != "documented_only" and any(s == "imported_in_code" for s in t["sources"]): pass
    # limitations / partial implementation
    for p, f in code.items():
        for s in f["structure"].get("stubs") or []: limits.append({"kind": "stub", "text": f"`{s['name']}` is a stub (pass / NotImplementedError)", "file": p, "line": s["line"]})
        for i, t in enumerate(f["structure"].get("try_blocks") or []):
            for h in t["handlers"]:
                if h["swallows"]: limits.append({"kind": "swallowed_exception", "text": f"exception `{h['type']}` is silently swallowed", "file": p, "line": h["line"]})
        if f["structure"].get("parse_error"): limits.append({"kind": "parse_error", "text": f"AST parse failed: {f['structure']['parse_error']}", "file": p, "line": 1})
        for ln, tx in source_lines(M, p).items():
            m = re.search(r"(?:#|//)\s*(TODO|FIXME|HACK|XXX)\b[:\s-]*(.*)", tx)
            if m and m.group(2).strip(): limits.append({"kind": "todo", "text": f"{m.group(1)}: {m.group(2).strip()[:140]}", "file": p, "line": ln})
    for ml in loads:
        if not ml["guarded"]: limits.append({"kind": "unguarded_model_load", "text": f"`{ml['call']}` is not inside try/except or an existence check" + (f" (path '{ml['path_arg']}')" if ml.get("path_arg") else ""), "file": ml["file"], "line": ml["line"]})
        if ml["present"] is False: limits.append({"kind": "missing_artifact", "text": f"model file '{ml['path_arg']}' is loaded but not present in the repository", "file": ml["file"], "line": ml["line"]})
    for p in docs:
        for c in M["chunks"].get(p, []):
            if re.search(r"limitation|known issue|not (?:yet )?implemented|future work|roadmap|caveat", (c.get("section") or ""), re.I):
                for ln in [l.strip(" -*") for l in c["text"].splitlines() if l.strip() and not l.startswith("#")][:6]: limits.append({"kind": "documented", "text": ln[:200], "file": p, "line": None})
    for p, f in F.items():
        if f["status"] == "FAILED": limits.append({"kind": "failed_file", "text": f"file could not be processed: {f['reason']}", "file": p, "line": None})
    M["limitations"] = limits
    M["entry_points"] = [{"file": p, **e} for p, f in code.items() for e in f["structure"].get("entry_points") or []]

# ------------------------------------------------------------ summary
def _ev(M, items, n=3): return [evidence_ref(M, p, a, b) for p, a, b in items[:n]]
def project_summary(db, repo=None):
    M = build_model(db, repo)
    if not M: return None
    F = M["files"]; G = M["graph"]; S = []
    def sec(title, items): S.append({"title": title, "items": items})
    readmes = sorted([p for p in F if PurePosixPath(p).name.lower().startswith("readme") and F[p]["indexed"]], key=lambda p: p.count("/")); rd = readmes[0] if readmes else None
    para = next((l.strip() for c in sorted(M["chunks"].get(rd, []), key=lambda c: c["idx"]) for l in c["text"].splitlines() if len(l.strip()) > 50 and not l.startswith(("#", "!", "[", "<", "|", "-", "*", "`"))), None) if rd else None
    sec("1. Project objective", [{"text": para[:400], "status": "documented", "evidence": [evidence_ref(M, rd)]}] if para else [{"text": "No README objective statement found in the indexed files.", "status": "unverified", "evidence": []}])
    feats = []
    if rd:
        for c in M["chunks"].get(rd, []):
            if re.search(r"feature|capabilit|highlight|what it does|functionality", c.get("section") or "", re.I):
                for ln in c["text"].splitlines():
                    t = re.sub(r"^[\s\-*\d.]+", "", ln).strip(" *_`")
                    if 8 < len(t) < 200 and ln.lstrip().startswith(("-", "*", "•")) or re.match(r"\s*\d+\.", ln): feats.append(t)
    allsym = " ".join(c["text"].lower() for p, f in F.items() if f["kind"] == "source" and f["indexed"] for c in M["chunks"].get(p, [])) + " " + " ".join(p.lower() for p in F)
    fitems = []
    for t in feats[:12]:
        toks = [w for w in re.findall(r"[a-z]{5,}", t.lower())]; hit = [w for w in toks if w in allsym]
        fitems.append({"text": t, "status": "documented_with_matching_code_symbols" if hit else "documented_not_cross_verified", "evidence": [evidence_ref(M, rd)], "matched_code_terms": hit[:4]})
    sec("2. Problem being solved & main features", fitems or [{"text": "No explicit feature list was found in the README.", "status": "unverified", "evidence": []}])
    comps = []
    for c, ps in sorted(M["comps"].items()):
        deps = sorted({G.nodes[v]["name"] for _, v, k in G.out_edges(f"component:{c}", keys=True) if k == "DEPENDS_ON"}); fac = defaultdict(int)
        for p in ps:
            for k, v in (M["facets"].get(p) or {}).items(): fac[k] += v["count"]
        top = [k for k, _ in sorted(fac.items(), key=lambda x: -x[1]) if k not in ("errors",)][:3]
        comps.append({"text": f"`{c}` ({len(ps)} source file{'s' if len(ps) != 1 else ''}; {', '.join(top) or 'general logic'})" + (f"; depends on {', '.join(deps)}" if deps else ""), "status": "verified_in_code", "basis": "extracted", "evidence": [evidence_ref(M, ps[0], 1)]})
    sec("3. System architecture & important modules", comps or [{"text": "No parsed source components.", "status": "unverified", "evidence": []}])
    sec("4. Technologies used", [{"text": f"{t['name']} — {t['status'].replace('_', ' ')}", "status": t["status"], "evidence": _ev(M, t["evidence"], 2)} for t in sorted(M["technologies"].values(), key=lambda t: (t["status"] != "verified_in_code", t["name"]))])
    sec("5. Algorithms and models", [{"text": f"{a['name']} — {a['status'].replace('_', ' ')}", "status": a["status"], "evidence": _ev(M, a["code_evidence"] or a["doc_evidence"], 3)} for a in M["algorithms"].values()] +
        [{"text": f"model/checkpoint file `{m['path']}` ({m['size']} bytes; metadata only, contents not read)", "status": "present_in_repo", "evidence": [evidence_ref(M, m["path"])]} for m in M["models"]])
    sec("6. Datasets and training sources", [{"text": f"{d['name']} — {d['status'].replace('_', ' ')}", "status": d["status"], "evidence": _ev(M, d["evidence"], 2)} for d in M["datasets"].values()] or [{"text": "No dataset file, data reference or training-source statement was found.", "status": "unverified", "evidence": []}])
    ep = M["entry_points"]
    sec("7. Data flow and execution flow", [{"text": f"entry point `{e['kind']}` in {e['file']}", "status": "verified_in_code", "evidence": [evidence_ref(M, e["file"], e["line"])]} for e in ep[:6]] +
        [{"text": f"{u.split(':',1)[1]} imports {v.split(':',1)[1]}", "status": "verified_in_code", "basis": d.get("basis"), "evidence": [evidence_ref(M, e["path"], e["line_start"]) for e in d["evidence"][:1]]} for u, v, k, d in G.edges(keys=True, data=True) if k == "IMPORTS" and v.startswith("file:")][:12])
    sec("8. Integrations and APIs", [{"text": f"{a['method']} {a['path']} → {a['handler'] or 'handler'} in {a['file']}", "status": "verified_in_code", "evidence": [evidence_ref(M, a["file"], a["line"])]} for a in M["apis"]] +
        [{"text": f"{c['file']} calls {c['url']}" + (" (matches a local endpoint)" if c["matches"] else ""), "status": "verified_in_code", "evidence": [evidence_ref(M, c["file"], c["line"] or None)]} for c in M["api_calls"][:10]])
    tf = [p for p in F if is_test_path(p) and F[p]["indexed"]]
    sec("9. Testing and validation", [{"text": f"{p} ({len((F[p]['structure'] or {}).get('tests') or [])} tests)", "status": "verified_in_code", "evidence": [evidence_ref(M, p, 1)]} for p in tf] or [{"text": "No test files were found.", "status": "unverified", "evidence": []}])
    aud = M["audio"]; sec("10. Assets", [{"text": f"audio file `{a['path']}`" + (f" (+{len(a['duplicates'])} identical copies)" if a["duplicates"] else ""), "status": "present_in_repo", "evidence": [evidence_ref(M, a["path"])]} for a in aud["present"]] +
        [{"text": f"audio name `{n}` is referenced but no such file exists in the repository", "status": "referenced_not_present", "evidence": []} for n in aud["referenced_missing"]])
    sec("11. Limitations and unresolved questions", [{"text": l["text"], "status": l["kind"], "evidence": [evidence_ref(M, l["file"], l["line"])]} for l in M["limitations"][:25]] or [{"text": "No limitations were detected in code or documentation.", "status": "unverified", "evidence": []}])
    sec("12. Repository coverage", [{"text": stats_sentence(M["repo"], M["branch"], M["commit"], "manifest", M["stats"]), "status": "manifest", "evidence": []}])
    return {"repository": M["repo"], "branch": M["branch"], "commit": M["commit"], "stats": M["stats"], "truncated": M["truncated"], "sections": S,
            "graph": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges(), "edges_by_type": dict(__import__("collections").Counter(k for _, _, k in G.edges(keys=True)))}}


class ScopedDB:
    """Read-only view of ONE repository's evidence. Association comes from chunk provenance (meta.repository); legacy records without
    provenance are matched only on the ingestion-assigned `github:<owner/repo> · ` document name. Unknown association => excluded."""
    def __init__(self, db, repo):
        self._db, self.repo = db, repo; reps = defaultdict(set)
        for c in db.chunks(): reps[c["doc_id"]].add((c.get("meta") or {}).get("repository"))
        self.ids = set()
        for d in db.documents():
            r = reps.get(d["id"], set()) - {None}
            if repo in r or (not r and d["filename"].startswith(f"github:{repo} · ")): self.ids.add(d["id"])
    def documents(self): return [d for d in self._db.documents() if d["id"] in self.ids]
    def chunks(self): return [c for c in self._db.chunks() if c["doc_id"] in self.ids]
    def claims(self): return [c for c in self._db.claims() if c["doc_id"] in self.ids]
    def __getattr__(self, n): return getattr(self._db, n)

def scope(db, repo=None):
    """Scope to the selected (default: most recently scanned) repository when one is connected; otherwise return db unchanged."""
    if isinstance(db, ScopedDB) or not has_repo(db): return db
    sc = db.c.execute("SELECT repo FROM repo_scans" + (" WHERE repo=?" if repo else " ORDER BY scanned_at DESC LIMIT 1"), (repo,) if repo else ()).fetchone()
    return ScopedDB(db, sc["repo"]) if sc else db
