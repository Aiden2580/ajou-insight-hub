"""그래프 기반 추천 (토큰 0).

점수 = Σ(공지 특징 노드의 관심 활성도) + 최신성 + 마감 임박 가산
공지 특징 = 태그(카테고리 노드) + 키워드 + 제목에 등장하는 사용자 토픽
"""
from __future__ import annotations

import math
from datetime import date

from .db import get_conn, row_to_notice
from .interest import activation


def _age_days(d: str | None) -> float:
    if not d:
        return 60
    try:
        return max((date.today() - date.fromisoformat(d)).days, 0)
    except ValueError:
        return 60


def recommend(user_id: str, limit: int = 20, max_age_days: int = 120) -> list[dict]:
    act = activation(user_id)
    topics = sorted((k for k in act), key=lambda k: -act[k])[:60]
    today = date.today().isoformat()
    with get_conn() as c:
        rows = c.execute("SELECT * FROM notices ORDER BY posted_at DESC LIMIT 2000").fetchall()
        booked = {r[0] for r in c.execute("SELECT notice_id FROM bookmarks WHERE user_id=?", (user_id,))}
    out = []
    for r in rows:
        n = row_to_notice(r)
        if n["deadline"] and n["deadline"] < today:
            continue
        age = _age_days(n["posted_at"])
        if age > max_age_days and not n["pinned"]:
            continue
        reasons: dict[str, float] = {}
        for t in n["tags"]:
            if t in act and t != "모집":
                reasons[t] = act[t] * 0.6
        title_low = n["title"].lower()
        for k in n["keywords"]:
            if k in act:
                reasons[k] = max(reasons.get(k, 0), act[k])
        for t in topics:
            if len(t) >= 2 and t in title_low:
                reasons[t] = max(reasons.get(t, 0), act[t])
        # 오래된 고정 공지가 상단을 독차지하지 않도록 관심 점수에도 완만한 시간 감쇠
        interest = sum(sorted(reasons.values(), reverse=True)[:4]) * (0.6 + 0.4 * math.exp(-age / 60))
        recency = 0.4 * math.exp(-age / 14)
        urgency = 0.0
        if n["deadline"]:
            left = (date.fromisoformat(n["deadline"]) - date.today()).days
            if 0 <= left <= 7:
                urgency = 0.3
            n["days_left"] = left
        score = interest + recency + urgency if act else recency + urgency + (0.2 if n["pinned"] else 0)
        n["score"] = round(score, 3)
        n["reasons"] = [k for k, _ in sorted(reasons.items(), key=lambda kv: -kv[1])[:4]]
        n["bookmarked"] = n["id"] in booked
        if act and not reasons:
            continue  # 관심과 무관한 공지는 추천에서 제외
        out.append(n)
    out.sort(key=lambda x: -x["score"])
    return out[:limit]
