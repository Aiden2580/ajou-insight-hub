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
from app.interest import add_signals

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DEMO_USER = "demo-user-0001"

FIXTURE_MAP = {"kr_ajou_notice": "ajou_main_notice.html", "software_notice": "software_notice.html"}

DEMO_SIGNALS = {
    "folder": [
        "3학년2학기/운영체제/과제3_CPU스케줄링.pdf",
        "3학년2학기/인공지능/딥러닝_CNN_정리.ipynb",
        "3학년2학기/인공지능/LLM_프롬프트_실습.ipynb",
        "프로젝트/캡스톤디자인/중간발표_자료.pptx",
        "프로젝트/캡스톤디자인/AI_추천시스템_설계.docx",
        "창업동아리/IR_피칭덱_v2.pptx",
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
    print("tagging:", tagger.tag_pending(use_llm=True))
    for kind, items in DEMO_SIGNALS.items():
        add_signals(DEMO_USER, kind, items)
    print(f"demo user '{DEMO_USER}' seeded")


if __name__ == "__main__":
    main()
