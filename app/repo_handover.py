"""Perspective-specific handover sections (incoming / departing / administrator).
Built only from the repository model, the role view and repository-scoped claims; each perspective selects different evidence and has its own structure.
Nothing here invents owners, progress, decisions or deadlines: unknowns are reported as MISSING."""
import hashlib
from collections import defaultdict
from pathlib import PurePosixPath
from .repo_intel import evidence_ref
from .repo_scan import is_test_path
from .repo_roles import gaps, sec_interfaces, collaboration, OWNER

MANIFESTS = ("requirements.txt", "package.json", "pyproject.toml", "pom.xml", "build.gradle", "go.mod", "cargo.toml", "makefile", "docker-compose.yml", ".env.example", "setup.py")

def _ev(M, path, line=1):
    try:
        e = evidence_ref(M, path, line)
        return {"document": e["document"], "chunk_id": e["chunk_id"], "quote": e["quote"], "path": path, "line_start": line, "url": e.get("url")}
    except Exception:
        return None

def _evs(M, pairs): return [e for e in (_ev(M, p, l) for p, l in pairs) if e]

def _item(title, ev=None, status="SOURCE_CODE_EVIDENCE", **details):
    if status == "SOURCE_CODE_EVIDENCE" and not ev: status = "MANIFEST_DERIVED"  # counted/derived from the ingestion manifest or parsed structure, no single quotable line
    return {"id": "rh-" + hashlib.md5(title.encode()).hexdigest()[:8], "title": title, "verification": status, "evidence_status": status, "details": details,
            "observations": {}, "evidence": ev or [], "needs_confirmation": status not in ("SOURCE_CODE_EVIDENCE", "VERIFIED", "MANIFEST_DERIVED")}

def _sec(title, items, empty): return {"title": title, "items": items or [_item(empty, status="MISSING")]}
def _name(p): return PurePosixPath(p).name

def _node_item(n, prefix=""):
    a = n.get("attrs") or {}
    ev = [{"document": e.get("doc_name"), "chunk_id": e.get("chunk_id"), "quote": e.get("text"), "timestamp": e.get("timestamp")} for e in (n.get("evidence") or [])[:2]]
    return _item(prefix + str(n["name"]), ev, n.get("status") or "INFERRED", **{k: a[k] for k in ("owner", "status", "due", "role") if a.get(k)})

def _symbols(M, p):
    S = M["files"][p]["structure"] or {}
    return [c["name"] for c in S.get("classes") or []][:3] + [f["name"] for f in S.get("functions") or []][:3]

def _context(M):
    st = M["stats"]; sha = (M["commit"] or "unknown")[:10]
    out = [_item(f"{M['repo']}@{sha} ({M['branch']}): {st['indexed']} of {st['discovered']} files indexed; {st['metadata_only']} metadata-only, {st['excluded']} excluded, {st['failed']} failed.")]
    tv = sorted(t["name"] for t in M["technologies"].values() if t["status"] != "documented_only"); td = sorted(t["name"] for t in M["technologies"].values() if t["status"] == "documented_only")
    noise = {"com", "org", "net", "java", "class", "self", "sniper_cooldown_token"}
    tv = [t for t in tv if t.lower() not in noise and "." not in t]
    td = [t for t in td if t.lower() not in noise and "." not in t]
    if tv: out.append(_item("Technologies verified in source/manifests: " + ", ".join(tv[:12])))
    if td: out.append(_item("Mentioned in documentation only (not verified in code): " + ", ".join(td[:8]), status="UNVERIFIED"))
    return out

