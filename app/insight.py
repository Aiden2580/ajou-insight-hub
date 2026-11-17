"""일일 인사이트 (A4 1~3장).

토큰 절약 핵심:
  - 원문 공지/방문 기록을 보내지 않는다. 그래프에서 뽑은 '압축 컨텍스트'만 보낸다
    (상위 관심 노드 12개 + 핵심 연결 15개 + 추천 공지 8개의 한 줄 요약) ≈ 수백 토큰.
  - 컨텍스트 해시가 전날과 같으면 다시 생성하지 않는다.
  - 사용자당 하루 1회 호출. 키가 없으면 템플릿 보고서로 대체(비용 0).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from . import config, llm
from .db import get_conn
from .interest import activation, load_graph
from .recommend import recommend

KST = timezone(timedelta(hours=9))


def today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def build_context(user_id: str) -> dict:
    nodes, edges = load_graph(user_id)
    act = activation(user_id)
    top_nodes = sorted(nodes.values(), key=lambda n: -n["weight"])[:12]
    mx = top_nodes[0]["weight"] if top_nodes else 1
    top_edges = sorted(edges, key=lambda e: -e["weight"])[:15]
    recs = recommend(user_id, limit=8)
    lines = ["# 관심 노드(가중치)"]
    lines += [f"- {n['label']}({n['weight']/mx:.2f}){' [분야]' if n['kind']=='category' else ''}" for n in top_nodes]
    lines.append("# 연결")
    lines += [f"- {e['a']} — {e['b']}" for e in top_edges]
    lines.append("# 추천 활동")
    for r in recs:
        dl = f"[마감 {r['deadline']}] " if r.get("deadline") else ""
        summ = r.get("summary") or ", ".join(r["keywords"][:3])
        lines.append(f"- {dl}{r['title']} — {summ} (연결: {', '.join(r['reasons'])}) {r['url']}")
    text = "\n".join(lines)
    return {
        "text": text,
        "hash": hashlib.sha1(text.encode()).hexdigest(),
        "tokens_est": len(text) // 2,  # 한국어는 대략 2자당 1토큰 정도로 보수 추정
        "top_nodes": top_nodes,
        "recs": recs,
        "act": act,
    }


_SYSTEM = """너는 아주대학교 학생을 위한 개인 맞춤 리서치 코치다.
주어진 '관심 그래프 요약'과 '추천 활동'만 근거로, 한국어 마크다운 인사이트 리포트를 쓴다.
분량은 A4 1~3장(약 1,500~4,000자). 구성:
# 제목(오늘의 핵심 주제를 담아)
## 오늘의 관심 지도 — 그래프에서 읽히는 관심 흐름과 클러스터 2~3개
## 깊이 읽기 — 가장 강한 관심 주제 1개에 대한 배경지식, 최근 동향, 학습 로드맵(일반 지식 기반, 단정적 수치 금지)
## 이번 주 아주대에서 할 일 — 추천 활동을 우선순위대로, 마감일과 왜 맞는지(연결 노드) 포함, 링크 유지
## 연결해서 생각해보기 — 서로 떨어진 관심 노드를 잇는 아이디어/프로젝트 제안 1~2개
## 내일을 위한 한 걸음 — 실행 체크리스트 3개
추천 활동에 없는 아주대 행사나 마감일을 지어내지 말 것."""


def _template_report(ctx: dict) -> str:
    nodes = ctx["top_nodes"]
    recs = ctx["recs"]
    d = today_kst()
    if not nodes:
        return (f"# {d} 인사이트\n\n아직 관심사 데이터가 없습니다. **관심사 연동** 메뉴에서 학습 폴더나 "
                "방문 기록을 연결하거나 관심 키워드를 입력하면 맞춤 인사이트가 생성됩니다.\n")
    main = nodes[0]["label"]
    cats = [n["label"] for n in nodes if n["kind"] == "category"][:3]
    topics = [n["label"] for n in nodes if n["kind"] == "topic"][:6]
    out = [f"# {d} · '{main}' 중심의 하루",
           "",
           "## 오늘의 관심 지도",
           f"가장 강한 관심 노드는 **{main}**입니다. "
           f"분야로는 {', '.join(cats) or '아직 뚜렷하지 않음'}, 세부 주제로는 {', '.join(topics) or '-'}가 두드러집니다.",
           "",
           "## 이번 주 아주대에서 할 일"]
    if recs:
        for i, r in enumerate(recs, 1):
            dl = f" · **마감 {r['deadline']}**" if r.get("deadline") else ""
            out.append(f"{i}. [{r['title']}]({r['url']}){dl}  \n   연결된 관심: {', '.join(r['reasons']) or '최신 공지'}")
    else:
        out.append("현재 관심사와 연결된 진행 중 공지가 없습니다. 크롤링 대상을 늘리거나 관심 키워드를 추가해 보세요.")
    urgent = [r for r in recs if r.get("days_left") is not None and r["days_left"] <= 7]
    if urgent:
        out += ["", "## 마감 임박"] + [f"- {r['title']} (D-{r['days_left']})" for r in urgent]
    out += ["", "## 내일을 위한 한 걸음",
            f"- [ ] '{main}' 관련 추천 활동 1개 신청 여부 결정하기",
            "- [ ] 관심 그래프에서 연결이 약한 주제 하나를 골라 30분 탐색하기",
            "- [ ] 마감 임박 공지 캘린더에 등록하기",
            "",
            "> Claude API 키가 설정되면 이 자리에 그래프 기반 심층 리포트(A4 1~3장)가 생성됩니다."]
    return "\n".join(out)


def generate(user_id: str, force: bool = False) -> dict:
    d = today_kst()
    today = get(user_id, d)
    # 하루 1번: 오늘 것이 이미 있으면 그대로 (LLM이 꺼져 있을 때 만든 템플릿만 예외)
    if today and not force and (today["generated_by"] != "template" or not config.llm_enabled()):
        return today
    ctx = build_context(user_id)
    with get_conn() as c:
        prev = c.execute(
            "SELECT * FROM insights WHERE user_id=? ORDER BY date DESC LIMIT 1", (user_id,)
        ).fetchone()
    if prev and not force and prev["ctx_hash"] == ctx["hash"] and prev["generated_by"] != "template":
        # 그래프와 추천이 그대로면 재생성하지 않음 (토큰 0)
        if prev["date"] != d:
            with get_conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO insights(user_id,date,title,content_md,generated_by,ctx_hash,ctx_tokens_est)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (user_id, d, prev["title"], prev["content_md"], "reused", ctx["hash"], 0),
                )
        return get(user_id, d)

    md, by = None, "template"
    if ctx["top_nodes"] and config.llm_enabled():
        md = llm.text_call("insight", config.INSIGHT_MODEL, _SYSTEM,
                           f"오늘 날짜: {d}\n\n{ctx['text']}", max_tokens=8000, effort="medium")
        if md:
            by = config.INSIGHT_MODEL
    if not md:
        md = _template_report(ctx)
    title = next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), f"{d} 인사이트")
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO insights(user_id,date,title,content_md,generated_by,ctx_hash,ctx_tokens_est)"
            " VALUES(?,?,?,?,?,?,?)",
            (user_id, d, title, md, by, ctx["hash"], ctx["tokens_est"]),
        )
    return get(user_id, d)


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
