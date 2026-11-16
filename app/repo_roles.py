"""Role-aware repository intelligence: component ranking by source-derived facets (never README keywords), source-backed responsibilities and
suggestions, and deterministic evidence-cited question answering whose emphasis (not facts) follows the selected role."""
import os, re
from collections import defaultdict
from pathlib import PurePosixPath
from .repo_intel import build_model, evidence_ref, cite, source_lines, _component_of, is_test_path, UI_SYM, DEC_SYM
from .search import search

ROLE_W = {
 "backend": {"api": 4, "persistence": 4, "core_logic": 2, "security": 1},
 "frontend": {"ui": 4, "assets": 1.5},
 "fullstack": {"api": 3, "ui": 3, "persistence": 2, "core_logic": 1, "errors": 0.5},
 "ml": {"ml": 4, "data": 2, "model_io": 2, "rl": 1.5},
 "ai": {"rl": 4, "llm": 4, "decision": 3, "model_io": 1.5, "ml": 1},
 "dataeng": {"data": 4, "persistence": 3},
 "datasci": {"data": 2, "ml": 3},
 "devops": {"infra": 5, "errors": 0.3, "security": 0.5},
 "qa": {"test": 5, "errors": 1},
 "security": {"security": 5, "api": 1, "errors": 1, "persistence": 0.5},
 "lead": {"api": 1, "ui": 1, "ml": 1, "rl": 1, "llm": 1, "persistence": 1, "core_logic": 1, "infra": 0.5, "model_io": 1, "decision": 1},
}
FACET_LABEL = {"ui": "rendering/UI", "api": "API/service interfaces", "persistence": "data persistence", "ml": "ML training/inference", "rl": "reinforcement-learning policy", "llm": "LLM/GenAI integration", "data": "data handling",
               "model_io": "model loading", "errors": "error handling/fallback", "core_logic": "core application logic", "test": "tests", "infra": "build/deploy configuration", "security": "security-sensitive code",
               "decision": "decision logic", "assets": "asset usage"}
OWNER = {"ui": "Frontend Developer", "api": "Backend Developer", "persistence": "Backend/Data Engineer", "ml": "ML Engineer", "rl": "AI/GenAI Engineer", "llm": "AI/GenAI Engineer", "data": "Data Engineer", "infra": "DevOps Engineer",
         "test": "QA Engineer", "security": "Security Engineer", "decision": "AI/GenAI Engineer", "model_io": "ML Engineer", "core_logic": "Backend Developer"}
LENS = {"backend": ["interfaces", "flow", "failure", "config"], "frontend": ["ui_effect", "flow", "interfaces"], "fullstack": ["flow", "interfaces", "ui_effect", "failure"], "ml": ["model", "decision", "failure", "config"],
        "ai": ["decision", "model", "failure", "flow"], "dataeng": ["config", "flow", "model"], "datasci": ["model", "config", "decision"], "devops": ["config", "failure", "flow"], "qa": ["failure", "interfaces", "tests"],
        "security": ["failure", "config", "interfaces"], "lead": ["flow", "interfaces", "failure"], None: ["interfaces", "flow", "failure"]}
LENS_TITLE = {"interfaces": "Interfaces & module integration", "flow": "Execution flow", "failure": "Error handling & fallback", "config": "Configuration", "model": "Models & inference", "decision": "Decision logic",
              "ui_effect": "Effect on visible behaviour / UI", "tests": "Test coverage"}

def role_key(role, extra=""):
    r = (role or "").lower()
    for k, rx in (("fullstack", r"full[- ]?stack"), ("backend", r"back[- ]?end|server"), ("frontend", r"front[- ]?end|\bui\b|\bux\b"), ("ai", r"genai|gen ai|\bai\b|llm|prompt"), ("ml", r"\bml\b|machine learning"), ("dataeng", r"data engineer"),
                  ("datasci", r"data scien|analyst"), ("devops", r"devops|sre|platform|infra"), ("qa", r"\bqa\b|test|quality"), ("security", r"secur"), ("lead", r"lead|architect|manager|principal")):
        if re.search(rx, r): return k
    return "custom" if r.strip() else None

STOP = set("the and for that this with what when how does from are was who which where why about into your their there explain describe tell give show please can could would should have has had not its use used using work works project repository repo code file files".split())
def _stem(w): return re.sub(r"(ing|ed|es|s|ion|er)$", "", w) if len(w) > 5 else w
def _tokens(s): return [w for w in re.findall(r"[a-z][a-z0-9]{2,}", re.sub(r"([a-z])([A-Z])", r"\1 \2", s).lower().replace("_", " ")) if w not in STOP]
SYN = {"decision": ["decide", "policy", "action", "controller", "agent", "tactic"], "decisions": ["decide", "policy", "action", "controller"], "training": ["train", "fit", "learn"], "inference": ["predict", "infer", "classify"],
       "frontend": ["ui", "draw", "render"], "backend": ["api", "endpoint", "server"], "checkpoint": ["load", "model", "checkpoint"], "gesture": ["gesture", "landmark", "classify"], "ppo": ["ppo", "policy", "learn"]}

def _symbols(S):
    out = [(c["name"], c["line"], c.get("doc", ""), "class") for c in S.get("classes") or []] + [(f["name"], f["line"], f.get("doc", ""), "function") for f in S.get("functions") or []]
    out += [(f"{c['name']}.{m['name']}", m["line"], m.get("doc", ""), "method") for c in S.get("classes") or [] for m in c.get("methods") or []]
    return out

