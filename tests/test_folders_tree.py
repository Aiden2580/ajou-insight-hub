"""학습 폴더 이름 선별, 트리형 그래프, 관련도 순 추천."""
import json
import os
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "t.db"))
os.environ["ENABLE_SCHEDULER"] = "0"

from app import config, folders, llm  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.interest import add_signals, load_graph, set_folder_selection  # noqa: E402

PATHS = [
    "공부/3학년1학기/자료구조/과제2_트리.pdf",
    "공부/3학년1학기/자료구조/중간고사_정리.hwp",
    "공부/3학년1학기/선형대수/고유값_정리.pdf",
    "게임/MyUnityGame/ProjectSettings/ProjectVersion.txt",
    "게임/MyUnityGame/Assets/Scripts/PlayerController.cs",
    "게임/MyUnityGame/Library/ArtifactDB",
    "C++/Homework3/Homework3.sln",
    "C++/Homework3/x64/Debug/Homework3.exe",
    "웹/site/node_modules/react/index.js",
    "웹/site/.git/HEAD",
    "동아리/로봇소학회_RAIL/ROS_세미나.pptx",
    "사진/홍길동_증명사진.jpg",
]


def test_filter_skips_junk_and_collapses_projects():
    kept = folders.filter_paths(PATHS)
    joined = "\n".join(kept)
    for bad in ("PlayerController", "ArtifactDB", "node_modules", ".git", "x64", "Homework3.exe", "ProjectSettings"):
        assert bad not in joined
    assert "게임/MyUnityGame/[프로젝트]" in kept and "C++/Homework3/[프로젝트]" in kept
    digest = folders.compress(kept)
    assert "자료구조/ : 과제2_트리, 중간고사_정리" in digest and len(digest) < 2000


def test_select_calls_llm_once_and_caches(monkeypatch):
    init_db()
    calls = []

    def fake(purpose, model, system, user, schema, **kw):
        calls.append((model, user))
        return {"courses": [{"name": "자료구조", "category": "AI/SW"}, {"name": "선형대수", "category": "기타"}],
                "clubs": [{"name": "로봇소학회 RAIL", "category": "공학/기술"}],
                "keywords": [{"name": "ROS", "category": "공학/기술"}, {"name": "홍", "category": "없는분야"}]}

    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "json_call", fake)
    a = folders.select(PATHS + ["공부/3학년1학기/자료구조/과제3_그래프.pdf"])
    b = folders.select(PATHS + ["공부/3학년1학기/자료구조/과제3_그래프.pdf"])
    assert len(calls) == 1 and calls[0][0] == config.TAG_MODEL and not a["cached"] and b["cached"]
    assert "PlayerController" not in calls[0][1] and "node_modules" not in calls[0][1]
    assert [c["name"] for c in a["courses"]] == ["자료구조", "선형대수"]
    assert a["keywords"] == [{"name": "ROS", "category": "공학/기술"}]  # 한 글자 이름은 버림


def test_graph_is_a_tree():
    init_db()
    u = "tree-user-0001"
    with get_conn() as c:
        c.execute("DELETE FROM signals WHERE user_id=?", (u,))
    set_folder_selection(u, {
        "courses": [{"name": "자료구조", "category": "AI/SW"}, {"name": "운영체제", "category": "AI/SW"}],
        "clubs": [{"name": "창업동아리", "category": "창업"}, {"name": "로봇소학회", "category": "기타"}],
        "keywords": [{"name": "강화학습", "category": "AI/SW"}, {"name": "투자유치", "category": "창업"}],
    })
    add_signals(u, "keyword", ["인공지능", "딥러닝 프레임워크", "스타트업 투자"])
    add_signals(u, "history", ["강화학습 PPO 알고리즘 정리\thttps://x/1"] * 3)
    nodes, edges = load_graph(u)
    parents = {}
    for e in edges:
        assert e["a"] not in parents, f"{e['a']} 에 부모가 둘"
        parents[e["a"]] = e["b"]
    for k, n in nodes.items():
        if n["kind"] == "group":
            assert k not in parents
        else:
            assert k in parents, k
    assert not [e for e in edges if nodes[e["a"]]["kind"] == "topic" and nodes[e["b"]]["kind"] == "topic"]
    assert parents["자료구조"] == "교과" and parents["창업동아리"] == "창업" and parents["창업"] == "교내외 활동"
    assert parents["로봇소학회"] == "교내외 활동" and parents["강화학습"] == "AI/SW"

    # 다시 연동하면 이전 폴더 결과를 바꾼다
    set_folder_selection(u, {"courses": [{"name": "선형대수", "category": "기타"}], "clubs": [], "keywords": []})
    nodes, _ = load_graph(u)
    assert "선형대수" in nodes and "자료구조" not in nodes and "창업동아리" not in nodes


def test_recommendations_sorted_by_relevance():
    from app.recommend import recommend
    init_db()
    u = "rel-user-0001"
    with get_conn() as c:
        c.execute("DELETE FROM signals WHERE user_id=?", (u,))
        c.execute("DELETE FROM notices")
        today = date.today().isoformat()
        rows = [
            ("https://x/r1", "강화학습 여름 캠프 참가자 모집", '["AI/SW"]', '["강화 학습", "캠프"]'),  # 부분 일치 + 분야
            ("https://x/r2", "AI 해커톤 참가팀 모집", '["AI/SW", "공모전/대회"]', '["해커톤"]'),       # 분야만 일치
            ("https://x/r3", "도자기 공방 체험 안내", '["행사/축제"]', '["도자기"]'),                  # 무관
        ]
        for url, title, tags, kws in rows:
            c.execute("INSERT INTO notices(source_id,url,title,posted_at,tags,keywords,kind,content,tagged_by) "
                      "VALUES('s',?,?,?,?,?,'activity','','rule')", (url, title, today, tags, kws))
    set_folder_selection(u, {"courses": [], "clubs": [],
                             "keywords": [{"name": "강화학습", "category": "AI/SW"}]})
    add_signals(u, "keyword", ["강화학습"])
    recs = recommend(u, limit=10)
    titles = [r["title"] for r in recs]
    assert titles[:2] == ["강화학습 여름 캠프 참가자 모집", "AI 해커톤 참가팀 모집"]
    assert "도자기 공방 체험 안내" not in titles
    assert recs[0]["relevance"] > recs[1]["relevance"] > 0


def test_folder_endpoint_without_llm():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as cl:
        r = cl.post("/api/signals/folder", json={"paths": PATHS}, headers={"X-User-Id": "api-folder-user-01"}).json()
    assert r["by"] == "rule" and r["received"] == len(PATHS) and r["kept"] < len(PATHS)
    names = json.dumps(r["selected"], ensure_ascii=False)
    assert "자료구조" in names and "PlayerController" not in names and "홍길동" not in names
