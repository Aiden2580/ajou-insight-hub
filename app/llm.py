"""Claude 호출 래퍼.

- BAZE_API_KEY 가 있으면 아주LLM(BAZE API Gateway)의 Claude 호환 엔드포인트로,
  없으면 ANTHROPIC_API_KEY 로 Anthropic API에 직접 보낸다. 둘 다 없으면 None을 돌려주고
  호출부가 규칙 기반/템플릿으로 대체한다.
- 게이트웨이가 일부 최신 파라미터(구조화 출력, effort, 서버측 fallback)를 지원하지 않을 수 있어서
  400이 나면 해당 파라미터를 빼고 한 번 더 시도한다.
- 모든 호출의 토큰 사용량을 llm_usage 테이블에 기록한다.
"""
from __future__ import annotations

import json
import logging
import re

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
        if config.LLM_PROVIDER == "baze":
            # SDK가 {base_url}/v1/messages 로 키를 실어 호출한다 (게이트웨이는 Bearer/x-api-key 모두 허용).
            _client = anthropic.Anthropic(api_key=config.BAZE_API_KEY, base_url=config.BAZE_BASE_URL)
        else:
            _client = anthropic.Anthropic()
    return _client


def _log_usage(purpose: str, model: str, usage) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO llm_usage(purpose,model,input_tokens,output_tokens) VALUES(?,?,?,?)",
            (purpose, model, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0)),
        )


def _extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)  # 코드블록 등으로 감싸진 경우
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _create(cl, purpose: str, model: str, **kw):
    """messages.create. 게이트웨이가 output_config를 거부하면 빼고 재시도."""
    import anthropic
    try:
        try:
            resp = cl.messages.create(model=model, **kw)
        except anthropic.BadRequestError as e:
            if "output_config" not in kw:
                raise
            log.info("output_config 거부됨 → 빼고 재시도: %s", e)
            kw = {k: v for k, v in kw.items() if k != "output_config"}
            resp = cl.messages.create(model=model, **kw)
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
    return resp


def json_call(purpose: str, model: str, system: str, user: str, schema: dict,
              max_tokens: int = 4000, effort: str = "low") -> dict | None:
    """구조화 출력(JSON schema)으로 한 번 호출. 지원되지 않으면 프롬프트로 JSON을 요구한다."""
    cl = client()
    if cl is None:
        return None
    system = system + "\n반드시 다음 JSON 스키마를 따르는 JSON 객체 하나만 출력한다:\n" + json.dumps(schema, ensure_ascii=False)
    resp = _create(
        cl, purpose, model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if resp is None:
        return None
    return _extract_json(next((b.text for b in resp.content if b.type == "text"), None))


def text_call(purpose: str, model: str, system: str, user: str,
              max_tokens: int = 8000, effort: str = "medium") -> str | None:
    """긴 글(인사이트) 생성."""
    cl = client()
    if cl is None:
        return None
    import anthropic
    kw = dict(model=model, max_tokens=max_tokens, system=system,
              messages=[{"role": "user", "content": user}], output_config={"effort": effort})
    try:
        if config.LLM_PROVIDER == "anthropic":
            # Anthropic 직접 연결일 때만 서버측 refusal fallback(beta) 사용
            with cl.beta.messages.stream(**kw, betas=["server-side-fallback-2026-07-01"], fallbacks="default") as s:
                resp = s.get_final_message()
        else:
            try:
                with cl.messages.stream(**kw) as s:
                    resp = s.get_final_message()
            except anthropic.BadRequestError as e:
                log.info("output_config 거부됨 → 빼고 재시도: %s", e)
                kw.pop("output_config")
                with cl.messages.stream(**kw) as s:
                    resp = s.get_final_message()
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


WEB_SEARCH_TOOL = "web_search_20260209"


def research_call(purpose: str, model: str, system: str, user: str, max_uses: int = 4,
                  max_tokens: int = 12000, effort: str = "medium") -> dict | None:
    """웹 검색(서버 도구)을 켜고 글을 쓴다. 최신 소식·논문은 실제 검색 결과에서만 인용하게 하기 위함.

    돌려주는 값: {"text", "sources": [{url,title}], "searched": bool}
    게이트웨이가 웹 검색 도구를 지원하지 않으면(400) 검색 없이 쓰고 searched=False 로 알려 준다.
    """
    cl = client()
    if cl is None:
        return None
    import anthropic
    tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_uses}]
    messages = [{"role": "user", "content": user}]
    base = dict(model=model, max_tokens=max_tokens, system=system, output_config={"effort": effort})
    texts, cited, seen, searched = [], {}, {}, True
    try:
        for _ in range(3):  # 서버 도구가 길어지면 pause_turn → 이어서 다시 보낸다
            try:
                with cl.messages.stream(**base, tools=tools, messages=messages) as s:
                    resp = s.get_final_message()
            except anthropic.BadRequestError as e:
                if not searched:
                    raise
                log.info("웹 검색 도구 거부됨 → 검색 없이 작성: %s", e)
                searched = False
                text = text_call(purpose, model, system + "\n(이번에는 웹 검색을 쓸 수 없다. 특정 최신 소식·날짜·수치를 "
                                 "지어내지 말고, 잘 알려진 사실과 개념 위주로 쓴다. 링크는 넣지 않는다.)", user,
                                 max_tokens=max_tokens, effort=effort)
                return {"text": text, "sources": [], "searched": False} if text else None
            _log_usage(purpose, model, resp.usage)
            if resp.stop_reason == "refusal":
                return None
            for b in resp.content:
                if b.type == "text":
                    texts.append(b.text)
                    for ct in getattr(b, "citations", None) or []:
                        url = getattr(ct, "url", None)
                        if url:
                            cited.setdefault(url, getattr(ct, "title", None) or url)
                elif b.type == "web_search_tool_result" and isinstance(b.content, list):
                    for r in b.content:
                        url = getattr(r, "url", None)
                        if url:
                            seen.setdefault(url, getattr(r, "title", None) or url)
            if resp.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": resp.content}]
    except anthropic.RateLimitError as e:
        log.warning("rate limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("api error %s: %s", e.status_code, e)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("connection error: %s", e)
        return None
    text = "".join(texts).strip()
    if not text:
        return None
    # 본문에서 실제로 인용한 출처 우선, 없으면 검색해서 읽은 결과 일부
    src = cited or dict(list(seen.items())[:6])
    return {"text": text, "sources": [{"url": u, "title": t} for u, t in list(src.items())[:12]], "searched": True}


def check() -> dict:
    """연결 확인용: 모델 목록 + 아주 짧은 호출 1회."""
    cl = client()
    if cl is None:
        return {"provider": "none", "ok": False, "message": "BAZE_API_KEY 또는 ANTHROPIC_API_KEY가 없습니다."}
    out = {"provider": config.LLM_PROVIDER, "models": {"tagging": config.TAG_MODEL, "insight": config.INSIGHT_MODEL}}
    for role, model in out["models"].items():
        resp = _create(cl, "check", model, max_tokens=64,
                       messages=[{"role": "user", "content": "'ok' 한 단어로만 답해."}],
                       output_config={"effort": "low"})
        out[role] = ("ok: " + "".join(b.text for b in resp.content if b.type == "text").strip()[:20]) if resp else "실패 (로그 확인)"
    out["ok"] = all(str(out[r]).startswith("ok") for r in ("tagging", "insight"))
    return out
