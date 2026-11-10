"""B. NLP knowledge extraction. Claim = {kind, fields, evidence (verbatim source lines), certainty}.
RuleExtractor is the offline fallback; LLMExtractor is an optional, provider-pluggable path whose output is
validated so every claim's evidence must be copied verbatim from the chunk (no invented facts)."""
import json, logging, os, re
log = logging.getLogger("knowbridge.extract")
ID = r"[A-Z]{1,4}-\d+"; DATE = r"\d{4}-\d{2}-\d{2}"
STATUS_MAP = {"completed": "done", "complete": "done", "done": "done", "in progress": "in_progress", "ongoing": "in_progress",
              "open": "in_progress", "blocked": "blocked", "not started": "not_started", "pending": "not_started", "todo": "not_started"}
HEDGE = re.compile(r"\b(probably|likely|might|seems?|appears?|should|perhaps|possibly|unclear)\b", re.I)
SEP = r"\s*[:\-–]\s*"
KINDS = {"person", "task", "decision", "dependency", "risk", "milestone", "question", "resource", "status"}
def clean(s): return re.sub(r"\s+", " ", s or "").strip(" .;,-–")
def norm_status(s): return STATUS_MAP.get((s or "").strip().lower())

class RuleExtractor:
    name = "rules"
    def extract(self, chunk):
        if (chunk.get("meta") or {}).get("row"): return self._row(chunk)
        claims, last = [], None
        def add(kind, ev, cert="explicit", **f):
            c = {"kind": kind, "fields": {k: v for k, v in f.items() if v not in (None, "")}, "evidence": ev, "certainty": cert}
            claims.append(c); return c
        for raw in chunk["text"].splitlines():
            ln = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", raw).strip()
            if not ln or re.match(r"^(date|last updated|updated)\s*:", ln, re.I): continue
            cert = "hedged" if HEDGE.search(ln) else "explicit"
            m = re.match(r"^attendees?\s*:\s*(.+)$", ln, re.I)
            if m:
                for part in re.split(r",\s*", m.group(1)):
                    pm = re.match(r"^([A-Z][\w.'-]+(?: [A-Z][\w.'-]+)+)\s*(?:\(([^)]+)\))?$", part.strip())
                    if pm: add("person", ln, name=pm.group(1), role=pm.group(2))
                continue
            m = re.match(r"^(?:action(?:\s+item)?|task)\s*(%s)?%s(.+)$" % (ID, SEP), ln, re.I)
            if m:
                ref, body = m.group(1), m.group(2)
                asof = re.search(r"\(updated:\s*(%s)\)" % DATE, body); body = re.sub(r"\s*\(updated:[^)]*\)", "", body)
                own = re.search(r"\(owner\s*:\s*([^)]+)\)|\bassigned to\s+([A-Z]\w+(?: [A-Z]\w+)*)", body)
                due = re.search(r"\bdue(?: by)?\s*(%s)" % DATE, body)
                title = clean(re.sub(r"\(owner\s*:[^)]*\)|\bassigned to\s+[A-Z]\w+(?: [A-Z]\w+)*|\bdue(?: by)?\s*%s" % DATE, "", body))
                add("task", ln, cert, ref=ref, title=title, owner=clean(own.group(1) or own.group(2)) if own else None, due=due.group(1) if due else None, as_of=asof.group(1) if asof else None)
                continue
            m = re.match(r"^([A-Z][a-z]+(?: [A-Z][a-z]+)?) will (.+?)(?:\s+by\s+(%s))?\.?$" % DATE, ln)
            if m: add("task", ln, cert, title=clean(m.group(2)), owner=m.group(1), due=m.group(3)); continue
            m = re.match(r"^decision%s(.+)$" % SEP, ln, re.I) or re.match(r"^(?:we|the team)\s+(?:have\s+)?(?:decided|agreed)\s+to\s+(.+)$", ln, re.I)
            if m: last = self._decision(add, m.group(1), ln, cert); continue
            m = re.match(r"^(?:rationale|reason)%s(.+)$" % SEP, ln, re.I)
            if m and last and last["kind"] == "decision":
                last["fields"]["rationale"] = clean(m.group(1)); last["evidence"] += "\n" + ln; continue
            m = re.match(r"^risk%s(.+)$" % SEP, ln, re.I)
            if m:
                body = m.group(1); mit = re.search(r"\bmitigation%s(.+)$" % SEP, body, re.I)
                last = add("risk", ln, "explicit", text=clean(body[:mit.start()] if mit else body), mitigation=clean(mit.group(1)) if mit else None); continue
            m = re.match(r"^mitigation%s(.+)$" % SEP, ln, re.I)
            if m and last and last["kind"] == "risk":
                last["fields"]["mitigation"] = clean(m.group(1)); last["evidence"] += "\n" + ln; continue
            m = re.match(r"^blocker%s(.+)$" % SEP, ln, re.I)
            if m: add("risk", ln, cert, text=clean(m.group(1)), blocker=True, blocks=sorted(set(re.findall(ID, m.group(1))))); continue
            m = re.match(r"^milestone%s(.+?)\s*(?:\((?:due\s*)?(%s)\))?$" % (SEP, DATE), ln, re.I)
            if m: add("milestone", ln, cert, name=clean(m.group(1)), date=m.group(2)); continue
            m = re.match(r"^(?:resource|tool|repo|dataset)%s(.+)$" % SEP, ln, re.I)
            if m: add("resource", ln, cert, name=clean(m.group(1))); continue
            m = re.match(r"^(?:open question|question|unresolved)%s(.+)$" % SEP, ln, re.I)
            if m or ln.endswith("?"): add("question", ln, cert, text=clean(m.group(1) if m else ln)); continue
            for a, rel, b in re.findall(r"\b(%s)\s+(depends on|is blocked by|requires)\s+(%s)" % (ID, ID), ln, re.I):
                add("dependency", ln, cert, task=a.upper(), depends_on=b.upper(), via="blocked by" if "blocked" in rel.lower() else "depends on")
            sm = re.search(r"\b(%s)\b[^.\n]*?\b(completed|complete|done|in progress|ongoing|blocked|not started|pending)\b" % ID, ln, re.I)
            if sm:
                ao = re.search(r"\(updated:\s*(%s)\)" % DATE, ln)
                add("status", ln, cert, ref=sm.group(1).upper(), status=norm_status(sm.group(2)), as_of=ao.group(1) if ao else None)
            else: self._prose(add, ln, cert)
        return claims

    def _prose(self, add, ln, cert):
        """Free-form phrasing (only reached when no structured pattern matched). Evidence is always the verbatim line."""
        N = r"([A-Z][\w'-]+(?: [A-Z][\w'-]+)?)"
        m = re.match(r"^%s (?:is responsible for|is handling|is leading|is in charge of|owns|has taken|is taking) (.+?)\.?$" % N, ln)
        if m: return add("task", ln, cert, title=clean(m.group(2)), owner=m.group(1))
        m = re.match(r"^(.+?) (?:is|are) (?:owned by|assigned to|handled by|led by) %s\.?$" % N, ln)
        if m: return add("task", ln, cert, title=clean(m.group(1)), owner=m.group(2))
        m = re.match(r"^(?:it was decided(?: that| to)?|(?:we|the team)(?:'re| are| will be)? going with|(?:we|the team) (?:chose|selected|picked|went with))\s+(.+)$", ln, re.I)
        if m: return self._decision(add, m.group(1), ln, cert)
        m = re.match(r"^(.+?)\s+(?:is|are) (?:blocked by|waiting on|waiting for|stuck on)\s+(.+?)\.?$", ln, re.I)
        if m: return add("risk", ln, cert, text=clean(ln), blocker=True, blocks=sorted(set(re.findall(ID, m.group(1)))))
        m = re.match(r"^(?:we )?(?:still )?(?:need|have) to (?:decide|figure out|confirm|clarify|determine|find out)\s+(.+?)\.?$", ln, re.I)
        if m: return add("question", ln, cert, text=clean(m.group(1)))
        if re.search(r"\b(?:not sure|unsure|unknown|TBD|to be decided|undecided)\b", ln, re.I): return add("question", ln, "hedged", text=clean(ln))

    def _decision(self, add, body, ev, cert):
        by = re.search(r"\(decided by ([^)]+)\)", body); b2 = re.sub(r"\(decided by [^)]+\)", "", body)
        sup = re.search(r"\b(?:instead of|rather than|replaces|replacing|supersedes)\s+(.+?)(?:[.;]|$)", b2, re.I)
        rat = re.search(r"\b(?:because|since|due to|so that|in order to)\b\s*(.+)$", b2, re.I)
        text = clean(b2[:rat.start()] if rat else b2)
        text = re.sub(r"^(?:we|the team)\s+(?:have\s+)?(?:decided|agreed)\s+to\s+", "", text, flags=re.I)
        return add("decision", ev, cert, text=text[:1].upper() + text[1:], rationale=clean(rat.group(1)) if rat else None,
                   decided_by=clean(by.group(1)) if by else None, supersedes=clean(sup.group(1)) if sup else None)

    def _row(self, chunk):
        r = chunk["meta"]["row"]; ev = chunk["text"]
        g = lambda *ks: next((clean(r[k]) for k in ks if r.get(k, "").strip()), None)
        ref, title = g("task_id", "id", "ref"), g("task", "title", "name")
        if not (ref or title): return []
        mk = lambda kind, **f: {"kind": kind, "fields": {k: v for k, v in f.items() if v}, "evidence": ev, "certainty": "explicit"}
        asof = g("updated", "last_updated")   # row-level timestamp drives staleness
        out = [mk("task", ref=ref, title=title, owner=g("owner", "assignee"), due=g("due", "due_date", "deadline"), as_of=asof)]
        st = norm_status(g("status"))
        if st: out.append(mk("status", ref=ref, title=title, status=st, as_of=asof))
        for d in re.findall(ID, g("depends_on", "dependency") or ""):
            if ref: out.append(mk("dependency", task=ref, depends_on=d, via="depends on", as_of=asof))
        return out

