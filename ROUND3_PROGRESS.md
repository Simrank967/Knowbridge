# Round 3 Progress

- Completed: generic evidence-grounded LLM synthesis prompt with adaptive response format, citations, bounded context and provider failure fallback.
- Completed: chat route now supplies recent conversation history so follow-up references can be resolved by retrieval and the LLM.
- Completed: upload parser support expanded to JSON, YAML, XML, HTML, common source/config files, XLSX/XLS and safe recursive ZIP archive ingestion (nested archives bounded by existing archive safety limits).
- Completed: chat renderer now formats headings, inline code and fenced code, shows source filenames/pages in expandable evidence, and provides Copy answer; upload picker reflects supported extensions.
- Modified: `app/chatbot/service.py`, `app/chatbot/router.py`, `app/ingestion.py`, `app/static/index.html`, `tests/test_generic_chatbot.py`, `app/sources.py`, `app/routers/sources.py`, `requirements.txt`, `app/docs_ui.py`, `ROUND3_PROGRESS.md`, `ROUND3_IMPLEMENTATION_REPORT.md`.
- Tests: focused regression tests passed; final full suite: 46 passed.
- Decisions: RAG remains the adaptation mechanism; no foundation-model fine-tuning added. Semantic retrieval remains optional and existing hybrid keyword/embedding + graph retrieval preserved, with structured extracted-claim retrieval added.
- Remaining: live Hugging Face and deployed Render verification; true project-level document isolation; OCR/vision/audio transcription; full multi-hop graph and SQL planning; mobile/browser usability testing. XLSX uses the added openpyxl dependency.

- Regression fixes: code statements, assignments, guards and uppercase debug strings are no longer extracted as unresolved project questions; handover generation also filters stale code-derived question entities from previously indexed corpora.
- Regression fixes: dataset-identification questions now require explicit dataset/training-source evidence and return an INSUFFICIENT/MISSING answer instead of unrelated README snippets; weak lexical matches are no longer presented as generated conclusions when the LLM is unavailable.
- Additional tests: `tests/test_regression_noise.py` covers code-noise extraction and missing dataset evidence.
- Final test run after fixes: 48 passed.


## Chat readability regression fix (2026-11-14)
- Completed: project overview now prioritises README/narrative documentation and excludes source-code chunks, model metadata, directory trees and Markdown table rows from the primary response.
- Completed: deterministic overview fallback uses a short structured summary; detailed source passages remain in the collapsible evidence panel.
- Completed: chat renderer now formats paragraphs, headings, lists, inline code and code blocks with improved spacing and wrapping.
- Files modified: `app/chatbot/service.py`, `app/static/index.html`, `tests/test_regression_noise.py`.
- Tests: full pytest suite, 49 passed.
- Remaining: verify on deployed Render instance; confirm repository indexing coverage and LLM availability separately.
- Decision: never treat raw retrieval chunks as a user-facing project summary; preserve them only as expandable evidence.

## GitHub coverage and README cleanup (2026-11-14)
- Fixed README overview cleanup for badge links, inline HTML/images, Markdown links, headings, lists and inline formatting; raw badge URLs/HTML no longer pollute the answer.
- GitHub ingestion now creates a repository asset-inventory document for unsupported/binary files, recording paths, extensions and sizes without pretending to parse binary contents.
- Ingestion summary now reports inspected files, indexed documents, unsupported/binary inventory count and parse failures separately.
- Expanded text/code extension support for MDX, Go, Ruby, Rust, Kotlin, PHP, Swift, shell, R, Scala, Vue, Svelte, Terraform and related documentation formats.
- Rule extraction now captures explicit technology/tool references and `X-based` algorithm mentions as resource entities, supporting knowledge graph population from technical README content.
- Tests added for technology resource extraction and README badge/HTML cleanup. Full suite: 51 passed.
- Remaining: confirm live GitHub archive contents and Render deployment; unsupported binary payloads remain filename/metadata-only unless a dedicated parser is available.

## Role-aware task allocation (2026-11-14)
- Completed: added explicit responsibility profiles for backend, frontend, full-stack, ML, AI/GenAI, data engineering/science, DevOps, QA, security and technical lead roles.
- Completed: actual graph task nodes are matched to a selected role using task title/description evidence; assignments include primary owner, rationale, matched responsibility terms, priority rationale, dependencies, expected outcome and source evidence.
- Completed: generated handover payload now includes `role_task_plan` and an honest empty-state explanation; recommended work is not fabricated as existing project tasks.
- Completed: frontend handover output displays role task plan with owner, rationale, priority, status, dependencies, expected outcome and source document.
- Files modified: `app/handover.py`, `app/static/index.html`, `tests/test_handover.py`, `ROUND3_PROGRESS.md`.
- Tests: targeted handover tests 5 passed; full suite 53 passed.
- Decisions: assignments are inferred only for explicit extracted task entities; role ownership must be reviewed by project owner. Missing task evidence yields an explicit empty state.
- Remaining: task extraction coverage depends on source documents explicitly expressing work items; live Render deployment not verified.

## Role-specific component intelligence (2026-11-14)