def _setup(M):
    out = [_item(f"Build/dependency file `{p}`", _evs(M, [(p, 1)])) for p in sorted(M["files"]) if _name(p).lower() in MANIFESTS or _name(p).lower().startswith("dockerfile")][:6]
    pins = [f"{n} ({d['spec'] or 'unpinned'})" for n, d in list(M["deps"].items())[:10]]
    if pins: out.append(_item("Declared dependencies: " + ", ".join(pins)))
    envs = [(p, v["line"], v["name"]) for p, f in M["files"].items() for v in ((f["structure"] or {}).get("env_vars") or [])][:6]
    for p, ln, nm in envs: out.append(_item(f"Environment variable `{nm}` is read in `{p}`", _evs(M, [(p, ln)])))
    return out

def _modules(rv): return [_item(f"{r['title']} — {r['description']}", r["evidence"][:2], "SOURCE_CODE_EVIDENCE", files=r["source_files"]) for r in rv["responsibilities"]]

def _flow(M, rv):
    out = []
    for p in rv["files"][:4]:
        lines = [x.split(" [")[0] for x in sec_interfaces(M, p, 1)[:3]]
        if lines: out.append(_item(f"`{_name(p)}`: " + "; ".join(lines), _evs(M, [(p, 1)])))
    return out

def _guards(M, files):
    out = []
    for p in sorted(files):
        for t in ((M["files"][p]["structure"] or {}).get("try_blocks") or [])[:2]:
            for h in t["handlers"][:1]:
                out.append(_item(f"`{_name(p)}` line {h['line']}: catches `{h['type']}` " + ("and discards it (no logging/fallback)." if h.get("swallows") else "and handles it."), _evs(M, [(p, h["line"])]), note="Rationale not documented in the repository."))
    return out[:5]

def _stubs(M, files):
    return [_item(f"`{s['name']}` in `{p}` is a stub (pass/NotImplementedError)", _evs(M, [(p, s["line"])])) for p in sorted(files) for s in ((M["files"][p]["structure"] or {}).get("stubs") or [])][:6]

def _tests(M, files):
    out = []
    for p in sorted(files):
        t = M["test_of"].get(p)
        if t: out.append(_item(f"`{_name(p)}` is covered by " + ", ".join(f"`{x}`" for x in sorted(map(str, t))[:3]), _evs(M, [(p, 1)])))
    return out[:5] or [_item("Test coverage for this role's files could not be established from the extracted import relationships; inspect the test suite before concluding coverage is missing.", status="UNVERIFIED")]

def _nodes(nodes, kind): return [n for n in nodes if n["type"] == kind and n.get("evidence")]

def _pending(M, rv, nodes, tasks):
    out = [_item(str(t.get("title") or t.get("name")), t.get("evidence") or [], t.get("verification") or "INFERRED") for t in tasks[:6]]
    out += _stubs(M, rv["files"]) + [dict(_item("Suggested (not a confirmed task): " + s["title"], s["evidence"], "INFERRED"), needs_confirmation=True) for s in rv["suggestions"][:4]]
    return out

def _owners(M):
    out = []
    for c, paths in sorted(M["comps"].items(), key=lambda kv: -len(kv[1]))[:8]:
        fac = defaultdict(int)
        for p in paths:
            for f, x in (M["facets"].get(p) or {}).items(): fac[f] += x["count"]
        if fac: out.append(_item(f"`{c}` ({len(paths)} files): likely {OWNER.get(max(fac, key=fac.get), 'unassigned')} area — inferred from code; no named owner is recorded.", status="INFERRED"))
    return out

def _progress(nodes):
    tasks = _nodes(nodes, "task")
    if not tasks: return []
    by = defaultdict(int)
    for n in tasks: by[str((n.get("attrs") or {}).get("status") or "status not recorded")] += 1
    return [_item(f"{len(tasks)} recorded task(s): " + ", ".join(f"{v} {k}" for k, v in sorted(by.items())))] + [_node_item(n) for n in tasks[:8]]

