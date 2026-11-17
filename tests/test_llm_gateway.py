"""BAZE 게이트웨이 흉내 서버로 llm 래퍼의 재시도/파싱 경로를 검증한다 (네트워크 불필요)."""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CALLS = []


class FakeGateway(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        key = self.headers.get("x-api-key") or (self.headers.get("authorization") or "").removeprefix("Bearer ")
        CALLS.append({"path": self.path, "key": key, "body": body})
        if "output_config" in body:  # 게이트웨이가 output_config를 모른다고 가정 (x-api-key / Bearer 둘 다 허용됨)
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"invalid_request_error","message":"output_config: unknown"}}')
            return
        text = '```json\n{"items":[{"id":1,"tags":["AI/SW"],"keywords":["llm"],"summary":"s"}]}\n```'
        resp = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5}}
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())


def test_baze_json_call_retries_without_output_config():
    srv = HTTPServer(("127.0.0.1", 0), FakeGateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "t.db")
    from app import config, llm
    from app.db import init_db
    config.BAZE_API_KEY = "test-key"
    config.BAZE_BASE_URL = f"http://127.0.0.1:{srv.server_port}/v1/gateway/claude"
    config.LLM_PROVIDER = "baze"
    llm._client = None
    init_db()
    out = llm.json_call("tagging", "claude-sonnet-5-5", "sys", "user", {"type": "object"})
    srv.shutdown()
    llm._client = None
    config.LLM_PROVIDER = "none"
    assert out["items"][0]["tags"] == ["AI/SW"]
    assert [c["path"] for c in CALLS] == ["/v1/gateway/claude/v1/messages"] * 2
    assert CALLS[0]["key"] == "test-key" and "output_config" not in CALLS[1]["body"]
