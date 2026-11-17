"""크롤러 공통 타입과 파서 레지스트리.

새 게시판 형식을 추가하려면:
  1. app/crawler/parsers.py 에 `@register("이름")` 으로 파서 클래스를 추가하고
  2. app/sources.json 에 {"parser": "이름", ...} 항목을 넣으면 끝.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

_PARSERS: dict[str, type["BoardParser"]] = {}


def register(name: str):
    def deco(cls):
        cls.name = name
        _PARSERS[name] = cls
        return cls
    return deco


def get_parser(name: str) -> type["BoardParser"]:
    if name not in _PARSERS:
        raise KeyError(f"알 수 없는 parser '{name}'. 등록된 parser: {list(_PARSERS)}")
    return _PARSERS[name]


def available_parsers() -> list[str]:
    return sorted(_PARSERS)


@dataclass
class NoticeItem:
    url: str
    title: str
    posted_at: str | None = None       # YYYY-MM-DD
    category: str | None = None
    author: str | None = None
    pinned: bool = False
    ext_key: str | None = None         # 여러 게시판에 같은 글이 올라오는 경우 중복 제거용
    extra: dict = field(default_factory=dict)


class BoardParser:
    """게시판 하나를 다루는 파서. source 설정(dict)을 받아 동작한다."""

    name = "base"

    def __init__(self, source: dict):
        self.source = source

    def list_urls(self) -> list[str]:
        """크롤링할 목록 페이지 URL들."""
        raise NotImplementedError

    def parse_list(self, html: str, page_url: str) -> list[NoticeItem]:
        raise NotImplementedError

    def parse_detail(self, html: str) -> str:
        """본문 텍스트. 실패하면 빈 문자열."""
        return ""

    def detail_date(self, html: str) -> str | None:
        """상세 페이지의 작성일 (있으면 목록 날짜보다 우선). 없으면 None."""
        return None

    def detail_from_list(self) -> bool:
        """True면 상세 페이지를 따로 가져오지 않는다 (RSS/API처럼 목록에 내용이 있는 경우)."""
        return False


_DATE_RE = re.compile(
    r"(?<!\d)(?:(20\d{2})|(\d{2}))\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\d)"
)


def normalize_date(text: str | None, not_after: date | None = None) -> str | None:
    """텍스트에서 '작성일로 그럴듯한' 첫 날짜를 YYYY-MM-DD로.

    - 2026-11-17 / 2026.11.17 / 26.11.17 / 2026년 11월 17일 지원
    - 미래 날짜(오늘+1일 초과)나 2000년 이전은 버리고 다음 후보를 본다
      (본문·제목 속 마감일 '~2027.1.22'를 작성일로 오인하는 문제 방지)
    """
    if not text:
        return None
    limit = not_after or (date.today() + timedelta(days=1))
    for m in _DATE_RE.finditer(text):
        y4, y2, mo, d = m.groups()
        y = int(y4) if y4 else 2000 + int(y2)
        try:
            dt = date(y, int(mo), int(d))
        except ValueError:
            continue
        if date(2000, 1, 1) <= dt <= limit:
            return dt.isoformat()
    return None


def clean_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()
