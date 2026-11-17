"""간단한 백그라운드 스케줄러 (별도 인프라 없이 단일 프로세스로 배포 가능하게)."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from . import config, insight, tagger
from .crawler.runner import crawl_all
from .db import get_conn

log = logging.getLogger("scheduler")
_state = {"running": False, "last_crawl": None, "last_result": None}
_run_lock = threading.Lock()


def run_pipeline() -> dict:
    if not _run_lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        _state["running"] = True
        res = {"crawl": crawl_all(), "tagging": tagger.tag_pending()}
        _state["last_crawl"] = datetime.now().isoformat(timespec="seconds")
        _state["last_result"] = res
        return res
    finally:
        _state["running"] = False
        _run_lock.release()


def run_daily_insights() -> int:
    with get_conn() as c:
        users = [r[0] for r in c.execute("SELECT DISTINCT user_id FROM interest_nodes")]
    for u in users:
        try:
            insight.generate(u)
        except Exception as e:  # 한 사용자 실패가 전체를 막지 않게
            log.warning("insight fail %s: %s", u, e)
    return len(users)


def _loop():
    last_insight_day = None
    next_crawl = 0.0
    while True:
        now = time.time()
        if now >= next_crawl:
            try:
                run_pipeline()
            except Exception as e:
                log.exception("pipeline error: %s", e)
            next_crawl = now + config.CRAWL_INTERVAL_MIN * 60
        kst = datetime.now(insight.KST)
        if kst.hour >= config.INSIGHT_HOUR_KST and last_insight_day != kst.date():
            run_daily_insights()
            last_insight_day = kst.date()
        time.sleep(30)


def start():
    threading.Thread(target=_loop, daemon=True, name="scheduler").start()


def status() -> dict:
    return dict(_state)
