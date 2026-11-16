# KNOWBRIDGE — Round 3 Implementation Report

## Scope of this update

This report records the changes made to the uploaded `knowbridge 13` source snapshot. It distinguishes completed and tested code changes from work that still needs to be completed before the Round 3 submission.

## Baseline

- Existing FastAPI application with SQLite persistence and NetworkX knowledge graph.
- Existing document ingestion, rule-based extraction, optional Anthropic extraction, evidence verification, knowledge-debt detection, TF-IDF retrieval, chatbot, role-aware handover lifecycle and PDF export.
- The baseline test run completed with **34 passed and 1 failed**. The failing test was `test_role_specific_questions_and_answer_status`: strict role filtering omitted open questions from some handover profiles.

## Changes implemented in this update

### 1. LLM extraction validation and resilience

File: `app/extraction.py`

- Rewrote the extraction instructions to focus on explicitly supported project claims and exact source evidence.
- Added `_validated_llm_claims` to validate JSON array shape, allowed claim kinds, field structure, certainty values and exact evidence presence in the source chunk.
- Invalid or unsupported claims are dropped before persistence.
- Added configurable `KNOWBRIDGE_LLM_TIMEOUT` (default 20 seconds).
- Configured the Anthropic client with a bounded retry count.
- Kept the existing rule-based fallback path; an LLM error continues to fall back to deterministic rule extraction.
- Kept the model configurable through `KNOWBRIDGE_LLM_MODEL`.

**Not verified:** a live Anthropic API request was not executed because this environment does not have the user's production API credentials. Provider-specific live behavior must still be tested with a valid key.

### 2. Hybrid evidence retrieval

File: `app/search.py`

- Replaced the previous single-mode retrieval selection with reciprocal-rank fusion over keyword TF-IDF and optional semantic embedding results.
- Added a lightweight lexical reranking signal.
- Added in-process caching for the embedding model and document vectors.
- Preserved dependency-free keyword retrieval when semantic embeddings are disabled or unavailable.
- Added an out-of-corpus guard so unrelated queries do not receive arbitrary chunks.
- Search relevance remains a ranking value, not a confidence score.

Semantic mode remains optional and requires `KNOWBRIDGE_EMBEDDINGS=1` plus the optional `sentence-transformers` dependency. The default path is hybrid keyword retrieval; semantic model inference was not run in this environment.

File: `app/chatbot/service.py`

- Added graph-derived evidence retrieval from stored knowledge entities.
- Combined graph evidence with retrieved source passages and deduplicated the combined results.
- Answers continue to quote or summarise only retrieved source evidence.

**Remaining limitation:** this update adds entity/evidence retrieval from the graph but does not yet implement a complete multi-hop graph traversal, SQL query planner or a separately benchmarked reranker.

### 3. Handover correctness

File: `app/handover.py`

- Corrected strict role filtering so explicitly documented open questions remain visible in handovers.
- Corrected general role filtering so open questions are not dropped merely because their keyword score is below the normal inclusion threshold.
- This also restores the role-specific clarification actions tied to those questions.

## Tests

Executed using `python -m pytest -q` from the extracted project root.

**Final result: 37 passed.**

New tests in `tests/test_round3_improvements.py` cover:
- Rejection of LLM claims without exact source evidence or valid schema.
- Retrieval of relevant keyword evidence.
- Rejection of unrelated out-of-corpus queries.

Existing tests cover ingestion, extraction, graph construction, verification, knowledge debt, chatbot, PDF reports and handover lifecycle.

## Files changed

- `app/extraction.py`
- `app/search.py`
- `app/chatbot/service.py`
- `app/handover.py`
- `tests/test_round3_improvements.py`

## Work still required before final submission

1. Run a live LLM extraction test using the configured provider and a valid secret stored only in the deployment environment.
2. Install and test the optional semantic embedding path in a deployment-compatible environment; record model, latency and retrieval-quality results.
3. Add a proper multi-hop graph retrieval stage and structured database retrieval if these are part of the team's promised Hybrid RAG specification.
4. Extend the handover readiness explanation and validate dependency-aware recommendations against multiple project fixtures.
5. Complete a dedicated frontend pass. The current UI is a dark-themed chat workspace with upload/connect controls and a handover dialog; this update does **not** claim that a full dashboard, interactive graph explorer or complete multi-page SaaS interface has been delivered.
6. Perform a full browser-based usability test on desktop and mobile.
7. Deploy to the existing Render service only after local validation and backup. Deployment was not performed by this update.
8. Update the final project PDF and team contribution statements based on the actual final code.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Run tests:

```bash
python -m pytest -q
```