def find_files(M, question, k=5):
    """Hybrid retrieval over code chunks + symbol/path matching -> [(path, score, reasons)]."""
    toks = []
    for w in _tokens(question): toks += [w] + SYN.get(w, [])
    stems = {_stem(w) for w in toks}; score, why = defaultdict(float), defaultdict(list)
    for p, f in M["files"].items():
        if f["kind"] != "source" or not f["structure"] or is_test_path(p) and not re.search(r"\btest", question.lower()): continue
        ptoks = {_stem(t) for t in _tokens(p)}
        h = stems & ptoks
        if h: score[p] += 2 * len(h); why[p].append("path matches " + ", ".join(sorted(h)))
        for name, ln, doc, kind in _symbols(f["structure"]):
            st = {_stem(t) for t in _tokens(name)} | {_stem(t) for t in _tokens(doc)}
            hh = stems & st
            if hh: score[p] += 1.5 * len(hh); why[p].append(f"{kind} `{name}` (line {ln})")
    chunks = [c for p, cs in M["chunks"].items() if M["files"].get(p, {}).get("kind") == "source" for c in cs]
    hits, _ = search(chunks, " ".join(toks), 8) if toks else ([], "")
    cmap = {c["id"]: c for c in chunks}
    for h in hits:
        c = cmap.get(h["chunk_id"]); p = (c["meta"] or {}).get("path") if c else None
        if p: score[p] += 1 + h["relevance"]; why[p].append(f"retrieved passage lines {c['meta'].get('line_start')}-{c['meta'].get('line_end')}")
    ranked = sorted(score.items(), key=lambda x: (-x[1], x[0]))[:k]
    top = ranked[0][1] if ranked else 0
    return [(p, s, why[p][:4]) for p, s in ranked if s >= 2 and s >= 0.4 * top]

# ------------------------------------------------------------ file narration (role-neutral facts, role-ordered presentation)
def callers(M, path): return sorted({p for p, lst in M["internal"].items() if any(t == path for t, _ in lst)})
def _sig(f): return f"{f['name']}({', '.join(f['args'][:6])})"

def sec_interfaces(M, p, depth=1):
    S = M["files"][p]["structure"] or {}; out = []
    for c in (S.get("classes") or [])[:4]:
        ms = ", ".join(m["name"] for m in (c.get("methods") or [])[:6]); out.append(f"class `{c['name']}`" + (f" ({', '.join(c['bases'])})" if c.get("bases") else "") + (f" — {c['doc']}" if c.get("doc") else "") + (f"; methods: {ms}" if ms else "") + f" [{cite(p, c['line'], c['end'])}]")
    for f in (S.get("functions") or [])[:6 if depth else 3]:
        if f["name"].startswith("_") and depth < 2: continue
        out.append(f"`{_sig(f)}`" + (f" — {f['doc']}" if f.get("doc") else "") + f" [{cite(p, f['line'], f['end'])}]")
    for e in S.get("endpoints") or []: out.append(f"endpoint {e['method']} `{e['path']}` → `{e.get('handler') or '?'}` [{cite(p, e['line'])}]")
    return out

def sec_flow(M, p, depth=1):
    out = []; S = M["files"][p]["structure"] or {}
    deps = sorted({(t, l) for t, l in M["internal"].get(p, []) if M["files"].get(t, {}).get("kind") == "source"})
    if deps: out.append("imports project modules: " + ", ".join(f"`{t}` [{cite(p, l)}]" for t, l in deps[:6]))
    cs = callers(M, p)
    if cs: out.append("used by: " + ", ".join(f"`{c}`" for c in cs[:6]))
    for e in S.get("entry_points") or []: out.append(f"entry point `{e['kind']}` [{cite(p, e['line'])}]")
    ext = sorted((M["external"].get(p) or {}).keys())
    if ext and depth: out.append("external libraries: " + ", ".join(ext[:8]))
    return out

def sec_failure(M, p, depth=1):
    S = M["files"][p]["structure"] or {}; out = []
    for m in S.get("model_loads") or []:
        out.append(f"model load `{m['call']}`" + (f" of '{m['path_arg']}'" if m.get("path_arg") else "") + (" is guarded by try/except or an existence check" if m["guarded"] else " is NOT guarded (a missing file raises)") + f" [{cite(p, m['line'])}]")
    for t in S.get("try_blocks") or []:
        for h in t["handlers"]:
            lines = source_lines(M, p); body = " ".join(lines.get(i, "").strip() for i in range(h["line"], min(h["line"] + 3, t["end"] + 1)))[:110]
            out.append(f"handles `{h['type']}` → {'swallowed' if h['swallows'] else ('fallback/return value' if h['fallback'] else 'handled')}: `{body}` [{cite(p, h['line'])}]")
    for s in S.get("stubs") or []: out.append(f"`{s['name']}` is an unimplemented stub [{cite(p, s['line'])}]")
    return out[:8]

def sec_config(M, p, depth=1):
    S = M["files"][p]["structure"] or {}
    return [f"constant `{c['name']}` = {c['value']} [{cite(p, c['line'])}]" for c in (S.get("config") or [])[:6]] + [f"env var `{v['name']}` [{cite(p, v['line'])}]" for v in (S.get("env_vars") or [])[:4]]

