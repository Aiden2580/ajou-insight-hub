"""FastAPI 앱: JSON API + 정적 웹 UI(PC/모바일 반응형)."""
from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, grouping, insight, interest, scheduler, tagger
from .crawler.base import available_parsers
from .crawler.runner import load_sources
from .db import get_conn, init_db, row_to_notice
from .recommend import recommend
from .taxonomy import TAXONOMY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Ajou Notice Hub", version="0.1.0")
STATIC = config.BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_uid_re = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def uid(x_user_id: str | None) -> str:
    if not x_user_id or not _uid_re.match(x_user_id):
        raise HTTPException(400, "X-User-Id 헤더가 필요합니다")
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (x_user_id,))
    return x_user_id


@app.on_event("startup")
def _startup():
    init_db()
    logging.getLogger("startup").info("backfill: %s", tagger.backfill())
    if config.ENABLE_SCHEDULER:
        scheduler.start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


# ---------- 공지 / 활동 ----------
@app.get("/api/notices")
def list_notices(
    q: str = "", tag: str = "", source: str = "", sort: str = "latest",
    page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100), active_only: bool = False,
    include_admin: bool = False, x_user_id: str | None = Header(None),
):
    where, args = [], []
    if not include_admin:  # 단순 행정 안내는 체크박스를 켰을 때만
        where.append("(kind IS NULL OR kind <> 'admin')")
    if q:
        where.append("(title LIKE ? OR content LIKE ? OR keywords LIKE ?)")
        args += [f"%{q}%"] * 3
    if tag:
        where.append("tags LIKE ?")
        args.append(f'%"{tag}"%')
    if source:
        where.append("source_id = ?")
        args.append(source)
    if active_only:
        where.append("(deadline IS NULL OR deadline >= date('now','+9 hours'))")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    # 같은 공지 묶음(여러 학과 중복·재공지)은 가장 최근 글 하나로 보여 준다.
    # 마감이 확정적으로 지난 활동은 어떤 정렬이든 맨 뒤로 (expired=1).
    order = {
        "latest": "posted_at DESC, id DESC",
        "deadline": "CASE WHEN g_deadline IS NULL THEN 1 ELSE 0 END, g_deadline ASC, posted_at DESC",
        "tag": "tags ASC, posted_at DESC",
    }.get(sort, "posted_at DESC, id DESC")
    grouped = f"""
        WITH f AS (SELECT n.*, COALESCE(n.grp, n.id) AS g FROM notices n {w}),
        r AS (SELECT f.*,
                     ROW_NUMBER() OVER (PARTITION BY g ORDER BY COALESCE(posted_at,'') DESC, id DESC) AS rn,
                     COALESCE(deadline, MAX(deadline) OVER (PARTITION BY g)) AS g_deadline
              FROM f)
        SELECT r.*, CASE WHEN g_deadline < date('now','+9 hours') THEN 1 ELSE 0 END AS expired
        FROM r WHERE rn = 1"""
    with get_conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM ({grouped})", args).fetchone()[0]
        rows = c.execute(f"SELECT * FROM ({grouped}) ORDER BY expired ASC, {order} LIMIT ? OFFSET ?",
                         args + [size, (page - 1) * size]).fetchall()
        booked = set()
        if x_user_id and _uid_re.match(x_user_id):
            booked = {r[0] for r in c.execute("SELECT notice_id FROM bookmarks WHERE user_id=?", (x_user_id,))}
        items = []
        for r in rows:
            it = row_to_notice(r)
            it["deadline"] = it.pop("g_deadline")
            it["expired"] = bool(it["expired"])
            for k in ("g", "rn"):
                it.pop(k, None)
            items.append(it)
        attach_groups(c, items)
    for it in items:
        it["bookmarked"] = it["id"] in booked
    return {"total": total, "page": page, "size": size, "items": items}


def attach_groups(c, items: list[dict]) -> None:
    """묶음이 2건 이상이면 펼침 정보(출처별 글, 회차)를 붙인다."""
    gids = [it.get("grp") or it["id"] for it in items]
    members = grouping.group_members(c, list(set(gids)))
    for it, g in zip(items, gids):
        ms = members.get(g, [])
        if len(ms) > 1:
            it.update(grouping.describe(ms))


@app.get("/api/tags")
def tags(include_admin: bool = False):
    cnt = Counter()
    w = "" if include_admin else "WHERE kind IS NULL OR kind <> 'admin'"
    with get_conn() as c:  # 묶음 단위로 센다
        for (t,) in c.execute(f"SELECT tags FROM notices {w} GROUP BY COALESCE(grp, id)"):
            cnt.update(json.loads(t or "[]"))
        admin_n = c.execute("SELECT COUNT(DISTINCT COALESCE(grp, id)) FROM notices WHERE kind='admin'").fetchone()[0]
    return {"tags": [{"tag": k, "count": cnt.get(k, 0)} for k in TAXONOMY if cnt.get(k, 0)], "admin_count": admin_n}


@app.get("/api/notices/{nid}")
def notice_detail(nid: int):
    with get_conn() as c:
        r = c.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone()
    if not r:
        raise HTTPException(404)
    d = row_to_notice(r)
    d["content"] = (r["content"] or "")[:3000]
    return d


def _is_admin(nid: int) -> bool:
    with get_conn() as c:
        r = c.execute("SELECT kind FROM notices WHERE id=?", (nid,)).fetchone()
    return bool(r and r[0] == "admin")


@app.post("/api/notices/{nid}/click")
def notice_click(nid: int, x_user_id: str | None = Header(None)):
    u = uid(x_user_id)
    if not _is_admin(nid):  # 행정 안내 열람은 관심사로 치지 않는다
        interest.add_signals(u, "click", [str(nid)])
    return {"ok": True}


