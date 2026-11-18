"""수집 파이프라인 실행기.

기본값은 수동 갱신: 사용자가 화면에서 '공지 새로고침'을 누를 때만 run_pipeline이 돈다.
AUTO_CRAWL=1 / AUTO_INSIGHT=1 이면 백그라운드 스레드가 주기 실행한다.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .grouping import assign_groups
from . import config, insight, tagger
from .crawler.runner import crawl_all
from .db import get_conn, meta_get, meta_set

log = logging.getLogger("scheduler")
_state = {"running": False, "started_at": None, "last_result": None}
_run_lock = threading.Lock()


def run_pipeline() -> dict:
    if not _run_lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        _state["running"] = True
        _state["started_at"] = datetime.now(insight.KST).isoformat(timespec="seconds")
        crawl = crawl_all()
        groups = assign_groups()
        tagging = tagger.tag_pending()
        res = {"ok": crawl["ok"], "failed": crawl["failed"], "new": crawl["new"],
               "details_fetched": crawl["details_fetched"], "tagging": tagging, "grouping": groups}
        _state["last_result"] = res
        meta_set("last_crawl", datetime.now(insight.KST).isoformat(timespec="seconds"))
        return res
    except Exception as e:
        log.exception("pipeline error")
        _state["last_result"] = {"error": str(e)}
        return _state["last_result"]
    finally:
        _state["running"] = False
        _run_lock.release()


def start_background_refresh() -> bool:
    if _state["running"]:
        return False
    threading.Thread(target=run_pipeline, daemon=True, name="refresh").start()
    return True


def run_daily_insights() -> int:
    with get_conn() as c:
        users = [r[0] for r in c.execute("SELECT DISTINCT user_id FROM interest_nodes")]
    for u in users:
        try:
            insight.generate(u)  # 오늘 것이 있으면 재사용 (토큰 0)
        except Exception as e:
            log.warning("insight fail %s: %s", u, e)
    return len(users)


def _loop():
    next_crawl = time.time() + 60  # 서버 시작 직후 바로 긁지 않는다
    while True:
        now = time.time()
        if config.AUTO_CRAWL and now >= next_crawl:
            run_pipeline()
            next_crawl = now + config.CRAWL_INTERVAL_MIN * 60
        if config.AUTO_INSIGHT:
            kst = datetime.now(insight.KST)
            if kst.hour >= config.INSIGHT_HOUR_KST and meta_get("last_auto_insight") != kst.date().isoformat():
                meta_set("last_auto_insight", kst.date().isoformat())  # 재시작해도 하루 1번
                run_daily_insights()
        time.sleep(30)


def start():
    threading.Thread(target=_loop, daemon=True, name="scheduler").start()


def status() -> dict:
    return {**_state, "last_crawl": meta_get("last_crawl"), "auto_crawl": config.AUTO_CRAWL}
