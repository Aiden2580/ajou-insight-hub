"""오프라인 데모 데이터: 실제 아주대 게시판에서 저장한 HTML(tests/fixtures)을
실제 파서로 파싱해 DB에 넣고, 데모 사용자의 관심 신호를 넣는다.

    python -m scripts.seed_demo
"""
from pathlib import Path

from app import tagger
from app.crawler import parsers  # noqa: F401
from app.crawler.base import get_parser
from app.crawler.runner import load_sources, save_items
from app.db import init_db
from app.grouping import assign_groups
from app.folders import select
from app.interest import add_signals, set_folder_selection

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DEMO_USER = "demo-user-0001"

FIXTURE_MAP = {"kr_ajou_notice": "ajou_main_notice.html", "software_notice": "software_notice.html"}

# 데모용: 같은 공지를 여러 학과가 올린 경우와 재공지(회차)를 보여 주기 위한 사본.
# 링크는 원본 글로 열리도록 원본 URL에 #출처 조각만 붙인다 (실제 수집에서는 각 학과 글 URL이 들어감).
DEMO_DUPLICATES = [
    # (원본 제목에 포함된 문자열, 출처 게시판 id, 제목, 작성일)
    ("AJOU-ANCHORING", "sw_board_notice", "2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내 (~11.22(일) 24:00까지)", "2026-11-13"),
    ("AJOU-ANCHORING", "security_board_under-notice", "2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내 (~11.22(일) 24:00까지)", "2026-11-13"),
    ("AJOU-ANCHORING", "coss_community_community01", "2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내 (~11.22(일) 24:00까지)", "2026-11-14"),
    ("AJOU-ANCHORING", "kr_ajou_notice", "2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내 (~11.22(일) 24:00까지)", "2026-11-14"),
    ("AJOU-ANCHORING", "media_board_notice", "[재공지] 2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내 (~11.22(일) 24:00까지)", "2026-11-17"),
    ("동계방학 현장실습 운영일정", "software_notice", "[앵커] 2026 동계방학 현장실습 운영일정 안내", "2026-10-30"),
    ("동계방학 현장실습 운영일정", "software_notice", "[수정] [앵커] 2026 동계방학 현장실습 운영일정 안내(★학생 지원신청: ~12/7(월)까지, 기업 추가)", "2026-11-16"),
]

DEMO_SIGNALS = {
    "folder": [
        "3학년2학기/운영체제/과제3_CPU스케줄링.pdf",
        "3학년2학기/인공지능/딥러닝_CNN_정리.ipynb",
        "3학년2학기/인공지능/LLM_프롬프트_실습.ipynb",
        "프로젝트/캡스톤디자인/중간발표_자료.pptx",
        "프로젝트/캡스톤디자인/AI_추천시스템_설계.docx",
        "창업동아리/IR_피칭덱_v2.pptx",
        "창업동아리/데모데이_발표_리허설.pptx",
        "AI소학회_ALPS/추천시스템_논문리뷰.pdf",
        "AI소학회_ALPS/LLM_파인튜닝_실습.ipynb",
        "3학년2학기/운영체제/과제4_페이지교체.pdf",
        "3학년2학기/인공지능/강화학습_정리.pdf",
        "게임프로젝트/MyGame/ProjectSettings/ProjectVersion.txt",
        "게임프로젝트/MyGame/Assets/Scripts/PlayerController.cs",
        "게임프로젝트/MyGame/Library/ArtifactDB",
        "C++과제/Homework3/Homework3.sln",
        "C++과제/Homework3/x64/Debug/Homework3.exe",
        "사진/김철수_졸업사진.jpg",
        "취업준비/현장실습_자기소개서.hwp",
    ],
    "history": [
        "LLM 에이전트 설계 패턴 정리\thttps://example.com/blog/llm-agent",
        "그래프 신경망 GNN 입문\thttps://example.com/gnn",
        "https://www.google.com/search?q=추천시스템+그래프+임베딩",
        "스타트업 투자유치 데모데이 준비 방법\thttps://example.com/demoday",
        "글로벌 인턴십 후기\thttps://example.com/global-intern",
    ],
    "keyword": ["인공지능", "창업", "캡스톤디자인"],
}


def main():
    init_db()
    for s in load_sources():
        f = FIXTURE_MAP.get(s["id"])
        if not f:
            continue
        p = get_parser(s["parser"])(s)
        items = p.parse_list((FIX / f).read_text(encoding="utf-8"), s["url"])
        new = save_items(s["id"], items)
        with __import__("app.db", fromlist=["get_conn"]).get_conn() as c:  # 오프라인 데모: 본문 수집 생략
            c.execute("UPDATE notices SET content='' WHERE content IS NULL")
        print(f"{s['id']}: parsed {len(items)}, new {len(new)}")
    with __import__("app.db", fromlist=["get_conn"]).get_conn() as c:
        for needle, src, title, posted in DEMO_DUPLICATES:
            orig = c.execute("SELECT url FROM notices WHERE title LIKE ? ORDER BY id LIMIT 1", (f"%{needle}%",)).fetchone()
            if orig:
                c.execute("INSERT OR IGNORE INTO notices(source_id,url,title,posted_at,content) VALUES(?,?,?,?,'')",
                          (src, f"{orig['url'].split('#')[0]}#{src}-{posted}", title, posted))
    print("grouping:", assign_groups())
    print("tagging:", tagger.tag_pending(use_llm=True))
    for kind, items in DEMO_SIGNALS.items():
        if kind == "folder":  # 실제 앱과 같은 경로: 이름 목록 → 과목·동아리·키워드 선별
            set_folder_selection(DEMO_USER, select(items))
        else:
            add_signals(DEMO_USER, kind, items)
    print(f"demo user '{DEMO_USER}' seeded")


if __name__ == "__main__":
    main()
