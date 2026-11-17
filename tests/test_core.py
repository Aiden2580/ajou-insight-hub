import os
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "t.db")
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.crawler import parsers  # noqa: E402,F401
from app.crawler.base import get_parser, normalize_date  # noqa: E402
from app.taxonomy import extract_deadline, rule_tags  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
MAIN = {"id": "ajou-main", "parser": "k2web", "url": "https://www.ajou.ac.kr/kr/ajou/notice.do"}
SW = {"id": "software", "parser": "generic", "url": "https://software.ajou.ac.kr/board/notice",
      "link_pattern": r"/board/notice/\d+"}


def test_k2web_parser():
    p = get_parser("k2web")(MAIN)
    items = p.parse_list((FIX / "ajou_main_notice.html").read_text(), MAIN["url"])
    assert len(items) == 12
    first = items[0]
    assert first.title.startswith("[학부] 강의시간 운영 방식 변경")
    assert "[공지]" not in first.title
    assert first.url == "https://www.ajou.ac.kr/kr/ajou/notice.do?mode=view&articleNo=379412"
    assert first.posted_at == "2026-11-16" and first.category == "학사" and first.pinned
    last = items[-1]
    assert last.category == "취업" and last.author == "국제교류팀" and not last.pinned


def test_generic_parser():
    p = get_parser("generic")(SW)
    items = p.parse_list((FIX / "software_notice.html").read_text(), SW["url"])
    assert len(items) == 10
    assert items[0].url == "https://software.ajou.ac.kr/board/notice/1561"
    assert items[0].posted_at == "2026-07-13" and items[0].pinned
    assert items[-1].posted_at == "2026-11-13" and not items[-1].pinned


def test_dates_and_deadlines():
    assert normalize_date("26.11.17") == "2026-11-17"
    assert extract_deadline("신청안내(~11/20 13:00까지)", "2026-11-17") == "2026-11-20"
    assert extract_deadline("제출 안내 (~2027.1.22)", "2026-08-28") == "2027-01-22"
    assert extract_deadline("TOPCIT 제출 안내(~26.12.31.)", "2026-11-16") == "2026-12-31"
    assert extract_deadline("(11.16.~11.20.)", "2026-11-13") == "2026-11-20"
    assert extract_deadline("지원신청: ~12/7(월)까지", "2026-11-13") == "2026-12-07"


def test_rule_tags():
    tags = rule_tags("2026 AJOU-ANCHORING 투자유치 데모데이 참가자 모집안내")
    assert "창업" in tags and "모집" in tags


def test_end_to_end_demo():
    from scripts.seed_demo import DEMO_USER, main
    from app import insight
    from app.interest import graph_payload
    from app.recommend import recommend
    main()
    g = graph_payload(DEMO_USER)
    labels = {n["id"] for n in g["nodes"]}
    assert "AI/SW" in labels and "창업" in labels and len(g["links"]) > 5
    recs = recommend(DEMO_USER, limit=10)
    assert recs and all(r["reasons"] for r in recs)
    ins = insight.generate(DEMO_USER)
    assert ins["generated_by"] == "template" and "할 일" in ins["content_md"]


def test_pyxis_parser():
    p = get_parser("pyxis")({"id": "library_notice", "parser": "pyxis", "url": "x"})
    items = p.parse_list((FIX / "library_pyxis.json").read_text(), "x")
    assert len(items) == 3 and items[1].category == "교육·행사"
    assert items[0].url == "https://library.ajou.ac.kr/guide/bulletins/notice/zcak433yua3b1kef"
    assert items[0].posted_at == "2026-11-17"


def test_rss_parser():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>제목A</title><link>https://press.ajou.ac.kr/news/articleView.html?idxno=1</link>
    <pubDate>Mon, 16 Nov 2026 10:00:00 +0900</pubDate><description>&lt;p&gt;본문&lt;/p&gt;</description></item>
    </channel></rss>"""
    items = get_parser("rss")({"id": "press_rss", "parser": "rss", "url": "x"}).parse_list(xml, "x")
    assert items[0].title == "제목A" and items[0].posted_at == "2026-11-16" and items[0].extra["description"] == "본문"


def test_k2web_dedup_key():
    p = get_parser("k2web")(MAIN)
    items = p.parse_list((FIX / "ajou_main_notice.html").read_text(), MAIN["url"])
    assert items[0].ext_key == "ajou:379412"


def test_sources_json_valid():
    import json
    from app.crawler.base import available_parsers
    data = json.loads((Path(__file__).parent.parent / "app" / "sources.json").read_text())
    ids = [s["id"] for s in data["sources"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 100
    assert all(s["parser"] in available_parsers() for s in data["sources"])


def test_posted_date_not_from_title():
    p = get_parser("k2web")(MAIN)
    items = p.parse_list((FIX / "ajou_main_notice.html").read_text(), MAIN["url"])
    lang = next(i for i in items if "어학졸업인증" in i.title)
    assert lang.posted_at == "2026-08-28"
    sw = get_parser("generic")(SW).parse_list((FIX / "software_notice.html").read_text(), SW["url"])
    topcit = next(i for i in sw if "TOPCIT" in i.title)
    assert topcit.posted_at == "2026-11-16"


def test_k2web_gallery_layout():
    html = """<ul><li class=""><div class="b-box01"><div class="b-img-box"><a href="?mode=view&amp;articleNo=379494"><img alt="x"></a></div></div>
    <div class="b-box02"><div class="b-title-box"><span class="b-new">NEW</span>
    <a class="b-title" href="?mode=view&amp;articleNo=379494&amp;article.offset=0">아주대, 노벨화학상 수상자 초청 국제 학술행사 공동 개최</a>
    <a class="b-btn-location" href="#a">위치 확인</a></div>
    <div class="b-etc-box"><ul class="b-info-box"><li class="b-writer">작성자이솔</li><li class="b-date">작성일2026-11-17</li></ul></div></div></li></ul>"""
    items = get_parser("k2web")({**MAIN, "url": "https://www.ajou.ac.kr/kr/ajou/news.do"}).parse_list(html, "x")
    assert len(items) == 1 and items[0].posted_at == "2026-11-17" and items[0].author == "이솔"
