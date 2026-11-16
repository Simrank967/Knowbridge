"""Tests for link ingestion, readable output, free-form rules, trace, search. Network is mocked; no fixtures needed for the pure tests."""
import base64, json
from pathlib import Path
import pytest
from app import sources
from app.db import DB
from app.extraction import RuleExtractor
from app.ingestion import _chunk_text, ingest_text
from app.narrative import debt_markdown, narrate, readiness, template_brief
from app.pipeline import extract_all, build
from app.search import search
from app.services import trace_entity

PROSE = Path(__file__).parent.parent / "sample_data_prose" / "standup_prose.md"
ISSUES = [
    {"number": 12, "title": "Set up CI", "state": "closed", "updated_at": "2026-10-01T10:00:00Z", "assignee": {"login": "asha"}, "body": ""},
    {"number": 13, "title": "Deploy to staging", "state": "open", "updated_at": "2026-10-08T10:00:00Z", "assignee": None, "body": "This depends on #12 and is blocked by #99."},
    {"number": 14, "title": "Fix login", "state": "closed", "updated_at": "2026-10-09T10:00:00Z", "pull_request": {"merged_at": "2026-10-09T09:00:00Z"}, "body": ""},
]

def fake_http(routes):
    def get(url, headers=None):
        for k, v in routes.items():
            if k in url: return (v if isinstance(v, bytes) else json.dumps(v).encode()), "text/html" if k.startswith("example") else "application/json"
        raise sources.SourceError("HTTP 404")
    return get

def with_http(routes, fn):
    old = sources._http_get; sources._http_get = fake_http(routes)
    old_check = sources.check_url; sources.check_url = lambda u: u
    try: return fn()
    finally: sources._http_get, sources.check_url = old, old_check

def test_ssrf_blocked():
    for bad in ("http://127.0.0.1/x", "http://localhost:8000", "http://169.254.169.254/latest", "ftp://example.com/a", "file:///etc/passwd", "http://10.0.0.5/a"):
        with pytest.raises(sources.SourceError): sources.check_url(bad)

def test_github_issues_become_extractable_facts():
    text, newest = sources.issues_markdown(ISSUES)
    assert newest == "2026-10-09" and "depends on GH-12" in text and "GH-99" in text
    claims = [c for ch in _chunk_text(text) for c in RuleExtractor().extract({"text": ch[0], "meta": None})]
    tasks = {c["fields"]["ref"]: c["fields"] for c in claims if c["kind"] == "task"}
    assert tasks["GH-12"]["owner"] == "asha" and "owner" not in tasks["GH-13"] and "PR-14" in tasks
    st = {c["fields"]["ref"]: c["fields"]["status"] for c in claims if c["kind"] == "status"}
    assert st == {"GH-12": "done", "GH-13": "in_progress", "PR-14": "done"}
    assert any(c["kind"] == "dependency" and c["fields"]["depends_on"] == "GH-12" for c in claims)

def test_github_whole_repo_all_folders(monkeypatch):
    import io, zipfile
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as z:
        z.writestr("nested-project/child/notes.md", "# Nested notes\nDecision: Use Redis because it is fast\n")
        z.writestr("nested-project/src/deep.py", "def nested_function():\n    return 'indexed'\n")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("repo-main/README.md", "# Demo\nDecision: Use Redis because it is fast\n")
        z.writestr("repo-main/docs/deep/plan.md", "# Plan\nAction T-1: Ship it (owner: Ann Lee)\n")
        z.writestr("repo-main/src/app.py", "x = 1\n# TODO(asha): rotate keys\n# FIXME handle timeout\n")
        z.writestr("repo-main/frontend/src/App.jsx", "export default function App(){ return 'hello'; }")
        z.writestr("repo-main/resources/archive.zip", nested.getvalue())
        z.writestr("repo-main/node_modules/x/readme.md", "must be skipped")
    routes = {"/issues?": ISSUES, "api.github.com/repos/o/r": {"pushed_at": "2026-10-10T00:00:00Z", "default_branch": "main"}}
    old_archive = sources._http_get_archive
    sources._http_get_archive = lambda url, headers=None: outer.getvalue()
    try:
        res, errs, notes = with_http(routes, lambda: sources.connect(["https://github.com/o/r"]))
    finally:
        sources._http_get_archive = old_archive
    names = [r["doc"]["filename"] for r in res]
    assert not errs
    assert any("docs/deep/plan.md" in n for n in names)
    assert any("src/app.py" in n for n in names)
    assert any("frontend/src/App.jsx" in n for n in names)
    assert any("resources/archive/nested-project/child/notes.md" in n for n in names)
    assert any("resources/archive/nested-project/src/deep.py" in n for n in names)
    assert not any("node_modules" in n for n in names)
    code_doc = next(r for r in res if "src/app.py" in r["doc"]["filename"])
    assert any("TODO(asha)" in ch["text"] for ch in code_doc["chunks"])
    assert code_doc["chunks"][0]["meta"]["path"] == "src/app.py"
    assert any("recursively inspected" in n for n in notes)


