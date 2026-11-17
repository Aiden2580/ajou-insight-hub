"""같은 공지 묶기 (토큰 0, 규칙 기반).

- 여러 학과·기관이 같은 공지를 각각 올린 경우 → 하나로 병합 (출처 여러 곳)
- 같은 공지를 재공지·수정·마감연장·리마인드로 여러 번 올린 경우 → 회차로 묶음

notices.grp 에 묶음 대표 id(묶음에서 가장 먼저 수집된 공지 id)를 저장한다. 혼자인 공지는 grp = 자기 id.
판정:
  1) 제목 정규화 키(머리말 [..], 날짜·기간 괄호, 재공지/수정/연장 표기, 기호, 공백 제거)가 같고
     작성일이 GROUP_WINDOW_DAYS 이내이면 같은 묶음
  2) 키가 다르더라도 토큰 자카드 ≥ 0.6 이고 문자열 유사도 ≥ 0.85 이면 같은 묶음 (회차마다 제목이 조금 바뀌는 경우)
너무 짧은 제목(키 8자 미만)은 우연한 일치를 피하려고 묶지 않는다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher

from .db import get_conn

GROUP_WINDOW_DAYS = 120
MIN_KEY_LEN = 8
_REVISION = r"재공지|재안내|재게시|수정|정정|변경|연장|마감\s*연장|기간\s*연장|추가\s*모집|추가모집|리마인드|remind|업데이트|update|최종|긴급|필독|중요"


_REVISION_RE = re.compile(_REVISION, re.I)


def norm_title(title: str) -> str:
    t = title or ""
    t = re.sub(r"\[[^\]]*\]|【[^】]*】|〔[^〕]*〕|<[^>]*>", " ", t)          # 머리말 [앵커] [재공지-OO사업단] 등
    for _ in range(3):  # 날짜·기간·재공지 표기 괄호 (중첩 괄호도 바깥까지 지우도록 반복)
        t = re.sub(r"\([^()]*(\d|~|까지|" + _REVISION + r")[^()]*\)", " ", t, flags=re.I)
    t = re.sub(r"(?<![가-힣])(" + _REVISION + r")(?![가-힣])", " ", t, flags=re.I)
    t = re.sub(r"~\s*\d{1,4}[./-]\d{1,2}(?:[./-]\d{1,2})?\.?(\([^)]*\))?\s*(\d{1,2}:\d{2})?\s*(까지)?", " ", t)
    t = re.sub(r"[^\w가-힣]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _tokens(nk: str) -> set[str]:
    return {w for w in nk.split() if len(w) >= 2}


def _key(nk: str) -> str:
    return nk.replace(" ", "")


def _days(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 0
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return 0


def _similar(a: str, b: str, ta: set[str], tb: set[str]) -> bool:
    if not ta or not tb:
        return False
    jac = len(ta & tb) / len(ta | tb)
    if jac < 0.6:
        return False
    sm = SequenceMatcher(None, _key(a), _key(b))
    return sm.real_quick_ratio() >= 0.85 and sm.quick_ratio() >= 0.85 and sm.ratio() >= 0.85


def assign_groups(regroup_all: bool = False) -> dict:
    """grp가 비어 있는 공지(또는 전체)에 묶음을 정한다. 수집 직후와 서버 시작 시 호출."""
    with get_conn() as c:
        if regroup_all:
            c.execute("UPDATE notices SET grp=NULL")
        rows = c.execute("SELECT id, title, posted_at, grp FROM notices ORDER BY id").fetchall()
    # 이미 묶인 공지로 색인을 만든다: 정확 키 → 묶음들, 토큰 → 묶음들
    exact: dict[str, list[tuple[int, str | None]]] = defaultdict(list)
    by_tok: dict[str, set[int]] = defaultdict(set)
    rep: dict[int, tuple[str, set[str], str | None]] = {}  # 묶음 → (대표 정규화 제목, 토큰, 최근 작성일)

    def index(gid: int, nk: str, posted: str | None):
        exact[_key(nk)].append((gid, posted))
        toks = _tokens(nk)
        if gid not in rep or (posted or "") > (rep[gid][2] or ""):
            rep[gid] = (nk, toks, posted)
        for w in toks:
            by_tok[w].add(gid)

    todo = []
    for r in rows:
        nk = norm_title(r["title"])
        if r["grp"] is not None:
            if len(_key(nk)) >= MIN_KEY_LEN:
                index(r["grp"], nk, r["posted_at"])
        else:
            todo.append((r, nk))

    updates, merged = [], 0
    for r, nk in todo:
        gid = None
        k = _key(nk)
        if len(k) >= MIN_KEY_LEN:
            for g, posted in exact.get(k, []):
                if _days(posted, r["posted_at"]) <= GROUP_WINDOW_DAYS:
                    gid = g
                    break
            if gid is None:
                toks = _tokens(nk)
                cands = defaultdict(int)
                for w in toks:
                    for g in by_tok.get(w, ()):
                        cands[g] += 1
                for g, _ in sorted(cands.items(), key=lambda kv: -kv[1])[:30]:
                    rn, rt, rp = rep[g]
                    if _days(rp, r["posted_at"]) <= GROUP_WINDOW_DAYS and _similar(nk, rn, toks, rt):
                        gid = g
                        break
        if gid is None:
            gid = r["id"]
        else:
            merged += 1
        updates.append((gid, r["id"]))
        if len(k) >= MIN_KEY_LEN:
            index(gid, nk, r["posted_at"])
    if updates:
        with get_conn() as c:
            c.executemany("UPDATE notices SET grp=? WHERE id=?", updates)
    return {"assigned": len(updates), "merged": merged}


def group_members(c, gids: list[int], source_name=lambda s: s) -> dict[int, list[dict]]:
    """묶음별 구성 공지 (오래된 것부터)."""
    if not gids:
        return {}
    q = ",".join("?" * len(gids))
    out: dict[int, list[dict]] = defaultdict(list)
    for r in c.execute(f"SELECT id, grp, source_id, title, url, posted_at, deadline FROM notices "
                       f"WHERE grp IN ({q}) ORDER BY COALESCE(posted_at,''), id", gids):
        out[r["grp"]].append({"id": r["id"], "source_id": r["source_id"], "title": r["title"],
                              "url": r["url"], "posted_at": r["posted_at"], "deadline": r["deadline"]})
    return out


def describe(members: list[dict]) -> dict:
    """프런트에서 펼칠 때 쓰는 정보: 출처별 최신 글, 회차 목록."""
    sources: dict[str, dict] = {}
    for m in members:  # 오래된 것부터 → 마지막이 출처별 최신
        sources[m["source_id"]] = m
    # 회차: 최초 공지 + (이미 올렸던 곳이 다시 올린 글 또는 재공지·수정·연장 표기가 있는 글).
    # 다른 학과가 며칠 늦게 같은 글을 퍼 온 것은 새 회차로 보지 않는다.
    versions, posted_by, seen = [], set(), set()
    for m in members:
        key = (m["posted_at"], re.sub(r"\s+", "", m["title"]))
        if key in seen:
            continue
        seen.add(key)
        if not versions or m["source_id"] in posted_by or _REVISION_RE.search(m["title"]):
            versions.append(m)
        posted_by.add(m["source_id"])
    return {"sources": list(sources.values()), "versions": versions}
