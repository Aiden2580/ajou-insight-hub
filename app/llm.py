"""Claude API 호출 래퍼. 키가 없으면 None을 돌려주고 호출부가 규칙 기반으로 대체한다.
모든 호출의 토큰 사용량을 llm_usage 테이블에 기록해 비용을 화면에서 확인할 수 있게 한다."""
from __future__ import annotations

import json
import logging

from . import config
from .db import get_conn

log = logging.getLogger("llm")
_client = None


def client():
    global _client
    if not config.llm_enabled():
        return None
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def _log_usage(purpose: str, model: str, usage) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO llm_usage(purpose,model,input_tokens,output_tokens) VALUES(?,?,?,?)",
            (purpose, model, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0)),
        )


def json_call(purpose: str, model: str, system: str, user: str, schema: dict,
              max_tokens: int = 4000, effort: str = "low") -> dict | None:
    """구조화 출력(JSON schema)으로 한 번 호출."""
    cl = client()
    if cl is None:
        return None
    import anthropic
    try:
        resp = cl.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.RateLimitError as e:
        log.warning("rate limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("api error %s: %s", e.status_code, e)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("connection error: %s", e)
        return None
    _log_usage(purpose, model, resp.usage)
    if resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def text_call(purpose: str, model: str, system: str, user: str,
              max_tokens: int = 16000, effort: str = "medium") -> str | None:
    """긴 글(인사이트) 생성. 스트리밍 + 서버측 refusal fallback."""
    cl = client()
    if cl is None:
        return None
    import anthropic
    try:
        with cl.beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.RateLimitError as e:
        log.warning("rate limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("api error %s: %s", e.status_code, e)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("connection error: %s", e)
        return None
    _log_usage(purpose, model, resp.usage)
    if resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text") or None
