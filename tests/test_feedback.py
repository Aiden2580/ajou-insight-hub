"""피드백 반영 테스트: 작성일/마감일, 단순 안내 분리, 태깅 캐시, 관심 그래프 정리."""
import os
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "t.db"))
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("BAZE_API_KEY", None)

from app import config, db, tagger  # noqa: E402
from app.crawler.base import normalize_date  # noqa: E402
from app.taxonomy import classify_kind, extract_deadline  # noqa: E402


def _insert(c, url, title, posted, category=None):
    c.execute("INSERT INTO notices(source_id,url,title,category,posted_at,content) VALUES('t',?,?,?,?, '')",
              (url, title, category, posted))
    return c.execute("SELECT id FROM notices WHERE url=?", (url,)).fetchone()["id"]


def test_classify_kind():
    for t in ["전자출결 방법 안내", "자퇴 절차 안내", "2026학년도 1학기 수강신청 일정 안내", "휴학 및 복학 신청 안내",
              "현장실습 교과목 수강신청 관련 안내", "예비군 훈련 일정 안내"]:
        assert classify_kind(t, None) == "admin", t
    for t in ["2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내", "캡스톤디자인 경진대회 개최",
              "동계 국외현장실습 참가자 모집", "AI 특강 안내"]:
        assert classify_kind(t, None) == "activity", t


def test_deadline_not_rolled_to_next_year():
    # 11/17에 올라온 '결과 안내 (~11.10.)'는 2027-11-10이 아니라 2026-11-10
    assert extract_deadline("결과 안내 (~11.10.)", "2026-11-17") == "2026-11-10"
    # 연말 공지의 1~2월 마감만 다음 해로 넘긴다
    assert extract_deadline("신청 (~1.15.)", "2026-12-10") == "2027-01-15"


def test_normalize_date_skips_future():
    future = (date.today() + timedelta(days=40)).strftime("%Y.%m.%d")
    assert normalize_date(f"{future} 2026.11.03") == "2026-11-03"
    assert normalize_date(future) is None
    assert normalize_date("2026년 11월 3일") == "2026-11-03"


def test_migration_from_old_schema(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE notices (id INTEGER PRIMARY KEY, source_id TEXT, url TEXT UNIQUE, title TEXT, "
                 "category TEXT, author TEXT, posted_at TEXT, content TEXT, tags TEXT DEFAULT '[]', "
                 "keywords TEXT DEFAULT '[]', summary TEXT DEFAULT '', deadline TEXT, pinned INTEGER DEFAULT 0, "
                 "tagged_by TEXT, fetched_at TEXT)")
    conn.execute("INSERT INTO notices(source_id,url,title,posted_at,content,tagged_by) "
                 "VALUES('x','u1','자퇴 절차 안내','2099-01-01','본문','rule')")
    conn.commit(); conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(path))
    db.init_db()
    out = tagger.backfill()
    assert out["future_posted_cleared"] == 1 and out["kinds_classified"] == 1
    with db.get_conn() as c:
        r = c.execute("SELECT posted_at, content, kind FROM notices").fetchone()
        assert r["posted_at"] is None and r["content"] is None and r["kind"] == "admin"
        assert c.execute("SELECT count(*) FROM tag_cache").fetchone()[0] == 0


def test_tag_cache_reuses_results(monkeypatch):
    db.init_db()
    calls = []

    def fake_json_call(purpose, model, system, user, schema, **kw):
        calls.append(user)
        ids = [int(line.split("|")[0]) for line in user.splitlines()[1:]]
        return {"items": [{"id": i, "kind": "activity", "tags": ["창업"], "keywords": ["피칭"], "summary": "요약"}
                          for i in ids]}

    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    monkeypatch.setattr(tagger.llm, "json_call", fake_json_call)
    today = date.today().isoformat()
    with db.get_conn() as c:
        a = _insert(c, "https://x/cache-1", "알쏭달쏭 피칭 데이 열려요", today)
    r1 = tagger.tag_pending()
    assert len(calls) == 1 and r1["llm_tagged"] >= 1
    # 같은 제목의 재공지는 LLM을 다시 부르지 않고 캐시로 처리
    with db.get_conn() as c:
        b = _insert(c, "https://x/cache-2", "[재공지] 알쏭달쏭 피칭 데이 열려요", today)
    r2 = tagger.tag_pending()
    assert len(calls) == 1 and r2["from_cache"] == 1 and r2["sent_to_llm"] == 0
    with db.get_conn() as c:
        ra = c.execute("SELECT tags, tagged_by FROM notices WHERE id=?", (a,)).fetchone()
        rb = c.execute("SELECT tags, tagged_by FROM notices WHERE id=?", (b,)).fetchone()
    assert ra["tagged_by"] == "llm" and rb["tagged_by"] == "cache:llm" and "창업" in rb["tags"]
    # 이미 태깅된 공지는 다시 보내지 않는다
    assert tagger.tag_pending()["new"] == 0 and len(calls) == 1


def test_admin_hidden_and_graph_clean():
    from fastapi.testclient import TestClient

    from app.interest import add_signals, graph_payload
    from app.main import app
    from app.recommend import recommend
    from scripts.seed_demo import DEMO_USER, main

    main()
    with TestClient(app) as cl:
        h = {"X-User-Id": DEMO_USER}
        default = cl.get("/api/notices?size=100", headers=h).json()
        full = cl.get("/api/notices?size=100&include_admin=true", headers=h).json()
        assert full["total"] > default["total"]
        assert all(n.get("kind") != "admin" for n in default["items"])
        tags = cl.get("/api/tags", headers=h).json()
        assert tags["admin_count"] == full["total"] - default["total"] and isinstance(tags["tags"], list)

    with db.get_conn() as c:
        admin_ids = {r["id"] for r in c.execute("SELECT id FROM notices WHERE kind='admin'")}
    assert admin_ids and not admin_ids & {r["id"] for r in recommend(DEMO_USER, limit=50)}

    add_signals(DEMO_USER, "folder", ["2학기/운영체제/3주차/과제2_스케줄링.pdf", "2학기/운영체제/week5 lab.pdf"])
    g = graph_payload(DEMO_USER)
    ids = {n["id"] for n in g["nodes"]}
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"group", "category", "topic"} <= kinds
    assert not any(("주차" in i or "과제" in i or "week" in i.lower() or "학기" in i) for i in ids)
    assert ids & {"학습", "교과", "비교과", "교내외 활동"}
    assert "운영체제" in ids and "학사" not in ids and "모집" not in ids
