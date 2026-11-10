"""Human-readable renderings of the structured report. JSON stays the source of truth; text is always rendered from it.
Optional LLM narrative is accepted only if every issue ID it cites exists in the report (otherwise the template is used)."""
import json, logging, os, re
from .extraction import llm_enabled
log = logging.getLogger("knowbridge.narrative")
ICON = {"High": "🔴", "Medium": "🟠", "Low": "🟡"}

def debt_markdown(rep):
    s = rep["summary"]; out = [f"# Knowledge Debt Report", "", f"**{s['total']} open issues** — " + ", ".join(f"{n} {k}" for k, n in sorted(s["by_severity"].items(), key=lambda x: ["High", "Medium", "Low"].index(x[0]))) if s["total"] else "**No issues found.**", ""]
    for i in rep["issues"]:
        out += [f"### {ICON[i['severity']]} {i['id']} · {i['title']}", f"*{i['severity']} · {i['category']}*", "", i["description"]]
        for e in i["supporting_evidence"][:2]: out.append(f"> “{e['text'].splitlines()[0][:200]}” — {e['document']}" + (f", {e['timestamp']}" if e.get("timestamp") else ""))
        out += ["", f"**To verify:** {i['recommended_verification']}", ""]
    return "\n".join(out)

def debt_text(rep): return re.sub(r"[#*>]|\*", "", debt_markdown(rep)).replace("  ", " ")

def template_brief(rep):
    s = rep["summary"]
    if not s["total"]: return "No knowledge gaps were detected in the analysed sources."
    top = [i for i in rep["issues"] if i["severity"] == "High"][:3]
    cats = ", ".join(f"{n} × {k.lower()}" for k, n in sorted(s["by_category"].items(), key=lambda x: -x[1])[:4])
    txt = f"The sources leave {s['total']} knowledge gaps ({s['by_severity'].get('High', 0)} high severity). Most common: {cats}."
    if top: txt += " Fix first: " + "; ".join(f"{i['title']} [{i['id']}]" for i in top) + "."
    return txt + " Each issue lists the source passage and what to verify."

def summary_markdown(sm):
    d = sm["knowledge_debt"]
    return "\n".join([f"# Project knowledge summary", f"- {sm['documents']} documents → {sm['chunks']} chunks → {sm['claims']} extracted claims",
        "- Entities: " + ", ".join(f"{n} {k}" for k, n in sm["entities_by_type"].items()),
        "- Verification: " + ", ".join(f"{n} {k}" for k, n in sm["entities_by_verification"].items()),
        f"- Knowledge debt: {d['total']} issues ({', '.join(f'{n} {k}' for k, n in d['by_severity'].items())})",
        f"- Handover readiness: **{sm['readiness']['verdict']}**"])

def readiness(rep):
    h = rep["summary"]["by_severity"].get("High", 0)
    return {"high_open": h, "total_open": rep["summary"]["total"],
            "verdict": "Not ready — resolve High-severity gaps first" if h else ("Ready with caveats — review Medium/Low gaps" if rep["summary"]["total"] else "Ready — no gaps detected")}

def narrate(rep, sink=None):
    """-> (markdown text, 'llm'|'template')."""
    base = template_brief(rep)
    if not llm_enabled() or not rep["issues"]: return base, "template"
    try:
        import anthropic
        facts = [{k: i[k] for k in ("id", "title", "category", "severity", "description", "recommended_verification")} for i in rep["issues"]]
        prompt = ("Write a short plain-English briefing (max 150 words, markdown) of these project knowledge gaps for a manager. "
                  "Use only these facts, cite issue IDs like KD-001 for every claim, add nothing else.\n" + json.dumps(facts))
        msg = anthropic.Anthropic().messages.create(model=os.getenv("KNOWBRIDGE_LLM_MODEL", "claude-sonnet-5-5"), max_tokens=500, messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if sink: sink("narrative", "debt-report", text)
        cited = set(re.findall(r"KD-\d{3}", text))
        if not cited or not cited <= {i["id"] for i in rep["issues"]}: raise ValueError("narrative cites missing/unknown issue IDs")
        return text, "llm"
    except Exception as e: log.warning("LLM narrative rejected (%s); using template", e); return base, "template"
