"""FastAPI app: JSON API + static SPA.   Run:  python -m uvicorn app.main:app --port 8000"""
import asyncio
import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import backtest, blocklist, briefing, clusters, db, rulestore, service
from .llm import rule_draft
from .llm import chat as chat_mod
from .llm import client, judge
from .calibration import load_calibration
from .rules import catalog

WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app):
    n = service.ingest()
    print(f"ingested {n} cases; LLM configured={client.configured()} models={client.models()}")
    if client.configured():
        asyncio.create_task(judge.assess_all())  # background triage of the whole queue
    yield


app = FastAPI(title="Junior AI Investigator", lifespan=lifespan)


class Actor(BaseModel):
    name: str
    role: str


def actor(x_actor: str = Header("investigator"), x_role: str = Header("investigator")):
    if x_role not in ("investigator", "supervisor"):
        raise HTTPException(400, "bad role")
    return Actor(name=x_actor, role=x_role)


def guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- filters
class Filter(BaseModel):
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    levels: Optional[list[str]] = None
    care_types: Optional[list[str]] = None
    states: Optional[list[str]] = None
    statuses: Optional[list[str]] = None
    rule_ids: Optional[list[str]] = None
    rule_mode: str = "any"
    ai_levels: Optional[list[str]] = None
    ai_disagrees: bool = False
    q: Optional[str] = None
    sort: str = "score"
    order: str = "desc"


@app.get("/api/meta")
def meta():
    cs = db.rows("SELECT DISTINCT care_type, state FROM cases")
    return {"care_types": sorted({c["care_type"] for c in cs}), "states": sorted({c["state"] for c in cs}),
            "statuses": service.STATUSES, "rules": catalog(), "llm": {"configured": client.configured(), "models": client.models()},
            "batch": judge.BATCH, "calibration": load_calibration()}


@app.post("/api/cases/search")
def search(f: Filter):
    return service.list_cases(f.model_dump())


@app.get("/api/cases/{case_id}")
def case(case_id: str):
    c = service.get_case(case_id)
    if not c:
        raise HTTPException(404, "unknown case")
    return c


# ---------------------------------------------------------------- AI
@app.post("/api/cases/{case_id}/assess")
async def assess(case_id: str, a: Actor = Depends(actor)):
    if not service.get_case(case_id):
        raise HTTPException(404, "unknown case")
    return await judge.assess_case(case_id, force=True, actor=a.name, role=a.role)


@app.post("/api/assess-all")
async def assess_all(force: bool = False):
    asyncio.create_task(judge.assess_all(force=force))
    return {"started": True}


class Q(BaseModel):
    question: str


@app.post("/api/cases/{case_id}/chat")
async def chat_case(case_id: str, q: Q, a: Actor = Depends(actor)):
    try:
        return await chat_mod.answer(case_id, q.question, a.name)
    except client.LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}")


@app.get("/api/cases/{case_id}/chat")
def chat_history(case_id: str):
    return chat_mod.history(case_id, 50)


@app.post("/api/queue/chat")
async def chat_queue(q: Q, a: Actor = Depends(actor)):
    try:
        return await chat_mod.answer(None, q.question, a.name)
    except client.LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}")


@app.get("/api/queue/chat")
def chat_queue_history():
    return chat_mod.history(None, 50)


# ---------------------------------------------------------------- notes / feedback
class Note(BaseModel):
    text: str


@app.post("/api/cases/{case_id}/notes")
def add_note(case_id: str, n: Note, a: Actor = Depends(actor)):
    return {"id": guard(service.add_note, case_id, n.text, a.name, a.role)}


def _regen():
    """Assessments purged/made stale by a deletion or outcome are regenerated in the background."""
    if client.configured():
        asyncio.create_task(judge.assess_all())


@app.delete("/api/cases/{case_id}/notes/{note_id}")
async def delete_note(case_id: str, note_id: int, a: Actor = Depends(actor)):
    purged = guard(service.delete_note, case_id, note_id, a.name, a.role)
    _regen()
    return purged


class Outcome(BaseModel):
    outcome: str  # FRAUD | LEGITIMATE
    reason: str


@app.post("/api/cases/{case_id}/outcome")
async def mark_outcome(case_id: str, o: Outcome, a: Actor = Depends(actor)):
    res = guard(service.mark_outcome, case_id, o.outcome, o.reason, a.name, a.role)
    _regen()
    return res


@app.delete("/api/cases/{case_id}/outcome")
async def retract_outcome(case_id: str, a: Actor = Depends(actor)):
    res = guard(service.retract_outcome, case_id, a.name, a.role)
    _regen()
    return res


class Feedback(BaseModel):
    assessment_id: int
    indicator_key: str
    indicator_text: str
    decision: str  # ACCEPT | REJECT | CLEAR


@app.post("/api/cases/{case_id}/feedback")
def feedback(case_id: str, f: Feedback, a: Actor = Depends(actor)):
    guard(service.set_feedback, case_id, f.assessment_id, f.indicator_key, f.indicator_text, f.decision, a.name, a.role)
    return {"ok": True}


