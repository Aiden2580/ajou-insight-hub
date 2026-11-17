"""공지 태깅.

비용 전략 (태깅은 공지당 평생 1회):
  1) 모든 공지는 먼저 규칙 기반(무료)으로 태그/키워드/마감일/종류(활동·행정)를 붙인다.
  2) LLM은 아래 조건을 모두 만족하는 공지에만, 25개씩 묶어 한 번 호출한다.
     - 규칙 태그가 부족함(애매함)
     - 단순 행정 안내가 아님
     - 최근 LLM_TAG_MAX_AGE_DAYS일 이내 공지 (옛 공지는 규칙 태그로 충분)
     - 한 번 갱신에 최대 LLM_TAG_MAX_PER_RUN개
  3) 결과는 notices와 tag_cache(제목 해시)에 영구 저장. 같은 제목의 재공지·다른 게시판 중복은
     캐시로 처리해 다시 보내지 않는다. tagged_by가 정해진 공지는 다시 태깅하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, timedelta

from . import config, llm
from .db import get_conn
from .taxonomy import TAXONOMY, classify_kind, extract_deadline, keywords, rule_tags

BATCH = 25
LLM_TAG_MAX_AGE_DAYS = int(os.getenv("LLM_TAG_MAX_AGE_DAYS", "45"))
LLM_TAG_MAX_PER_RUN = int(os.getenv("LLM_TAG_MAX_PER_RUN", "100"))

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "kind": {"type": "string", "enum": ["activity", "admin"]},
                    "tags": {"type": "array", "items": {"type": "string", "enum": list(TAXONOMY)}},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
                "required": ["id", "kind", "tags", "keywords", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_SYSTEM = (
    "아주대학교 공지를 분류한다. 각 공지마다 kind(학생이 참여할 활동·기회면 activity, "
    "절차·방법·일정만 알리는 단순 행정 안내면 admin), tags(주어진 목록에서 1~3개), "
    "keywords(구체적 주제어 2~4개, 명사), summary(40자 이내 한 줄 요약)를 낸다."
)


def title_key(title: str) -> str:
    t = re.sub(r"\[(재공지|수정|연장|마감연장)[^\]]*\]|\((재공지|수정|연장)\)", "", title or "")
    t = re.sub(r"\s+", "", t).lower()
    return hashlib.sha1(t.encode()).hexdigest()


def _rule_pass(row) -> dict:
    text = f"{row['title']} {row['category'] or ''} {(row['content'] or '')[:400]}"
    tags = rule_tags(text)
    if row["category"] and row["category"] in TAXONOMY and row["category"] not in tags:
        tags.insert(0, row["category"])
    return {
        "tags": tags[:5],
        "keywords": keywords(row["title"]),
        "deadline": extract_deadline(row["title"], row["posted_at"]),
        "kind": classify_kind(row["title"], row["category"]),
    }


def _apply_cache(c, nid: int, hit) -> None:
    c.execute(
        "UPDATE notices SET tags=?, keywords=?, summary=?, kind=?, tagged_by=? WHERE id=?",
        (hit["tags"], hit["keywords"], hit["summary"] or "", hit["kind"], "cache:" + (hit["tagged_by"] or ""), nid),
    )


def tag_pending(use_llm: bool = True) -> dict:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM notices WHERE tagged_by IS NULL").fetchall()
    cache_hits, ambiguous = 0, []
    recent = (date.today() - timedelta(days=LLM_TAG_MAX_AGE_DAYS)).isoformat()
    for r in rows:
        key = title_key(r["title"])
        with get_conn() as c:
            hit = c.execute("SELECT * FROM tag_cache WHERE key=?", (key,)).fetchone()
            if hit:
                _apply_cache(c, r["id"], hit)
                c.execute("UPDATE notices SET deadline=? WHERE id=?",
                          (extract_deadline(r["title"], r["posted_at"]), r["id"]))
                cache_hits += 1
                continue
            res = _rule_pass(r)
            tags_j = json.dumps(res["tags"], ensure_ascii=False)
            kws_j = json.dumps(res["keywords"], ensure_ascii=False)
            c.execute(
                "UPDATE notices SET tags=?, keywords=?, deadline=?, kind=?, tagged_by='rule' WHERE id=?",
                (tags_j, kws_j, res["deadline"], res["kind"], r["id"]),
            )
            confident = len([t for t in res["tags"] if t != "모집"]) >= config.RULE_TAG_CONFIDENT
            if confident or res["kind"] == "admin":
                c.execute("INSERT OR IGNORE INTO tag_cache(key,tags,keywords,summary,kind,tagged_by) VALUES(?,?,?,?,?,?)",
                          (key, tags_j, kws_j, "", res["kind"], "rule"))
            elif (r["posted_at"] or "") >= recent:
                ambiguous.append(r)
    ambiguous = ambiguous[:LLM_TAG_MAX_PER_RUN]

    llm_tagged = 0
    if use_llm and ambiguous and config.llm_enabled():
        for i in range(0, len(ambiguous), BATCH):
            chunk = ambiguous[i: i + BATCH]
            lines = "\n".join(
                f"{r['id']}|{r['title']}|{(r['content'] or '')[:160].replace(chr(10), ' ')}" for r in chunk
            )
            out = llm.json_call("tagging", config.TAG_MODEL, _SYSTEM, "id|제목|본문일부\n" + lines, _SCHEMA,
                                max_tokens=4000, effort="low")
            if not out:
                continue
            ids = {r["id"]: r for r in chunk}
            with get_conn() as c:
                for it in out.get("items", []):
                    r = ids.get(it.get("id"))
                    cur = c.execute("SELECT tags, keywords FROM notices WHERE id=?", (it.get("id"),)).fetchone()
                    if not r or not cur:
                        continue
                    tags = [t for t in it.get("tags", []) if t in TAXONOMY]
                    tags = list(dict.fromkeys(json.loads(cur["tags"]) + tags))[:5]
                    kws = list(dict.fromkeys([k.lower() if k.isascii() else k for k in it.get("keywords", [])]
                                             + json.loads(cur["keywords"])))[:6]
                    kind = it.get("kind") if it.get("kind") in ("activity", "admin") else None
                    vals = (json.dumps(tags, ensure_ascii=False), json.dumps(kws, ensure_ascii=False),
                            (it.get("summary") or "")[:120])
                    c.execute("UPDATE notices SET tags=?, keywords=?, summary=?, kind=COALESCE(?, kind), "
                              "tagged_by='llm' WHERE id=?", (*vals, kind, r["id"]))
                    c.execute("INSERT OR REPLACE INTO tag_cache(key,tags,keywords,summary,kind,tagged_by) "
                              "VALUES(?,?,?,?,COALESCE(?, ?),'llm')",
                              (title_key(r["title"]), *vals, kind, classify_kind(r["title"], r["category"])))
                    llm_tagged += 1
    return {"new": len(rows), "from_cache": cache_hits,
            "sent_to_llm": len(ambiguous) if (use_llm and config.llm_enabled()) else 0, "llm_tagged": llm_tagged}


def backfill() -> dict:
    """기존 DB 보정 (토큰 0): 제외 게시판 글 삭제, 종류 미분류 공지 분류, 마감일 재계산, 미래 작성일 정리, 같은 공지 묶기."""
    from .grouping import assign_groups
    limit = (date.today() + timedelta(days=1)).isoformat()
    with get_conn() as c:
        excluded = 0
        if config.EXCLUDED_SOURCES:
            q = ",".join("?" * len(config.EXCLUDED_SOURCES))
            ex = list(config.EXCLUDED_SOURCES)
            c.execute(f"DELETE FROM bookmarks WHERE notice_id IN (SELECT id FROM notices WHERE source_id IN ({q}))", ex)
            excluded = c.execute(f"DELETE FROM notices WHERE source_id IN ({q})", ex).rowcount
        rows = c.execute("SELECT id,title,category,posted_at,kind,deadline FROM notices").fetchall()
        fixed_dates = c.execute(
            "UPDATE notices SET posted_at=NULL, content=NULL WHERE posted_at > ?", (limit,)).rowcount
        kinds = deadlines = 0
        for r in rows:
            posted = r["posted_at"] if (r["posted_at"] or "") <= limit else None
            dl = extract_deadline(r["title"], posted)
            if dl != r["deadline"]:
                c.execute("UPDATE notices SET deadline=? WHERE id=?", (dl, r["id"]))
                deadlines += 1
            if r["kind"] is None:
                c.execute("UPDATE notices SET kind=? WHERE id=?", (classify_kind(r["title"], r["category"]), r["id"]))
                kinds += 1
    groups = assign_groups()
    return {"excluded_removed": excluded, "future_posted_cleared": fixed_dates, "deadlines_recomputed": deadlines,
            "kinds_classified": kinds, "grouped": groups}
