# 아주 인사이트 허브 (Ajou Insight Hub)

아주대학교 학과·센터·사업단 공지를 한곳에 모아 자동으로 태깅하고, 학생 개인의 관심사를 **그래프**로 정리해
아주대 활동을 추천하고 매일 A4 1~3장 분량의 인사이트를 만들어 주는 프로토타입입니다.

> 핵심 가치: **그래프형 자료구조로 컨텍스트를 압축해, 적은 LLM 토큰 비용으로 개인 맞춤 서비스를 제공한다.**

PC와 모바일 브라우저 모두에서 동작하는 반응형 웹앱이며(홈 화면에 추가 가능), 서버 하나(Python)로 배포됩니다.

## 화면

| 메뉴 | 내용 |
|---|---|
| 관심 그래프 | 연동한 데이터로 만든 관심사 그래프. **영역(학습·교과·비교과·교내외 활동) → 분야 → 과목·주제** 구조이며, 주차·과제·학기 같은 잡음은 제외. 드래그·확대·호버 하이라이트·노드 클릭 시 연결 주제와 관련 활동 표시. 추천 활동이 그래프 위에 ◆ 노드로 함께 표시됨 |
| 인사이트 | A4 리포트. 하루 처음 열 때 1회 자동 생성(이후 재사용), "다시 생성"은 직접 누를 때만. 날짜별 기록, 인쇄/PDF 저장 |
| 추천 활동 | 관심 그래프와 연결된 진행 중 공지. 왜 추천됐는지(연결 노드)와 D-day 표시 |
| 모든 활동 | 전체 공지 검색, 태그 필터, 최신/마감임박/태그순 정렬, 기관 필터. 수강신청·자퇴·전자출결 같은 **단순 행정 안내는 기본 숨김**("단순 안내도 보기" 체크 시 표시). **공지 새로고침** 버튼 |
| 데이터 연동 | 학습 폴더(파일 이름만 전송), 브라우저 방문 기록(Google 테이크아웃 JSON 또는 목록), 관심 키워드 |
| 관리 | 수집 대상 게시판 상태, 공지 새로고침, LLM 토큰 사용량 |

## 빠른 실행

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 선택: BAZE_API_KEY(아주LLM) 또는 ANTHROPIC_API_KEY 입력 (없어도 동작)
python -m scripts.check_llm # 선택: LLM 연결 확인
python -m scripts.seed_demo # 선택: 오프라인 데모 데이터 + 데모 사용자
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000` 을 엽니다. 같은 와이파이의 휴대폰에서는 `http://<PC IP>:8000`.
공지는 **모든 활동** 또는 **관리** 화면의 **공지 새로고침** 버튼을 누를 때만 수집합니다(토큰 절약 기본값).
주기적 자동 수집은 `AUTO_CRAWL=1`(`CRAWL_INTERVAL_MIN`분마다), 매일 정해진 시각 인사이트 일괄 생성은 `AUTO_INSIGHT=1`(`INSIGHT_HOUR_KST`시)로 켤 수 있습니다. 그래프 화면의 **데모 프로필로 체험** 버튼으로 시드 데이터를 바로 볼 수 있습니다.

Docker: `docker build -t ajou-hub . && docker run -p 8000:8000 -v $PWD/data:/data --env-file .env ajou-hub`
Render.com: 저장소 연결 후 `render.yaml` Blueprint 사용.

## LLM 연결 (아주LLM BAZE / Anthropic)

`.env` 에 키를 넣으면 됩니다. 키는 `.gitignore` 로 커밋에서 제외됩니다.

| 설정 | 동작 |
|---|---|
| `BAZE_API_KEY=...` | 아주LLM BAZE API Gateway의 Claude 엔드포인트(`https://factchat-cloud.mindlogic.ai/v1/gateway/claude`) 사용. 사용량은 키를 만든 BAZE 계정에 청구 |
| `ANTHROPIC_API_KEY=...` | Anthropic API 직접 사용 |
| 둘 다 없음 | 규칙 태깅 + 템플릿 인사이트 (비용 0) |

