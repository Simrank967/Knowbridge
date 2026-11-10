"""SQLite storage: documents, chunks, extracted claims (with provenance)."""
import json, sqlite3
from pathlib import Path
from .config import DB_PATH
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, filename TEXT, source_type TEXT, upload_date TEXT, doc_date TEXT, n_chunks INTEGER);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, doc_id TEXT, idx INTEGER, section TEXT, page INTEGER, text TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS claims(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, chunk_id TEXT, kind TEXT, fields TEXT, evidence TEXT, certainty TEXT, extractor TEXT, timestamp TEXT);
CREATE TABLE IF NOT EXISTS llm_raw_outputs(id INTEGER PRIMARY KEY AUTOINCREMENT, purpose TEXT, ref TEXT, raw TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
"""
class DB:
    def __init__(self, path=None):
        self.path = str(path or DB_PATH); Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(self.path, check_same_thread=False); self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
    def add_document(self, res):
        d, ch = res["doc"], res["chunks"]
        self.c.execute("DELETE FROM chunks WHERE doc_id=?", (d["id"],)); self.c.execute("DELETE FROM claims WHERE doc_id=?", (d["id"],))
        self.c.execute("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?)", (d["id"], d["filename"], d["source_type"], d["upload_date"], d["doc_date"], d["n_chunks"]))
        self.c.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)", [(c["id"], c["doc_id"], c["idx"], c["section"], c["page"], c["text"], json.dumps(c["meta"])) for c in ch])
        self.c.commit()
    def documents(self): return [dict(r) for r in self.c.execute("SELECT * FROM documents ORDER BY filename")]
    def chunks(self):
        return [{**dict(r), "meta": json.loads(r["meta"])} for r in self.c.execute("SELECT * FROM chunks ORDER BY doc_id, idx")]
    def replace_claims(self, claims):
        self.c.execute("DELETE FROM claims")
        self.c.executemany("INSERT INTO claims(doc_id,chunk_id,kind,fields,evidence,certainty,extractor,timestamp) VALUES(?,?,?,?,?,?,?,?)",
            [(c["doc_id"], c["chunk_id"], c["kind"], json.dumps(c["fields"]), c["evidence"], c["certainty"], c["extractor"], c["timestamp"]) for c in claims])
        self.c.commit()
    def claims(self):
        return [{**dict(r), "fields": json.loads(r["fields"])} for r in self.c.execute("SELECT * FROM claims ORDER BY id")]

    def log_llm(self, purpose, ref, raw):
        self.c.execute("INSERT INTO llm_raw_outputs(purpose,ref,raw) VALUES(?,?,?)", (purpose, ref, raw)); self.c.commit()
    def llm_outputs(self, limit=50):
        return [dict(r) for r in self.c.execute("SELECT * FROM llm_raw_outputs ORDER BY id DESC LIMIT ?", (limit,))]

    def reset(self):
        """Remove every document, chunk and claim. Returns the number of documents removed."""
        n = self.c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        for t in ("claims", "chunks", "documents", "llm_raw_outputs"): self.c.execute(f"DELETE FROM {t}")
        self.c.commit(); return n
    def counts(self):
        q = lambda t: self.c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return {"documents": q("documents"), "chunks": q("chunks"), "claims": q("claims")}
    def close(self):
        try: self.c.close()
        except Exception: pass

# Chat and generated-report persistence (added compatibly to existing databases).
CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, project_id TEXT, title TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, content TEXT, metadata TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, conversation_id TEXT, title TEXT, format TEXT, path TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP, metadata TEXT);
"""
# Apply the additive schema for both new and existing DBs.
_original_db_init = DB.__init__
def _db_init_with_chat(self, path=None):
    _original_db_init(self, path)
    self.c.executescript(CHAT_SCHEMA)
DB.__init__ = _db_init_with_chat

def _create_conversation(self, cid, project_id=None, title="New chat"):
    self.c.execute("INSERT OR IGNORE INTO conversations(id,project_id,title) VALUES(?,?,?)", (cid, project_id, title)); self.c.commit()
    return dict(self.c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone())
def _save_message(self, cid, role, content, metadata=None):
    self.c.execute("INSERT INTO messages(conversation_id,role,content,metadata) VALUES(?,?,?,?)", (cid, role, content, json.dumps(metadata or {})))
    self.c.execute("UPDATE conversations SET updated=CURRENT_TIMESTAMP WHERE id=?", (cid,)); self.c.commit()
def _get_conversation(self, cid):
    c=self.c.execute("SELECT * FROM conversations WHERE id=?",(cid,)).fetchone()
    if not c: return None
    return {**dict(c), "messages":[{**dict(r), "metadata":json.loads(r["metadata"] or "{}")} for r in self.c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id",(cid,))]}
def _list_conversations(self): return [dict(r) for r in self.c.execute("SELECT * FROM conversations ORDER BY updated DESC")]
def _delete_conversation(self,cid):
    self.c.execute("DELETE FROM messages WHERE conversation_id=?",(cid,)); self.c.execute("DELETE FROM conversations WHERE id=?",(cid,)); self.c.commit()
def _save_report(self, rid, cid, title, fmt, path, metadata=None):
    self.c.execute("INSERT OR REPLACE INTO reports(id,conversation_id,title,format,path,metadata) VALUES(?,?,?,?,?,?)",(rid,cid,title,fmt,path,json.dumps(metadata or {}))); self.c.commit()
def _get_report(self,rid):
    r=self.c.execute("SELECT * FROM reports WHERE id=?",(rid,)).fetchone()
    return {**dict(r),"metadata":json.loads(r["metadata"] or "{}")} if r else None
DB.create_conversation=_create_conversation
DB.save_message=_save_message
DB.get_conversation=_get_conversation
DB.list_conversations=_list_conversations
DB.delete_conversation=_delete_conversation
DB.save_report=_save_report
DB.get_report=_get_report
