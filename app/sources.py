"""Step-1 source connectors: GitHub repos, web pages and direct file links -> the same {doc, chunks} shape as file ingestion.
Safety: http(s) only, private/loopback/link-local addresses blocked (SSRF) incl. on redirects, size + time limits."""
import base64, io, ipaddress, json, logging, os, re, socket, ssl, urllib.error, urllib.request, zipfile, hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse
from .config import DATA
from .ingestion import SUPPORTED, IngestError, ingest_file, ingest_text

log = logging.getLogger("knowbridge.sources")
MAX_BYTES, TIMEOUT = 5 * 1024 * 1024, 15
GH_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(?:tree|issues|pulls?|wiki)(?:/.*)?)?/?$")
GH_BLOB = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/blob/(.+)$")

class SourceError(Exception):
    """Unreachable, blocked, oversized or unsupported link (reported per URL, never crashes the batch)."""

def check_url(url):
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname: raise SourceError("only http(s) URLs are allowed")
    try: infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror: raise SourceError(f"cannot resolve host '{u.hostname}'")
    if any(not ipaddress.ip_address(i[4][0]).is_global for i in infos): raise SourceError("URL points to a private or reserved address (blocked)")
    return u.geturl()

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl); return super().redirect_request(req, fp, code, msg, headers, newurl)

def _ssl_context():
    """macOS python.org builds ship without CA certs; certifi's bundle fixes CERTIFICATE_VERIFY_FAILED."""
    try:
        import certifi; return ssl.create_default_context(cafile=certifi.where())
    except ImportError: return ssl.create_default_context()

def _http_get(url, headers=None):
    """-> (bytes, content_type). The single network entry point (tests replace it)."""
    req = urllib.request.Request(check_url(url), headers={"User-Agent": "knowbridge/1.0", **(headers or {})})
    try:
        with urllib.request.build_opener(_SafeRedirect, urllib.request.HTTPSHandler(context=_ssl_context())).open(req, timeout=TIMEOUT) as r:
            data = r.read(MAX_BYTES + 1); ct = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise SourceError("rate-limited or private (set GITHUB_TOKEN)" if e.code in (403, 429) else f"HTTP {e.code} from {urlparse(url).hostname}")
    except (urllib.error.URLError, TimeoutError, OSError) as e: raise SourceError(f"could not fetch: {getattr(e, 'reason', e)}" + (" - run: pip install certifi" if "CERTIFICATE" in str(e) else ""))
    if len(data) > MAX_BYTES: raise SourceError("response larger than 5 MB")
    return data, ct

def _http_get_archive(url, headers=None):
    """Download a bounded GitHub ZIP archive (larger than the ordinary 5 MB link limit)."""
    req = urllib.request.Request(check_url(url), headers={"User-Agent": "knowbridge/1.0", **(headers or {})})
    try:
        with urllib.request.build_opener(_SafeRedirect, urllib.request.HTTPSHandler(context=_ssl_context())).open(req, timeout=60) as r:
            data = r.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise SourceError("GitHub archive access denied or rate-limited; configure GITHUB_TOKEN" if e.code in (403, 404, 429) else f"HTTP {e.code} downloading repository archive")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"could not download repository archive: {getattr(e, 'reason', e)}")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SourceError(f"repository archive exceeds {MAX_ARCHIVE_BYTES // (1024*1024)} MB limit")
    return data

def _safe_zip_entries(payload, depth=0, prefix="", _state=None):
    """Yield (relative_path, bytes) from ZIPs recursively, with traversal and bomb limits."""
    if depth > MAX_ARCHIVE_DEPTH:
        raise SourceError("nested archive depth limit exceeded")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError):
        raise SourceError("GitHub returned an invalid repository ZIP archive")
    state = _state if _state is not None else {"total": 0, "count": 0}
    with archive:
        infos = archive.infolist()
        for info in infos:
            if info.is_dir():
                continue
            state["count"] += 1
            if state["count"] > MAX_ARCHIVE_FILES:
                raise SourceError("repository contains too many files to ingest safely")
            name = info.filename.replace("\\\\", "/")
            parts = [x for x in name.split("/") if x not in ("", ".")]
            if not parts or name.startswith("/") or any(x == ".." for x in parts):
                continue
            # Strip GitHub's top-level owner-repo directory only at the root.
            rel = "/".join(parts[1:] if depth == 0 and len(parts) > 1 else parts)
            sk = state.setdefault("skipped", [])
            if not rel: continue
            if SKIP.search(rel) or SKIP_FILE.search(rel):
                sk.append((rel, info.file_size, "EXCLUDED", "vendored/generated/lockfile path")); continue
            if info.file_size > MAX_DOC_BYTES:
                sk.append((rel, info.file_size, "METADATA_ONLY", f"oversized: {info.file_size} bytes > {MAX_DOC_BYTES} (not extracted)")); continue
            if info.compress_size and info.file_size / info.compress_size > MAX_ARCHIVE_RATIO:
                sk.append((rel, info.file_size, "EXCLUDED", "suspicious compression ratio (archive-bomb guard)")); continue
            state["total"] += info.file_size
            if state["total"] > MAX_EXTRACTED_BYTES:
                raise SourceError("repository expanded size exceeds safe extraction limit")
            try:
                data = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile) as exc:
                sk.append((rel, info.file_size, "FAILED", f"archive entry unreadable: {exc}")); continue
            if Path(rel).suffix.lower() == ".zip":
                yield from _safe_zip_entries(data, depth + 1, rel[:-4], state)
            else:
                yield (f"{prefix}/{rel}".strip("/"), data)

