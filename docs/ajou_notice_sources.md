# 아주대 공지 소스 조사 결과 (2026-11-17)

데이터 파일: `ajou_notice_sources.csv` (표), `ajou_notice_sources.json` (크롤러용, 플랫폼별 셀렉터 포함)

## 요약

- **조사 범위**: 94개 기관(본부, 단과대 12, 학과/학부 약 50, 대학원 11, 사업단 7, 센터 약 25), 게시판 120개
- **공식 API**: 학교 차원의 공지 API나 RSS는 **없음**. 예외는 중앙도서관(공개 JSON API)과 학보사(RSS) 두 곳
- **크롤링**: 120개 중 **117개가 로그인 없이 정적 HTML(또는 JSON/RSS)로 바로 수집 가능**. 헤드리스 브라우저 없이 `requests + BeautifulSoup` 수준으로 충분
- **핵심 발견**: 학과·단과대·대학원·센터 대부분(104개 게시판)이 **아주대 공통 CMS 하나**를 씀. 모든 사이트가 `https://www.ajou.ac.kr/{사이트코드}/...` 경로로도 열리므로 **크롤러 하나 + URL 목록**으로 커버됨

## 플랫폼별 정리

| 플랫폼 | 게시판 수 | 방식 | 난이도 | 비고 |
|---|---|---|---|---|
| 아주대 공통 CMS (`*.do`) | 104 | 정적 HTML | 쉬움 | `?mode=list&articleLimit=50&article.offset=0` 페이지네이션, 상세 `?mode=view&articleNo=ID` |
| 소프트웨어학과 자체 사이트 | 3 | 정적 HTML | 쉬움 | `/board/notice/{id}` |
| 대학일자리플러스센터 (ASP.NET) | 3 | 정적 HTML | 보통 | 공지 / **비교과 프로그램** / 교내채용. 상세 링크는 onclick에서 추출 |
| 그누보드5 (미래자동차·데이터보안 사업단) | 2 | 정적 HTML | 쉬움 | `bo_table=04_01`, RSS는 꺼져 있음 |
| 앵커사업단 | 1 | 정적 HTML | 쉬움 | 현장실습·PBL·창업 공지 |
| G-램프 사업단 (Nuxt SSR) | 1 | 정적 HTML | 쉬움 | |
| 중앙도서관 (Pyxis) | 1 | **공개 JSON API** | 쉬움 | `/pyxis-api/1/bulletin-boards/1/bulletins?offset=0&max=20` |
| 학보사 | 1 | **RSS** | 쉬움 | `cdn.press.ajou.ac.kr/rss/gns_allArticle.xml` |
| 입학처 | 1 | 정적 HTML | 쉬움 | 재학생 서비스엔 우선순위 낮음 |
| 교수학습개발센터 | 1 | AJAX | 어려움 | 헤드리스 브라우저 필요 추정 |
| 현장실습지원센터 | 1 | 로그인/세션 | 어려움 | 앵커사업단 공지로 대체 가능 |
| 창업지원단 | 1 | - | 불가 | 현재 403, 홈페이지 개편 중 |

## 공통 CMS 크롤링 방법

- 목록: `https://www.ajou.ac.kr/me/board/under-notice.do?mode=list&articleLimit=50&article.offset=0`
- 행: `table.board-table tbody tr` (상단 고정 공지는 `tr.b-top-box`)
- 제목/링크 `.b-title-box a`, 분류 `.b-cate`, 작성부서 `.b-writer`, 날짜 `.b-date`
- 상세 본문: `.b-main-box .b-content-box`, 첨부 `.b-file-box a` (`?mode=download&articleNo=..&attachNo=..`)
- **중복 제거**: `articleNo`가 전 사이트 공통 ID라, 본부 공지가 학과 게시판에도 그대로 노출됨 → `articleNo`로 dedup
- 주의: 상단 고정 공지의 날짜 칸에 게시 종료일(미래 날짜)이 찍히는 경우가 있음 → 정렬은 `articleNo` 기준이 안전

## robots.txt / 매너

- `www.ajou.ac.kr/robots.txt`: 일반 UA는 `Allow: /`. 단 **GPTBot, ClaudeBot, CCBot 등 AI 크롤러 UA는 전면 차단**. 자체 UA(예: `AjouNoticeBot/0.1 (+연락처)`)를 쓰고 AI 봇으로 위장하지 말 것
- `/_attach/` 등 첨부 경로는 검색엔진용 Disallow → 첨부파일 내용 수집은 최소화하고 링크만 저장 권장
- 호스트당 1~2초 간격, 1~3시간 주기 증분 수집 권장 (이미 본 `articleNo`가 나오면 중단)

## 활동 추천에 특히 쓸모 있는 소스

- 대학일자리플러스센터 **교내 프로그램**(비교과, D-day·마일리지 포함), 교내채용
- 앵커사업단(현장실습·PBL), 미래자동차/데이터보안 사업단(비교과·특강), G-램프
- 본부 장학공지, 학생 활동·수상 소식, 각 학과 취업공지/소학회 게시판

## 미확인 / 후속

- 교수학습개발센터(AJAX), 현장실습지원센터(로그인), 창업지원단(개편 중)
- 공동기기센터, 과학영재교육원, 불어권협력센터는 학생 대상 공지가 적어 이번 목록에서 제외
- 총학생회·동아리연합회 등은 주로 인스타그램/에브리타임 등 외부 채널이라 범위 밖
