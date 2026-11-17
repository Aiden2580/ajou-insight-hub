"""고정 태그 체계(그래프의 '카테고리' 노드)와 규칙 기반 키워드 사전.

규칙으로 대부분을 무료로 태깅하고, 애매한 공지만 LLM에 보낸다.
"""
import re

TAXONOMY: dict[str, list[str]] = {
    "학사": ["학사", "수강", "휴학", "복학", "전과", "계절", "학점", "성적", "출석", "전공", "교육과정", "강의"],
    "수강신청": ["수강신청", "수강 신청", "정정", "계절수업", "계절학기"],
    "졸업": ["졸업", "학위", "졸업요건", "어학졸업", "topcit"],
    "장학": ["장학", "등록금", "학자금", "국가장학", "근로"],
    "취업": ["취업", "채용", "인턴", "현장실습", "직무", "멘토링", "커리어", "잡페어", "면접", "자소서"],
    "현장실습/인턴": ["현장실습", "인턴", "인턴십", "internship"],
    "공모전/대회": ["공모전", "경진대회", "대회", "해커톤", "챌린지", "콘테스트", "경연"],
    "비교과": ["비교과", "프로그램", "워크숍", "캠프", "아카데미", "스터디"],
    "특강/세미나": ["특강", "세미나", "콜로키움", "강연", "포럼", "설명회", "웨비나"],
    "창업": ["창업", "스타트업", "데모데이", "투자유치", "ir ", "앵커링", "anchoring"],
    "국제교류": ["국제", "교환학생", "해외", "글로벌", "어학연수", "유학", "국외", "외국인"],
    "봉사": ["봉사", "자원봉사", "서포터즈", "기부"],
    "행사/축제": ["축제", "행사", "개최", "공연", "전시", "체육대회"],
    "연구/대학원": ["연구", "대학원", "논문", "학회", "학술", "랩", "연구실", "자기주도연구"],
    "AI/SW": ["ai", "인공지능", "소프트웨어", "sw", "프로그래밍", "코딩", "데이터", "머신러닝", "딥러닝", "llm", "클라우드", "보안", "topcit"],
    "공학/기술": ["공학", "반도체", "자동차", "미래자동차", "로봇", "전자", "기계", "에너지", "통신", "모빌리티"],
    "경영/경제": ["경영", "경제", "금융", "회계", "마케팅", "투자"],
    "인문/사회/예술": ["인문", "사회", "문화", "예술", "글쓰기", "독서", "역사", "철학"],
    "생활/복지": ["기숙사", "생활관", "상담", "건강", "식당", "셔틀", "주차", "복지", "인권", "심리"],
    "병무/예비군": ["예비군", "병무", "병역", "군"],
    "모집": ["모집", "선발", "지원", "신청", "참가자"],
}

STOPWORDS = set("""
안내 공지 재공지 관련 대한 대하여 및 등 의 수정 필독 개최 운영 참가 참가자 학생 학부 학사과정 학년도
학기 년 월 일 위한 통한 기간 신청 모집 방법 일정 결과 변경 제출 접수 마감 까지 이상 이후 이전 적용 통합 사항 참고
요청 지원 반드시 확인 가능 불가 동시 경우 대상 2026 2027 2025 kr ajou 아주대 아주대학교 공지사항
학년 학년도 학기 준비 후기 입문 방법 패턴 정리 소개 기초 심화 실습 과목 교과목 교양 필수 수강 프로그램 안내사항
""".split())

# 표기가 다른 같은 개념을 하나의 노드로 합친다
SYNONYMS = {"ai": "인공지능", "인공지능의이해와응용": "인공지능", "sw": "소프트웨어", "ml": "머신러닝",
            "gnn": "그래프신경망", "llm": "llm", "인턴십": "인턴", "글로벌인턴십": "인턴"}

_token_re = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9+#.]{1,}")


def rule_tags(text: str) -> list[str]:
    low = (text or "").lower()
    return [tag for tag, kws in TAXONOMY.items() if any(k in low for k in kws)]


def keywords(text: str, limit: int = 6) -> list[str]:
    """제목 등 짧은 텍스트에서 그래프 '토픽' 노드로 쓸 키워드를 뽑는다 (LLM 없이)."""
    out = []
    text = re.sub(r"\d+\s*학년\s*\d*\s*학기|\d{4}\s*학년도|\d+\s*학기", " ", text or "")
    for t in _token_re.findall(text):
        t = t.strip(".").lower() if t.isascii() else t
        if t in STOPWORDS or len(t) < 2 or t.isdigit():
            continue
        # 흔한 조사/접미사 제거 (가벼운 정규화)
        t = re.sub(r"(에서|으로|에게|까지|부터|이며|하는|합니다|안내|신청|모집)$", "", t)
        t = SYNONYMS.get(t, t)
        if len(t) >= 2 and t not in STOPWORDS and t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out


_DL = [
    # ~2027.1.22 / ~26.12.31.
    re.compile(r"~\s*(20\d{2}|\d{2})[./-](\d{1,2})[./-](\d{1,2})"),
    # ~11/20, ~12/7(월), 11.16.~11.20.
    re.compile(r"~\s*(\d{1,2})[./](\d{1,2})"),
]


def extract_deadline(title: str, posted_at: str | None) -> str | None:
    from datetime import date
    base_year = int(posted_at[:4]) if posted_at else date.today().year
    base = date.fromisoformat(posted_at) if posted_at else date.today()
    m = _DL[0].search(title or "")
    if m:
        y, mo, d = (int(x) for x in m.groups())
        y = y + 2000 if y < 100 else y
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    m = _DL[1].search(title or "")
    if m:
        mo, d = (int(x) for x in m.groups())
        try:
            dl = date(base_year, mo, d)
            if dl < base:  # 연말 공지의 1~2월 마감 → 다음 해
                dl = date(base_year + 1, mo, d)
            return dl.isoformat()
        except ValueError:
            return None
    return None
