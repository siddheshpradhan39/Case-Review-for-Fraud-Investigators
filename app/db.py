"""SQLite persistence. One connection per call (cheap for SQLite, thread-safe with FastAPI's threadpool)."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# DATA_DIR lets a host mount a persistent volume; defaults to the repo data/ folder
DB_PATH = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data") / "cases.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY, claim_number TEXT, claim_date TEXT, care_type TEXT, state TEXT,
  amount REAL, score REAL, rule_score REAL, level TEXT, anomaly REAL,
  status TEXT DEFAULT 'NEW', assigned_to TEXT DEFAULT 'investigator',
  data TEXT, rules TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, created_at TEXT, input_hash TEXT,
  status TEXT, model TEXT, rule_score REAL, rule_level TEXT, ai_score REAL, ai_level TEXT,
  verdict TEXT, action TEXT, result TEXT, warnings TEXT, trace TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_assess_case ON assessments(case_id, id);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, author TEXT, role TEXT, text TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, role TEXT, action TEXT, case_id TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS chat (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, role TEXT, content TEXT, trace TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, assessment_id INTEGER, indicator_key TEXT,
  indicator_text TEXT, decision TEXT, actor TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS escalations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, from_user TEXT, reason TEXT, summary TEXT,
  status TEXT DEFAULT 'PENDING', decided_by TEXT, decision_note TEXT, created_at TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS blocklist (
  id TEXT PRIMARY KEY, name TEXT UNIQUE, kind TEXT, spec TEXT, reason TEXT, status TEXT,
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT, version INTEGER
);
CREATE TABLE IF NOT EXISTS declines (
  case_id TEXT PRIMARY KEY, entry_ids TEXT, details TEXT, prev_status TEXT, enforced INTEGER, declined_at TEXT,
  overridden INTEGER DEFAULT 0, covered TEXT, override_by TEXT, override_reason TEXT, override_at TEXT
);
CREATE TABLE IF NOT EXISTS blocklist_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, entry_id TEXT, event TEXT, ts TEXT, actor TEXT
);
CREATE TABLE IF NOT EXISTS custom_rules (
  id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT, domain TEXT, level TEXT, status TEXT, logic TEXT,
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT, version INTEGER
);
CREATE TABLE IF NOT EXISTS rule_overrides (
  rule_id TEXT PRIMARY KEY, enabled INTEGER, level TEXT, elevated REAL, extreme REAL, updated_by TEXT, updated_at TEXT, version INTEGER
);
CREATE TABLE IF NOT EXISTS rule_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT, version INTEGER, definition TEXT, changed_by TEXT, changed_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT UNIQUE, outcome TEXT, reason TEXT, marked_by TEXT, marked_at TEXT,
  prev_status TEXT, rule_level TEXT, rule_score REAL, ai_level TEXT, ai_score REAL, ai_action TEXT, missed INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, created_at TEXT, mode TEXT, lane TEXT, calls INTEGER,
  tokens_in INTEGER, tokens_out INTEGER, est_cost REAL, latency_ms INTEGER, stop_reason TEXT, contested INTEGER,
  agents TEXT, blackboard TEXT, batch_size INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_runs_case ON runs(case_id, id);
CREATE TABLE IF NOT EXISTS spans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, case_id TEXT, agent TEXT, kind TEXT, name TEXT,
  detail TEXT, ms INTEGER, tokens INTEGER
);
CREATE INDEX IF NOT EXISTS ix_spans_run ON spans(run_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def run(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def audit(actor, role, action, case_id=None, detail=None):
    run("INSERT INTO audit(ts,actor,role,action,case_id,detail) VALUES(?,?,?,?,?,?)",
        (now(), actor, role, action, case_id, json.dumps(detail or {}, default=str)))