def _gh(path):
    h = {"Accept": "application/vnd.github+json"}
    if os.getenv("GITHUB_TOKEN"): h["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"
    return json.loads(_http_get("https://api.github.com" + path, h)[0])

def _one_line(s): return re.sub(r"\s+", " ", s or "").strip()

def issues_markdown(items):
    """GitHub issues/PRs -> lines in the extractor's own grammar. Only facts the API states are written (state, assignee, dates, links)."""
    out, newest = [], ""
    for it in items:
        n, pr = it["number"], "pull_request" in it; ref = f"{'PR' if pr else 'GH'}-{n}"
        upd = (it.get("updated_at") or "")[:10]; newest = max(newest, upd)
        merged = pr and (it["pull_request"] or {}).get("merged_at")
        state = "done" if merged or (not pr and it["state"] == "closed") else ("in progress" if it["state"] == "open" else None)
        asg = (it.get("assignee") or {}).get("login"); title = _one_line(it["title"]).replace("(", "[").replace(")", "]")
        out += [f"## {ref}: {title}", f"Action {ref}: {title}" + (f" (owner: {asg})" if asg else "") + (f" (updated: {upd})" if upd else "")]
        if state: out.append(f"{ref} is {state}" + (f" (updated: {upd})" if upd else ""))
        for m in sorted(set(re.findall(r"(?:depends on|blocked by|requires)\s+#(\d+)", it.get("body") or "", re.I))):
            if int(m) != n: out.append(f"{ref} depends on GH-{m}")
        out.append("")
    return "\n".join(out), (newest or None)

TEXT_EXT = {".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc"}; FILE_EXT = {".csv", ".pdf", ".docx", ".xlsx", ".xls"}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".c", ".cpp", ".h", ".hpp", ".cs", ".php", ".swift", ".kt", ".kts", ".rs", ".sh", ".r", ".m", ".scala", ".vue", ".svelte", ".tf", ".sql"}
SKIP = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.git|\.venv|venv|__pycache__|target|\.next|coverage)/|(package-lock\.json|yarn\.lock|poetry\.lock)$|\.min\.")
MAX_DOCS, MAX_CODE, MAX_CODE_BYTES, MAX_DOC_BYTES, MAX_TODOS = 500, 500, 1_000_000, 2_000_000, 100
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 3
MAX_ARCHIVE_FILES = 3000
MAX_ARCHIVE_RATIO = 150
CODE_TEXT_EXT = CODE_EXT | {".html", ".htm", ".css", ".json", ".toml", ".xml", ".ini", ".cfg", ".properties", ".gradle", ".dockerfile", ".makefile"}
SKIP_FILE = re.compile(r"(^|/)(package-lock\\.json|yarn\\.lock|pnpm-lock\\.yaml|poetry\\.lock|Cargo\\.lock)$|\\.min\\.")

TODO_RE = re.compile(r"(?:#|//|/\*|<!--|--|^\s*\*)\s*(TODO|FIXME|HACK|XXX)\b(?:\(([^)]*)\))?[:\s-]*(.+)$")

def _raw(o, r, b, path): return _http_get(f"https://raw.githubusercontent.com/{o}/{r}/{quote(b)}/{quote(path)}")[0]