# ---------------------------------------------------------------- dispositions
class Status(BaseModel):
    status: str
    reason: str = ""


@app.post("/api/cases/{case_id}/status")
def status(case_id: str, s: Status, a: Actor = Depends(actor)):
    guard(service.set_status, case_id, s.status, s.reason, a.name, a.role)
    return {"ok": True}


class BulkPreview(BaseModel):
    filter: Optional[Filter] = None
    case_ids: Optional[list[str]] = None   # explicit selection (ticked rows) instead of a filter
    allow_without_ai: bool = False


@app.post("/api/bulk/preview")
def bulk_preview(b: BulkPreview):
    if b.case_ids is not None:
        want = set(b.case_ids)
        cases = [c for c in service.list_cases({}) if c["case_id"] in want]
    else:
        cases = service.list_cases((b.filter or Filter()).model_dump())
    ok, blocked = service.bulk_evaluate(cases, b.allow_without_ai)
    slim = lambda c: {"case_id": c["case_id"], "score": c["score"], "level": c["level"], "care_type": c["care_type"],
                      "amount": c["amount"], "ai_level": c["ai"] and c["ai"]["ai_level"], "status": c["status"]}
    return {"matched": len(cases), "clearable": [slim(c) for c in ok],
            "blocked": [{**slim(x["case"]), "reasons": x["reasons"]} for x in blocked]}


class BulkClear(BaseModel):
    case_ids: list[str]
    reason: str
    allow_without_ai: bool = False


@app.post("/api/bulk/clear")
def bulk_clear(b: BulkClear, a: Actor = Depends(actor)):
    return guard(service.bulk_clear, b.case_ids, b.reason, a.name, a.role, b.allow_without_ai)


# ---------------------------------------------------------------- escalation
class Escalate(BaseModel):
    reason: str
    summary: str = ""


@app.post("/api/cases/{case_id}/escalate/draft")
async def escalate_draft(case_id: str):
    try:
        return await chat_mod.draft_handoff(case_id)
    except client.LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}")


@app.post("/api/cases/{case_id}/escalate")
def escalate(case_id: str, e: Escalate, a: Actor = Depends(actor)):
    return {"escalation_id": guard(service.escalate, case_id, e.reason, e.summary, a.name, a.role)}


class BulkEscalate(BaseModel):
    case_ids: list[str]
    reason: str


@app.post("/api/bulk/escalate")
def bulk_escalate(b: BulkEscalate, a: Actor = Depends(actor)):
    return {"escalation_ids": [guard(service.escalate, i, b.reason, "", a.name, a.role) for i in b.case_ids]}


@app.get("/api/escalations")
def escalations(status: Optional[str] = None):
    return service.list_escalations(status)


class Decision(BaseModel):
    decision: str
    note: str = ""


@app.post("/api/escalations/{eid}/decide")
def decide(eid: int, d: Decision, a: Actor = Depends(actor)):
    guard(service.decide_escalation, eid, d.decision, d.note, a.name, a.role)
    return {"ok": True}


# ---------------------------------------------------------------- briefing / audit
@app.get("/api/briefing")
def get_briefing():
    s = briefing.stats()
    return {"stats": s, "headlines": briefing.headlines(s)}


@app.post("/api/briefing/narrative")
async def get_narrative(force: bool = False):
    return await briefing.narrative(briefing.stats(), force)