- Root cause: the previous handover path primarily ranked extracted graph entities and only created role ownership plans from explicit `task` nodes. GitHub repositories commonly contain implementation components in source files without explicit task records, so otherwise useful repositories produced empty and near-identical role plans.
- Added deterministic, role-specific component retrieval over the existing indexed chunks and document filenames. It uses curated responsibility vocabularies for backend, frontend, full-stack, ML, AI/GenAI, data engineering, data science, DevOps, QA, security and technical lead roles; custom role descriptions are incorporated when supplied.
- Handover payload now separates source-backed project components, existing responsibilities to understand, suggested improvements (explicitly not confirmed tasks), and confirmed task records. Each generated responsibility/suggestion retains source file, chunk ID and evidence excerpt.
- Updated the handover frontend and PDF to display the separate sections. Confirmed tasks remain sourced from explicit task graph records; suggested improvements never become confirmed tasks. Unrecorded task priorities are now shown as unprioritised rather than assigned a fabricated medium priority.
- Files changed: `app/handover.py`, `app/static/index.html`, `tests/test_handover.py`, `ROUND3_PROGRESS.md`.
- Regression coverage: role-to-component retrieval for Backend Developer, ML Engineer, AI/GenAI Engineer and Frontend Developer; source provenance; distinct role outputs; suggestion/confirmed-task separation; honest empty evidence state.
- Validation: `pytest -q` — 55 passed.
- Limitations: component matching is deterministic lexical retrieval, not semantic model reasoning. Coverage is bounded by files and passages actually ingested and indexed. Graph component relationships are not yet persisted as new graph nodes/edges; the handover derives component matches from the existing source chunk/document records to avoid a schema migration.

## Repository intelligence upgrade (full-repo ingestion + role-aware assistant)
- New modules: `app/repo_scan.py` (file classification, Python AST / regex structure, structure-aware code chunks with line ranges, binary header metadata), `app/repo_intel.py` (manifest persistence, incremental hashes, NetworkX project model with extracted/inferred edges, evidence-cited summary), `app/repo_roles.py` (facet-based role ranking, role-lens answers, responsibilities/suggestions/gaps).
- `db.py`: additive `repo_files` / `repo_scans` tables, `DB.delete_document`; reset clears them.
- `sources.py`: `github_results` rewritten: every archive entry (including ones the reader used to drop: vendored, oversized, bomb-guard, unreadable) gets a manifest row with DISCOVERED/INDEXED/METADATA_ONLY/EXCLUDED/FAILED; git-tree API fallback; unchanged files (sha256) reused; bounded by KB_MAX_INDEXED_FILES / KB_MAX_RAW_FETCH / KB_MAX_FILE_BYTES; truncation reported, never silent.
- `routers/sources.py`: scans persisted after documents are stored; response carries per-repo stats and a PARTIAL warning on failures; new `GET /repo/manifest`, `/repo/summary`, `/repo/file` (open source evidence).
- Chat: `role` / `role_description` accepted; repo-aware answer first, generic pipeline as fallback (unrelated queries still return no evidence). Handover: source-derived components/responsibilities/suggestions replace keyword matching; adds `repo_context` (commit, stats, collaboration, gaps, summary). UI: role selector, ingestion stats/PARTIAL warning.
- Tests: `tests/test_repo_intelligence.py` (synthetic repo: accounting, incremental reuse, role-different answers, dataset/audio, unrelated rejection, handover). Full suite: 62 passed.
- Remaining: GitHub-token-less rate limits cap the git-tree fallback; non-Python structure is regex-level; LLM synthesis only with HF token (deterministic otherwise); UI shows stats in chat text (no dedicated manifest panel yet); `/repo/file` serves indexed text only (no binary preview).

## Project isolation, handover accuracy, perspectives, streaming upload
- **Isolation**: `repo_intel.ScopedDB/scope` give a read-only view of one repository (default: latest scan; `Generate.repository` selects another). Association = chunk provenance (`meta.repository`); legacy records match only on the ingestion-assigned `github:<owner/repo> · ` name; unknown association is excluded. Applied at the top of `chatbot.answer` and in `generate_payload`, so other repos, unassociated uploads and synthetic demo documents never reach retrieval, graph, tasks or handovers. Uploads can be explicitly associated via `POST /documents/upload?repository=owner/repo`. Every GitHub chunk now carries repository_id, repository_url, commit, file_path, content hash (document_id/source_type come from the document row).
- **False questions**: `_looks_like_code` now rejects assignments, annotations, `.get(...)`, calls with literals, tracebacks, decorators, config sections; source-code chunks (`github_code` / file_kind source|config) are no longer sent through claim extraction at all.
- **Role relevance**: `repo_roles.PRIMARY` — a file qualifies for a role only with a primary facet; RL/ML/UI-dominated files are excluded from Backend/Full-Stack/DevOps/QA/Security (model loading / try-except alone no longer qualifies). Responsibilities describe real symbols; boilerplate outcome text removed.
- **Recommendations**: deduped by (component, title); "Add tests" is one per component and only when test-to-module evidence shows a gap (and only for api/persistence modules for Backend/Full-Stack).
- **Perspectives**: new `repo_handover.py` — incoming (context, setup, modules, responsibilities, execution flow, pending/how to continue), departing (implemented work, decisions/design choices, files, incomplete work, risks, successor instructions), administrator (status, team, component ownership, task progress, dependencies/blockers, gaps/risks, evidence-derived priorities). Different retrieval and structure; no owners/progress/decisions invented (unknown => MISSING / MANIFEST_DERIVED / INFERRED).
- **Chat**: follow-ups inherit the previous user question; strict scope on every path.
- **Upload**: streamed to `<name>.part` in 1 MB blocks, 413 beyond `KNOWBRIDGE_MAX_UPLOAD_MB` (default now 200), temp file always removed; `ingest_file` hashes streaming instead of reading whole files.
- **Tests**: `tests/test_isolation_perspectives.py` (9) + earlier suite: 71 passed on Python 3.13 and 3.11.
- **Deployment config**: `KNOWBRIDGE_MAX_UPLOAD_MB` must not exceed the host/proxy body limit; PDF/DOCX/XLSX parsing still loads the document in the parser (memory ≈ file size); `GITHUB_TOKEN` recommended; HF token only for optional LLM synthesis (no Anthropic key used).
- **Remaining**: extraction of legacy databases keeps old claims until `/knowledge/extract` is rerun; association of uploads is explicit (no automatic project linking); non-Python structure is regex-level; large text files are still decoded in memory before chunking.

