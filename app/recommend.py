"""그래프 기반 추천 (토큰 0).

관련도(정확 일치·부분 일치·같은 분야·같은 영역) 순으로 정렬하고, 최신성·마감 임박은 작은 가산점.
"""
from __future__ import annotations

import math
import re
from datetime import date

from .db import get_conn, row_to_notice
from .interest import activation, load_graph
from .taxonomy import CATEGORY_GROUP


def _age_days(d: str | None) -> float:
    if not d:
        return 60
    try:
        return max((date.today() - date.fromisoformat(d)).days, 0)
    except ValueError:
        return 60


def _tokens(text: str) -> set[str]:
    return {w for w in re.split(r"[^0-9A-Za-z가-힣]+", text.lower()) if len(w) >= 2}


def recommend(user_id: str, limit: int = 20, max_age_days: int = 120) -> list[dict]:
    """관련도 순 추천 (토큰 0).

    정확히 같은 단어가 있는지만 보지 않고 관련성을 점수로 매긴다.
      - 관심 노드와 공지 키워드·태그가 정확히 같음: 활성도 × 1.0
      - 관심어가 제목에 포함되거나 서로 부분 일치(예: '강화학습'과 '강화 학습 캠프'): × 0.7
      - 관심 노드의 분야(부모)와 공지 태그가 같음: 분야 활성도 × 0.6
      - 같은 영역(학습·비교과·교내외 활동)의 활동: 영역 비중 × 0.25
    관련도 = 상위 근거 4개 합을 0~100으로 환산. 정렬은 관련도 우선, 최신성·마감 임박은 작은 가산점.
    """
    nodes, edges = load_graph(user_id)
    act = activation(user_id)
    parent = {e["a"]: e["b"] for e in edges}  # 트리: 자식 → 부모
    gw = {k: n["weight"] for k, n in nodes.items() if n["kind"] == "group"}
    gtot = sum(gw.values()) or 1
    group_share = {g: w / gtot for g, w in gw.items()}
    labels = sorted((k for k in act if len(k) >= 2), key=lambda k: -act[k])[:80]
    today = date.today().isoformat()
    with get_conn() as c:
        rows = c.execute(  # 단순 행정 안내는 추천하지 않는다
            "SELECT * FROM notices WHERE kind IS NULL OR kind <> 'admin' ORDER BY posted_at DESC LIMIT 3000").fetchall()
        booked = {r[0] for r in c.execute("SELECT notice_id FROM bookmarks WHERE user_id=?", (user_id,))}
    out = []
    for r in rows:
        n = row_to_notice(r)
        if n["deadline"] and n["deadline"] < today:
            continue
        age = _age_days(n["posted_at"])
        if age > max_age_days and not n["pinned"]:
            continue
        tags = [t for t in n["tags"] if t != "모집"]
        kws = [k.lower() for k in n["keywords"]]
        title = n["title"].lower()
        ntoks = _tokens(n["title"] + " " + " ".join(kws))
        reasons: dict[str, float] = {}

        def add(k, v):
            if v > reasons.get(k, 0):
                reasons[k] = v
        for t in tags:
            if t in act:
                add(t, act[t] * 0.6)
            elif group_share.get(CATEGORY_GROUP.get(t)):
                add(CATEGORY_GROUP[t], group_share[CATEGORY_GROUP[t]] * 0.25)
        for k in kws:
            if k in act:
                add(k, act[k])
        for lab in labels:
            if lab in reasons:
                continue
            low = lab.lower()
            if low in title or any(low in k or (len(k) >= 3 and k in low) for k in kws) or _tokens(low) & ntoks:
                v = act[lab] * 0.7
                p = parent.get(lab)  # 관심어의 분야가 공지 태그와도 맞으면 더 관련 있음
                if p in tags:
                    v += 0.15
                add(lab, v)
        rel = sum(sorted(reasons.values(), reverse=True)[:4])
        if act and rel < 0.08:
            continue  # 관심과 거의 무관
        recency = math.exp(-age / 14)
        urgency = 0.0
        if n["deadline"]:
            left = (date.fromisoformat(n["deadline"]) - date.today()).days
            if 0 <= left <= 7:
                urgency = 1.0
            n["days_left"] = left
        n["relevance"] = round(min(1.0, rel / 1.6) * 100) if act else 0
        n["score"] = round((rel if act else 0.3) + 0.12 * recency + 0.08 * urgency + (0.05 if n["pinned"] else 0), 4)
        n["reasons"] = [k for k, _ in sorted(reasons.items(), key=lambda kv: -kv[1])[:4]]
        n["bookmarked"] = n["id"] in booked
        out.append(n)
    out.sort(key=lambda x: -x["score"])
    # 같은 공지 묶음(여러 학과 중복·재공지)은 점수가 가장 높은 글 하나만
    seen, uniq = set(), []
    for n in out:
        g = n.get("grp") or n["id"]
        if g not in seen:
            seen.add(g)
            uniq.append(n)
    return uniq[:limit]
