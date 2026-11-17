"""공지 태깅.

비용 전략:
  1) 모든 공지는 먼저 규칙 기반(무료)으로 태그/키워드/마감일을 붙인다.
  2) 규칙 태그가 부족한(애매한) 공지만 모아서 한 번의 배치 호출로 LLM 태깅한다.
     - 입력은 제목 + 본문 앞 160자만 (토큰 최소화)
     - 태그는 고정 체계(enum)에서만 고르게 해서 출력도 짧다.
  3) 공지 태깅 결과는 모든 사용자가 공유한다 (사용자 수와 무관한 고정 비용).
"""
from __future__ import annotations

import json

from . import config, llm
from .db import get_conn
from .taxonomy import TAXONOMY, extract_deadline, keywords, rule_tags

BATCH = 25

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string", "enum": list(TAXONOMY)}},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
                "required": ["id", "tags", "keywords", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_SYSTEM = (
    "아주대학교 공지를 분류한다. 각 공지마다 tags(주어진 목록에서 1~3개), "
    "keywords(구체적 주제어 2~4개, 명사), summary(40자 이내 한 줄 요약)를 낸다."
)


def _rule_pass(row) -> dict:
    text = f"{row['title']} {row['category'] or ''} {(row['content'] or '')[:400]}"
    tags = rule_tags(text)
    if row["category"] and row["category"] in TAXONOMY and row["category"] not in tags:
        tags.insert(0, row["category"])
    return {
        "tags": tags[:5],
        "keywords": keywords(row["title"]),
        "deadline": extract_deadline(row["title"], row["posted_at"]),
    }


def tag_pending(use_llm: bool = True) -> dict:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM notices WHERE tagged_by IS NULL").fetchall()
    ambiguous = []
    for r in rows:
        res = _rule_pass(r)
        with get_conn() as c:
            c.execute(
                "UPDATE notices SET tags=?, keywords=?, deadline=?, tagged_by='rule' WHERE id=?",
                (json.dumps(res["tags"], ensure_ascii=False), json.dumps(res["keywords"], ensure_ascii=False),
                 res["deadline"], r["id"]),
            )
        if len(res["tags"]) < config.RULE_TAG_CONFIDENT:
            ambiguous.append(r)

    llm_tagged = 0
    if use_llm and ambiguous and config.llm_enabled():
        for i in range(0, len(ambiguous), BATCH):
            chunk = ambiguous[i : i + BATCH]
            lines = "\n".join(
                f"{r['id']}|{r['title']}|{(r['content'] or '')[:160].replace(chr(10), ' ')}" for r in chunk
            )
            out = llm.json_call("tagging", config.TAG_MODEL, _SYSTEM, "id|제목|본문일부\n" + lines, _SCHEMA,
                                max_tokens=4000, effort="low")
            if not out:
                continue
            with get_conn() as c:
                for it in out.get("items", []):
                    cur = c.execute("SELECT tags, keywords FROM notices WHERE id=?", (it["id"],)).fetchone()
                    if not cur:
                        continue
                    tags = list(dict.fromkeys(json.loads(cur["tags"]) + it["tags"]))[:5]
                    kws = list(dict.fromkeys([k.lower() if k.isascii() else k for k in it["keywords"]]
                                             + json.loads(cur["keywords"])))[:6]
                    c.execute(
                        "UPDATE notices SET tags=?, keywords=?, summary=?, tagged_by='llm' WHERE id=?",
                        (json.dumps(tags, ensure_ascii=False), json.dumps(kws, ensure_ascii=False),
                         it["summary"][:120], it["id"]),
                    )
                    llm_tagged += 1
    return {"rule_tagged": len(rows), "sent_to_llm": len(ambiguous) if config.llm_enabled() else 0,
            "llm_tagged": llm_tagged}
