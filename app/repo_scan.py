"""Repository scanning: per-file classification/manifest, source-structure extraction (Python AST + lightweight regex for other
languages) and structure-aware code chunking with line ranges. Pure functions; persistence lives in repo_intel.py."""
import ast, json, re, struct, wave, io
from pathlib import PurePosixPath

TEXT_EXT = {".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc"}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".php", ".swift", ".kt", ".kts",
            ".rs", ".sh", ".bash", ".r", ".m", ".scala", ".vue", ".svelte", ".tf", ".sql", ".bat", ".ps1", ".lua"}
CONFIG_EXT = {".html", ".htm", ".css", ".scss", ".json", ".toml", ".xml", ".ini", ".cfg", ".properties", ".gradle", ".yaml", ".yml", ".env", ".conf", ".ipynb", ".gitignore"}
NAMED_TEXT = {"dockerfile", "makefile", "procfile", "requirements.txt", "license", "gemfile", "pipfile", "cmakelists.txt", ".env.example", ".dockerignore", ".gitignore"}
DOC_FILE_EXT = {".csv", ".pdf", ".docx", ".xlsx", ".xls"}  # parsed by the existing ingest_file()
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".mid", ".midi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
MODEL_EXT = {".pt", ".pth", ".ckpt", ".h5", ".hdf5", ".onnx", ".pkl", ".pickle", ".joblib", ".safetensors", ".pb", ".tflite", ".bin", ".model"}
DATA_BIN_EXT = {".npy", ".npz", ".parquet", ".feather", ".db", ".sqlite", ".sqlite3", ".pickle5", ".mat", ".tfrecord"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2"}
LOCK_RE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.lock|Pipfile\.lock|composer\.lock)$|\.min\.(js|css)$")
VENDOR_RE = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.git|\.venv|venv|env|__pycache__|target|\.next|coverage|\.idea|\.pytest_cache|site-packages)/")

def file_kind(path):
    """-> (kind, ext). kind in source|config|doc|document|audio|image|video|model|data|archive|binary."""
    p = PurePosixPath(path); ext = p.suffix.lower(); name = p.name.lower()
    if name in NAMED_TEXT or name.startswith("requirements") and ext == ".txt": return ("config" if name != "license" else "doc"), ext
    if ext in CODE_EXT: return "source", ext
    if ext in TEXT_EXT: return "doc", ext
    if ext in CONFIG_EXT: return "config", ext
    if ext in DOC_FILE_EXT: return "document", ext
    for k, s in (("audio", AUDIO_EXT), ("image", IMAGE_EXT), ("video", VIDEO_EXT), ("model", MODEL_EXT), ("data", DATA_BIN_EXT), ("archive", ARCHIVE_EXT)):
        if ext in s: return k, ext
    return "binary", ext