## Consolidated UI and intelligence refresh (2026-11-16)
- Refreshed the existing KNOWBRIDGE chat workspace styling with a cohesive premium dark visual system: refined sidebar, typography, gradient accents, interactive cards, responsive spacing, focus states and composer styling. Existing routes and frontend actions are preserved.
- Tightened Backend role matching so generic exception handling/core logic/model loading do not pull ML/RL modules into Backend by default.
- Grouped repeated recommendation types by component and retained supporting source files/evidence.
- Corrected departing-member report semantics: exception handlers are surfaced as potential risks, not technical decisions. Missing test-coverage evidence is labelled unknown rather than asserted as absent.
- Filtered obvious package-prefix/parser noise from the displayed technology summary.
- Validation: no backend/server commands or tests were run, as requested. Review the generated ZIP and deploy/test in the intended environment.
- Upload policy: the application-level upload cap is now disabled by default (`KNOWBRIDGE_MAX_UPLOAD_MB=0`) while retaining streamed-to-disk upload; deployments can set a positive cap to match host limits. Proxy, memory, disk and parser limits still apply; this is not unlimited hosting capacity.

## Follow-up fixes from local regression output (2026-11-16)
- Repository role facets now recognize non-UI source modules with at least one extracted class/function as application core logic. The existing dominance gate still excludes RL/ML/LLM-dominated files from Backend, while allowing ordinary gameplay/application modules to match Backend.
- Role handovers now label unestablished test coverage as `UNVERIFIED` (not an unsupported `UNKNOWN` status), preserving the distinction between absent evidence and proven absence.
- No backend commands, server startup, or tests were run for this patch.
- Follow-up from latest local pytest output: restored the Backend role's primary `core_logic` weight (2) alongside API/persistence/security. The previous edit had unintentionally removed that weight, so ordinary application/gameplay modules were filtered out. RL/ML/LLM dominance exclusion remains in place. No backend commands or tests run by assistant.

## First-time user onboarding and indigo/violet UI refresh (2026-11-16)
- Reworked the main workspace introduction to explain KNOWBRIDGE in plain language: connect GitHub or upload files, ask natural-language questions grounded in project sources, and create role-aware handovers.
- Added a dedicated three-step first-visit guide and clearer example-question section.
- Improved readability with larger supporting copy, stronger contrast, clearer section hierarchy, and a more consistent blue/purple/indigo palette.
- Preserved existing action handlers and key element IDs. No backend commands or tests were run.

## Synthetic demo isolation and concise first-answer polish (2026-11-16)
- Chatbot answers now exclude the explicitly bundled Smart Campus synthetic demo fixtures from ordinary user project retrieval when no GitHub repository is connected. The explicit demo-loading endpoint remains available for demonstrations/tests.
- If no genuine project sources remain, the chatbot returns its existing connect/upload guidance instead of presenting synthetic records as the user's project.
- Project overview output is shorter (up to two concise excerpts and three evidence passages) and its count label is simplified.
- Increased supporting text sizes and contrast across the onboarding panel, onboarding cards, example prompts and helper text.
- No backend/server commands or tests were run.

## Explicit demo opt-in regression fix (2026-11-16)
- Added an additive SQLite runtime flag so synthetic sample data is visible in chatbot answers only after the explicit `/demo/load` action; `/demo/reset` clears that flag.
- Ordinary project chat continues to exclude bundled synthetic fixtures when demo mode has not been selected.
- The reported `/knowledge/extract` 200-vs-409 failure indicates test database state contamination: documents exist at the start of `test_api_end_to_end`, so its empty-database precondition is not met. This is a test isolation issue, not a reason to weaken the production endpoint's 409 guard.
- No backend/server commands or tests were run.