def sec_model(M, p, depth=1):
    S = M["files"][p]["structure"] or {}; out = []
    for c in S.get("model_ctors") or []: out.append(f"constructs `{c['name']}` [{cite(p, c['line'])}]")
    for c in S.get("fit_calls") or []: out.append(f"training call `{c['call']}` [{cite(p, c['line'])}]")
    for c in S.get("predict_calls") or []: out.append(f"inference call `{c['call']}` [{cite(p, c['line'])}]")
    for m in S.get("model_loads") or []: out.append(f"loads `{m['call']}`" + (f" '{m['path_arg']}'" if m.get("path_arg") else "") + f" [{cite(p, m['line'])}]")
    for d in S.get("data_refs") or []: out.append(f"reads data file '{d['value']}' [{cite(p, d['line'])}]")
    return out[:8]

def sec_decision(M, p, depth=1):
    S = M["files"][p]["structure"] or {}; out = []
    for name, ln, doc, kind in _symbols(S):
        if DEC_SYM.search(name) or re.search(r"predict|classif|choose|select", name, re.I):
            f = next((x for x in (S.get("functions") or []) + [m for c in S.get("classes") or [] for m in c.get("methods") or []] if x["name"] == name.split(".")[-1]), None)
            calls = ", ".join(c for c in (f or {}).get("calls", []) if not c.startswith(("len", "int", "str", "float", "range")))[:90]
            out.append(f"`{name}`" + (f" — {doc}" if doc else "") + (f"; calls {calls}" if calls else "") + f" [{cite(p, ln, (f or {}).get('end'))}]")
    return out[:6]

def sec_ui_effect(M, p, depth=1):
    out = []; S = M["files"][p]["structure"] or {}
    for c in callers(M, p):
        cs = M["files"][c]["structure"] or {}; ui = "ui" in (M["facets"].get(c) or {})
        names = {f["name"] for f in (S.get("functions") or [])} | {m["name"] for k in S.get("classes") or [] for m in k.get("methods") or []} | {k["name"] for k in S.get("classes") or []}
        using = [f["name"] + f" [{cite(c, f['line'], f['end'])}]" for f in (cs.get("functions") or []) + [m for k in cs.get("classes") or [] for m in k.get("methods") or []] if any(x.split(".")[-1] in names or x.split(".")[0] in names for x in f.get("calls", []))]
        if ui or using: out.append(f"`{c}` {'(UI/rendering module) ' if ui else ''}imports it" + (f"; functions touching it: {', '.join(using[:4])}" if using else ""))
    for p2, f in M["files"].items():
        for call in (f["structure"] or {}).get("http_calls") or []:
            if any(call["url"].endswith(a["path"]) and a["file"] == p for a in M["apis"]): out.append(f"`{p2}` calls this module's endpoint `{call['url']}` [{cite(p2, call['line'] or None)}]")
    return out[:6]

def sec_tests(M, p, depth=1):
    ts = M["test_of"].get(p, []); return [f"covered by tests: {', '.join(ts)}"] if ts else ["no test file imports or is named after this module"]

SECTIONS = {"interfaces": sec_interfaces, "flow": sec_flow, "failure": sec_failure, "config": sec_config, "model": sec_model, "decision": sec_decision, "ui_effect": sec_ui_effect, "tests": sec_tests}

# ------------------------------------------------------------ role view (handover)
def _score_file(key, fac, W): return sum(W.get(f, 0) * min(v["count"], 4) for f, v in fac.items())

# A file only belongs to a role when it shows a PRIMARY facet for that role; secondary facets (model loading, error handling) never qualify a file alone.
PRIMARY = {"backend": {"api", "persistence", "core_logic"}, "frontend": {"ui"}, "fullstack": {"api", "ui", "persistence"}, "ml": {"ml", "data"}, "ai": {"rl", "llm", "decision"},
           "dataeng": {"data", "persistence"}, "datasci": {"ml", "data"}, "devops": {"infra"}, "qa": {"test"}, "security": {"security"}}
def _gated_score(key, fac, W):
    s = _score_file(key, fac, W); prim = PRIMARY.get(key)
    if not prim: return s
    own = max((fac[f]["count"] for f in prim if f in fac), default=0)
    if not own: return 0
    if key in ("backend", "fullstack", "devops", "qa", "security") and max((fac[f]["count"] for f in ("rl", "ml", "llm", "ui") if f in fac and f not in prim), default=0) > own: return 0  # RL/ML/UI-dominated file
    return s

def _symbol_summary(M, rs):
    out = []
    for r in rs[:2]:
        S = M["files"][r["path"]]["structure"] or {}; names = [c["name"] for c in S.get("classes") or []][:3] + [f["name"] for f in S.get("functions") or []][:3]
        if names: out.append(f"{PurePosixPath(r['path']).name} defines " + ", ".join(f"`{n}`" for n in names))
    return "; ".join(out) or "No classes or functions were extracted from the matched files"

def custom_terms(role, desc): return [t for t in _tokens((role or "") + " " + (desc or "")) if t not in {"engineer", "developer", "role", "team", "work", "lead", "owner"}]