def is_test_path(path):
    n = PurePosixPath(path).name.lower()
    return bool(re.search(r"(^|/)(tests?|__tests__|spec)/", path.lower()) or n.startswith("test_") or n.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts", "_test.go")))

def binary_metadata(ext, data):
    """Only metadata that is genuinely read from the bytes (never claims to have read the contents)."""
    m = {}
    try:
        if ext == ".wav":
            with wave.open(io.BytesIO(data)) as w: m = {"channels": w.getnchannels(), "sample_rate": w.getframerate(), "duration_s": round(w.getnframes() / float(w.getframerate() or 1), 2)}
        elif ext == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n": m = dict(zip(("width", "height"), struct.unpack(">II", data[16:24])))
        elif ext == ".gif" and data[:3] == b"GIF": m = dict(zip(("width", "height"), struct.unpack("<HH", data[6:10])))
        elif ext == ".npy" and data[:6] == b"\x93NUMPY": m = {"header": data[10:10 + struct.unpack("<H", data[8:10])[0]].decode("latin1").strip()[:160]}
        elif ext in (".pt", ".pth", ".ckpt") and data[:2] == b"PK": m = {"format": "torch zip checkpoint"}
        elif ext in (".pkl", ".pickle", ".joblib"): m = {"format": "pickle/joblib (not unpickled)"}
    except Exception: pass
    return m

# ----------------------------------------------------------------- Python AST
_MODEL_CTORS = r"RandomForest\w*|GradientBoosting\w*|DecisionTree\w*|LogisticRegression|LinearRegression|SVC|SVR|KNeighbors\w*|KMeans|GaussianNB|MLPClassifier|XGB\w+|LGBM\w+|CatBoost\w*|PPO|DQN|A2C|SAC|TD3|DDPG|Sequential|LSTM|GRU|Conv2d|Transformer\w*|AutoModel\w*|Pipeline"
_MODEL_RE = re.compile(rf"^({_MODEL_CTORS})$")
_LOAD_NAMES = {"load_model", "torch.load", "joblib.load", "pickle.load", "PPO.load", "DQN.load", "A2C.load", "from_pretrained", "load_state_dict", "keras.models.load_model", "tf.keras.models.load_model", "onnxruntime.InferenceSession", "load_weights"}
_DATAFILE_RE = re.compile(r"\.(csv|npy|npz|parquet|jsonl|h5|pkl|tsv|feather)\b", re.I)
_AUDIO_RE = re.compile(r"[\w./\\-]+\.(?:wav|mp3|ogg|flac|m4a|mid)\b", re.I)
_HTTP = {"get", "post", "put", "delete", "patch", "head", "options"}

def _dotted(n):
    if isinstance(n, ast.Name): return n.id
    if isinstance(n, ast.Attribute):
        b = _dotted(n.value); return f"{b}.{n.attr}" if b else n.attr
    return ""

def _args(f):
    a = f.args; names = [x.arg for x in getattr(a, "posonlyargs", []) + a.args]
    if a.vararg: names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg: names.append("**" + a.kwarg.arg)
    return names

def _is_stub(f):
    body = [b for b in f.body if not (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant) and isinstance(b.value.value, str))]
    return bool(body) and all(isinstance(b, ast.Pass) or (isinstance(b, ast.Raise) and "NotImplementedError" in ast.dump(b)) or (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and b.value.value is Ellipsis) for b in body)

