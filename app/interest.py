"""개인 관심사 그래프.

입력 신호(브라우저 기록, 학습 폴더 파일명, 직접 입력 키워드, 앱 내 클릭/북마크)를
LLM 없이 로컬에서 키워드로 쪼개 그래프로 만든다. 그래프 구축에는 토큰을 쓰지 않는다.

  노드: category(고정 태그 체계) / topic(신호에서 나온 주제어)
  간선: 같은 신호 안에서 함께 등장(co-occurrence), topic→category 소속
  가중치: 신호 종류별 가중치 × 시간 감쇠(반감기 30일)
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
from .taxonomy import TAXONOMY, keywords, rule_tags

KIND_WEIGHT = {"keyword": 3.0, "bookmark": 2.0, "click": 1.0, "folder": 1.2, "history": 0.6}
HALF_LIFE_DAYS = 30
MAX_TOPICS = 60

NOISE = set("""
google naver daum youtube 검색 로그인 login 홈 home index main www com html php do http https
새 폴더 새폴더 download downloads 다운로드 desktop 바탕화면 documents 문서 img image images 사진 screenshot
스크린샷 final 최종 copy 복사본 temp tmp untitled 제목없음 pdf docx hwp pptx ppt xlsx zip jpg png txt md py ipynb
lecture week 주차 과제 레포트 report hw homework 강의자료 자료 수업 정리 노트 note notes 파일 file
""".split())

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


def extract_terms(kind: str, item: str) -> tuple[list[str], list[str]]:
    text = _item_text(kind, item)
    if kind in ("keyword",):
        terms = [text.strip().lower() if text.isascii() else text.strip()]
    else:
        terms = [t for t in keywords(text, limit=8) if t not in NOISE and not re.fullmatch(r"[a-z]?\d+[a-z]?", t)]
    cats = rule_tags(text + " " + " ".join(terms))
    cats = [c for c in cats if c != "모집"]
    return terms, cats


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

    for s in sigs:
        w = KIND_WEIGHT.get(s["kind"], 1.0) * _decay(s["created_at"])
        if s["kind"] in ("click", "bookmark"):
            # item = 공지 id. 공지의 태그/키워드를 그대로 사용
            with get_conn() as c:
                n = c.execute("SELECT tags,keywords FROM notices WHERE id=?", (s["item"],)).fetchone()
            if not n:
                continue
            terms, cats = json.loads(n["keywords"])[:4], [t for t in json.loads(n["tags"]) if t != "모집"]
        else:
            terms, cats = extract_terms(s["kind"], s["item"])
        for t in terms:
            node_w[t] += w
            node_kind.setdefault(t, "topic")
            node_src[t][s["kind"]] += 1
        for cat in cats:
            node_w[cat] += w * 0.5
            node_kind[cat] = "category"
            node_src[cat][s["kind"]] += 1
        for a, b in combinations(sorted(set(terms))[:6], 2):
            edge_w[(a, b)] += w * 0.5
        for t in terms:
            for cat in cats:
                if t != cat:
                    edge_w[tuple(sorted((t, cat)))] += w

    # 토픽 상위 N개만 유지 (그래프를 작게 유지 = 이후 LLM 컨텍스트도 작아짐)
    topics = sorted((k for k, v in node_kind.items() if v == "topic"), key=lambda k: -node_w[k])
    keep = set(topics[:MAX_TOPICS]) | {k for k, v in node_kind.items() if v == "category"}
    # 한 번만 등장하고 아무와도 안 이어진 약한 토픽은 노이즈로 간주
    degree = defaultdict(int)
    for (a, b) in edge_w:
        degree[a] += 1
        degree[b] += 1
    keep = {k for k in keep if node_kind[k] == "category" or node_w[k] >= 1.0 or degree[k] > 0}

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
    mx = max(n["weight"] for n in nodes.values()) or 1
    act = {k: n["weight"] / mx for k, n in nodes.items()}
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
    mx = max((n["weight"] for n in nodes.values()), default=1) or 1
    return {
        "nodes": [
            {"id": k, "label": k, "kind": n["kind"], "weight": round(n["weight"] / mx, 3), "sources": n["sources"]}
            for k, n in nodes.items()
        ],
        "links": [{"source": e["a"], "target": e["b"], "weight": e["weight"]} for e in edges],
        "categories": list(TAXONOMY),
    }
