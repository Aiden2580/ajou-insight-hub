"""개인 관심사 그래프.

입력 신호(브라우저 기록, 학습 폴더 파일명, 직접 입력 키워드, 앱 내 클릭/북마크)를
LLM 없이 로컬에서 키워드로 쪼개 그래프로 만든다. 그래프 구축에는 토큰을 쓰지 않는다.

  노드: group(영역 허브: 학습 / 교과 / 비교과 / 교내외 활동)
        category(고정 태그 체계 중 그래프용 분야) / topic(신호에서 나온 주제어)
  간선: group─category, category─topic(소속), topic─topic(같은 신호에 함께 등장),
        교과─과목폴더─파일주제 (학습 폴더 구조)
  가중치: 신호 종류별 가중치 × 시간 감쇠(반감기 30일)
  잡음 제거: 주차·과제·시험·자료 같은 학습 파일 공통어, 숫자 조합, 사이트 이름, 한 번 스친 방문 기록
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from urllib.parse import unquote, urlparse

from .db import get_conn
from .taxonomy import CATEGORY_GROUP, GRAPH_EXCLUDED_TAGS, INTEREST_GROUPS, TAXONOMY, keywords, rule_tags

KIND_WEIGHT = {"keyword": 3.0, "bookmark": 2.0, "click": 1.0, "folder": 1.2, "history": 0.6}
HALF_LIFE_DAYS = 30
MAX_TOPICS = 60

NOISE = set("""
google naver daum youtube 검색 로그인 login 홈 home index main www com html php do http https
새 폴더 새폴더 download downloads 다운로드 desktop 바탕화면 documents 문서 img image images 사진 screenshot
스크린샷 final 최종 copy 복사본 temp tmp untitled 제목없음 pdf docx hwp pptx ppt xlsx zip jpg png txt md py ipynb
lecture week 주차 과제 레포트 report hw homework 강의자료 자료 수업 정리 노트 note notes 파일 file
차시 회차 중간 기말 중간고사 기말고사 시험 퀴즈 quiz exam midterm 답안 풀이 해설 solution answer sample 예제
template 양식 서식 제출 제출용 버전 최종본 수정본 사본 발표 발표자료 보고서 요약 필기 복습 예습 강의노트 실습 lab
assignment project 프로젝트 기타 misc etc 공유 공유폴더 백업 backup old new 새로운 임시 test 테스트 data 데이터셋
학기 학년 학년도 수강 교재 참고 참고자료 문제 연습 과제물 제출물 피드백 결과 결과물 사진들 영상 녹화 recording zoom
github stackoverflow wikipedia 위키백과 나무위키 블로그 blog tistory velog notion 노션 chatgpt gpt claude 뉴스 news
""".split())
SHORT_OK = {"ai", "ml", "os", "db", "ui", "ux", "ir", "vr", "ar", "iot", "nlp", "cv", "gnn", "llm", "cnn", "rnn", "api"}
_noise_num = re.compile(r"\d+\s*(주차|주|차시|강|장|절|회차|회|차|교시|week|wk|w)\b|\d+\s*(학년|학기)", re.I)

_ext_re = re.compile(r"\.(pdf|docx?|hwpx?|pptx?|xlsx?|zip|jpe?g|png|txt|md|py|ipynb|c|cpp|java|js|ts|html?)$", re.I)


def _item_text(kind: str, item: str) -> str:
    s = item.strip()
    if kind == "history":
        # "제목\tURL" 또는 URL 단독
        if "\t" in s:
            return s.split("\t")[0]
        if s.startswith("http"):
            u = urlparse(s)
            q = unquote(u.query)
            m = re.search(r"(?:q|query|search_query|keyword)=([^&]+)", q)
            return m.group(1).replace("+", " ") if m else unquote(u.path).replace("/", " ")
        return s
    if kind == "folder":
        parts = re.split(r"[\\/]", s)
        parts[-1] = _ext_re.sub("", parts[-1])
        return " ".join(re.sub(r"[_\-.()\[\]]+", " ", p) for p in parts)
    return s


def _clean_terms(text: str, limit: int = 8) -> list[str]:
    text = _noise_num.sub(" ", text)
    out = []
    for t in keywords(text, limit=limit * 2):
        if t in NOISE or re.search(r"\d", t):
            continue
        if t.isascii() and (len(t) <= 2 or len(t) > 20) and t not in SHORT_OK:
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def extract_terms(kind: str, item: str) -> tuple[list[str], list[str]]:
    text = _item_text(kind, item)
    if kind in ("keyword",):
        t = text.strip()
        terms = [t.lower() if t.isascii() else t] if t else []
    else:
        terms = _clean_terms(text)
    cats = rule_tags(text + " " + " ".join(terms))
    cats = [c for c in cats if c not in GRAPH_EXCLUDED_TAGS]
    return terms, cats


def _course_of(item: str) -> str | None:
    """학습 폴더 경로에서 과목명으로 보이는 폴더 (파일 바로 위 폴더, 학기 폴더 제외)."""
    parts = [p for p in re.split(r"[\\/]", item.strip()) if p]
    for d in reversed(parts[:-1]):
        terms = _clean_terms(re.sub(r"[_\-.()\[\]]+", " ", d), limit=2)
        if terms:
            # '취업준비', '창업동아리'처럼 활동 분야로 읽히는 폴더는 과목이 아니다
            if [c for c in rule_tags(terms[0]) if c in CATEGORY_GROUP]:
                return None
            return terms[0]
    return None

def add_signals(user_id: str, kind: str, items: list[str]) -> int:
    items = [i for i in (x.strip() for x in items) if i][:5000]
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (user_id,))
        c.executemany("INSERT INTO signals(user_id,kind,item) VALUES(?,?,?)", [(user_id, kind, i) for i in items])
    rebuild_graph(user_id)
    return len(items)


def _decay(created_at: str) -> float:
    try:
        t = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
    except ValueError:
        return 1.0
    days = (datetime.now(timezone.utc) - t).total_seconds() / 86400
    return 0.5 ** (max(days, 0) / HALF_LIFE_DAYS)


def rebuild_graph(user_id: str) -> dict:
    with get_conn() as c:
        sigs = c.execute("SELECT kind,item,created_at FROM signals WHERE user_id=?", (user_id,)).fetchall()
    node_w: dict[str, float] = defaultdict(float)
    node_kind: dict[str, str] = {}
    node_src: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    edge_w: dict[tuple[str, str], float] = defaultdict(float)

    def link(a: str, b: str, w: float):
        if a != b:
            edge_w[tuple(sorted((a, b)))] += w

    for s in sigs:
        w = KIND_WEIGHT.get(s["kind"], 1.0) * _decay(s["created_at"])
        course = None
        if s["kind"] in ("click", "bookmark"):
            with get_conn() as c:
                n = c.execute("SELECT tags,keywords,kind FROM notices WHERE id=?", (s["item"],)).fetchone()
            if not n or n["kind"] == "admin":
                continue
            terms = _clean_terms(" ".join(json.loads(n["keywords"])[:4]), limit=4)
            cats = [t for t in json.loads(n["tags"]) if t not in GRAPH_EXCLUDED_TAGS]
        else:
            terms, cats = extract_terms(s["kind"], s["item"])
            if s["kind"] == "folder":
                course = _course_of(s["item"])
        cats = [c for c in cats if c in CATEGORY_GROUP]
        for t in terms:
            node_w[t] += w
            node_kind.setdefault(t, "topic")
            node_src[t][s["kind"]] += 1
        for cat in cats:
            node_w[cat] += w * 0.5
            node_kind[cat] = "category"
            node_src[cat][s["kind"]] += 1
        for a, b in combinations(sorted(set(terms))[:6], 2):
            link(a, b, w * 0.5)
        for t in terms:
            for cat in cats:
                link(t, cat, w)
        if course:  # 교과 ─ 과목 ─ 파일 주제
            node_kind[course] = "course"
            node_w[course] += w * 0.5
            node_src[course][s["kind"]] += 1
            for t in terms:
                if t != course:
                    link(course, t, w)
        elif not cats and s["kind"] in ("history", "keyword"):
            # 분야를 모르는 학습 주제는 '학습' 영역에 붙인다
            for t in terms:
                node_src[t]["_learn"] = 1

    # 잡음 제거: 한 번 스친 약한 주제(예: 방문 기록 1회)는 버리고 상위 N개만 유지
    topics = sorted((k for k, v in node_kind.items() if v == "topic" and node_w[k] >= 1.0), key=lambda k: -node_w[k])
    keep = set(topics[:MAX_TOPICS]) | {k for k, v in node_kind.items() if v in ("category", "course")}

    # 영역 허브 연결
    groups_used: dict[str, float] = defaultdict(float)
    for k in list(keep):
        kind = node_kind[k]
        g = CATEGORY_GROUP.get(k) if kind == "category" else "교과" if kind == "course" else None
        if kind == "topic":
            linked_cat = any((a == k and b in keep and node_kind.get(b) in ("category", "course")) or
                             (b == k and a in keep and node_kind.get(a) in ("category", "course"))
                             for (a, b) in edge_w)
            if not linked_cat:
                g = "학습"
        if g:
            link(k, g, node_w[k] * (1.0 if kind != "topic" else 0.5))
            groups_used[g] += node_w[k]
    for g, w in groups_used.items():
        node_kind[g] = "group"
        node_w[g] = w
        keep.add(g)
    for k in keep:
        node_src[k].pop("_learn", None)

    with get_conn() as c:
        c.execute("DELETE FROM interest_nodes WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM interest_edges WHERE user_id=?", (user_id,))
        c.executemany(
            "INSERT INTO interest_nodes(user_id,label,kind,weight,sources) VALUES(?,?,?,?,?)",
            [(user_id, k, node_kind[k], round(node_w[k], 4), json.dumps(node_src[k])) for k in keep],
        )
        c.executemany(
            "INSERT INTO interest_edges(user_id,a,b,weight) VALUES(?,?,?,?)",
            [(user_id, a, b, round(w, 4)) for (a, b), w in edge_w.items() if a in keep and b in keep],
        )
    return {"nodes": len(keep), "edges": sum(1 for (a, b) in edge_w if a in keep and b in keep)}


def load_graph(user_id: str) -> tuple[dict[str, dict], list[dict]]:
    with get_conn() as c:
        nodes = {r["label"]: dict(r) for r in c.execute(
            "SELECT label,kind,weight,sources FROM interest_nodes WHERE user_id=?", (user_id,))}
        edges = [dict(r) for r in c.execute("SELECT a,b,weight FROM interest_edges WHERE user_id=?", (user_id,))]
    for n in nodes.values():
        n["sources"] = json.loads(n["sources"] or "{}")
    return nodes, edges


def activation(user_id: str) -> dict[str, float]:
    """관심 노드에서 1-hop 확산한 활성도(0~1). 추천과 인사이트 컨텍스트에 사용."""
    nodes, edges = load_graph(user_id)
    if not nodes:
        return {}
    nodes = {k: n for k, n in nodes.items() if n["kind"] != "group"}  # 영역 허브는 추천 근거가 아님
    mx = max((n["weight"] for n in nodes.values()), default=1) or 1
    act = {k: n["weight"] / mx for k, n in nodes.items()}
    edges = [e for e in edges if e["a"] in nodes and e["b"] in nodes]
    emax = max((e["weight"] for e in edges), default=1) or 1
    spread = defaultdict(float)
    for e in edges:
        w = e["weight"] / emax
        spread[e["b"]] += 0.35 * act.get(e["a"], 0) * w
        spread[e["a"]] += 0.35 * act.get(e["b"], 0) * w
    for k, v in spread.items():
        act[k] = min(1.0, act.get(k, 0) + v)
    return act


def graph_payload(user_id: str) -> dict:
    nodes, edges = load_graph(user_id)
    # 종류별로 정규화 (허브가 다른 노드를 압도하지 않게)
    mx = defaultdict(float)
    for n in nodes.values():
        mx[n["kind"]] = max(mx[n["kind"]], n["weight"])
    return {
        "nodes": [
            {"id": k, "label": k, "kind": n["kind"], "weight": round(n["weight"] / (mx[n["kind"]] or 1), 3),
             "sources": n["sources"]}
            for k, n in nodes.items()
        ],
        "links": [{"source": e["a"], "target": e["b"], "weight": e["weight"]} for e in edges],
        "categories": list(TAXONOMY),
        "groups": list(INTEREST_GROUPS),
        "category_group": CATEGORY_GROUP,
    }
