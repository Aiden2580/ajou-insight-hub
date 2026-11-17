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

# Claude API (없으면 규칙 기반 태깅/인사이트로 자동 대체)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# 핵심 가치 = 저비용. 태깅은 대량·단순 작업이라 가장 저렴한 모델, 인사이트는 하루 1회라 고품질 모델.
TAG_MODEL = os.getenv("TAG_MODEL", "claude-haiku-5-5")
INSIGHT_MODEL = os.getenv("INSIGHT_MODEL", "claude-opus-5-5")
# 규칙 기반 태거가 이 개수 이상 태그를 찾으면 LLM 태깅을 생략한다 (토큰 절약)
RULE_TAG_CONFIDENT = int(os.getenv("RULE_TAG_CONFIDENT", "2"))

# 스케줄러
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1") == "1"
CRAWL_INTERVAL_MIN = int(os.getenv("CRAWL_INTERVAL_MIN", "60"))
INSIGHT_HOUR_KST = int(os.getenv("INSIGHT_HOUR_KST", "7"))

# 크롤러 예절
USER_AGENT = os.getenv(
    "CRAWLER_USER_AGENT",
    "AjouNoticeHub/0.1 (+student prototype; polite crawler)",
)
REQUEST_DELAY_SEC = float(os.getenv("REQUEST_DELAY_SEC", "1.0"))
MAX_DETAIL_FETCH_PER_RUN = int(os.getenv("MAX_DETAIL_FETCH_PER_RUN", "150"))


def llm_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_AUTH_TOKEN"))
