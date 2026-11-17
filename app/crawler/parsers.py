"""게시판 형식별 파서.

- k2web   : 아주대 대표 홈페이지(www.ajou.ac.kr) 및 같은 CMS를 쓰는 학과/센터 게시판
            (목록: .b-title-box a, ?mode=list&article.offset=N)
- rss     : RSS 2.0 (아주대학보)
- pyxis   : 중앙도서관 Pyxis 공개 JSON API
- generic : 그 외 게시판(소프트웨어학과 자체 사이트, 그누보드5, 앵커사업단 등). 링크 정규식(link_pattern)으로 글을 찾고 같은 행에서 날짜를 추출.
            예) 소프트웨어학과 software.ajou.ac.kr/board/notice
"""
from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, urlunparse

from bs4 import BeautifulSoup

from .base import BoardParser, NoticeItem, clean_text, normalize_date, register


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _text_of(el) -> str:
    if el is None:
        return ""
    for bad in el.select("script, style, noscript"):
        bad.decompose()
    return clean_text(el.get_text(" "))


@register("k2web")
class K2WebParser(BoardParser):
    def list_urls(self) -> list[str]:
        base = self.source["url"]
        limit = int(self.source.get("page_size", 30))
        pages = int(self.source.get("pages", 1))
        return [
            f"{base}?mode=list&articleLimit={limit}&article.offset={p * limit}"
            for p in range(pages)
        ]

    def parse_list(self, html: str, page_url: str) -> list[NoticeItem]:
        soup = _soup(html)
        items = []
        for a in soup.select(".b-title-box a[href*='articleNo=']"):
            href = a.get("href", "")
            m = re.search(r"articleNo=(\d+)", href)
            if not m:
                continue
            # 페이징 파라미터를 제거한 정규 URL (중복 방지)
            url = f"{self.source['url']}?mode=view&articleNo={m.group(1)}"
            for n in a.select(".b-notice"):
                n.decompose()
            title = clean_text(a.get_text(" "))
            # 목록형(tr)과 갤러리형(li: 뉴스 게시판) 모두 지원
            row = a.find_parent("tr") or a.find_parent("li")
            scope = row or a.find_parent(class_="b-title-box")
            cate = scope.select_one(".b-cate") if scope else None
            writer = scope.select_one(".b-writer") if scope else None
            date_el = scope.select_one(".b-date") if scope else None
            tds = row.find_all("td", recursive=False) if row and row.name == "tr" else []
            posted = normalize_date(date_el.get_text() if date_el else "") or \
                normalize_date(tds[-1].get_text() if tds else "")
            pinned = bool(row and "b-top-box" in (row.get("class") or []))
            strip_label = lambda el: clean_text(el.get_text()).removeprefix("작성자").strip() if el else None
            items.append(NoticeItem(
                url=url, title=title, posted_at=posted,
                category=clean_text(cate.get_text()) if cate else None,
                author=strip_label(writer),
                pinned=pinned,
                ext_key=f"ajou:{m.group(1)}",  # articleNo는 아주대 CMS 전역 ID
            ))
        return items

    def parse_detail(self, html: str) -> str:
        soup = _soup(html)
        return _text_of(soup.select_one(".b-content-box") or soup.select_one(".fr-view"))

    def detail_date(self, html: str) -> str | None:
        soup = _soup(html)
        el = soup.select_one(".b-date-box") or soup.select_one(".b-main-box .b-date")
        return normalize_date(el.get_text(" ")) if el else None