@app.post("/api/notices/{nid}/bookmark")
def notice_bookmark(nid: int, x_user_id: str | None = Header(None)):
    u = uid(x_user_id)
    with get_conn() as c:
        exists = c.execute("SELECT 1 FROM bookmarks WHERE user_id=? AND notice_id=?", (u, nid)).fetchone()
        if exists:
            c.execute("DELETE FROM bookmarks WHERE user_id=? AND notice_id=?", (u, nid))
    if exists:
        return {"bookmarked": False}
    with get_conn() as c:
        c.execute("INSERT INTO bookmarks(user_id,notice_id) VALUES(?,?)", (u, nid))
    if not _is_admin(nid):
        interest.add_signals(u, "bookmark", [str(nid)])
    return {"bookmarked": True}


# ---------- 관심사 그래프 ----------
@app.get("/api/graph")
def graph(x_user_id: str | None = Header(None)):
    return interest.graph_payload(uid(x_user_id))


@app.post("/api/signals")
def add_signals(payload: dict = Body(...), x_user_id: str | None = Header(None)):
    u = uid(x_user_id)
    kind = payload.get("kind")
    if kind not in ("history", "folder", "keyword"):
        raise HTTPException(400, "kind는 history | folder | keyword")
    n = interest.add_signals(u, kind, list(payload.get("items", [])))
    return {"added": n, **interest.graph_payload(u)}


def _parse_history_file(raw: bytes, filename: str) -> list[str]:
    text = raw.decode("utf-8", errors="ignore")
    if filename.endswith(".json"):
        data = json.loads(text)
        arr = data.get("Browser History", data) if isinstance(data, dict) else data
        out = []
        for e in arr if isinstance(arr, list) else []:
            if isinstance(e, dict):
                out.append(f"{e.get('title','')}\t{e.get('url','')}" if e.get("title") else e.get("url", ""))
        return out
    return [l for l in text.splitlines() if l.strip()]


@app.post("/api/signals/upload")
async def upload_signals(kind: str = Form("history"), file: UploadFile = File(...),
                         x_user_id: str | None = Header(None)):
    u = uid(x_user_id)
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "20MB 이하 파일만")
    try:
        items = _parse_history_file(raw, file.filename or "")
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON 파싱 실패")
    n = interest.add_signals(u, kind, items)
    return {"added": n}


@app.get("/api/signals/summary")
def signals_summary(x_user_id: str | None = Header(None)):
    u = uid(x_user_id)
    with get_conn() as c:
        rows = c.execute("SELECT kind, COUNT(*) n FROM signals WHERE user_id=? GROUP BY kind", (u,)).fetchall()
    return {r["kind"]: r["n"] for r in rows}


@app.delete("/api/signals")
def reset_signals(x_user_id: str | None = Header(None)):
    u = uid(x_user_id)
    with get_conn() as c:
        for t in ("signals", "interest_nodes", "interest_edges", "insights", "bookmarks"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (u,))
    return {"ok": True}


# ---------- 추천 / 인사이트 ----------
@app.get("/api/recommendations")
def recommendations(limit: int = 20, x_user_id: str | None = Header(None)):
    recs = recommend(uid(x_user_id), limit=limit)
    with get_conn() as c:
        attach_groups(c, recs)
    return recs


@app.get("/api/insight")
def get_insight(date: str | None = None, x_user_id: str | None = Header(None)):
    u = uid(x_user_id)
    return {"insight": insight.get(u, date), "history": insight.history(u)}


@app.post("/api/insight/generate")
def gen_insight(force: bool = False, x_user_id: str | None = Header(None)):
    """오늘 인사이트가 이미 있으면 그대로 돌려준다(토큰 0). force=true일 때만 새로 생성."""
    return insight.generate(uid(x_user_id), force=force)


@app.post("/api/insight/deep")
def deep_insight(date: str | None = None, force: bool = False, x_user_id: str | None = Header(None)):
    """'더 알아보기': 칼럼 심층 글 + 논문·자료. 이미 만든 것이 있으면 그대로(토큰 0)."""
    ins = insight.deep_dive(uid(x_user_id), date, force=force)
    if not ins:
        raise HTTPException(404, "칼럼이 없습니다")
    return ins


# ---------- 운영 ----------
@app.get("/api/sources")
def sources():
    with get_conn() as c:
        last = {r["source_id"]: dict(r) for r in c.execute(
            "SELECT * FROM crawl_log WHERE id IN (SELECT MAX(id) FROM crawl_log GROUP BY source_id)")}
        counts = {r[0]: r[1] for r in c.execute("SELECT source_id, COUNT(*) FROM notices GROUP BY source_id")}
    return {
        "parsers": available_parsers(),
        "sources": [{**s, "last": last.get(s["id"]), "count": counts.get(s["id"], 0)} for s in load_sources()],
    }


@app.post("/api/crawl")
def crawl_now():
    """공지 새로고침 (사용자가 누를 때만 수집)."""
    return {"started": scheduler.start_background_refresh(), **scheduler.status()}


@app.get("/api/status")
def status():
    with get_conn() as c:
        usage = [dict(r) for r in c.execute(
            "SELECT purpose, model, COUNT(*) calls, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens "
            "FROM llm_usage GROUP BY purpose, model")]
        n = c.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
        by = {r[0] or "untagged": r[1] for r in c.execute("SELECT tagged_by, COUNT(*) FROM notices GROUP BY tagged_by")}
    return {"notices": n, "tagged_by": by, "llm_enabled": config.llm_enabled(), "llm_provider": config.LLM_PROVIDER,
            "models": {"tagging": config.TAG_MODEL, "insight": config.INSIGHT_MODEL},
            "llm_usage": usage, "scheduler": scheduler.status()}
