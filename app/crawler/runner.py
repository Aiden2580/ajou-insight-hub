"""소스 목록(sources.json)을 돌며 목록/본문을 가져와 DB에 저장한다.

1단계: 모든 소스의 목록 페이지 → 새 공지 저장 (ext_key/url로 중복 제거)
2단계: 본문이 없는 최신 공지부터 전역 상한(MAX_DETAIL_FETCH_PER_RUN)만큼 상세 페이지 수집
       → 첫 실행에 수천 건 상세를 한 번에 긁지 않고 여러 주기에 나눠 서버 부담을 줄인다.
"""
from __future__ import annotations

import json
import logging
import time

import requests

from .. import config
from ..db import get_conn
from . import parsers  # noqa: F401  (파서 등록)
from .base import get_parser

log = logging.getLogger("crawler")


def load_sources() -> list[dict]:
    with open(config.SOURCES_PATH, encoding="utf-8") as f:
        return json.load(f)["sources"]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    s.headers["Accept-Language"] = "ko-KR,ko;q=0.9"
    return s


def _fetch(sess: requests.Session, url: str) -> str:
    r = sess.get(url, timeout=20)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    time.sleep(config.REQUEST_DELAY_SEC)
    return r.text


def save_items(source_id: str, items, content_from_list: bool = False) -> list[int]:
    """새로 들어온 공지의 id 목록을 돌려준다."""
    new_ids = []
    with get_conn() as c:
        for it in items:
            content = it.extra.get("description", "") if content_from_list else None
            cur = c.execute(
                """INSERT OR IGNORE INTO notices(source_id,url,ext_key,title,category,author,posted_at,pinned,content)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (source_id, it.url, it.ext_key, it.title, it.category, it.author, it.posted_at,
                 int(it.pinned), content),
            )
            if cur.rowcount:
                new_ids.append(cur.lastrowid)
            else:  # 고정 여부/제목 변경 반영
                c.execute("UPDATE notices SET pinned=?, title=? WHERE url=?", (int(it.pinned), it.title, it.url))
    return new_ids


def crawl_source(source: dict, sess: requests.Session) -> dict:
    parser = get_parser(source["parser"])(source)
    found, new, err = 0, 0, None
    try:
        if source.get("warmup_url"):  # 세션 쿠키가 있어야 목록을 주는 사이트(그누보드 사업단)
            _fetch(sess, source["warmup_url"])
        for url in parser.list_urls():
            items = parser.parse_list(_fetch(sess, url), url)
            found += len(items)
            new += len(save_items(source["id"], items, parser.detail_from_list()))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        log.warning("crawl fail %s: %s", source["id"], err)
    with get_conn() as c:
        c.execute(
            "INSERT INTO crawl_log(source_id,ok,found,new,message) VALUES(?,?,?,?,?)",
            (source["id"], int(err is None), found, new, err or ""),
        )
    return {"source": source["id"], "ok": err is None, "found": found, "new": new, "error": err}


def fetch_details(sources: list[dict], sess: requests.Session, limit: int) -> int:
    by_id = {s["id"]: get_parser(s["parser"])(s) for s in sources}
    with get_conn() as c:
        rows = c.execute(
            "SELECT id,url,source_id FROM notices WHERE content IS NULL ORDER BY posted_at DESC LIMIT ?", (limit,)
        ).fetchall()
    done = 0
    for r in rows:
        p = by_id.get(r["source_id"])
        content = ""
        if p and not p.detail_from_list():
            try:
                html = _fetch(sess, r["url"])
                content = p.parse_detail(html)[:8000]
                real = p.detail_date(html)
                if real:  # 상세 페이지 작성일로 바로잡기 (목록 날짜와 다를 때)
                    with get_conn() as c:
                        c.execute("UPDATE notices SET posted_at=? WHERE id=? AND (posted_at IS NULL OR posted_at<>?)",
                                  (real, r["id"], real))
                done += 1
            except Exception as e:  # 본문 실패는 치명적이지 않음 → 빈 문자열로 표시해 재시도 안 함
                log.warning("detail fail %s: %s", r["url"], e)
        with get_conn() as c:
            c.execute("UPDATE notices SET content=? WHERE id=?", (content, r["id"]))
    return done


def crawl_all(only: list[str] | None = None, detail_limit: int | None = None) -> dict:
    sess = _session()
    sources = [s for s in load_sources() if s.get("enabled", True) and (not only or s["id"] in only)]
    results = [crawl_source(s, sess) for s in sources]
    details = fetch_details(sources, sess, detail_limit or config.MAX_DETAIL_FETCH_PER_RUN)
    return {"sources": results, "details_fetched": details,
            "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
            "new": sum(r["new"] for r in results)}