def _short_date(text: str) -> str | None:
    """그누보드 목록의 'MM-DD' 형식 날짜. 연도는 오늘 기준으로 추정(미래면 작년)."""
    from datetime import date
    m = re.search(r"(?<![\d.:])(\d{1,2})-(\d{1,2})(?![\d:])", text)
    if not m:
        return None
    today = date.today()
    try:
        d = date(today.year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    if d > today:
        d = date(today.year - 1, d.month, d.day)
    return d.isoformat()


@register("generic")
class GenericParser(BoardParser):
    """설정만으로 대부분의 HTML 게시판을 다루기 위한 범용 파서.

    source 설정:
      link_pattern     : 글 링크 href 정규식 (필수)  예) "/board/notice/\\d+"
      page_param       : 페이지 쿼리 이름 (기본 "page")
      pages            : 몇 페이지까지 (기본 1)
      content_selector : 본문 CSS 셀렉터 (없으면 휴리스틱)
      row_selector     : 날짜를 찾을 상위 요소 (기본 "tr, li")
    """

    def list_urls(self) -> list[str]:
        base = self.source["url"]
        pages = int(self.source.get("pages", 1))
        param = self.source.get("page_param", "page")
        urls = [base]
        for p in range(2, pages + 1):
            u = urlparse(base)
            q = parse_qs(u.query)
            q[param] = [str(p)]
            urls.append(urlunparse(u._replace(query=urlencode(q, doseq=True))))
        return urls

    def parse_list(self, html: str, page_url: str) -> list[NoticeItem]:
        soup = _soup(html)
        pat = re.compile(self.source["link_pattern"])
        row_sel = self.source.get("row_selector", "tr, li")
        seen, items = set(), []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not pat.search(href):
                continue
            url = urljoin(page_url, href)
            # 목록 페이지 번호 같은 꼬리 쿼리 제거
            url = re.sub(r"\?&?page=\d+$", "", url).rstrip("?&")
            title = clean_text(a.get_text(" "))
            if not title or url in seen:
                continue
            seen.add(url)
            row = None
            for sel in row_sel.split(","):
                row = a.find_parent(sel.strip())
                if row:
                    break
            # 제목 안의 날짜(예: "~2027.1.22")를 작성일로 오인하지 않도록 제목을 빼고 찾는다
            row_text = (row.get_text(" ") if row else "").replace(a.get_text(" "), " ")
            first_cell = row.find(["td", "th"]) if row else None
            pinned = bool(row) and (
                (row.select_one(".tag01") is not None and "공지" in row.select_one(".tag01").get_text())
                or (first_cell is not None and clean_text(first_cell.get_text()) == "공지")
                or row.select_one(".notice_icon, .bo_notice, .fa-bullhorn") is not None
                or "bo_notice" in (row.get("class") or [])
            )
            posted = normalize_date(row_text) or _short_date(row_text)
            items.append(NoticeItem(url=url, title=title, posted_at=posted, pinned=pinned))
        return items

    def parse_detail(self, html: str) -> str:
        soup = _soup(html)
        sel = self.source.get("content_selector")
        if sel and soup.select_one(sel):
            return _text_of(soup.select_one(sel))
        for bad in soup.select("header, footer, nav, script, style, aside"):
            bad.decompose()
        # 휴리스틱: 텍스트가 가장 많은 블록(자식 블록 텍스트 비중이 낮은 것)
        best, best_len = None, 0
        for el in soup.find_all(["div", "td", "article", "section"]):
            t = el.get_text(" ", strip=True)
            if 80 < len(t) < 20000 and len(t) > best_len and len(el.find_all("a")) < 30:
                best, best_len = el, len(t)
        return _text_of(best)


@register("rss")
class RSSParser(BoardParser):
    def list_urls(self) -> list[str]:
        return [self.source["url"]]

    def parse_list(self, html: str, page_url: str) -> list[NoticeItem]:
        soup = BeautifulSoup(html, "xml")
        items = []
        for it in soup.find_all("item")[: int(self.source.get("max_items", 30))]:
            link = clean_text(it.link.get_text()) if it.link else ""
            if not link:
                continue
            items.append(NoticeItem(
                url=link, title=clean_text(it.title.get_text()) if it.title else link,
                posted_at=_rss_date(it.pubDate.get_text() if it.pubDate else ""),
                category=clean_text(it.category.get_text()) if it.category else None,
                author=clean_text(it.author.get_text()) if it.author else None,
                extra={"description": _text_of(BeautifulSoup(it.description.get_text(), "lxml")) if it.description else ""},
            ))
        return items

    def detail_from_list(self) -> bool:
        return True


def _rss_date(s: str) -> str | None:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        return normalize_date(s)


@register("pyxis")
class PyxisParser(BoardParser):
    """중앙도서관: data.list[].{id,title,dateCreated,writer,bulletinCategory}"""

    def list_urls(self) -> list[str]:
        return [self.source["url"]]

    def parse_list(self, html: str, page_url: str) -> list[NoticeItem]:
        import json
        data = json.loads(html).get("data", {})
        view = self.source.get("view_url", "https://library.ajou.ac.kr/guide/bulletins/notice/{id}")
        return [
            NoticeItem(
                url=view.format(id=b["id"]), title=clean_text(b.get("title")),
                posted_at=normalize_date(b.get("dateCreated")),
                category=(b.get("bulletinCategory") or {}).get("name"), author=b.get("writer"),
            )
            for b in data.get("list", [])
        ]

    def detail_from_list(self) -> bool:
        return True  # SPA라 본문 HTML이 없음 → 제목만 사용