def _py_structure(text):
    tree = ast.parse(text); consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str) for t in n.targets if isinstance(t, ast.Name)}
    S = {"language": "python", "imports": [], "classes": [], "functions": [], "endpoints": [], "config": [], "env_vars": [],
        "model_loads": [], "model_ctors": [], "fit_calls": [], "predict_calls": [], "try_blocks": [], "stubs": [], "data_refs": [], "audio_refs": [],
        "entry_points": [], "http_calls": [], "tests": [], "module_doc": (ast.get_docstring(tree) or "").split("\n")[0][:200]}
    stack = []
    def guard_lines(node):  # line ranges of try-bodies / `if os.path.exists` guards, to judge whether a call is guarded
        return [(t.lineno, t.end_lineno) for t in ast.walk(tree) if isinstance(t, ast.Try) for _ in [0] if t.body and any(h for h in t.handlers)]
    tries = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Try):
            b0, b1 = n.body[0].lineno, n.body[-1].end_lineno
            hs = [{"type": _dotted(h.type) if h.type is not None else "bare", "line": h.lineno, "swallows": all(isinstance(x, (ast.Pass, ast.Continue)) for x in h.body),
                   "fallback": any(isinstance(x, (ast.Return, ast.Assign)) for x in h.body)} for h in n.handlers]
            if hs: tries.append((b0, b1)); S["try_blocks"].append({"start": n.lineno, "end": n.end_lineno, "body": [b0, b1], "handlers": hs})
    exists_guards = [(i.lineno, i.end_lineno) for i in ast.walk(tree) if isinstance(i, ast.If) and re.search(r"exists|isfile|is_file", ast.dump(i.test))]
    def guarded(line): return any(a <= line <= b for a, b in tries + exists_guards)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names: S["imports"].append({"module": a.name, "names": [], "alias": a.asname, "line": n.lineno, "level": 0})
        elif isinstance(n, ast.ImportFrom):
            S["imports"].append({"module": n.module or "", "names": [a.name for a in n.names], "alias": None, "line": n.lineno, "level": n.level})
        elif isinstance(n, ast.Call):
            fn = _dotted(n.func); short = fn.split(".")[-1]; ln = n.lineno
            if fn in _LOAD_NAMES or short in ("load_model", "from_pretrained", "load_weights") or fn.endswith(".load") and fn.split(".")[0] in ("torch", "joblib", "pickle", "PPO", "DQN", "A2C", "SAC", "np", "numpy"):
                arg = next((a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)), None) or next((consts[a.id] for a in n.args if isinstance(a, ast.Name) and a.id in consts), None)
                S["model_loads"].append({"call": fn, "line": ln, "path_arg": arg, "guarded": guarded(ln)})
            if _MODEL_RE.match(short): S["model_ctors"].append({"name": short, "line": ln})
            if short in ("fit", "learn", "train", "train_on_batch"): S["fit_calls"].append({"call": fn, "line": ln})
            if short in ("predict", "predict_proba", "forward", "generate", "run", "inference"): S["predict_calls"].append({"call": fn, "line": ln}) if short != "run" else None
            if fn in ("os.getenv", "os.environ.get") and n.args and isinstance(n.args[0], ast.Constant): S["env_vars"].append({"name": n.args[0].value, "line": ln})
            if short in ("get", "post", "put", "delete", "patch", "request", "fetch", "urlopen") and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str) and fn.split(".")[0] in ("requests", "httpx", "axios", "session", "client", "urllib"):
                S["http_calls"].append({"method": short.upper(), "url": n.args[0].value, "line": ln})
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 200:
            if _DATAFILE_RE.search(n.value) and "\n" not in n.value: S["data_refs"].append({"value": n.value, "line": n.lineno})
            for m in _AUDIO_RE.findall(n.value) if "\n" not in n.value else []: S["audio_refs"].append({"value": m, "line": n.lineno})
        elif isinstance(n, ast.If) and "__main__" in ast.dump(n.test): S["entry_points"].append({"line": n.lineno, "kind": "__main__"})
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", t.id):
                    try: v = ast.literal_eval(n.value); v = v if isinstance(v, (int, float, str, bool)) or v is None else str(v)[:80]
                    except Exception: v = "<expression>"
                    S["config"].append({"name": t.id, "value": str(v)[:80], "line": n.lineno})
    def func_info(f, cls=None):
        decos = [_dotted(d.func if isinstance(d, ast.Call) else d) for d in f.decorator_list]
        calls = sorted({c for c in (_dotted(x.func) for x in ast.walk(f) if isinstance(x, ast.Call)) if c})[:30]
        info = {"name": f.name, "args": _args(f), "line": f.lineno, "end": f.end_lineno, "doc": (ast.get_docstring(f) or "").split("\n")[0][:160], "calls": calls, "class": cls, "decorators": decos}
        for d in f.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                att = d.func.attr.lower()
                if att in _HTTP: S["endpoints"].append({"method": att.upper(), "path": d.args[0].value, "handler": f.name, "line": f.lineno})
                elif att == "route":
                    ms = next((k.value for k in d.keywords if k.arg == "methods"), None)
                    meths = [e.value.upper() for e in getattr(ms, "elts", []) if isinstance(e, ast.Constant)] or ["GET"]
                    for m in meths: S["endpoints"].append({"method": m, "path": d.args[0].value, "handler": f.name, "line": f.lineno})
        if _is_stub(f): S["stubs"].append({"name": f.name, "line": f.lineno})
        if f.name == "main": S["entry_points"].append({"line": f.lineno, "kind": "main()"})
        return info
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)): S["functions"].append(func_info(n))
        elif isinstance(n, ast.ClassDef):
            ms = [func_info(m, n.name) for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            S["classes"].append({"name": n.name, "bases": [_dotted(b) for b in n.bases], "line": n.lineno, "end": n.end_lineno, "doc": (ast.get_docstring(n) or "").split("\n")[0][:160], "methods": ms})
            if any(re.search(r"Model|Base|Schema|Table", _dotted(b)) for b in n.bases) and any(isinstance(x, ast.Assign) or isinstance(x, ast.AnnAssign) for x in n.body):
                S.setdefault("schemas", []).append({"name": n.name, "line": n.lineno, "bases": [_dotted(b) for b in n.bases]})
    S["tests"] = [f["name"] for f in S["functions"] if f["name"].startswith("test")] + [m["name"] for c in S["classes"] for m in c["methods"] if m["name"].startswith("test")]
    return S

# ----------------------------------------------------------- other languages
_LANG = {".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".vue": "javascript", ".svelte": "javascript", ".java": "java", ".go": "go", ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
         ".cs": "csharp", ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".sh": "shell", ".sql": "sql", ".html": "html", ".css": "css"}
_DEFS = {
 "javascript": [("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\("), ("function", r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"), ("class", r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)")],
 "java": [("class", r"^\s*(?:public\s+|abstract\s+|final\s+)*(?:class|interface|enum)\s+(\w+)"), ("function", r"^\s+(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\],]+\s+(\w+)\s*\([^;]*\)\s*(?:throws [\w, ]+)?\{")],
 "go": [("function", r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*\("), ("class", r"^type\s+(\w+)\s+(?:struct|interface)")],
 "rust": [("function", r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"), ("class", r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)")],
 "c": [("function", r"^[A-Za-z_][\w\s\*]+\s+\**(\w+)\s*\([^;]*\)\s*\{?\s*$")], "cpp": [("class", r"^\s*(?:class|struct)\s+(\w+)"), ("function", r"^[A-Za-z_][\w:\s\*&<>]+\s+\**(\w+)\s*\([^;]*\)\s*\{?\s*$")],
 "csharp": [("class", r"^\s*(?:public\s+|internal\s+)*(?:class|interface)\s+(\w+)")], "ruby": [("function", r"^\s*def\s+(\w+)"), ("class", r"^\s*class\s+(\w+)")],
 "php": [("function", r"^\s*(?:public\s+|private\s+)?function\s+(\w+)"), ("class", r"^\s*class\s+(\w+)")], "kotlin": [("function", r"^\s*(?:fun)\s+(\w+)"), ("class", r"^\s*(?:data\s+)?class\s+(\w+)")],
 "shell": [("function", r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{")], "typescript": None}
_DEFS["typescript"] = _DEFS["javascript"]
_IMP = [r"""import\s+(?:[\w*{}\s,]+\s+from\s+)?['"]([^'"]+)['"]""", r"""require\(\s*['"]([^'"]+)['"]\s*\)""", r"^\s*#include\s*[<\"]([^>\"]+)[>\"]", r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", r"""^\s*"([\w./-]+)"\s*$""", r"^\s*use\s+([\w:]+)"]
_EP = [r"""\b(?:app|router|api|server)\.(get|post|put|delete|patch)\(\s*['"]([^'"]+)['"]""", r"""@(Get|Post|Put|Delete|Patch)Mapping\(\s*(?:value\s*=\s*)?['"]([^'"]+)['"]"""]
_CALLS = r"""\b(fetch|axios\.(?:get|post|put|delete)|\$\.(?:get|post|ajax))\(\s*[`'"]([^`'"]+)[`'"]"""

def _generic_structure(path, text, lang):
    S = {"language": lang, "imports": [], "classes": [], "functions": [], "endpoints": [], "config": [], "env_vars": [], "model_loads": [], "model_ctors": [], "fit_calls": [], "predict_calls": [],
         "try_blocks": [], "stubs": [], "data_refs": [], "audio_refs": [], "entry_points": [], "http_calls": [], "tests": [], "module_doc": ""}
    lines = text.splitlines(); defs = []
    for i, ln in enumerate(lines, 1):
        for rx in _IMP:
            m = re.search(rx, ln)
            if m and not ln.lstrip().startswith(("//", "*")): S["imports"].append({"module": m.group(1), "names": [], "alias": None, "line": i, "level": 0}); break
        for rx in _EP:
            m = re.search(rx, ln)
            if m: S["endpoints"].append({"method": m.group(1).upper(), "path": m.group(2), "handler": "", "line": i})
        m = re.search(_CALLS, ln)
        if m: S["http_calls"].append({"method": "CALL", "url": m.group(2), "line": i})
        for m in _AUDIO_RE.findall(ln): S["audio_refs"].append({"value": m, "line": i})
        if lang in ("javascript", "typescript") and re.search(r"\b(?:it|test|describe)\(\s*['\"]", ln): S["tests"].append(ln.strip()[:80])
        for kind, rx in (_DEFS.get(lang) or []):
            m = re.match(rx, ln)
            if m: defs.append((i, kind, m.group(1))); break
        if re.search(r"process\.env\.(\w+)", ln): S["env_vars"].append({"name": re.search(r"process\.env\.(\w+)", ln).group(1), "line": i})
        if re.match(r"\s*(?:\}\s*)?catch\b|\s*except\b|\s*rescue\b", ln): S["try_blocks"].append({"start": i, "end": i, "body": [i, i], "handlers": [{"type": "catch", "line": i, "swallows": False, "fallback": False}]})
    for k, (ln, kind, name) in enumerate(defs):
        end = (defs[k + 1][0] - 1) if k + 1 < len(defs) else len(lines)
        (S["classes"] if kind == "class" else S["functions"]).append({"name": name, "args": [], "line": ln, "end": end, "doc": "", "calls": [], "class": None, "decorators": [], "methods": []} if kind == "class" else
            {"name": name, "args": [], "line": ln, "end": end, "doc": "", "calls": [], "class": None, "decorators": []})
    return S

def extract_structure(path, text):
    """-> dict (never raises). Python via AST; others via lightweight regex. `parse_error` set when AST parsing fails."""
    ext = PurePosixPath(path).suffix.lower()
    try:
        if ext == ".py": return _py_structure(text)
        lang = _LANG.get(ext)
        if lang and lang not in ("html", "css", "sql"): return _generic_structure(path, text, lang)
        if lang == "html":
            S = _generic_structure(path, text, "html"); S["imports"] = [{"module": m, "names": [], "alias": None, "line": 0, "level": 0} for m in re.findall(r"""<(?:script|link)[^>]+(?:src|href)=["']([^"']+)""", text)]
            S["http_calls"] += [{"method": "CALL", "url": m, "line": 0} for m in re.findall(_CALLS, text) for m in [m[1]]]; return S
        if lang == "sql":
            return {"language": "sql", "schemas": [{"name": m, "line": text[:mm.start()].count("\n") + 1} for mm in re.finditer(r"(?i)create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"]?(\w+)", text) for m in [mm.group(1)]], "imports": [], "classes": [], "functions": [], "endpoints": []}
    except SyntaxError as e: return {"language": "python", "parse_error": f"{e.msg} (line {e.lineno})", "imports": [], "classes": [], "functions": [], "endpoints": []}
    except Exception as e: return {"language": ext, "parse_error": str(e)[:120], "imports": [], "classes": [], "functions": [], "endpoints": []}
    return {"language": ext.lstrip(".") or "text", "imports": [], "classes": [], "functions": [], "endpoints": []}

def notebook_to_text(raw):
    """Jupyter notebook -> source-like text (code cells as code, markdown as comments); outputs ignored."""
    nb = json.loads(raw); out = []
    for c in nb.get("cells", []):
        src = "".join(c.get("source", [])) if isinstance(c.get("source"), list) else str(c.get("source", ""))
        out.append(src if c.get("cell_type") == "code" else "\n".join("# " + l for l in src.splitlines()))
    return "\n\n".join(out)

# --------------------------------------------------------------- chunking
MAX_CHUNK_CHARS, MAX_CHUNK_LINES = 3000, 80

def _split_lines(lines, start, base_section, ctx, imports_prefix=""):
    """Split an over-long span at line boundaries -> [(text, section, a, b)]."""
    out, cur, n, a = [], [], 0, start
    for i, ln in enumerate(lines, start):
        if cur and (n + len(ln) > MAX_CHUNK_CHARS or len(cur) >= MAX_CHUNK_LINES):
            out.append((cur, a, i - 1)); cur, n, a = [], 0, i
        cur.append(ln); n += len(ln) + 1
    if cur: out.append((cur, a, start + len(lines) - 1))
    return [(f"{ctx}\n{imports_prefix}" + "\n".join(c), base_section + (f" (part {k + 1}/{len(out)})" if len(out) > 1 else ""), x, y) for k, (c, x, y) in enumerate(out)]

def code_chunks(path, text, S=None):
    """Structure-aware chunks -> [(text, section, meta)]. meta has path, line_start, line_end, symbol. Functions/classes stay whole when small;
    long classes split per method (class context preserved in header); used imports are carried into each chunk."""
    lines = text.splitlines(); S = S if S is not None else extract_structure(path, text); out = []
    def ctx(sym, a, b): return f"[{path} :: {sym} (lines {a}-{b})]"
    def add(items, sym, kind):
        for body, sec, a, b in items: out.append((body, sec, {"path": path, "line_start": a, "line_end": b, "symbol": sym, "kind": kind}))
    imps = S.get("imports") or []
    def used_imports(body):
        keep = []
        for im in imps:
            ident = (im["alias"] or im["module"].split(".")[0]) if not im["names"] else None
            toks = im["names"] if im["names"] else [ident]
            if any(t and re.search(r"\b" + re.escape(t.split('.')[0]) + r"\b", body) for t in toks) and 0 < im["line"] <= len(lines): keep.append(lines[im["line"] - 1].strip())
        return ("# imports: " + "; ".join(sorted(set(keep))[:12]) + "\n") if keep else ""
    spans = []  # (a, b, symbol, kind)
    for c in S.get("classes", []):
        if c["end"] - c["line"] + 1 <= MAX_CHUNK_LINES and len("\n".join(lines[c["line"] - 1:c["end"]])) <= MAX_CHUNK_CHARS: spans.append((c["line"], c["end"], c["name"], "class"))
        else:
            ms = c.get("methods") or []; first = ms[0]["line"] - 1 if ms else c["end"]
            spans.append((c["line"], max(c["line"], first - 1), c["name"] + " (header)", "class"))
            for m in ms: spans.append((m["line"], m["end"], f"{c['name']}.{m['name']}", "method"))
    for f in S.get("functions", []): spans.append((f["line"], f["end"], f["name"], "function"))
    spans.sort(); covered = [False] * (len(lines) + 2); final = []
    for a, b, sym, kind in spans:
        if any(covered[a:b + 1]) and kind != "method": continue
        for i in range(a, b + 1): covered[i] = True
        final.append((a, b, sym, kind))
    for a, b, sym, kind in final:
        body_lines = lines[a - 1:b]; pre = used_imports("\n".join(body_lines))
        add(_split_lines(body_lines, a, sym, ctx(sym, a, b), pre), sym, kind)
    # remaining module-level lines (imports/config/main guard) as module chunks
    rest, start = [], None
    for i in range(1, len(lines) + 1):
        if not covered[i] and lines[i - 1].strip():
            if start is None: start = i
            rest.append(lines[i - 1])
        elif rest and (covered[i] or not lines[i - 1].strip()) and covered[i]:
            add(_split_lines(rest, start, "module level", ctx("module level", start, i - 1)), "module level", "module"); rest, start = [], None
    if rest: add(_split_lines(rest, start, "module level", ctx("module level", start, start + len(rest) - 1)), "module level", "module")
    if not out:  # no definitions (config, scripts, markup): windows with line ranges
        body = [l for l in lines]
        add(_split_lines(body, 1, "file", ctx("file", 1, len(lines))), "file", "file")
    out.sort(key=lambda x: x[2]["line_start"]); return out
