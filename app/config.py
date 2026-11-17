"""환경 변수 기반 설정. 모든 값은 .env 또는 환경 변수로 덮어쓸 수 있다."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# .env 파일이 있으면 읽는다 (이미 설정된 환경 변수가 우선)
_env = BASE_DIR / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            if _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip())

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "hub.db"))
SOURCES_PATH = os.getenv("SOURCES_PATH", str(BASE_DIR / "app" / "sources.json"))

# ---- LLM 연결 ----
# 1) BAZE_API_KEY 가 있으면 아주LLM(BAZE API Gateway)의 Claude 엔드포인트를 쓴다 (우선).
# 2) 없으면 ANTHROPIC_API_KEY 로 Anthropic API를 직접 쓴다.
# 3) 둘 다 없으면 규칙 기반 태깅 + 템플릿 인사이트로 동작한다 (비용 0).
BAZE_API_KEY = os.getenv("BAZE_API_KEY", "")
BAZE_BASE_URL = os.getenv("BAZE_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway/claude")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_PROVIDER = "baze" if BAZE_API_KEY else ("anthropic" if ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_AUTH_TOKEN") else "none")

# 핵심 가치 = 저비용. 태깅은 대량 작업이라 Sonnet(낮은 effort), 인사이트는 하루 1회라 Opus.
TAG_MODEL = os.getenv("TAG_MODEL", "claude-sonnet-5-5")
INSIGHT_MODEL = os.getenv("INSIGHT_MODEL", "claude-opus-5-5")
# 규칙 기반 태거가 이 개수 이상 태그를 찾으면 LLM 태깅을 생략한다 (토큰 절약)
RULE_TAG_CONFIDENT = int(os.getenv("RULE_TAG_CONFIDENT", "2"))

# 갱신 정책: 기본은 사용자가 '공지 새로고침'을 누를 때만 수집한다 (토큰·서버 부담 최소화).
AUTO_CRAWL = os.getenv("AUTO_CRAWL", "0") == "1"           # 1이면 CRAWL_INTERVAL_MIN마다 자동 수집
CRAWL_INTERVAL_MIN = int(os.getenv("CRAWL_INTERVAL_MIN", "180"))
# 인사이트는 사용자가 인사이트 탭을 열 때 하루 1번만 생성한다. 1이면 매일 INSIGHT_HOUR_KST시에 미리 생성.
AUTO_INSIGHT = os.getenv("AUTO_INSIGHT", "0") == "1"
INSIGHT_HOUR_KST = int(os.getenv("INSIGHT_HOUR_KST", "7"))
ENABLE_SCHEDULER = AUTO_CRAWL or AUTO_INSIGHT

# 크롤러 예절
USER_AGENT = os.getenv(
    "CRAWLER_USER_AGENT",
    "AjouNoticeHub/0.1 (+student prototype; polite crawler)",
)
REQUEST_DELAY_SEC = float(os.getenv("REQUEST_DELAY_SEC", "1.0"))
MAX_DETAIL_FETCH_PER_RUN = int(os.getenv("MAX_DETAIL_FETCH_PER_RUN", "150"))


def llm_enabled() -> bool:
    return LLM_PROVIDER != "none"