def role_view(M, role, description="", responsibilities=""):
    key = role_key(role); W = ROLE_W.get(key) if key != "custom" else None; rows = []
    terms = custom_terms(role, description + " " + responsibilities) if key in ("custom", None) else []
    for p, fac in M["facets"].items():
        if W is not None:
            s = _gated_score(key, fac, W); ev = [(f, e) for f, v in fac.items() if W.get(f) for e in v["evidence"][:2]]
        else:
            S = M["files"][p]["structure"] or {}; names = _tokens(p) + [t for n, *_ in _symbols(S) for t in _tokens(n)] + list(M["external"].get(p, {}).keys())
            hit = sorted({t for t in terms if _stem(t) in {_stem(x) for x in names}}); s = 2 * len(hit); ev = [("custom", {"line": 1, "what": "matches role term(s) " + ", ".join(hit)})] if hit else []
        if s >= 2: rows.append({"path": p, "score": round(s, 1), "facets": {f: v["count"] for f, v in fac.items() if (W or {}).get(f)} or {"custom": 1}, "ev": ev})
    comps = defaultdict(list)
    for r in rows: comps[_component_of(r["path"])].append(r)
    ranked = sorted(comps.items(), key=lambda kv: -sum(r["score"] for r in kv[1]))[:6]
    components, resp, sugg = [], [], []
    for c, rs in ranked:
        rs.sort(key=lambda r: -r["score"]); rs = rs[:4]; top = rs[0]
        facs = defaultdict(int)
        for r in rs:
            for f, n in r["facets"].items(): facs[f] += n
        facs_s = ", ".join(f"{FACET_LABEL.get(f, f)}" for f, _ in sorted(facs.items(), key=lambda x: -x[1])[:3])
        evid = [evidence_ref(M, r["path"], e["line"]) | {"what": e["what"]} for r in rs[:2] for _, e in r["ev"][:2]]
        desc = "; ".join(x.split(" [")[0] for r in rs[:2] for x in (sec_interfaces(M, r["path"], 1)[:2]))
        for r in rs: components.append({"component": f"{c} · {PurePosixPath(r['path']).name}", "source_file": M["docs"][M["chunks"][r["path"]][0]["doc_id"]]["filename"] if M["chunks"].get(r["path"]) else r["path"], "path": r["path"],
                                        "chunk_id": (M["chunks"].get(r["path"]) or [{}])[0].get("id"), "section": None, "relevance_score": r["score"], "matched_terms": [FACET_LABEL.get(f, f) for f in r["facets"]],
                                        "evidence": "; ".join(e["what"] for _, e in r["ev"][:3]), "verification": "SOURCE_CODE_EVIDENCE"})
        deps = sorted({M["graph"].nodes[v]["name"] for _, v, k in M["graph"].out_edges(f"component:{c}", keys=True) if k == "DEPENDS_ON"}) if f"component:{c}" in M["graph"] else []
        resp.append({"title": f"Own `{c}` — {facs_s}", "description": (desc or _symbol_summary(M, rs)) + (f". Depends on: {', '.join(deps)}" if deps else ""), "why_this_role": f"Source evidence for {facs_s} (derived from imports, symbols and calls, not README keywords).",
                     "related_component": c, "source_files": [r["path"] for r in rs], "evidence": [{"document": e["document"], "chunk_id": e["chunk_id"], "quote": f"{e['what']} — {e['quote']}"[:300], "path": e["path"], "line_start": e["line_start"], "line_end": e["line_end"], "url": e["url"]} for e in evid[:4]],
                     "expected_outcome": "Entry points: " + ", ".join(PurePosixPath(r["path"]).name for r in rs[:3]), "status": "EXISTING_RESPONSIBILITY", "basis": "extracted"})
    files_in = {r["path"] for _, rs in ranked for r in rs[:4]}
    sugg = suggestions(M, key, files_in, W)
    return {"role_key": key, "components": components, "responsibilities": resp, "suggestions": sugg, "collaboration": collaboration(M, key, [c for c, _ in ranked]), "gaps": gaps(M, key, files_in), "files": sorted(files_in)}

def _sug(M, title, desc, why, comp, path, line, outcome):
    e = evidence_ref(M, path, line)
    return {"title": title, "description": desc + " This is a suggestion, not a confirmed backlog item.", "why_this_role": why, "related_component": comp, "source_files": [path],
            "evidence": [{"document": e["document"], "chunk_id": e["chunk_id"], "quote": e["quote"], "path": path, "line_start": line, "line_end": line, "url": e["url"]}], "expected_outcome": outcome,
            "priority": "Unprioritised (suggestion)", "status": "SUGGESTED_NOT_CONFIRMED", "basis": "extracted"}