def todos_markdown(found):
    """[(path, line_no, kind, owner, text)] -> extractor grammar. Owner only if written as TODO(name)."""
    out = []
    for n, (path, ln, kind, owner, text) in enumerate(found[:MAX_TODOS], 1):
        text = _one_line(re.sub(r"(\*/|-->)\s*$", "", text)).replace("(", "[").replace(")", "]")
        out += [f"## {path}:{ln}", f"Action TODO-{n}: {kind} {text}" + (f" (owner: {_one_line(owner)})" if owner and owner.strip() else "")]
    return "\n".join(out)

def ingest_uploaded_archive(payload, archive_name="uploaded.zip"):
    """Safely ingest supported documents and source files from an uploaded ZIP."""
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise SourceError(f"archive exceeds {MAX_ARCHIVE_BYTES // (1024*1024)} MB limit")
    results, skipped = [], 0
    for path, data in _safe_zip_entries(payload):
        ext = Path(path).suffix.lower()
        if ext not in (TEXT_EXT | FILE_EXT | CODE_TEXT_EXT):
            continue
        if not data or len(data) > MAX_DOC_BYTES or b"\x00" in data[:4096]:
            skipped += 1
            continue
        name = f"{Path(archive_name).stem} · {path}"
        try:
            meta = {"archive": archive_name, "path": path, "content_sha256": hashlib.sha256(data).hexdigest()}
            if ext in FILE_EXT:
                dest = DATA / "uploads" / (hashlib.sha256((archive_name + path).encode()).hexdigest() + ext)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                result = ingest_file(dest)
                result["doc"]["filename"] = name
                for chunk in result["chunks"]:
                    chunk["meta"] = {**(chunk.get("meta") or {}), **meta}
            else:
                result = ingest_text(name, data.decode("utf-8", "replace"), "archive_code" if ext in CODE_EXT else "archive", meta=meta)
            results.append(result)
        except (IngestError, OSError, UnicodeError):
            skipped += 1
    if not results:
        raise SourceError("ZIP contains no readable supported documents or source files")
    return results, skipped


MAX_FILE_BYTES = int(os.getenv("KB_MAX_FILE_BYTES", 1_000_000))   # per text/source file
MAX_INDEXED = int(os.getenv("KB_MAX_INDEXED_FILES", 3000))         # bounded + resumable: the rest is listed as EXCLUDED
MAX_RAW_FETCH = int(os.getenv("KB_MAX_RAW_FETCH", 400))            # per-file downloads in the git-tree fallback
LAST_SCANS = []  # filled by github_results, persisted by the router once documents are stored