def test_recursive_zip_rejects_traversal_and_limits_depth():
    import io, zipfile
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("../escape.py", "unsafe = True")
        z.writestr("repo/good.py", "safe = True")
    extracted = list(sources._safe_zip_entries(payload.getvalue()))
    assert extracted == [("good.py", b"safe = True")]

def test_web_page_and_bad_link_do_not_block_each_other():
    html = b"<html><title>Plan</title><body><nav>menu</nav><main><h1>Plan</h1><p>Decision: Adopt MQTT because it is light</p></main></body></html>"
    res, errs, _ = with_http({"example.com/plan": html}, lambda: sources.connect(["https://example.com/plan", "https://example.com/missing"]))
    assert len(res) == 1 and len(errs) == 1 and "menu" not in res[0]["chunks"][0]["text"] and res[0]["doc"]["source_type"] == "web"

def test_free_form_prose_rules_and_no_invention():
    chunks = _chunk_text(PROSE.read_text()); claims = [c for t, *_ in chunks for c in RuleExtractor().extract({"text": t, "meta": None})]
    kinds = [c["kind"] for c in claims]
    owners = {c["fields"].get("owner") for c in claims if c["kind"] == "task"}
    assert {"Meera", "Dev Patel"} <= owners and kinds.count("question") == 2 and "decision" in kinds
    assert any(c["kind"] == "risk" and c["fields"].get("blocker") for c in claims)
    dec = next(c for c in claims if c["kind"] == "decision"); assert "already in our stack" in dec["fields"]["rationale"]
    assert all(c["evidence"] in PROSE.read_text() for c in claims)        # evidence is verbatim
    assert not [c for c in RuleExtractor().extract({"text": "Lunch was great and the weather is nice.", "meta": None})]

def test_readable_output_derived_from_json(kb):
    rep = kb["report"]; md = debt_markdown(rep)
    assert f"{rep['summary']['total']} open issues" in md and all(i["id"] in md for i in rep["issues"]) and "To verify" in md
    assert readiness(rep)["verdict"].startswith("Not ready") and str(rep["summary"]["total"]) in template_brief(rep)
    assert narrate(rep)[1] == "template"                                    # no LLM configured -> template

def test_trace_and_search(kb):
    db, G = kb["db"], kb["graph"]; t = trace_entity(G, db, "task:t-105")
    assert t["trail"] and all(x["verbatim_in_source"] for x in t["trail"]) and t["trail"][0]["char_start"] is not None
    hits, _ = search(db.chunks(), "who approves the cloud hosting budget"); assert hits and "budget" in hits[0]["text"].lower()
    assert search(db.chunks(), "quantum chromodynamics lattice") [0] == []

def test_llm_raw_output_stays_in_backend(tmp_path):
    db = DB(tmp_path / "x.db"); db.log_llm("extract", "c1", '[{"kind":"task"}]'); assert db.llm_outputs()[0]["raw"].startswith("[")