def suggestions(M, key, files, W):
    out, seen, untested = [], set(), defaultdict(list)
    dev = key in ("backend", "ml", "ai", "fullstack", "lead", "devops", "qa", "security", "datasci", "dataeng", "custom", None)
    for p in sorted(files):
        S = M["files"][p]["structure"] or {}; comp = _component_of(p)
        for m in S.get("model_loads") or []:
            if not m["guarded"] and key in ("ml", "ai", "devops", "qa", "lead", "fullstack", "custom", None):
                out.append(_sug(M, f"Guard model loading in {PurePosixPath(p).name}", f"`{m['call']}`" + (f" of '{m['path_arg']}'" if m.get("path_arg") else "") + " is not inside try/except or an existence check, so a missing or corrupt checkpoint raises at import/call time.", "Model-loading failure behaviour is part of this role's integration boundary.", comp, p, m["line"], "Explicit error or fallback when the model file is missing."))
            if m.get("path_arg") and M["files"] and not any(PurePosixPath(m["path_arg"]).name.lower() == PurePosixPath(q).name.lower() for q in M["files"]) and key in ("ml", "ai", "devops", "datasci", "lead"):
                out.append(_sug(M, f"Document how `{PurePosixPath(m['path_arg']).name}` is produced", f"The code loads '{m['path_arg']}' but no such file exists in the repository manifest.", "A required model artifact is not versioned or documented.", comp, p, m["line"], "Reproducible path to obtain or train the artifact."))
        for t in S.get("try_blocks") or []:
            for h in t["handlers"]:
                if h["swallows"] and key in ("backend", "qa", "security", "ai", "ml", "lead", "fullstack", "custom", None) and (p, h["line"]) not in seen:
                    seen.add((p, h["line"])); out.append(_sug(M, f"Stop swallowing `{h['type']}` in {PurePosixPath(p).name}", f"The handler at line {h['line']} discards the exception without logging or fallback, hiding failures.", "Silent failures affect debuggability and correctness.", comp, p, h["line"], "Logged or handled failure path."))
        for s in S.get("stubs") or []:
            if dev: out.append(_sug(M, f"Implement stub `{s['name']}`", f"`{s['name']}` in {p} has only pass/NotImplementedError, so the feature is partially implemented.", "Incomplete implementation in this role's component.", comp, p, s["line"], "Working implementation or removal of the stub."))
        if key in ("qa", "backend", "lead", "fullstack") and not is_test_path(p) and (S.get("functions") or S.get("classes")) and not M["test_of"].get(p) and (key in ("qa", "lead") or {"api", "persistence"} & set(M["facets"].get(p) or {})):
            untested[comp].append((p, (S.get("functions") or S.get("classes"))[0]["line"]))
    ntests = sum(is_test_path(q) for q in M["files"])
    for comp, items in untested.items():  # one recommendation per component, only where test-to-module evidence shows a gap
        names = ", ".join(f"`{PurePosixPath(p).name}`" for p, _ in items[:4]); p0, l0 = items[0]
        out.append(_sug(M, f"Add tests for `{comp}`", (f"{len(items)} module(s) ({names}) are not imported by or named after any test module, while the repository has {ntests} test file(s)." if ntests else f"The repository contains no test modules; {len(items)} module(s) ({names}) are uncovered."), "Coverage gap evidenced by test-to-module relationships extracted from imports.", comp, p0, l0, "Tests covering the listed modules' public functions."))
    if key in ("devops", "security", "lead"):
        for name, d in M["deps"].items():
            if not d["pinned"] and d["file"].lower().endswith("requirements.txt") or (not d["pinned"] and key == "security" and d["file"].lower().endswith("package.json")):
                ln = next((i for i, l in enumerate(source_lines(M, d["file"]).values(), 1) if l.lower().startswith(name)), 1); out.append(_sug(M, f"Pin dependency `{name}`", f"`{name}` in {d['file']} has no exact version pin ({d['spec'] or 'unpinned'}).", "Unpinned dependencies make builds non-reproducible and widen supply-chain exposure.", "dependencies", d["file"], ln, "Exact or bounded version constraint."))
    # Deduplicate repeated findings by component and issue class, not filename.
    grouped = {}
    for item in out:
        title = item["title"]
        if title.startswith("Guard model loading in "): kind = "guard-model-loading"
        elif title.startswith("Stop swallowing "): kind = "silent-exception"
        elif title.startswith("Add tests for "): kind = "test-coverage"
        elif title.startswith("Document how "): kind = "model-artifact-provenance"
        elif title.startswith("Implement stub "): kind = "unfinished-stub"
        elif title.startswith("Pin dependency "): kind = "dependency-pinning"
        else: kind = re.sub(r"\\s+", " ", title.lower())
        key0 = (item.get("related_component"), kind)
        if key0 not in grouped:
            grouped[key0] = item
        else:
            prev = grouped[key0]
            prev["source_files"] = sorted(set(prev.get("source_files", []) + item.get("source_files", [])))
            prev["evidence"] = (prev.get("evidence", []) + item.get("evidence", []))[:6]
            prev["description"] = prev.get("description", "") + " Additional affected files: " + ", ".join(item.get("source_files", [])) + "."
    return list(grouped.values())[:8]

def collaboration(M, key, comps):
    G = M["graph"]; out = []
    for c in comps:
        cid = f"component:{c}"
        for _, v, k, d in G.out_edges(cid, keys=True, data=True) if cid in G else []:
            if k == "DEPENDS_ON":
                fac = defaultdict(int); tgt = v.split(":", 1)[1]
                for p in M["comps"].get(tgt, []):
                    for f, x in (M["facets"].get(p) or {}).items(): fac[f] += x["count"]
                owner = OWNER.get(max(fac, key=fac.get)) if fac else None
                out.append({"component": c, "depends_on": tgt, "likely_counterpart": owner, "basis": d["basis"], "evidence": [evidence_ref(M, e["path"], e["line_start"]) for e in d["evidence"][:1]]})
        for u, _, k, d in G.in_edges(cid, keys=True, data=True):
            if k == "DEPENDS_ON":
                src = u.split(":", 1)[1]; fac = defaultdict(int)
                for p in M["comps"].get(src, []):
                    for f, x in (M["facets"].get(p) or {}).items(): fac[f] += x["count"]
                out.append({"component": c, "used_by": src, "likely_counterpart": OWNER.get(max(fac, key=fac.get)) if fac else None, "basis": d["basis"], "evidence": [evidence_ref(M, e["path"], e["line_start"]) for e in d["evidence"][:1]]})
    return out[:10]