모델은 `TAG_MODEL`(기본 `claude-sonnet-5-5`, effort low)과 `INSIGHT_MODEL`(기본 `claude-opus-5-5`)로 바꿀 수 있습니다.
`python -m scripts.check_llm` 은 게이트웨이의 모델 목록에서 두 ID가 있는지 확인하고 짧은 호출을 한 번씩 보내 봅니다.
게이트웨이가 구조화 출력이나 effort 파라미터를 지원하지 않으면 자동으로 빼고 다시 호출합니다.

## 비용을 낮추는 구조

```
 공지 수집 ──▶ 규칙 태깅(무료) ──▶ 애매한 공지만 배치로 저가 모델 태깅 ──▶ 공지 그래프 노드(모든 사용자 공유)
                                                                         │
 사용자 신호(폴더·방문기록·키워드·클릭) ──▶ 로컬 키워드 추출(무료) ──▶ 개인 관심 그래프
                                                                         │
                     그래프 확산(1-hop) 매칭 = 추천 (토큰 0) ◀───────────┘
                                                                         │
       상위 노드 12 + 연결 15 + 추천 8줄 ≈ 수백 토큰 ──▶ 하루 1회 인사이트 생성
```

- **태깅은 공지당 평생 1회**: 결과를 `notices`와 `tag_cache`(정규화한 제목 해시)에 저장해, 이미 태깅된 공지·같은 제목의 재공지·다른 게시판 중복 글은 다시 보내지 않습니다.
  LLM은 최근 `LLM_TAG_MAX_AGE_DAYS`(45)일 이내의 애매한 활동성 공지에만, 한 번 갱신에 최대 `LLM_TAG_MAX_PER_RUN`(100)건까지 씁니다. 단순 행정 안내는 LLM에 보내지 않습니다.
- **태깅**: 고정 태그 체계 21개(`app/taxonomy.py`)로 규칙 태깅을 먼저 하고, 규칙 태그가 2개 미만인 공지만
  25개씩 묶어 제목 + 본문 앞 160자만 보냅니다(`TAG_MODEL`, 기본 `claude-sonnet-5-5`, effort low). 공지 태깅 결과는 모든 사용자가 공유하므로 사용자 수와 무관한 고정 비용입니다.
- **관심 그래프**: LLM을 쓰지 않습니다. 신호를 키워드로 쪼개 분야(category)·주제(topic) 노드와 동시 등장 간선을 만들고, 신호 종류별 가중치와 30일 반감기를 적용합니다. 상위 60개 주제만 유지해 그래프가 작게 유지됩니다.
- **추천**: 관심 노드에서 1-hop 확산한 활성도로 공지의 태그·키워드를 점수화합니다(토큰 0). 최신성·마감 임박 가산, 마감 지난 공지 제외.
- **단순 안내 분리**: 규칙(`classify_kind`)으로 공지를 활동(activity)/단순 행정 안내(admin)로 나눕니다. admin은 관심 그래프·추천·모든 활동(기본)에서 빠집니다.
- **인사이트**: 하루 1회만 생성하고 서버 재시작으로 다시 만들지 않습니다. 원문 대신 그래프 요약만 보냅니다(입력 수백 토큰). 컨텍스트 해시가 이전과 같으면 재생성하지 않고 재사용합니다. 키가 없으면 템플릿 리포트(비용 0).
- 모든 LLM 호출의 토큰 사용량은 `llm_usage` 테이블에 기록되어 **관리** 화면에서 확인할 수 있습니다.

## 날짜 처리