Optional semantic retrieval:

```bash
pip install sentence-transformers
export KNOWBRIDGE_EMBEDDINGS=1
```

Optional Anthropic extraction:

```bash
export KNOWBRIDGE_LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY='your-key-in-environment'
export KNOWBRIDGE_LLM_MODEL='your-supported-model'
```

Do not commit `.env` files or API keys.


## Additional update — complete GitHub repository ingestion

Implemented after the previous report snapshot:

- Replaced README/tree-limited GitHub fetching with a download of the selected branch's repository ZIP archive.
- Added safe recursive ZIP inspection, including nested ZIP archives, with path traversal protection and archive size/depth/file-count/compression-ratio limits.
- Added recursive indexing of source-code files and common configuration formats, not only documentation files.
- Preserved repository-relative paths, branch, commit SHA (when GitHub API provides it), source URL and SHA-256 content hash in chunk metadata.
- Added code-specific chunking so source comments (including Python `#` comments) are retained instead of being mistaken for Markdown headings.
- Added a synthetic repository fixture test covering nested folders, source files, nested ZIPs, skipped dependency folders and ZIP traversal rejection.

Validation: `pytest -q` — **38 passed**.

Limitations: ingestion is bounded by configured archive/file caps; binary formats without a parser and generated/dependency folders are intentionally skipped. Live GitHub private-repository and large-archive behaviour still needs deployment-level validation with the team's credentials.


## Follow-up fix: direct audio inventory answers

- Added audio/music/voice-specific query expansion.
- Added a repository-wide scan over indexed chunks for audio filenames, audio asset directories, playback APIs and FFmpeg references.
- Added deterministic concise inventory answers so audio questions do not fall back to repeated raw source passages when the LLM is unavailable.
- Added regression coverage for named audio assets and playback references.
- Validation: 42 tests passed.

## Subsequent generic chatbot upgrade (14 November 2026)

### Implemented

- Replaced the narrow LLM prompt with a generic evidence-grounded synthesis prompt that adapts the response format to the question, uses recent conversation only for reference resolution, and requires valid evidence citations.
- Chat requests now pass the previous messages from the same conversation into the answer pipeline. Existing conversation storage is reused.
- Added structured extracted-claim retrieval alongside the existing keyword/optional semantic retrieval and knowledge-graph evidence retrieval.
- Improved the no-LLM fallback so it returns a concise, deduplicated evidence summary and explicitly identifies it as a fallback rather than presenting raw retrieval results as a generated answer.
- Expanded document ingestion to JSON, YAML, XML, HTML, common source/config files and XLSX/XLS. XLSX/XLS parsing uses `openpyxl`.
- Added bounded recursive ZIP upload ingestion, including nested ZIPs, using the existing safe archive limits and path traversal protections.
- Improved chat rendering for headings and code, made evidence expandable with source filename/page context, and added Copy answer.
- Updated API documentation and the upload file picker.

### Files changed in this batch

- `app/chatbot/service.py`
- `app/chatbot/router.py`
- `app/ingestion.py`
- `app/sources.py`
- `app/routers/sources.py`
- `app/static/index.html`
- `app/docs_ui.py`
- `requirements.txt`
- `tests/test_generic_chatbot.py`
- `ROUND3_PROGRESS.md`

### Validation

- `python -m py_compile` on modified Python modules: passed.
- `node --check` on the extracted frontend script: passed.
- `python -m pytest -q`: **46 passed**.
- ZIP archive integrity check: passed.

### Not verified / remaining

- No live Hugging Face inference call was made; valid credentials and provider/model availability are required to verify generated answers in deployment.
- Render deployment and browser-based desktop/mobile checks were not performed.
- Documents are still stored in the existing shared corpus; full per-project document isolation is not implemented in this batch.
- OCR, vision and audio transcription are not implemented; binary files are not treated as understood content.
- Multi-hop graph traversal and a SQL query planner are not implemented. Existing graph evidence plus the new structured-claim retrieval are used.
- Fine-tuning was intentionally not added; RAG remains the default adaptation method.


## Follow-up regression fixes (2026-11-14)
- Prevented code statements, assignments, control-flow lines and uppercase debug strings from being misclassified as unresolved questions.
- Filtered code-derived question entities from handover sections/actions, including previously indexed noisy claims.
- Dataset questions now require explicit dataset/training-data evidence; otherwise the chatbot reports insufficient evidence rather than repeating generic README passages.
- Tightened no-LLM fallback so weak lexical matches are not presented as an answer.
- Added regression tests for code-noise extraction and unsupported dataset questions.
- Final test result: 48 passed.