def gaps(M, key, files):
    g = []
    for ml in M["model_loads"]:
        if ml["file"] in files and ml["present"] is False: g.append(f"'{ml['path_arg']}' (loaded at {cite(ml['file'], ml['line'])}) is not in the repository — origin/versioning unknown.")
    for d in M["datasets"].values():
        if d["status"] == "referenced_in_code_not_in_repo" and key in ("ml", "datasci", "dataeng", "ai", "lead", "custom", None): g.append(f"data file '{d['name']}' is referenced in code but not present in the repository.")
        if d["status"] == "mentioned_in_docs_only": g.append(f"dataset '{d['name']}' is mentioned only in documentation; no loading code or file found.")
    for t in M["technologies"].values():
        if t["status"] == "documented_only" and key in ("lead", "devops", "ml", "ai", "backend", "frontend", "fullstack", None): g.append(f"technology '{t['name']}' is mentioned in docs but never imported or declared.")
    if M["stats"]["failed"]: g.append(f"{M['stats']['failed']} repository file(s) failed processing; analysis may be incomplete.")
    return g[:10]

# ------------------------------------------------------------ question answering
def _intent(q):
    l = q.lower()
    checks = [("audio", r"\b(audio|sound|sfx|music|wav|mp3)\b"), ("missing_model", r"(missing|absent|not found|doesn.t exist|does not exist|without|isn.t there|not present).{0,40}(checkpoint|model|weights)|(checkpoint|model|weights).{0,40}(missing|absent|not found|doesn.t exist|does not exist)"),
              ("dataset", r"\b(datasets?|training data|training set|data source|trained on|collected)\b"), ("algorithms", r"\b(algorithms?|which models?|what models?|machine learning models?|techniques)\b"),
              ("limits", r"\b(limitations?|known issues?|unfinished|todo|unresolved|stubs?|not implemented|partially implemented)\b"), ("deps", r"\b(dependenc\w*|librar\w+|technolog\w+|tech stack|packages?|frameworks?)\b"),
              ("comm", r"(frontend|front-end|ui|client).{0,60}(communicat|talk|call|connect|backend|api)|(communicat|talk|call).{0,40}(frontend|backend)"), ("summary", r"(what is this project|project (about|overview|summary)|summari[sz]e (the )?(project|repo)|^overview|what does (this|the) (project|repo|app))"),
              ("arch", r"\barchitecture\b|\bcomponents\b|\bmodules\b|how is .* structured"), ("relation", r"(relationship|related|difference|interact|connect)\w*.{0,40}\b\S+\.(py|js|ts|java|go)\b.{0,30}\b\S+\.(py|js|ts|java|go)\b"),
              ("implements", r"which (files?|modules?|source).{0,30}(implement|contain|handle|define|do)|where is .{0,50}(implemented|defined|handled)")]
    for k, rx in checks:
        if re.search(rx, l): return k
    return "explain"

def _ev_items(M, refs, extra=None):
    out = []
    for r in refs:
        c = next((c for c in M["chunks"].get(r["path"], []) if r.get("chunk_id") == c["id"]), None) or (M["chunks"].get(r["path"]) or [None])[0]
        out.append({"chunk_id": r.get("chunk_id"), "doc_id": r.get("doc_id"), "section": cite(r["path"], r.get("line_start"), r.get("line_end")), "page": None, "relevance": 1.0, "text": (r.get("quote") or (c or {}).get("text", ""))[:400], "path": r["path"],
                    "line_start": r.get("line_start"), "line_end": r.get("line_end"), "url": r.get("url"), "source": r["path"]})
    seen, u = set(), []
    for e in out:
        k = (e["path"], e["line_start"])
        if k not in seen: seen.add(k); u.append(e)
    return u

def _reply(text, M, refs, mode, verification="VERIFIED", role=None, suggestions=None):
    return {"answer": text, "evidence": _ev_items(M, refs), "verification": verification, "answer_mode": mode, "role_lens": role, "suggestions": suggestions or ["Which files implement the core logic?", "What are the known limitations?", "Which datasets and models are used?"],
            "repository": {"name": M["repo"], "commit": M["commit"], "branch": M["branch"]}}

