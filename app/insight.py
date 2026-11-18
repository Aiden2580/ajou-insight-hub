"""오늘의 칼럼 (하루 한 편 읽는 교양 글) + '더 알아보기' 심층 글.

- 관심 그래프에서 '오늘의 주제'를 고른다(토큰 0). 꼭 그래프와 일치할 필요는 없어서, 상위 관심사 중 최근 7일에
  다루지 않은 것을 날짜마다 돌아가며 고르고, 이웃 노드를 함께 줘서 옆길로 새는 흥미로운 연결도 허용한다.
- 칼럼은 웹 검색(서버 도구)을 켜고 쓴다. 최신 소식·논문은 실제 검색 결과에서만 인용하고 링크를 단다.
  게이트웨이가 웹 검색을 지원하지 않으면 최신 소식 없이 잘 알려진 내용 위주로 쓴다.
- 토큰 절약: 사용자당 하루 1편, 이미 있으면 재사용. 모델에는 관심사 이름 몇 개(수십 토큰)만 보낸다.
  '더 알아보기'는 누를 때 한 번만 만들고 저장해 두었다가 다시 보여 준다.
- 키가 없으면 주제와 직접 찾아볼 수 있는 검색 링크만 담은 템플릿(비용 0).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import config, llm
from .db import get_conn
from .interest import load_graph

KST = timezone(timedelta(hours=9))
RECENT_DAYS = 7


def today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def _recent_topics(user_id: str, d: str) -> list[str]:
    since = (datetime.fromisoformat(d) - timedelta(days=RECENT_DAYS)).date().isoformat()
    with get_conn() as c:
        return [r[0] for r in c.execute(
            "SELECT topic FROM insights WHERE user_id=? AND date>=? AND date<? AND topic IS NOT NULL",
            (user_id, since, d))]


def choose_topic(user_id: str, d: str) -> dict | None:
    """오늘의 주제 씨앗 (토큰 0). 상위 관심사 중 최근에 안 다룬 것을 날짜별로 돌아가며 고른다."""
    nodes, edges = load_graph(user_id)
    cands = sorted((n for n in nodes.values() if n["kind"] in ("topic", "category", "course")),
                   key=lambda n: -n["weight"])
    if not cands:
        return None
    recent = set(_recent_topics(user_id, d))
    pool = [n for n in cands[:10] if n["label"] not in recent] or cands[:10]
    pick = pool[int(hashlib.sha1(f"{user_id}:{d}".encode()).hexdigest(), 16) % min(4, len(pool))]
    label = pick["label"]
    nb = sorted(((e["b"] if e["a"] == label else e["a"], e["weight"]) for e in edges if label in (e["a"], e["b"])),
                key=lambda x: -x[1])
    related = [x for x, _ in nb if nodes.get(x, {}).get("kind") != "group"][:4]
    others = [n["label"] for n in cands[:10] if n["label"] != label]
    return {"topic": label, "related": related, "others": others, "recent": sorted(recent)}


_COLUMN_SYSTEM = """너는 대학생을 위한 교양 칼럼니스트다. 매일 한 편, 아침에 커피 한 잔 하며 읽을 만한 한국어 칼럼을 쓴다.
목표는 학생의 관심사에서 출발해 '오, 재밌네' 하고 더 알고 싶어지게 만드는 것이다. 관심 지도 설명이나 공부법·학습 계획은 쓰지 않는다.

주제 고르기
- '오늘의 주제 씨앗'에서 출발하되 꼭 그대로일 필요는 없다. 관련어나 다른 관심사와 이어지는 더 흥미로운 각도를 골라도 좋다.
- 웹 검색으로 최근 소식(새 논문, 발표, 사건, 논쟁)을 찾아 2~3개 엮어도 좋고, 너무 어렵다면 친숙한 사례·역사·일상 속 연결처럼
  흥미를 끄는 이야기로 풀어도 된다. 최근에 다룬 주제는 피한다.

