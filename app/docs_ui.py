"""API documentation layer: OpenAPI metadata + a branded Swagger UI page served at /docs."""
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from .config import VERSION

TITLE = "KNOWBRIDGE · Knowledge Reconstruction Engine"

DESCRIPTION = """
**What does the project actually know, what evidence supports it, and what important knowledge is missing?**

KNOWBRIDGE reads authorised project documents (PDF, DOCX, TXT, CSV, Markdown), reconstructs the knowledge inside them,
links every fact back to the exact passage it came from, and reports what is **missing, conflicting or outdated**
*before* a team member leaves.

> All bundled demo data is **synthetic**. Nothing here is a real project.

### Pipeline

| # | Stage | Endpoint | Output |
|---|-------|----------|--------|
| 1 | Ingest & chunk | `POST /documents/upload` | Documents and provenance-tagged chunks (doc ID, chunk ID, page/section) |
| 2 | NLP extraction | `POST /knowledge/extract` | Structured claims: people, tasks, decisions, rationale, dependencies, risks, questions |
| 3 | Knowledge graph | `GET /knowledge/graph` | NetworkX graph, exportable as JSON and GraphML |
| 4 | Evidence & verification | `GET /knowledge/evidence` | Every fact traced to its source passage and labelled |
| 5 | Knowledge Debt | `GET /knowledge/debt-report` | Prioritised gaps, conflicts and stale information |

### Verification labels
No arbitrary confidence percentages are used. Each fact carries exactly one label:

* **VERIFIED**: directly supported by source evidence
* **INFERRED**: derived from hedged or indirect statements, not stated explicitly
* **CONFLICTING**: sources disagree; all observations are kept and none is silently chosen
* **MISSING**: required information (owner, rationale, mitigation) has no supporting evidence
* **STALE**: possibly outdated by timestamp or superseded by a later record

### Try it in 30 seconds
1. Run **`POST /demo/load`** to ingest the synthetic project and run the full pipeline.
2. Open **`GET /knowledge/debt-report`** to see the gaps it found.
3. Copy an entity `id` from **`GET /knowledge/entities`** into **`GET /knowledge/evidence`** to trace a fact to its source.

### Design guarantees
* **Traceability**: every entity and relationship keeps its source document, chunk ID and verbatim supporting text.
* **No invention**: the rule extractor only reports what matches the text; the optional LLM extractor is rejected per claim if its evidence is not copied verbatim from the source.
* **Modular**: the extractor is pluggable (rules offline, LLM when credentials exist), so handover and UI modules can be built on top of these endpoints.
"""

TAGS = [
    {"name": "Demo & Operations", "description": "One-click demo data, service health and reset. Start here."},
    {"name": "1 · Documents", "description": "**Stage 1.** Upload PDF, DOCX, TXT, CSV or Markdown, **or connect GitHub repos / web pages / file links** (`POST /projects/ingest`). Files are text-extracted, chunked and stored with full provenance. Empty, corrupted and unsupported files are rejected with a clear error."},
    {"name": "2 · Extraction", "description": "**Stage 2.** Turn chunks into structured claims (people, tasks, decisions, rationale, ownership, dependencies, risks, deadlines, open questions)."},
    {"name": "3 · Knowledge Graph", "description": "**Stage 3.** The Knowledge Continuity Graph: people, tasks, decisions, risks, milestones, resources and open questions, connected by typed relationships."},
    {"name": "4 · Evidence", "description": "**Stage 4.** Trace any extracted fact back to the original document and passage, with its verification label."},
    {"name": "5 · Knowledge Debt", "description": "**Stage 5.** Detect missing owners, undocumented rationale, unmitigated risks, unresolved questions, conflicting statuses and outdated information."},
]

CONTACT = {"name": "KNOWBRIDGE · AI/ML & NLP Module"}
LICENSE = {"name": "Hackathon prototype · synthetic demo data"}