def repo_answer(db, question, role=None, role_description="", history=None):
    M = build_model(db)
    if not M or not M["files"]: return None
    key = role_key(role) if role else None; intent = _intent(question); refs = []; lines = []; ql = question.lower()
    rn = f" · {role} view" if role else ""
    if intent == "audio":
        a = M["audio"]; lines.append("**Audio assets actually present in the repository** (deduplicated by content):" if a["present"] else "**No audio files are present in the repository.**")
        for x in a["present"]:
            lines.append(f"- `{x['path']}` ({x['size']} bytes" + (f", {x['meta']['duration_s']}s" if x["meta"].get("duration_s") else "") + (f"; identical copies: {', '.join(x['duplicates'])}" if x["duplicates"] else "") + ")"); refs.append(evidence_ref(M, x["path"]))
        if a["referenced_missing"]: lines.append("\n**Referenced in code/docs but NOT present as files:** " + ", ".join(f"`{n}`" for n in a["referenced_missing"]))
        if a["present_unreferenced"]: lines.append("**Present but not referenced by any code/doc:** " + ", ".join(f"`{n}`" for n in a["present_unreferenced"]))
        for n, locs in list(a["referenced"].items())[:6]:
            for p, ln in locs[:1]: refs.append(evidence_ref(M, p, ln))
        lines.append("\nOnly filenames, sizes and headers were read; audio contents were not analysed.")
        return _reply("\n".join(lines), M, refs, "repo-inventory", role=role)
    if intent == "dataset":
        ds = M["datasets"]
        if not ds: return _reply("No dataset file, data-loading reference, known public dataset name or training-source statement was found in the indexed repository. I cannot name a training dataset from this evidence.", M, [], "repo-inventory", "MISSING", role)
        order = {"present_in_repo": 0, "referenced_in_code_not_in_repo": 1, "documented_statement": 2, "mentioned_in_docs_only": 3}
        lines.append("**Datasets / training data found** (status shows how strong the evidence is):")
        for d in sorted(ds.values(), key=lambda d: order.get(d["status"], 9)):
            e = d["evidence"][0]; lab = {"present_in_repo": "file present in repo", "referenced_in_code_not_in_repo": "referenced in code, file NOT in repo", "documented_statement": "documented statement", "mentioned_in_docs_only": "named in docs only"}[d["status"]]
            lines.append(f"- {d['name']} — {lab} [{cite(e[0], e[1], e[2])}]"); refs.append(evidence_ref(M, *e))
        trainers = [(p, f["structure"]["fit_calls"][0]) for p, f in M["files"].items() if f["structure"] and f["structure"].get("fit_calls")]
        for p, fc in trainers[:3]:
            reads = [d["value"] for d in M["files"][p]["structure"].get("data_refs") or []]
            lines.append(f"- Training call `{fc['call']}` in `{p}`" + (f" reads {', '.join(reads[:3])} (inferred link: same file)" if reads else " — no data file referenced in that file") + f" [{cite(p, fc['line'])}]"); refs.append(evidence_ref(M, p, fc["line"]))
        if key in ("ml", "datasci", "dataeng"): lines.append("\nFor your role: check file schema/labels in the present data files and where the missing ones come from.")
        return _reply("\n".join(lines), M, refs, "repo-inventory", role=role)
    if intent == "algorithms":
        al = M["algorithms"]
        if not al: return _reply("No known algorithm or model class was found in source code or documentation.", M, [], "repo-inventory", "MISSING", role)
        lines.append("**Algorithms and models** (verified in code vs only documented):")
        for a in sorted(al.values(), key=lambda a: a["status"] != "verified_in_code"):
            e = (a["code_evidence"] or a["doc_evidence"])[0]; lines.append(f"- {a['name']} — {a['status'].replace('_', ' ')} [{cite(e[0], e[1], e[2])}]"); refs.append(evidence_ref(M, *e))
        for m in M["models"]: lines.append(f"- checkpoint file `{m['path']}` ({m['size']} bytes; contents not inspected)"); refs.append(evidence_ref(M, m["path"]))
        return _reply("\n".join(lines), M, refs, "repo-inventory", role=role)
    if intent == "missing_model":
        ls = M["model_loads"]
        if not ls: return _reply("No model-loading call was found in the indexed source, so I cannot describe missing-checkpoint behaviour.", M, [], "repo-inventory", "MISSING", role)
        lines.append("**What happens when a model file is missing** (from each load site):")
        for m in ls:
            beh = "guarded by try/except or an existence check — see the handler below" if m["guarded"] else "NOT guarded: a missing file will raise an exception"
            lines.append(f"- `{m['file']}` line {m['line']}: `{m['call']}`" + (f" of '{m['path_arg']}' (file {'present' if m['present'] else 'NOT in repo'})" if m.get('path_arg') else "") + f" — {beh}"); refs.append(evidence_ref(M, m["file"], m["line"]))
            if m["guarded"]: lines += ["  - " + x for x in sec_failure(M, m["file"]) if "handles" in x][:2]
        return _reply("\n".join(lines), M, refs, "repo-structured", role=role)
    if intent == "limits":
        ls = M["limitations"]
        if not ls: return _reply("No limitations, TODOs, stubs or documented caveats were found in the indexed files.", M, [], "repo-inventory", "MISSING", role)
        lines.append("**Known limitations and partially implemented areas:**")
        for l in ls[:20]: lines.append(f"- ({l['kind'].replace('_', ' ')}) {l['text']} [{cite(l['file'], l['line'])}]"); refs.append(evidence_ref(M, l["file"], l["line"]))
        return _reply("\n".join(lines), M, refs, "repo-structured", role=role)
    if intent == "deps":
        ts = sorted(M["technologies"].values(), key=lambda t: (t["status"] != "verified_in_code", t["name"])); lines.append("**Dependencies & technologies** (deduplicated; status = strongest evidence):")
        for t in ts: e = t["evidence"][0]; lines.append(f"- {t['name']} — {t['status'].replace('_', ' ')} [{cite(e[0], e[1], e[2])}]"); refs.append(evidence_ref(M, *e))
        un = [n for n, d in M["deps"].items() if not d["pinned"]]
        if un and key in ("devops", "security", "lead"): lines.append(f"\nUnpinned: {', '.join(un[:10])}")
        return _reply("\n".join(lines), M, refs, "repo-inventory", role=role)
    if intent == "comm":
        lines.append("**Frontend ↔ backend communication**")
        if not M["api_calls"] and not M["apis"]: return _reply("No HTTP endpoints or client-side API calls were found in the indexed source, so I cannot describe frontend–backend communication.", M, [], "repo-structured", "MISSING", role)
        for a in M["apis"]: lines.append(f"- Backend: {a['method']} `{a['path']}` in `{a['file']}` [{cite(a['file'], a['line'])}]"); refs.append(evidence_ref(M, a["file"], a["line"]))
        for c in M["api_calls"]: lines.append(f"- Client: `{c['file']}` calls `{c['url']}`" + (" → matches " + ", ".join(f"{m['method']} {m['path']}" for m in c["matches"]) if c["matches"] else " (no matching local endpoint found)") + f" [{cite(c['file'], c['line'] or None)}]"); refs.append(evidence_ref(M, c["file"], c["line"] or None))
        return _reply("\n".join(lines), M, refs, "repo-structured", role=role)
    if intent in ("summary", "arch"):
        from .repo_intel import project_summary
        s = project_summary(db); pick = [x for x in s["sections"] if x["title"][:2] in (("1.", "2.", "3.", "4.", "5.", "7.") if intent == "summary" else ("3.", "7.", "8."))]
        lines.append(f"**{M['repo']}** @ `{(M['commit'] or '')[:10]}`" + rn)
        for sec in pick:
            lines.append(f"\n**{sec['title']}**")
            for it in sec["items"][:7]: lines.append(f"- {it['text']} _({it['status'].replace('_', ' ')})_"); refs += [dict(e) for e in it["evidence"][:1]]
        return _reply("\n".join(lines), M, refs, "repo-structured", role=role)
    if intent == "relation":
        named = [p for p in M["files"] if PurePosixPath(p).name.lower() in ql or p.lower() in ql][:2]
        if len(named) == 2:
            a, b = named; ab = [l for t, l in M["internal"].get(a, []) if t == b]; ba = [l for t, l in M["internal"].get(b, []) if t == a]
            lines.append(f"**`{a}` ↔ `{b}`**")
            lines.append(f"- `{a}` imports `{b}` [{cite(a, ab[0])}]" if ab else f"- `{a}` does not import `{b}`"); lines.append(f"- `{b}` imports `{a}` [{cite(b, ba[0])}]" if ba else f"- `{b}` does not import `{a}`")
            sh = sorted(set(M["external"].get(a, {})) & set(M["external"].get(b, {}))); lines.append("- shared libraries: " + (", ".join(sh) if sh else "none"))
            for fn, p in ((sec_interfaces, a), (sec_interfaces, b)): lines += [f"  - `{p}`: {x}" for x in fn(M, p, 0)[:3]]
            refs += [evidence_ref(M, a, (ab or [1])[0]), evidence_ref(M, b, (ba or [1])[0])]
            return _reply("\n".join(lines), M, refs, "repo-structured", role=role)
    # generic code explanation / implements / decision / training / etc.
    found = find_files(M, question, 4 if intent != "implements" else 6)
    if not found: return None
    if intent == "implements":
        lines.append("**Files that implement this** (ranked by source evidence):")
        for p, s, why in found: lines.append(f"- `{p}` — {'; '.join(why)}"); refs.append(evidence_ref(M, p, 1))
        return _reply("\n".join(lines), M, refs, "repo-structured", role=role)
    lens = LENS.get(key, LENS[None]) if key != "custom" else LENS[None]
    deep = 2 if key in ("backend", "ml", "ai", "frontend", "fullstack", "devops", "qa", "security", "dataeng", "datasci") else 1
    lines.append(f"**{'Role-focused explanation' if role else 'Explanation'}{rn}** — based on {len(found)} source file(s): " + ", ".join(f"`{p}`" for p, *_ in found))
    for l in lens:
        block = []
        for p, s, why in found:
            xs = SECTIONS[l](M, p, deep)
            if xs: block += [f"`{p}`: {x}" for x in xs[: (5 if deep > 1 else 4)]]
        if block:
            lines.append(f"\n**{LENS_TITLE[l]}**"); lines += ["- " + b for b in block[:8 if deep > 1 else 6]]
        elif l == "ui_effect":
            lines.append(f"\n**{LENS_TITLE[l]}**\n- No UI/rendering module in the indexed source imports or calls this code, so its effect on visible behaviour cannot be verified from source.")
    for p, s, why in found:
        S = M["files"][p]["structure"] or {}; first = (S.get("classes") or S.get("functions") or [None])[0]
        refs.append(evidence_ref(M, p, first["line"], first["end"]) if first else evidence_ref(M, p, 1))
    miss = [g for g in gaps(M, key, {p for p, *_ in found})]
    if miss: lines.append("\n**Missing evidence:** " + " ".join(miss[:3]))
    lines.append("\n_Facts above come from parsed source structure; only the emphasis follows your role._" if role else "")
    ans = "\n".join(lines)
    gen = _try_llm(question, role, ans, M, refs)
    return _reply(gen + "\n\n---\n" + ans if gen else ans, M, refs, "repo-llm-grounded" if gen else "repo-structured", "VERIFIED", role)

def _try_llm(question, role, structured, M, refs):
    """Optional synthesis over the structured facts; silently absent without a token (free-tier friendly)."""
    if not (os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")): return None
    try:
        from .chatbot.service import _grounded_llm
        q = question + (f"\n(Answer for a {role}: emphasise what matters to that role; do not change facts; cite file:line.)" if role else "")
        return _grounded_llm(q, [{"source": "parsed repository structure", "section": "facts", "page": None, "text": structured}] + [{"source": e["path"], "section": cite(e["path"], e.get("line_start"), e.get("line_end")), "page": None, "text": e.get("quote", "")} for e in refs[:5]])
    except Exception: return None
