"""같은 공지 묶기, 마감 활동 후순위, 학보사 제외."""
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "t.db"))
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("BAZE_API_KEY", None)

from app import config, db, grouping, tagger  # noqa: E402
from app.crawler.runner import load_sources  # noqa: E402


def test_norm_title_strips_prefix_dates_and_revision_marks():
    base = grouping.norm_title("2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내")
    for t in ["2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내 (~11.22(일) 24:00까지)",
              "[소프트웨어학과] 2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내(~11/22(일) 24:00까지)",
              "[재공지] 2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내"]:
        assert grouping.norm_title(t) == base, t


def _add(c, src, url, title, posted, deadline=None, kind="activity"):
    c.execute("INSERT INTO notices(source_id,url,title,posted_at,deadline,kind,content,tagged_by) "
              "VALUES(?,?,?,?,?,?,'','rule')", (src, url, title, posted, deadline, kind))
    return c.execute("SELECT id FROM notices WHERE url=?", (url,)).fetchone()["id"]


def test_grouping_merges_cross_posts_and_revisions_only():
    db.init_db()
    t = "2027 겨울 글로벌 해커톤 참가 학생 모집 안내"
    with db.get_conn() as c:
        a = _add(c, "dept_a", "https://x/g1", t + " (~12.10.)", "2026-11-01", "2026-12-10")
        b = _add(c, "dept_b", "https://x/g2", "[학과공지] " + t + "(~12/10까지)", "2026-11-02", "2026-12-10")
        r = _add(c, "dept_a", "https://x/g3", "[재공지] " + t + " (마감연장 ~12.20.)", "2026-11-15", "2026-12-20")
        other = _add(c, "dept_a", "https://x/g4", "2027 겨울 글로벌 봉사단 파견 학생 모집 안내", "2026-11-03")
        short = _add(c, "dept_c", "https://x/g5", "공지 안내", "2026-11-03")
        short2 = _add(c, "dept_d", "https://x/g6", "공지 안내", "2026-11-04")
    grouping.assign_groups()
    with db.get_conn() as c:
        grp = {r["id"]: r["grp"] for r in c.execute("SELECT id, grp FROM notices")}
        members = grouping.group_members(c, [grp[a]])[grp[a]]
    assert grp[a] == grp[b] == grp[r]
    assert grp[other] == other and grp[short] == short and grp[short2] == short2
    info = grouping.describe(members)
    assert {m["source_id"] for m in info["sources"]} == {"dept_a", "dept_b"}
    # 다른 학과가 하루 늦게 올린 글은 회차가 아니고, 같은 곳의 재공지는 회차
    assert [m["id"] for m in info["versions"]] == [a, r]


def test_notices_api_merges_groups_and_puts_expired_last():
    from fastapi.testclient import TestClient

    from app.main import app
    db.init_db()
    today = date.today()
    past, soon = today - timedelta(days=2), today + timedelta(days=5)
    md = lambda d: f"(~{d.month}.{d.day}.)"  # 마감일은 제목에서 추출된다  # noqa: E731
    with db.get_conn() as c:
        c.execute("DELETE FROM notices")
        old = _add(c, "dept_a", "https://x/e1", f"지난 창업 캠프 참가자 모집 안내 {md(past)}", today.isoformat(),
                   past.isoformat())
        live = _add(c, "dept_a", "https://x/e2", f"AI 경진대회 참가팀 모집 안내 {md(soon)}",
                    (today - timedelta(days=5)).isoformat(), soon.isoformat())
        dup = _add(c, "dept_b", "https://x/e3", f"[공지] AI 경진대회 참가팀 모집 안내 {md(soon)}",
                   (today - timedelta(days=4)).isoformat(), soon.isoformat())
    grouping.assign_groups()
    with TestClient(app) as cl:
        d = cl.get("/api/notices?sort=latest").json()
    ids = [n["id"] for n in d["items"]]
    assert d["total"] == 2 and ids == [dup, old]  # 묶음은 최신 글 하나, 마감된 건 최신이어도 뒤로
    first, last = d["items"]
    assert not first["expired"] and len(first["sources"]) == 2 and last["expired"]
    assert live not in ids


def test_press_excluded_and_purged():
    assert "press_rss" in config.EXCLUDED_SOURCES
    assert not next(s for s in load_sources() if s["id"] == "press_rss")["enabled"]
    db.init_db()
    with db.get_conn() as c:
        _add(c, "press_rss", "https://x/p1", "[칼럼] 대학 생활에 대하여", "2026-11-01")
    assert tagger.backfill()["excluded_removed"] == 1
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM notices WHERE source_id='press_rss'").fetchone()[0] == 0


def test_tag_model_defaults_to_haiku():
    assert os.getenv("TAG_MODEL") or config.TAG_MODEL == "claude-haiku-5-5"