def _code_doc(name, text, path, S, pushed, meta):
    """Structure-aware code document: one chunk per class/function/module block, each carrying path, line range and symbol."""
    from datetime import datetime, timezone
    from . import repo_scan as RS
    parts = RS.code_chunks(path, text, S)
    if not parts: raise IngestError(f"no extractable text in {name}")
    did = "doc-" + hashlib.sha1((name + text).encode()).hexdigest()[:8]
    doc = {"id": did, "filename": name, "source_type": "github_code", "upload_date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "doc_date": pushed, "n_chunks": len(parts)}
    return {"doc": doc, "chunks": [{"id": f"{did}-c{i:03d}", "doc_id": did, "idx": i, "section": sec, "page": None, "text": t, "meta": {**meta, **cm}} for i, (t, sec, cm) in enumerate(parts)]}


def _tree_entries(owner, repo, ref):
    """Fallback when the archive is unavailable: the complete recursive git tree (sizes only; contents fetched lazily)."""
    t = _gh(f"/repos/{owner}/{repo}/git/trees/{quote(ref)}?recursive=1")
    return [(e["path"], None, e.get("size") or 0) for e in t.get("tree", []) if e.get("type") == "blob"], bool(t.get("truncated"))


def github_results(owner, repo, url, db=None):
    """Recursively inspect the whole repository. Every discovered file gets a manifest row with an explicit status; nothing is skipped silently."""
    from . import repo_scan as RS, repo_intel as RI
    rid = f"{owner}/{repo}"; base = f"github:{rid}"
    info = _gh(f"/repos/{owner}/{repo}"); br = info.get("default_branch") or "main"; pushed = (info.get("pushed_at") or "")[:10] or None
    try: commit = (_gh(f"/repos/{owner}/{repo}/commits/{quote(br)}").get("sha") or "")[:40] or None
    except SourceError: commit = None
    headers = {"Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}"} if os.getenv("GITHUB_TOKEN") else {}
    source, truncated, notes, state = "archive", False, [], {"total": 0, "count": 0, "skipped": []}
    try:
        payload = _http_get_archive(f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{quote(br)}", headers)
        entries = [(p, d, len(d)) for p, d in _safe_zip_entries(payload, _state=state)]
    except SourceError as exc:
        notes.append(f"archive unavailable ({exc}); used the recursive git tree API instead")
        entries, truncated = _tree_entries(owner, repo, commit or br); source, state["skipped"] = "git-tree", []
        if truncated: notes.append("GitHub reported the git tree as truncated; files beyond it cannot be listed")
    known = {e[0] for e in entries}
    for rel, size, status, reason in state["skipped"]:  # entries the archive reader dropped are still accounted for
        if rel not in known: entries.append((rel, ("SKIP", status, reason), size))
    prior = RI.prior_hashes(db, rid) if db is not None else {}
    entries.sort(key=lambda e: (0 if Path(e[0]).name.lower().startswith("readme") else 1, e[0].count("/"), e[0]))
    rows, res, found, reused, raw_left, n_idx = [], [], [], 0, MAX_RAW_FETCH, 0
    for path, data, size in entries:
        kind, ext = RS.file_kind(path)
        row = {"path": path, "ext": ext, "kind": kind, "size": size, "sha256": None, "status": "DISCOVERED", "reason": None, "text_extracted": 0, "indexed": 0, "doc_id": None, "structure": None, "meta": {}}
        rows.append(row)
        if isinstance(data, tuple): row.update(status=data[1], reason=data[2]); continue
        if RS.VENDOR_RE.search(path) or RS.LOCK_RE.search(path): row.update(status="EXCLUDED", reason="vendored/generated/lockfile path"); continue
        textual = kind in ("source", "config", "doc", "document"); limit = MAX_DOC_BYTES if kind == "document" else MAX_FILE_BYTES
        if size > limit: row.update(status="METADATA_ONLY", reason=f"oversized: {size} bytes > {limit}"); continue
        if data is None and textual:
            if raw_left <= 0: truncated = True; row.update(status="EXCLUDED", reason="download budget reached; reconnect to resume"); continue
            try: data = _raw(owner, repo, br, path); raw_left -= 1
            except SourceError as exc: row.update(status="FAILED", reason=f"download failed: {exc}"); continue
        if data is None: row.update(status="METADATA_ONLY", reason="binary: contents not downloaded (name and size only)"); continue
        row["sha256"] = hashlib.sha256(data).hexdigest(); row["size"] = len(data)
        if not textual:
            row.update(status="METADATA_ONLY", reason="binary: only filename, size and header metadata read", meta=RS.binary_metadata(ext, data)); continue
        if not data: row.update(status="EXCLUDED", reason="empty file"); continue
        if kind != "document" and b"\x00" in data[:4096]: row.update(kind="binary", status="METADATA_ONLY", reason="binary content in a text-typed file (unsupported)"); continue
        if n_idx >= MAX_INDEXED: truncated = True; row.update(status="EXCLUDED", reason=f"file limit reached ({MAX_INDEXED}); reconnect to resume"); continue
        text = None
        if kind == "source":
            text = data.decode("utf-8", "replace")
            for ln, line in enumerate(text.splitlines(), 1):
                m = TODO_RE.search(line)
                if m and m.group(3).strip(): found.append((path, ln, m.group(1).upper(), m.group(2), m.group(3)))
        did = RI.doc_id_for(db, rid, path) if db is not None and prior.get(path) == row["sha256"] else None
        if did:  # unchanged since the last scan: reuse the stored document instead of re-indexing
            row.update(status="INDEXED", text_extracted=1, indexed=1, doc_id=did, structure=RI.prior_structure(db, rid, path)); reused += 1; n_idx += 1; continue
        name = f"{base} · {path}"
        meta = {"source_url": f"https://github.com/{owner}/{repo}/blob/{br}/{quote(path)}", "path": path, "repository": rid, "branch": br, "commit": commit, "content_sha256": row["sha256"], "file_kind": kind, "repository_id": rid, "repository_url": url, "file_path": path}
        try:
            if kind == "document":
                dest = DATA / "uploads" / (hashlib.sha256((rid + path).encode()).hexdigest() + ext); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
                r = ingest_file(dest); r["doc"]["filename"] = name; r["doc"]["source_type"] = "github"
                for c in r["chunks"]: c["meta"] = {**(c.get("meta") or {}), **meta}
            else:
                text = RS.notebook_to_text(data) if ext == ".ipynb" else (text if text is not None else data.decode("utf-8", "replace"))
                if kind in ("source", "config"):
                    S = RS.extract_structure(path, text); row["structure"] = S
                    if S.get("parse_error"): row["reason"] = f"structure unavailable: {S['parse_error']}"
                    r = _code_doc(name, text, path, S, pushed, meta)
                else: r = ingest_text(name, text, "github", pushed, meta)
            res.append(r); row.update(status="INDEXED", text_extracted=1, indexed=1, doc_id=r["doc"]["id"]); n_idx += 1
        except Exception as exc:  # one bad file never aborts the scan, and is reported as FAILED
            log.warning("GitHub file %s failed: %s", path, exc); row.update(status="FAILED", reason=f"{type(exc).__name__}: {exc}"[:200])
    if db is not None:  # drop superseded derived documents (they are regenerated below)
        for d in db.documents():
            if d["filename"] in (f"{base} · TODO/FIXME comments in code", f"{base} · issues & pull requests"): db.delete_document(d["id"])
    if found:
        try: res.append(ingest_text(f"{base} · TODO/FIXME comments in code", todos_markdown(found), "github", pushed, {"source_url": url, "path": "code comments", "repository": rid, "repository_id": rid, "repository_url": url, "file_path": "code comments"}))
        except IngestError: pass
    try:
        text, newest = issues_markdown(_gh(f"/repos/{owner}/{repo}/issues?state=all&per_page=40&sort=updated"))
        if text.strip(): res.append(ingest_text(f"{base} · issues & pull requests", text, "github", newest or pushed, {"source_url": url.rstrip("/") + "/issues", "repository": rid, "repository_id": rid, "repository_url": url, "file_path": "issues"}))
    except SourceError as exc: notes.append(f"issues: {exc}")
    stats = RI.manifest_stats(rows)
    notes.insert(0, RI.stats_sentence(rid, br, commit, source, stats, reused))
    if truncated: notes.append("processing bounds reached: unprocessed files are listed as EXCLUDED in the manifest (GET /repo/manifest?status=EXCLUDED); reconnect to resume")
    LAST_SCANS.append({"repo": rid, "branch": br, "commit": commit, "source": source, "truncated": truncated, "stats": stats, "rows": rows, "notes": list(notes)})
    if not res and not reused: raise SourceError("repository contained no supported readable files")
    return res, notes

def html_to_text(html):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        t = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
        return "", "\n".join(x.strip() for x in re.sub(r"<[^>]+>", "\n", t).splitlines() if x.strip())
    s = BeautifulSoup(html, "html.parser")
    for t in s(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]): t.decompose()
    root = s.find("main") or s.find("article") or s.body or s; lines, seen = [], set()
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "th", "blockquote"]):
        t = _one_line(el.get_text(" ", strip=True))
        if t and t not in seen: seen.add(t); lines.append(("# " + t) if el.name.startswith("h") else t)
    return (s.title.get_text(strip=True) if s.title else ""), "\n".join(lines)