SWAGGER_PARAMS = {
    "docExpansion": "list", "filter": True, "displayRequestDuration": True, "tryItOutEnabled": True,
    "defaultModelsExpandDepth": 0, "defaultModelExpandDepth": 2, "syntaxHighlight.theme": "monokai",
    "persistAuthorization": True, "tagsSorter": "alpha", "operationsSorter": "method",
}

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230f172a'/%3E"
           "%3Cpath d='M14 44V20m0 12h8l14-12m-14 12 14 12' stroke='%232dd4bf' stroke-width='6' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E"
           "%3Ccircle cx='46' cy='20' r='5' fill='%2338bdf8'/%3E%3Ccircle cx='46' cy='44' r='5' fill='%2338bdf8'/%3E%3C/svg%3E")

CSS = """
:root{--kb-ink:#0f172a;--kb-teal:#0d9488;--kb-sky:#0284c7;--kb-line:#e2e8f0;--kb-soft:#f8fafc}
body{background:var(--kb-soft);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.swagger-ui .topbar{display:none}
.kb-hero{background:linear-gradient(135deg,#0f172a 0%,#134e4a 60%,#0e7490 100%);color:#fff;padding:34px 0 30px}
.kb-hero .wrap{max-width:1460px;margin:0 auto;padding:0 20px}
.kb-brand{display:flex;align-items:center;gap:14px}
.kb-logo{width:46px;height:46px;border-radius:12px;background:#0b1220;display:grid;place-items:center;box-shadow:0 0 0 1px rgba(255,255,255,.15)}
.kb-brand h1{margin:0;font-size:30px;letter-spacing:.4px;font-weight:800}
.kb-brand h1 span{color:#5eead4;font-weight:600}
.kb-tag{margin:10px 0 16px;font-size:16px;color:#cbd5e1;max-width:880px;line-height:1.5}
.kb-badges{display:flex;flex-wrap:wrap;gap:8px}
.kb-badge{font-size:12px;font-weight:600;padding:5px 11px;border-radius:999px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.22);color:#e2e8f0}
.kb-badge.ok{background:rgba(45,212,191,.18);border-color:rgba(45,212,191,.5);color:#99f6e4}
.kb-flow{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:18px;font-size:12.5px;color:#94a3b8}
.kb-flow b{background:rgba(255,255,255,.08);color:#e2e8f0;padding:4px 9px;border-radius:6px;font-weight:600}
.swagger-ui .info{margin:28px 0 8px}
.swagger-ui .info .title{display:none}
.swagger-ui .info .description{max-width:1000px}
.swagger-ui .info .description p,.swagger-ui .info .description li{font-size:14.5px;line-height:1.6;color:#334155}
.swagger-ui .info .description h3{margin-top:22px;color:var(--kb-ink);font-size:18px;border-bottom:2px solid var(--kb-line);padding-bottom:6px}
.swagger-ui .info .description table{border-collapse:collapse;width:100%;margin:10px 0;background:#fff;border:1px solid var(--kb-line);border-radius:8px;overflow:hidden}
.swagger-ui .info .description th{background:var(--kb-ink);color:#fff;font-size:12px;text-transform:uppercase;letter-spacing:.5px;padding:9px 12px;text-align:left}
.swagger-ui .info .description td{padding:8px 12px;border-top:1px solid var(--kb-line);font-size:13.5px}
.swagger-ui .info .description code{background:#e0f2fe;color:#075985;padding:2px 6px;border-radius:4px;font-size:12.5px}
.swagger-ui .info .description blockquote{border-left:4px solid var(--kb-teal);background:#f0fdfa;margin:12px 0;padding:8px 14px;border-radius:0 8px 8px 0}
.swagger-ui .info .description blockquote p{margin:0}
.swagger-ui .scheme-container{background:transparent;box-shadow:none;padding:10px 0}
.swagger-ui .opblock-tag{font-size:19px;font-weight:700;color:var(--kb-ink);border-bottom:1px solid var(--kb-line);padding:14px 10px}
.swagger-ui .opblock-tag small{font-size:13.5px;color:#64748b;font-weight:400}
.swagger-ui .opblock{border-radius:10px;border-width:1px;box-shadow:0 1px 2px rgba(15,23,42,.06);margin:0 0 12px}
.swagger-ui .opblock .opblock-summary{padding:7px}
.swagger-ui .opblock .opblock-summary-method{border-radius:6px;min-width:76px;font-weight:700}
.swagger-ui .opblock .opblock-summary-path{font-weight:700;color:var(--kb-ink)}
.swagger-ui .opblock .opblock-summary-description{color:#475569;font-size:13.5px}
.swagger-ui .opblock-description-wrapper p,.swagger-ui .opblock-description-wrapper li{font-size:13.5px;line-height:1.55}
.swagger-ui .btn.execute{background:var(--kb-teal);border-color:var(--kb-teal);border-radius:6px;font-weight:700}
.swagger-ui .btn.execute:hover{background:#0f766e}
.swagger-ui section.models{border-radius:10px;border-color:var(--kb-line);background:#fff}
.swagger-ui .filter .operation-filter-input{border-radius:8px;border:1px solid var(--kb-line)}
.kb-footer{max-width:1460px;margin:30px auto 40px;padding:16px 20px;color:#64748b;font-size:12.5px;border-top:1px solid var(--kb-line);text-align:center}
"""

