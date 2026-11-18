"""오늘의 칼럼 + 더 알아보기: 주제 순환, 출처 첨부, 캐시, 웹 검색 미지원 게이트웨이 대응."""
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "t.db"))
os.environ["ENABLE_SCHEDULER"] = "0"

from app import config, insight, llm  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.interest import add_signals  # noqa: E402

U = "column-user-0001"


def _seed():
    init_db()
    with get_conn() as c:
        c.execute("DELETE FROM insights WHERE user_id=?", (U,))
    add_signals(U, "keyword", ["알고리즘", "인공지능", "그래프 이론", "창업", "로보틱스"])


def test_topic_rotates_and_avoids_recent():
    _seed()
    d = insight.today_kst()
    first = insight.choose_topic(U, d)["topic"]
    yday = (datetime.fromisoformat(d) - timedelta(days=1)).date().isoformat()
    with get_conn() as c:
        c.execute("INSERT INTO insights(user_id,date,title,content_md,generated_by,topic) VALUES(?,?,?,?,?,?)",
                  (U, yday, "t", "x", "m", first))
    seed = insight.choose_topic(U, d)
    assert seed["topic"] != first and first in seed["recent"]


def test_column_and_deep_dive_use_search_and_cache(monkeypatch):
    _seed()
    calls = []

    def fake_research(purpose, model, system, user, **kw):
        calls.append(purpose)
        body = "# 제목\n\n> 리드\n\n본문 [기사](https://news.example/a)\n\n## 오늘의 질문\n- 질문"
        return {"text": body, "searched": True,
                "sources": [{"url": "https://news.example/a", "title": "기사"},
                            {"url": "https://arxiv.org/abs/2601.00001", "title": "논문"}]}

    monkeypatch.setattr(config, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "research_call", fake_research)
    ins = insight.generate(U)
    assert "웹 검색" in ins["generated_by"] and ins["topic"]
    # 본문에 이미 있는 링크는 다시 붙이지 않고, 나머지 출처만 '참고한 자료'에
    assert ins["content_md"].count("https://news.example/a") == 1 and "arxiv.org/abs/2601.00001" in ins["content_md"]
    assert insight.generate(U)["content_md"] == ins["content_md"] and calls == ["insight"]  # 하루 한 편
    deep = insight.deep_dive(U)
    assert deep["deep_md"].startswith("# 제목") and calls == ["insight", "insight_deep"]
    assert insight.deep_dive(U)["deep_md"] == deep["deep_md"] and len(calls) == 2  # 저장된 것 재사용


def test_template_has_no_made_up_news():
    _seed()
    ins = insight.generate(U, force=True)
    assert ins["generated_by"] == "template" and "scholar.google.com" in ins["content_md"]
    deep = insight.deep_dive(U)
    assert deep["deep_by"] == "template" and "arxiv.org/search" in deep["deep_md"]


BODIES = []


class NoSearchGateway(BaseHTTPRequestHandler):
    """웹 검색 도구를 모르는 게이트웨이: tools가 있으면 400, 없으면 SSE로 짧은 글을 스트리밍."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        BODIES.append(body)
        if "tools" in body:
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"invalid_request_error","message":"tools: unsupported"}}')
            return
        ev = [
            ("message_start", {"type": "message_start", "message": {
                "id": "m1", "type": "message", "role": "assistant", "model": body["model"], "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "# 칼럼\n\n본문"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 4}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for name, data in ev:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())


def test_research_call_falls_back_when_gateway_has_no_web_search():
    srv = HTTPServer(("127.0.0.1", 0), NoSearchGateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = (config.BAZE_API_KEY, config.BAZE_BASE_URL, config.LLM_PROVIDER)
    try:
        init_db()
        config.BAZE_API_KEY, config.LLM_PROVIDER = "test-key", "baze"
        config.BAZE_BASE_URL = f"http://127.0.0.1:{srv.server_port}/v1/gateway/claude"
        llm._client = None
        out = llm.research_call("insight", "claude-opus-5-5", "sys", "user")
    finally:
        srv.shutdown()
        config.BAZE_API_KEY, config.BAZE_BASE_URL, config.LLM_PROVIDER = old
        llm._client = None
    assert out == {"text": "# 칼럼\n\n본문", "sources": [], "searched": False}
    assert "tools" in BODIES[0] and BODIES[0]["tools"][0]["type"] == llm.WEB_SEARCH_TOOL
    assert "tools" not in BODIES[-1] and "지어내지" in json.dumps(BODIES[-1]["system"], ensure_ascii=False)
