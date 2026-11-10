"""Step-1 source connectors: GitHub repos, web pages and direct file links -> the same {doc, chunks} shape as file ingestion.
Safety: http(s) only, private/loopback/link-local addresses blocked (SSRF) incl. on redirects, size + time limits."""
import base64, ipaddress, json, logging, os, re, socket, ssl, urllib.error, urllib.request
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

TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".adoc"}; FILE_EXT = {".csv", ".pdf", ".docx"}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".c", ".cpp", ".h", ".cs", ".php", ".swift", ".kt", ".rs", ".sh", ".yml", ".yaml", ".sql"}
SKIP = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.git|\.venv|venv|__pycache__|target|\.next|coverage)/|(package-lock\.json|yarn\.lock|poetry\.lock)$|\.min\.")
MAX_DOCS, MAX_CODE, MAX_CODE_BYTES, MAX_DOC_BYTES, MAX_TODOS = 60, 40, 200_000, 2_000_000, 30
TODO_RE = re.compile(r"(?:#|//|/\*|<!--|--|^\s*\*)\s*(TODO|FIXME|HACK|XXX)\b(?:\(([^)]*)\))?[:\s-]*(.+)$")

def _raw(o, r, b, path): return _http_get(f"https://raw.githubusercontent.com/{o}/{r}/{quote(b)}/{quote(path)}")[0]

def todos_markdown(found):
    """[(path, line_no, kind, owner, text)] -> extractor grammar. Owner only if written as TODO(name)."""
    out = []
    for n, (path, ln, kind, owner, text) in enumerate(found[:MAX_TODOS], 1):
        text = _one_line(re.sub(r"(\*/|-->)\s*$", "", text)).replace("(", "[").replace(")", "]")
        out += [f"## {path}:{ln}", f"Action TODO-{n}: {kind} {text}" + (f" (owner: {_one_line(owner)})" if owner and owner.strip() else "")]
    return "\n".join(out)

def github_results(owner, repo, url):
    """Whole repo: every doc-type file (md/txt/rst/csv/pdf/docx) in any folder, TODO/FIXME comments from source files, issues & PRs."""
    base, res, notes, skipped = f"github:{owner}/{repo}", [], [], 0
    info = _gh(f"/repos/{owner}/{repo}"); br = info.get("default_branch") or "main"; pushed = (info.get("pushed_at") or "")[:10] or None
    tree = _gh(f"/repos/{owner}/{repo}/git/trees/{quote(br)}?recursive=1")
    if tree.get("truncated"): notes.append("repository is very large; GitHub truncated the file list")
    blobs = [t for t in tree.get("tree", []) if t.get("type") == "blob" and not SKIP.search(t["path"])]
    ext = lambda p: Path(p).suffix.lower()
    docs = sorted([t for t in blobs if ext(t["path"]) in TEXT_EXT | FILE_EXT and t.get("size", 0) <= MAX_DOC_BYTES],
                  key=lambda t: ("readme" not in t["path"].lower(), t["path"].count("/"), t["path"]))[:MAX_DOCS]
    code = sorted([t for t in blobs if ext(t["path"]) in CODE_EXT and t.get("size", 0) <= MAX_CODE_BYTES], key=lambda t: (t["path"].count("/"), t["path"]))[:MAX_CODE]
    def grab(t):
        try: return t, _raw(owner, repo, br, t["path"])
        except SourceError as e: return t, e
    with ThreadPoolExecutor(8) as ex: fetched = list(ex.map(grab, docs + code))
    found, n_docs = [], 0
    for t, data in fetched:
        path, e = t["path"], ext(t["path"])
        if isinstance(data, SourceError): skipped += 1; continue
        link = f"https://github.com/{owner}/{repo}/blob/{br}/{quote(path)}"; meta = {"source_url": link, "path": path}; name = f"{base} · {path}"
        try:
            if e in CODE_EXT:
                for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                    m = TODO_RE.search(line)
                    if m and m.group(3).strip(): found.append((path, i, m.group(1).upper(), m.group(2), m.group(3)))
            elif e in FILE_EXT:
                dest = DATA / "uploads" / re.sub(r"[^\w.-]", "_", f"gh_{owner}_{repo}_{path}"); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
                r = ingest_file(dest); r["doc"]["filename"] = name
                for c in r["chunks"]: c["meta"] = {**(c["meta"] or {}), **meta}
                res.append(r); n_docs += 1
            else: res.append(ingest_text(name, data.decode("utf-8", "replace"), "github", pushed, meta)); n_docs += 1
        except IngestError: skipped += 1
    if found:
        try: res.append(ingest_text(f"{base} · TODO/FIXME comments in code", todos_markdown(found), "github", pushed, {"source_url": url, "path": "code comments"}))
        except IngestError: pass
    try:
        text, newest = issues_markdown(_gh(f"/repos/{owner}/{repo}/issues?state=all&per_page=40&sort=updated"))
        if text.strip(): res.append(ingest_text(f"{base} · issues & pull requests", text, "github", newest or pushed, {"source_url": url.rstrip("/") + "/issues"}))
    except SourceError as e: notes.append(f"issues: {e}")
    notes.append(f"read {n_docs} document files from all folders, scanned {len(code)} code files ({len(found)} TODO/FIXME found), skipped {skipped}")
    if not res: raise SourceError("repository has no readable documents, TODO comments or issues")
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

def fetch_source(url):
    """One URL -> (list of {doc, chunks}, notes). Raises SourceError/IngestError for that URL only."""
    url = check_url(url); m = GH_BLOB.match(url)
    if m: url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    elif GH_REPO.match(url): o, r = GH_REPO.match(url).group(1, 2); return github_results(o, r, f"https://github.com/{o}/{r}")
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

def connect(urls):
    """-> (ingest results, errors[{url,error}], notes). A bad link never stops the others."""
    ok, errors, notes = [], [], []
    for url in urls:
        try: r, n = fetch_source(url); ok += r; notes += [f"{url}: {x}" for x in n]
        except (SourceError, IngestError) as e: log.warning("source %s failed: %s", url, e); errors.append({"url": url, "error": str(e)})
    return ok, errors, notes