HERO = f"""
<div class="kb-hero"><div class="wrap">
  <div class="kb-brand">
    <div class="kb-logo"><svg width="28" height="28" viewBox="0 0 64 64"><path d="M14 44V20m0 12h8l14-12m-14 12 14 12" stroke="#2dd4bf" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="46" cy="20" r="5" fill="#38bdf8"/><circle cx="46" cy="44" r="5" fill="#38bdf8"/></svg></div>
    <h1>KNOWBRIDGE <span>Knowledge Reconstruction Engine</span></h1>
  </div>
  <p class="kb-tag">Evidence-driven project knowledge continuity: reconstruct what a project knows, prove where each fact came from, and surface what is missing before someone leaves.</p>
  <div class="kb-badges">
    <span class="kb-badge ok" id="kb-live">● checking service…</span>
    <span class="kb-badge">v{VERSION}</span><span class="kb-badge">OpenAPI 3.1</span>
    <span class="kb-badge">FastAPI · NetworkX · SQLite</span><span class="kb-badge">Rule-based + LLM-ready extraction</span>
    <span class="kb-badge">Synthetic demo data</span>
  </div>
  <div class="kb-flow"><b>Upload</b>→<b>Extract text</b>→<b>Chunk</b>→<b>NLP extraction</b>→<b>Knowledge graph</b>→<b>Evidence mapping</b>→<b>Conflict &amp; gap detection</b>→<b>Debt report</b></div>
</div></div>
"""

SCRIPT = """
<script>
(function(){
  var hero=document.createElement('div');hero.innerHTML=%s;
  document.body.insertBefore(hero.firstElementChild,document.body.firstChild);
  var foot=document.createElement('div');foot.className='kb-footer';
  foot.textContent='KNOWBRIDGE · AI/ML & NLP module · every finding is derived from the uploaded documents and traceable to its source passage.';
  document.body.appendChild(foot);
  fetch('/health').then(function(r){return r.json()}).then(function(h){
    var b=document.getElementById('kb-live');
    b.textContent='● online · '+h.documents_loaded+' documents loaded · extractor: '+h.extractor_mode;
  }).catch(function(){var b=document.getElementById('kb-live');b.textContent='● offline';});
})();
</script>
"""


def swagger_page(openapi_url: str) -> HTMLResponse:
    import json
    page = get_swagger_ui_html(openapi_url=openapi_url, title=TITLE, swagger_favicon_url=FAVICON,
                               swagger_ui_parameters=SWAGGER_PARAMS)
    html = page.body.decode()
    html = html.replace("</head>", f"<style>{CSS}</style></head>")
    html = html.replace("</body>", (SCRIPT % json.dumps(HERO)) + "</body>")
    return HTMLResponse(html)