사실성
- 최신 소식, 날짜, 수치, 논문 제목은 이번에 검색해서 확인한 것만 쓰고, 해당 문장 끝에 마크다운 링크를 단다.
- 확인되지 않은 소문이나 과장된 주장(예: 난제 해결 '주장')은 그렇다고 분명히 쓴다.

형식 (마크다운, 약 1,800~3,000자, A4 1~2장)
# 제목 (호기심을 끄는 한 줄)
> 한두 문장 리드
본문: 소제목(##) 2~4개로 자유롭게. 어려운 개념은 비유로 쉽게.
## 오늘의 질문 — 독자가 생각해 볼 질문 1~2개
마지막 줄에 다른 설명이나 인사말은 붙이지 않는다."""

_DEEP_SYSTEM = """너는 대학생 독자를 위한 심층 해설가다. 주어진 '오늘의 칼럼'을 읽은 학생이 '더 알아보기'를 눌렀다.
칼럼의 주제를 한 단계 더 깊게, 한국어 마크다운으로 쓴다 (약 2,500~4,500자).

구성
# 더 알아보기: (칼럼 주제를 담은 제목)
## 핵심 개념 — 칼럼에서 간단히 넘어간 개념을 정확하게 (필요하면 짧은 수식·예시)
## 지금 연구와 논쟁 — 웹 검색으로 확인한 최근 연구 흐름, 열린 문제, 반론
## 더 읽어볼 자료 — 논문 3~5개와 입문용 인터넷 자료(강의, 글, 영상) 2~4개.
   각 항목: [제목](링크) — 누가/언제, 무엇을 얻을 수 있는지 한 줄, 난이도(입문/중급/심화)
   논문은 arXiv·학회·저널 페이지 등 실제로 검색해서 확인한 링크만 쓴다. 확인 못 한 자료는 넣지 않는다.
## 직접 해보기 — 주말에 해 볼 수 있는 작은 실험·코드·읽기 1~2개

최신 소식, 날짜, 수치, 저자, 논문 제목은 검색으로 확인한 것만 쓴다. 마지막에 인사말을 붙이지 않는다."""


def _search_links(topic: str) -> list[str]:
    q = quote(topic)
    return [f"- [위키백과에서 '{topic}' 찾기](https://ko.wikipedia.org/w/index.php?search={q})",
            f"- [Google Scholar에서 '{topic}' 논문 찾기](https://scholar.google.com/scholar?q={q})",
            f"- [arXiv에서 '{topic}' 최신 논문 찾기](https://arxiv.org/search/?query={q}&searchtype=all)"]


def _template_column(seed: dict | None, d: str) -> str:
    if not seed:
        return (f"# {d} 오늘의 칼럼\n\n아직 관심사 데이터가 없습니다. **데이터 연동** 메뉴에서 학습 폴더나 방문 기록을 "
                "연결하거나 관심 키워드를 입력하면, 관심사에서 출발한 칼럼이 매일 한 편씩 만들어집니다.\n")
    t = seed["topic"]
    rel = ", ".join(seed["related"]) or "-"
    return "\n".join([
        f"# 오늘의 주제: {t}",
        "",
        f"> 오늘은 **{t}**에서 출발해 봅니다. 함께 엮어 볼 만한 주제는 {rel}입니다.",
        "",
        "## 직접 찾아보기",
        *_search_links(t),
        "",
        "## 오늘의 질문",
        f"- '{t}'가 내 전공이나 일상과 만나는 지점은 어디일까요?",
        "",
        "> LLM 키(BAZE 또는 Anthropic)를 설정하면 이 자리에 웹에서 찾은 최근 소식을 엮은 칼럼이 매일 한 편 실립니다.",
    ])


def _with_sources(md: str, sources: list[dict], heading: str) -> str:
    extra = [s for s in sources if s["url"] not in md]
    if not extra:
        return md
    lines = [f"- [{(s['title'] or s['url']).replace('[', '(').replace(']', ')')}]({s['url']})" for s in extra]
    return md.rstrip() + f"\n\n## {heading}\n" + "\n".join(lines) + "\n"


def generate(user_id: str, force: bool = False) -> dict:
    d = today_kst()
    today = get(user_id, d)
    # 하루 한 편: 오늘 것이 있으면 그대로 (LLM이 꺼져 있을 때 만든 템플릿만 예외)
    if today and not force and (today["generated_by"] != "template" or not config.llm_enabled()):
        return today
    seed = choose_topic(user_id, d)
    user_msg = ""
    md, by = None, "template"
    if seed and config.llm_enabled():
        user_msg = (f"오늘 날짜: {d}\n오늘의 주제 씨앗: {seed['topic']}\n"
                    f"씨앗과 연결된 관심어: {', '.join(seed['related']) or '-'}\n"
                    f"학생의 다른 관심사: {', '.join(seed['others']) or '-'}\n"
                    f"최근 {RECENT_DAYS}일 동안 다룬 주제(피할 것): {', '.join(seed['recent']) or '없음'}")
        out = llm.research_call("insight", config.INSIGHT_MODEL, _COLUMN_SYSTEM, user_msg, max_uses=4,
                                max_tokens=12000, effort="medium")
        if out and out["text"]:
            md = _with_sources(out["text"], out["sources"], "참고한 자료")
            by = config.INSIGHT_MODEL + (" + 웹 검색" if out["searched"] else "")
    if not md:
        md = _template_column(seed, d)
    title = next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), f"{d} 오늘의 칼럼")
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO insights(user_id,date,title,content_md,generated_by,ctx_hash,ctx_tokens_est,topic)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (user_id, d, title, md, by, hashlib.sha1(user_msg.encode()).hexdigest(), len(user_msg) // 2,
             seed["topic"] if seed else None),
        )
    return get(user_id, d)


def deep_dive(user_id: str, d: str | None = None, force: bool = False) -> dict | None:
    """'더 알아보기': 칼럼을 더 깊게 + 논문·인터넷 자료. 한 번 만들면 저장해 두고 재사용."""
    ins = get(user_id, d)
    if not ins:
        return None
    if ins.get("deep_md") and not force and (ins.get("deep_by") != "template" or not config.llm_enabled()):
        return ins
    md, by = None, "template"
    if config.llm_enabled() and ins["generated_by"] != "template":
        out = llm.research_call("insight_deep", config.INSIGHT_MODEL, _DEEP_SYSTEM,
                                f"오늘의 칼럼:\n\n{ins['content_md']}", max_uses=6, max_tokens=16000, effort="medium")
        if out and out["text"]:
            md = _with_sources(out["text"], out["sources"], "검색으로 확인한 출처")
            by = config.INSIGHT_MODEL + (" + 웹 검색" if out["searched"] else "")
    if not md:
        topic = ins.get("topic") or ins["title"]
        md = "\n".join([f"# 더 알아보기: {topic}", "", "## 직접 찾아보기", *_search_links(topic), "",
                        "> LLM 키를 설정하면 이 자리에 심층 해설과 확인된 논문·자료 목록이 만들어집니다."])
    with get_conn() as c:
        c.execute("UPDATE insights SET deep_md=?, deep_by=? WHERE user_id=? AND date=?",
                  (md, by, user_id, ins["date"]))
    return get(user_id, ins["date"])


def get(user_id: str, d: str | None = None) -> dict | None:
    with get_conn() as c:
        if d:
            r = c.execute("SELECT * FROM insights WHERE user_id=? AND date=?", (user_id, d)).fetchone()
        else:
            r = c.execute("SELECT * FROM insights WHERE user_id=? ORDER BY date DESC LIMIT 1", (user_id,)).fetchone()
    return dict(r) if r else None


def history(user_id: str) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT date,title,generated_by FROM insights WHERE user_id=? ORDER BY date DESC LIMIT 60", (user_id,))]