SYSTEM = ("Extract project knowledge from the text as a JSON array. Each item: {\"kind\": one of person|task|decision|dependency|risk|"
          "milestone|question|resource|status, \"fields\": {...}, \"evidence\": verbatim line(s) copied from the text, \"certainty\": "
          "\"explicit\" or \"hedged\"}. Never invent facts; omit anything not stated. Output JSON only.")
class LLMExtractor:
    name = "llm"
    def __init__(self, sink=None):
        import anthropic
        self.sink = sink; self.client, self.model = anthropic.Anthropic(), os.getenv("KNOWBRIDGE_LLM_MODEL", "claude-sonnet-5-5")
    def extract(self, chunk):
        for attempt in (1, 2):   # one retry on malformed JSON
            msg = self.client.messages.create(model=self.model, max_tokens=2000, system=SYSTEM, messages=[{"role": "user", "content": chunk["text"]}])
            raw = "".join(b.text for b in msg.content if b.type == "text")
            if self.sink: self.sink("extract", chunk["id"], raw)   # raw LLM JSON stays in the backend (SQLite)
            try: data = json.loads(re.search(r"\[.*\]", raw, re.S).group(0)); break
            except Exception:
                if attempt == 2: raise
        return [{"kind": c["kind"], "fields": c.get("fields", {}), "evidence": c["evidence"], "certainty": c.get("certainty", "explicit")}
                for c in data if c.get("kind") in KINDS and c.get("evidence")
                and all(l.strip() in chunk["text"] for l in c["evidence"].splitlines() if l.strip())]   # reject non-verbatim evidence

class FallbackExtractor:
    name = "llm+rules-fallback"
    def __init__(self, llm): self.llm, self.rules = llm, RuleExtractor()
    def extract(self, chunk):
        try: return self.llm.extract(chunk)
        except Exception as e: log.warning("LLM extraction failed (%s); using rules", e); return self.rules.extract(chunk)

def llm_enabled(): return os.getenv("KNOWBRIDGE_LLM_PROVIDER", "").lower() == "anthropic" and bool(os.getenv("ANTHROPIC_API_KEY"))

def get_extractor(prefer=None, sink=None):
    if prefer != "rules" and llm_enabled():
        try: return FallbackExtractor(LLMExtractor(sink))
        except Exception as e: log.warning("LLM unavailable (%s); using rules", e)
    return RuleExtractor()