def _priorities(M):
    st, out = M["stats"], []
    unguarded = [(p, m["line"]) for p, f in M["files"].items() for m in ((f["structure"] or {}).get("model_loads") or []) if not m.get("guarded")]
    if st["failed"]: out.append(_item(f"Resolve {st['failed']} failed repository file(s): analysis is incomplete.", status="INFERRED"))
    if unguarded: out.append(_item(f"{len(unguarded)} model load(s) are not guarded against a missing checkpoint.", _evs(M, unguarded[:2]), "INFERRED"))
    for g in gaps(M, "lead", set(M["files"]))[:3]: out.append(_item("Close knowledge gap: " + g, status="INFERRED"))
    return out  # derived from evidence only; no priority/owner/deadline is asserted

def build_sections(perspective, M, rv, role, nodes, tasks):
    files = set(rv["files"]); g = rv["gaps"]
    if perspective == "departing":
        decs = [_node_item(n) for n in _nodes(nodes, "decision")[:6]]
        return [_sec("Work completed and implemented", _modules(rv), "No implementation evidence matched this role."),
                _sec("Technical decisions and rationale", decs, "No decisions are documented and no design choices were extracted; rationale must be obtained from the departing member."),
                _sec("Changes made and relevant files", [_item(f"`{p}`", _evs(M, [(p, 1)]), symbols=_symbols(M, p)) for p in sorted(files)[:10]], "No relevant files matched."),
                _sec("Incomplete work and unresolved issues", _stubs(M, files) + [_node_item(n) for n in _nodes(nodes, "task") if str((n.get("attrs") or {}).get("status") or "").lower() not in ("done", "completed")][:6], "No stubs or open task records were found for this area."),
                _sec("Known risks and blockers", [_node_item(n) for n in _nodes(nodes, "risk")[:5]] + [_item(x, status="INFERRED") for x in g] + _guards(M, files), "No risks or blockers are recorded."),
                _sec("Handover instructions for the successor", _tests(M, files) + _flow(M, rv)[:2] + _setup(M)[:2], "No instructions could be derived from the repository.")]
    if perspective == "administrator":
        team = [_node_item(n) for n in _nodes(nodes, "person")[:10]]
        dep = [_item(f"`{c['component']}` " + (f"depends on `{c['depends_on']}`" if c.get("depends_on") else f"is used by `{c['used_by']}`"), c.get("evidence") or [], "SOURCE_CODE_EVIDENCE" if c.get("basis") == "extracted" else "INFERRED") for c in collaboration(M, "lead", list(M["comps"])[:8])] + [_node_item(n) for n in _nodes(nodes, "risk")[:4]]
        return [_sec("Overall project status", _context(M), "No status could be derived."),
                _sec("Team roles and responsibilities", team, "No team members are recorded in the connected sources."),
                _sec("Component ownership", _owners(M), "No components were identified."),
                _sec("Confirmed task progress", _progress(nodes), "No confirmed task records exist for this repository."),
                _sec("Dependencies and blockers", dep, "No cross-component dependencies or blockers were found."),
                _sec("Knowledge gaps and project risks", [_item(x, status="INFERRED") for x in gaps(M, "lead", set(M["files"]))], "No knowledge gaps were detected."),
                _sec("Management priorities (derived from evidence, unassigned)", _priorities(M), "No evidence-based priorities were identified.")]
    return [_sec("Project context", _context(M), "No project context could be derived."),
            _sec("Development environment and setup", _setup(M), "No dependency manifests, build files or environment variables were found."),
            _sec("Relevant modules and source files", [_item(f"`{p}`", _evs(M, [(p, 1)]), symbols=_symbols(M, p)) for p in sorted(files)[:10]], "No modules matched this role."),
            _sec(f"{role} responsibilities supported by source evidence", _modules(rv), "No source evidence supports a responsibility for this role."),
            _sec("Existing implementation and execution flow", _flow(M, rv), "No execution flow could be extracted."),
            _sec("Pending tasks and how to continue", _pending(M, rv, nodes, tasks) + _tests(M, files)[:2], "No confirmed tasks; start with the modules listed above.")]
