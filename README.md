# KNOWBRIDGE – Knowledge Reconstruction Engine (Simran's module)
Answers one question: **what does the project actually know, what evidence supports it, and what is missing?**
Out of scope (Harshit's module): handover generation, role-based filtering, frontend. All demo data is **synthetic**.

## Run on macOS
```bash
cd knowbridge && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                  # 32 tests (13 engine + 9 API + 9 new engine/link tests + 1 new API e2e)
python demo.py                       # full pipeline on sample_data/, prints findings
uvicorn app.main:app --reload        # API docs: http://127.0.0.1:8000/docs
```
**Fastest demo:** open http://127.0.0.1:8000/docs, run `POST /demo/load`, then `GET /knowledge/debt-report`.

Manual flow: upload the 4 files in `sample_data/` via `POST /documents/upload`, then `POST /knowledge/extract`, then `GET /knowledge/{entities,graph,evidence,debt-report}`. Add `?export_files=true` to `/knowledge/graph` to write JSON + GraphML to `data/exports/`.
Optional LLM extraction: `pip install anthropic`, set `ANTHROPIC_API_KEY` and `KNOWBRIDGE_LLM_PROVIDER=anthropic` (never hardcode keys). Falls back to rules per chunk on any error.

## New in v1.1
- **Step 1 accepts links**: `POST /sources/connect` (JSON `urls`) or `POST /projects/ingest` (files + `urls`, then auto-extract). GitHub repo -> every doc file in every folder (md/txt/rst/csv/pdf/docx, skipping node_modules etc.), TODO/FIXME comments from source files, issues & PRs (assignee, state, `depends on #n`); caps: 60 docs, 40 code files; web pages -> main text; file links (PDF/DOCX/CSV/MD/TXT). Source URL kept as provenance. SSRF-guarded. Optional `GITHUB_TOKEN`.
- **Readable output, JSON kept in backend**: `GET /knowledge/debt-report?format=markdown|text`, `GET /knowledge/summary?format=markdown`, `GET /knowledge/brief` (narrative + readiness verdict). Narrative is rendered from the structured findings; an LLM narrative is accepted only if every cited `KD-xxx` exists. Raw LLM JSON is stored in SQLite (`llm_raw_outputs`), never returned.
- **Free-form prose rules** (`X is responsible for...`, `we're going with...`, `blocked by / waiting on`, `still need to decide`, `not sure whether`) - see `sample_data_prose/`.
- `GET /knowledge/trace/{entity_id}`: fact -> document -> chunk -> exact character span + `verbatim_in_source` check.
- `GET /knowledge/ask?q=`: TF-IDF retrieval (sentence-transformers if `KNOWBRIDGE_EMBEDDINGS=1`); returns passages only, or "no evidence found".
- LLM extractor: one retry on malformed JSON; evidence must still be verbatim. **Still not tested with a live key.**

## Pipeline and modules (what to tell judges)
| Stage | File | What it does |
|---|---|---|
| A Ingest | `ingestion.py` | PDF/DOCX/TXT/CSV/MD -> text; heading/page/row-aware chunks; doc + chunk IDs; clean errors for empty/corrupt/unsupported files |
| B Extract | `extraction.py` | Pluggable extractor -> claims `{kind, fields, evidence, certainty}`. Rule fallback works offline; LLM path rejects any claim whose evidence is not copied verbatim from the chunk |
| C Graph | `graph.py` | NetworkX graph: person/task/decision/risk/milestone/resource/question nodes; ASSIGNED_TO, DEPENDS_ON, BLOCKS, DECIDED_BY, SUPPORTED_BY, RELATED_TO, SUPERSEDES; JSON + GraphML export |
| D Verify | `evidence.py` | Labels VERIFIED / INFERRED / CONFLICTING / MISSING / STALE. Conflicts keep every observation, never auto-pick. No confidence percentages |
| E Debt | `debt.py` | Rule-based Knowledge Debt Report (title, category, description, severity, entity, evidence, what to verify) |
| Glue | `pipeline.py`, `db.py`, `main.py` | Orchestration, SQLite storage, FastAPI endpoints |

## Rules worth knowing
- **Conflict**: differing task statuses within 3 days, or a regression (done -> in progress). A normal forward change over weeks is history, not a conflict.
- **Stale**: newest evidence > 30 days older than the newest document (not for done tasks), or a decision superseded by a later one.
- **INFERRED**: only hedged wording ("probably", "might") or indirect mentions (RELATED_TO) support it.

## Honest limitations
- The rule extractor matches the explicit patterns in the demo formats (`Decision:`, `Action T-1:`, `X depends on Y`...). It is not general free-text NLP; free-form prose needs the LLM path (written, **not tested with a live key**).
- No FAISS/embedding retrieval yet (the deck lists FAISS); `/knowledge/evidence?q=` is keyword search. Natural next step.
- Dates come from `Date:` lines / CSV `updated` columns; unknown dates fall back to upload date.

## API at a glance (http://127.0.0.1:8000/docs)
| Tag | Endpoint | Purpose |
|---|---|---|
| Demo & Operations | `POST /demo/load` | Load synthetic project and run the whole pipeline in one call |
| | `GET /health`, `GET /knowledge/summary`, `DELETE /demo/reset` | Status, counts-only overview, clean slate |
| 1 · Documents | `POST /documents/upload`, `GET /documents` | Ingest PDF/DOCX/TXT/CSV/MD |
| 2 · Extraction | `POST /knowledge/extract`, `GET /knowledge/entities?type=&status=&q=` | Structured claims and entities |
| 3 · Knowledge Graph | `GET /knowledge/graph`, `GET /knowledge/graph/download?format=json or graphml` | Graph + file export |
| 4 · Evidence | `GET /knowledge/evidence?entity_id= or q=` | Fact -> source passage |
| 5 · Knowledge Debt | `GET /knowledge/debt-report?severity=&category=` | Prioritised gaps |

Code layout: `app/routers/` (thin endpoints), `app/schemas.py` (typed responses/examples), `app/docs_ui.py` (OpenAPI text + branded Swagger page), `app/services.py`, `app/deps.py`.
`/redoc` gives an alternative read-only reference. CORS is open so the UI module can call the API from another origin.

## Chatbot experience (added)
The root URL (`http://127.0.0.1:8000/`) now serves the KNOWBRIDGE conversational workspace. It reuses the existing source ingestion, extraction, evidence and debt modules.

- `POST /chat` — evidence-grounded project Q&A with conversation persistence.
- `GET /conversations`, `GET /conversations/{id}`, `DELETE /conversations/{id}` — chat history.
- `POST /reports/generate` — create a real PDF from a natural-language report request.
- `GET /reports/{id}`, `GET /reports/{id}/download` — report metadata and PDF download.
- Chat UI supports file upload, GitHub/web source connection, evidence passages and PDF report cards.

The current chatbot uses the existing TF-IDF retrieval and structured knowledge/debt engine. It does not pretend to be a general-purpose LLM: if project evidence is insufficient, it says so. LLM-powered free-form synthesis can be enabled as a separate, credential-dependent enhancement. PDF reports are generated from the currently ingested corpus and include evidence passages and scope limitations.