# ---------------------------------------------------------------- blocklist (hard rules -> decline)
def _bl(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ValueError as e:
        raise HTTPException(409 if str(e).startswith("BROAD") else 400, str(e))


class BlBody(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    spec: Optional[dict] = None
    reason: Optional[str] = None
    status: Optional[str] = None
    confirm_broad: bool = False


@app.get("/api/blocklist")
def bl_list():
    st = blocklist.stats()
    return [{**e, **st[e["id"]]} for e in blocklist.list_entries()]


@app.post("/api/blocklist/preview")
def bl_preview(b: BlBody):
    return _bl(blocklist.preview, b.model_dump())


@app.post("/api/blocklist")
def bl_create(b: BlBody, a: Actor = Depends(actor)):
    e = _bl(blocklist.create, b.model_dump(exclude={"confirm_broad"}), a.name, a.role, b.confirm_broad)
    return {"entry": e, "rescore": service.rescore()}


@app.put("/api/blocklist/{bid}")
def bl_update(bid: str, b: BlBody, a: Actor = Depends(actor)):
    e = _bl(blocklist.update, bid, b.model_dump(exclude={"confirm_broad"}, exclude_none=True), a.name, a.role, b.confirm_broad)
    return {"entry": e, "rescore": service.rescore()}


@app.delete("/api/blocklist/{bid}")
def bl_delete(bid: str, a: Actor = Depends(actor)):
    _bl(blocklist.delete, bid, a.name, a.role)
    return {"rescore": service.rescore()}


@app.get("/api/blocklist/{bid}/history")
def bl_history(bid: str):
    return blocklist.history(bid)


@app.get("/api/declined")
def declined():
    return service.declined_cases()


class Override(BaseModel):
    reason: str


@app.post("/api/cases/{case_id}/decline/override")
def decline_override(case_id: str, o: Override, a: Actor = Depends(actor)):
    guard(service.override_decline, case_id, o.reason, a.name, a.role)
    return {"ok": True}


# ---------------------------------------------------------------- rules tab
@app.get("/api/rules")
def rules_list():
    return rulestore.all_rules()


@app.get("/api/rules/fields")
def rules_fields():
    return {"fields": rulestore.field_catalogue(), "domains": rulestore.DOMAIN_CHOICES, "levels": rulestore.LEVELS}


@app.get("/api/rules/leaderboard")
def rules_leaderboard(label_mode: str = "loo", positive_at: str = "HIGH", min_level: Optional[str] = None):
    return guard(backtest.leaderboard, label_mode, positive_at, min_level or None)


class BacktestReq(BaseModel):
    rule_id: Optional[str] = None
    draft: Optional[dict] = None
    label_mode: str = "loo"
    positive_at: str = "HIGH"
    min_level: Optional[str] = None


@app.post("/api/rules/backtest")
def rules_backtest(b: BacktestReq):
    if b.draft and (b.rule_id is None or b.rule_id[:1] == "X"):
        guard(rulestore.validate_definition, {**b.draft, "status": "active"}, b.rule_id, False)  # draft must be valid (name uniqueness not checked)
    return guard(backtest.backtest_rule, b.rule_id, b.draft, b.label_mode, b.positive_at, b.min_level or None)


class RuleBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    level: Optional[str] = None
    domain: Optional[str] = None
    status: Optional[str] = None
    logic: Optional[dict] = None
    # built-in tuning
    enabled: Optional[bool] = None
    elevated: Optional[float] = None
    extreme: Optional[float] = None


def _saved(rule):
    return {"rule": rule, "rescore": service.rescore()}


@app.post("/api/rules")
def rules_create(b: RuleBody, a: Actor = Depends(actor)):
    return _saved(guard(rulestore.create, b.model_dump(exclude_none=True), a.name, a.role))


@app.put("/api/rules/{rid}")
def rules_update(rid: str, b: RuleBody, a: Actor = Depends(actor)):
    if rid[:1] == "X":
        return _saved(guard(rulestore.update, rid, b.model_dump(exclude_none=True), a.name, a.role))
    patch = {k: v for k, v in b.model_dump().items() if k in ("enabled", "level", "elevated", "extreme") and (v is not None or k in b.model_fields_set)}
    return _saved(guard(rulestore.set_override, rid, patch, a.name, a.role))


@app.delete("/api/rules/{rid}")
def rules_delete(rid: str, a: Actor = Depends(actor)):
    if rid[:1] != "X":
        raise HTTPException(400, "built-in rules cannot be deleted; disable them instead")
    guard(rulestore.delete, rid, a.name, a.role)
    return {"rescore": service.rescore()}


@app.post("/api/rules/{rid}/reset")
def rules_reset(rid: str, a: Actor = Depends(actor)):
    guard(rulestore.reset, rid, a.name, a.role)
    return {"rescore": service.rescore()}


@app.get("/api/rules/{rid}/history")
def rules_history(rid: str):
    return rulestore.history(rid)


class DraftReq(BaseModel):
    description: str


@app.post("/api/rules/draft")
async def rules_draft(d: DraftReq):
    try:
        return await rule_draft.draft(d.description)
    except client.LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/cases/{case_id}/runs")
def case_runs(case_id: str):
    runs = db.rows("SELECT * FROM runs WHERE case_id=? ORDER BY id DESC LIMIT 5", (case_id,))
    for r in runs:
        r["spans"] = db.rows("SELECT agent,kind,name,detail,ms,tokens FROM spans WHERE run_id=? ORDER BY id", (r["id"],))
    return runs


@app.get("/api/runs/summary")
def runs_summary():
    from .evals import metrics
    return {"crew": metrics.cost_by_lane("crew"), "agreement": metrics.agreement(), "mode": judge._mode()}


@app.get("/api/clusters")
def get_clusters():
    return clusters.cluster_context()


@app.get("/api/audit")
def audit(limit: int = 500, case_id: Optional[str] = None):
    return service.audit_log(limit, case_id)


@app.get("/api/audit.csv")
def audit_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "actor", "role", "action", "case_id", "detail"])
    for r in service.audit_log(100000):
        w.writerow([r["ts"], r["actor"], r["role"], r["action"], r["case_id"], r["detail"]])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=audit_log.csv"})


def _asset_version():
    return str(int(max(f.stat().st_mtime for f in WEB.iterdir() if f.is_file())))


@app.get("/")
def index():
    """Serve index.html with asset URLs versioned by file mtime, so a changed app.js/styles.css is never served from browser cache."""
    html = (WEB / "index.html").read_text().replace("/static/app.js", f"/static/app.js?v={_asset_version()}").replace("/static/styles.css", f"/static/styles.css?v={_asset_version()}")
    return HTMLResponse(html)


@app.middleware("http")
async def no_stale_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"   # always revalidate (ETag makes this cheap)
    return resp


app.mount("/static", StaticFiles(directory=WEB), name="static")