- 작성일은 목록의 작성일 칸(`.b-date` 등)에서만 읽고, 제목·본문 속 날짜는 쓰지 않습니다. 상세 페이지를 가져올 때 상세의 작성일로 한 번 더 보정합니다.
- 오늘 이후(미래) 날짜는 작성일로 받지 않습니다. 기존 DB의 미래 작성일은 서버 시작 시 자동으로 비우고 다음 새로고침 때 다시 가져옵니다.
- 마감일은 "~11.10." 같은 표기를 작성일 기준 연도로 해석하고, 연말(10월 이후) 공지의 1~2월 마감만 다음 해로 넘깁니다.
- 기존 DB는 서버 시작 시 새 열/테이블이 자동 추가되고 종류 분류·마감일이 다시 계산됩니다(토큰 0).

## 수집 대상 (게시판 120개, 활성 113개)

`docs/ajou_notice_sources.md` 에 조사 결과가 있고, `python -m scripts.import_sources` 로 `app/sources.json` 을 다시 만들 수 있습니다.

| parser | 대상 | 수 |
|---|---|---|
| `k2web` | 아주대 공통 CMS (`www.ajou.ac.kr/{사이트}/...do`): 본부·학과·단과대·대학원·센터 | 104 |
| `generic` | 링크 정규식 기반: 소프트웨어학과 자체 사이트, 그누보드5 사업단, 앵커사업단, G-램프 | 7 |
| `pyxis` | 중앙도서관 공개 JSON API | 1 |
| `rss` | 아주대학보 RSS | 1 |
| (비활성) | 대학일자리플러스센터(ASP.NET), 교수학습개발센터(POST), 현장실습지원센터(로그인), 창업지원단(접속불가), 입학처 | 7 |

새 게시판 추가: `app/sources.json` 에 항목을 넣거나, 새 형식이면 `app/crawler/parsers.py` 에 `@register("이름")` 파서를 추가합니다.

크롤링 예절: 자체 User-Agent(`AjouNoticeHub/0.1`)를 쓰고 AI 봇 UA로 위장하지 않습니다. 요청 간 1초 대기(`REQUEST_DELAY_SEC`),
상세 페이지는 실행당 최대 150건(`MAX_DETAIL_FETCH_PER_RUN`)씩 나눠 수집하며, 같은 글이 여러 게시판에 올라오면 `articleNo`로 중복 제거합니다.

## 개인정보

- 로그인 없는 프로토타입이라 브라우저마다 무작위 ID가 발급되고(localStorage), 데이터는 그 ID에 묶입니다.
- 학습 폴더는 파일 **경로 문자열만** 서버로 보내며 파일 내용은 읽지 않습니다.
- **데이터 연동 > 내 데이터 모두 삭제**로 신호·그래프·인사이트·저장 목록을 지웁니다.
- 방문 기록 "자동" 수집은 현재 파일 업로드 방식이며, 다음 단계로 브라우저 확장 프로그램을 계획하고 있습니다.

## 개발

```bash
pip install -r requirements.txt
python -m pytest -q          # 파서(실제 아주대 페이지 HTML 픽스처), 마감일 추출, 추천·인사이트 E2E
```

구조:

```
app/
  main.py          FastAPI 라우트 + 정적 파일
  config.py        환경 변수 (.env 지원)
  db.py            SQLite 스키마
  crawler/         base.py(레지스트리) parsers.py(k2web/generic/rss/pyxis) runner.py(수집 파이프라인)
  taxonomy.py      태그 체계, 키워드 추출, 마감일 추출
  tagger.py        규칙 → LLM 보강 태깅
  interest.py      개인 관심 그래프
  recommend.py     그래프 기반 추천
  insight.py       압축 컨텍스트 → 일일 인사이트
  scheduler.py     백그라운드 수집/인사이트 스케줄러
  static/          index.html, app.js, style.css, force-graph.min.js (MIT, vasturiano/force-graph 1.51.4)
scripts/           seed_demo.py, import_sources.py
docs/              게시판 조사 결과
tests/
```

## 다음 단계

- 브라우저 확장으로 방문 기록 자동 연동, 학교 SSO 로그인
- 대학일자리플러스센터 비교과 프로그램 목록(ASP.NET) 파서 → 활동 추천 품질 향상
- 관심 그래프 노드 간 의미 유사도(소형 임베딩 모델, 로컬) 연결
