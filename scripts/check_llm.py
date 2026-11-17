"""LLM 연결 확인. 키는 .env 또는 환경 변수에서 읽는다 (화면/채팅에 붙여 넣을 필요 없음).

    python -m scripts.check_llm
"""
import json

import requests

from app import config, llm
from app.db import init_db


def main():
    init_db()
    print(f"provider: {config.LLM_PROVIDER}")
    if config.LLM_PROVIDER == "baze":
        gw = config.BAZE_BASE_URL.rsplit("/claude", 1)[0]
        r = requests.get(f"{gw}/models/", headers={"x-api-key": config.BAZE_API_KEY}, timeout=20)
        print(f"GET {gw}/models/ -> {r.status_code}")
        if r.ok:
            data = r.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            ids = [m.get("id") if isinstance(m, dict) else m for m in items]
            claude = [i for i in ids if i and "claude" in str(i)]
            print("Claude 모델:", ", ".join(claude) or "(없음)")
            for role, want, family in (("TAG_MODEL", config.TAG_MODEL, "haiku"), ("INSIGHT_MODEL", config.INSIGHT_MODEL, "opus")):
                if want in ids:
                    print(f"  {role}={want}: 목록에 있음")
                else:
                    alt = [i for i in claude if family in str(i).lower()]
                    print(f"  {role}={want}: 목록에 없음 → .env 의 {role}를 다음 중 하나로 바꾸세요: {', '.join(alt) or '(같은 계열 없음)'}")
        elif r.status_code == 401:
            print("401: 키가 잘못됐거나 다른 BAZE 배포에서 발급된 키입니다.")
    print(json.dumps(llm.check(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