def fetch_source(url, db=None):
    """One URL -> (list of {doc, chunks}, notes). Raises SourceError/IngestError for that URL only."""
    url = check_url(url); m = GH_BLOB.match(url)
    if m: url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    elif GH_REPO.match(url): o, r = GH_REPO.match(url).group(1, 2); return github_results(o, r, f"https://github.com/{o}/{r}", db)
    data, ct = _http_get(url); u = urlparse(url); name = Path(u.path).name or u.hostname; ext = Path(name).suffix.lower()
    meta = {"source_url": url}
    if ext in (".pdf", ".docx", ".csv") :
        dest = DATA / "uploads" / re.sub(r"[^\w.-]", "_", name); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
        res = ingest_file(dest)
        for c in res["chunks"]: c["meta"] = {**(c["meta"] or {}), **meta}
        return [res], []
    text = data.decode("utf-8", "replace")
    if "html" in ct.lower() or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, body = html_to_text(text); return [ingest_text(f"web:{u.hostname}{u.path or ''}"[:90] + (f" ({title[:40]})" if title else ""), body, "web", None, meta)], []
    return [ingest_text(f"web:{u.hostname}/{name}"[:90], text, "markdown" if ext == ".md" else "txt", None, meta)], []

def connect(urls, db=None):
    """-> (ingest results, errors[{url,error}], notes). A bad link never stops the others."""
    ok, errors, notes = [], [], []; LAST_SCANS.clear()
    for url in urls:
        try: r, n = fetch_source(url, db) if db is not None else fetch_source(url); ok += r; notes += [f"{url}: {x}" for x in n]
        except (SourceError, IngestError) as e: log.warning("source %s failed: %s", url, e); errors.append({"url": url, "error": str(e)})
    return ok, errors, notes
