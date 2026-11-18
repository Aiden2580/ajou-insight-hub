"""학습 폴더 → 관심 키워드 선별.

1) 브라우저는 파일 '이름'만 읽는다(내용은 열지 않음, ls처럼). 프로젝트 폴더(Visual Studio·Unity·Node·Git 등)와
   빌드·캐시 폴더는 안으로 들어가지 않고 폴더 이름만 남긴다.
2) 서버는 이름 목록을 다시 거르고(잡동사니 폴더, 확장자, 숫자만 있는 이름) 폴더 구조 위주로 짧게 압축한다(수천 토큰 이하).
3) 압축 목록을 TAG_MODEL(Haiku)에 한 번 보내 과목·동아리/소학회·핵심 키워드만 고른다. 사람 이름이나
   프로젝트 내부 파일명 같은 잡음은 버린다. 같은 목록은 해시로 캐시해서 다시 보내지 않는다.
4) 키가 없으면 규칙으로 고른다(과목처럼 보이는 폴더, 동아리·학회 폴더, 여러 파일에 반복되는 단어).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict

from . import config, llm
from .db import meta_get, meta_set
from .taxonomy import CATEGORY_GROUP, rule_tags

# 들어가지 않을 폴더 (빌드 산출물, 캐시, 도구 설정, 엔진 내부)
JUNK_DIRS = {
    "node_modules", ".git", ".svn", ".hg", ".vs", ".vscode", ".idea", "__pycache__", ".pytest_cache", ".mypy_cache",
    "venv", ".venv", "env", ".env", "site-packages", "bin", "obj", "debug", "release", "x64", "x86", "arm64",
    "packages", "library", "temp", "logs", "usersettings", "projectsettings", "build", "builds", "dist", "out",
    "target", ".gradle", "gradle", ".next", ".nuxt", ".cache", "cache", "coverage", "ipch", ".ipynb_checkpoints",
    "$recycle.bin", "system volume information", ".ds_store", "thumbs.db", "__macosx", ".trash", "vendor",
    "cmakefiles", "deriveddata", "pods", ".dart_tool", "intermediate", "saved", "binaries", "plugins",
}
# 이 표식이 있는 폴더는 '프로젝트 하나'로 보고 이름만 남긴다
PROJECT_MARKERS = re.compile(r"(\.sln|\.csproj|\.vcxproj|\.xcodeproj|\.uproject|package\.json|pom\.xml|build\.gradle|"
                             r"cmakelists\.txt|cargo\.toml|go\.mod|pyproject\.toml|setup\.py)$", re.I)
_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_ONLY_NUM = re.compile(r"^[\d\s._\-()\[\]]+$")
MAX_LINES = 400
MAX_CHARS = 9000

_ITEM = {"type": "object",
         "properties": {"name": {"type": "string"},
                        "category": {"type": "string", "enum": list(CATEGORY_GROUP) + ["기타"]}},
         "required": ["name", "category"], "additionalProperties": False}
_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "array", "items": _ITEM} for k in ("courses", "clubs", "keywords")},
    "required": ["courses", "clubs", "keywords"],
    "additionalProperties": False,
}

_SYSTEM = """대학생의 학습 폴더 '이름 목록'(파일 내용 아님)에서 관심 그래프에 넣을 항목만 고른다.
- courses: 수강한 과목 이름 또는 계열 (예: 운영체제, 자료구조, 선형대수, 캡스톤디자인). 약어·영문 폴더는 흔한 한국어 과목명으로 정리. 최대 25개
- clubs: 활동한 동아리, 소학회, 스터디, 학생회, 대회팀. 최대 10개
- keywords: 그 밖에 짧게 정리되는 관심 주제 (예: 강화학습, 게임 개발, 투자유치). 최대 15개
- category: 주어진 분야 중 가장 가까운 것, 없으면 기타
버릴 것: 사람 이름, 학번, 날짜·학기·주차, 과제·시험·자료 같은 일반어, 프로젝트 내부 파일명·클래스명, 개인정보, 사진 폴더.
목록에 근거가 없는 항목은 만들지 않는다."""


def _norm_seg(seg: str) -> str:
    return seg.strip().lower()


def filter_paths(paths: list[str]) -> list[str]:
    """잡동사니 폴더 아래는 버리고, 프로젝트 폴더는 이름만 남긴다 (브라우저에서 못 거른 경우 대비)."""
    project_roots = set()
    for p in paths:
        parts = [x for x in re.split(r"[\\/]", p) if x]
        if parts and PROJECT_MARKERS.search(parts[-1]):
            project_roots.add("/".join(parts[:-1]))
        if "projectsettings" in (s.lower() for s in parts):  # Unity
            i = [s.lower() for s in parts].index("projectsettings")
            project_roots.add("/".join(parts[:i]))
    out = []
    for p in paths:
        parts = [x for x in re.split(r"[\\/]", p.strip()) if x]
        if not parts or any(_norm_seg(s) in JUNK_DIRS or s.startswith(".") for s in parts):
            continue
        cut = next((i for i in range(1, len(parts) + 1) if "/".join(parts[:i]) in project_roots), None)
        if cut is not None:
            out.append("/".join(parts[:cut]) + "/[프로젝트]")
            continue
        out.append("/".join(parts))
    return list(dict.fromkeys(out))


def compress(paths: list[str]) -> str:
    """폴더 트리 위주의 짧은 목록: 폴더 경로 + 폴더별 파일 이름 몇 개."""
    files_by_dir: dict[str, list[str]] = defaultdict(list)
    dirs: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
        stem = _EXT.sub("", parts[-1])
        if stem and not _ONLY_NUM.match(stem) and stem != "[프로젝트]":
            files_by_dir["/".join(parts[:-1])].append(stem)
        elif stem == "[프로젝트]":
            files_by_dir["/".join(parts[:-1])].append("[프로젝트]")
    lines = []
    for d in sorted(dirs):
        names = list(dict.fromkeys(files_by_dir.get(d, [])))
        extra = f" : {', '.join(names[:5])}" + (f" 외 {len(names) - 5}개" if len(names) > 5 else "") if names else ""
        lines.append(d + "/" + extra)
        if len(lines) >= MAX_LINES:
            lines.append(f"... 외 폴더 {len(dirs) - MAX_LINES}개")
            break
    text = "\n".join(lines)
    return text[:MAX_CHARS]


def _rule_select(paths: list[str]) -> dict:
    """LLM 없이: 과목처럼 보이는 폴더, 동아리·학회 폴더, 여러 파일에 반복되는 단어."""
    from .interest import _clean_terms  # 순환 import 방지
    courses, clubs, words = Counter(), Counter(), Counter()
    for p in paths:
        parts = p.split("/")
        for seg in parts[:-1]:
            name = re.sub(r"[_\-.()\[\]]+", " ", seg).strip()
            if re.search(r"동아리|소학회|학회|스터디|club|학생회", name, re.I):
                clubs[name] += 1
        if parts[-1] == "[프로젝트]":  # 프로젝트 폴더 이름은 과목이 아니다
            continue
        if len(parts) >= 2:
            terms = _clean_terms(re.sub(r"[_\-.()\[\]]+", " ", parts[-2]), limit=1)
            if terms and not re.search(r"동아리|소학회|학회|스터디|club", parts[-2], re.I):
                if [c for c in rule_tags(terms[0]) if CATEGORY_GROUP.get(c) in ("비교과", "교내외 활동")]:
                    words[terms[0]] += 2  # '취업준비'처럼 활동 분야로 읽히는 폴더는 키워드로
                else:
                    courses[terms[0]] += 1
        for t in _clean_terms(re.sub(r"[_\-.()\[\]]+", " ", _EXT.sub("", parts[-1])), limit=4):
            words[t] += 1

    def cat(name):
        c = [x for x in rule_tags(name) if x in CATEGORY_GROUP]
        return c[0] if c else "기타"
    return {
        "courses": [{"name": n, "category": cat(n)} for n, _ in courses.most_common(25)],
        "clubs": [{"name": n, "category": cat(n)} for n, _ in clubs.most_common(10)],
        "keywords": [{"name": n, "category": cat(n)} for n, k in words.most_common(40) if k >= 2
                     and n not in courses][:15],
    }


def _clean(sel: dict) -> dict:
    out = {}
    for key, cap in (("courses", 25), ("clubs", 10), ("keywords", 15)):
        seen, items = set(), []
        for it in sel.get(key) or []:
            name = re.sub(r"\s+", " ", str(it.get("name", ""))).strip()[:30]
            if len(name) < 2 or name.lower() in seen or _ONLY_NUM.match(name):
                continue
            seen.add(name.lower())
            c = it.get("category")
            items.append({"name": name, "category": c if c in CATEGORY_GROUP else "기타"})
        out[key] = items[:cap]
    return out


def select(paths: list[str]) -> dict:
    """이름 목록 → {courses, clubs, keywords, by, cached, digest_chars}."""
    kept = filter_paths(paths)
    digest = compress(kept)
    if not digest:
        return {"courses": [], "clubs": [], "keywords": [], "by": "none", "cached": False, "digest_chars": 0,
                "kept": 0}
    key = "folder_sel:" + hashlib.sha1(digest.encode()).hexdigest()
    hit = meta_get(key)
    if hit:
        return {**json.loads(hit), "cached": True, "digest_chars": len(digest), "kept": len(kept)}
    sel, by = None, "rule"
    if config.llm_enabled():
        cats = ", ".join(CATEGORY_GROUP)
        out = llm.json_call("folder_select", config.TAG_MODEL, _SYSTEM + f"\n분야 목록: {cats}",
                            "폴더/파일 이름 목록:\n" + digest, _SCHEMA, max_tokens=3000, effort="low")
        if out:
            sel, by = out, config.TAG_MODEL
    if sel is None:
        sel = _rule_select(kept)
    res = {**_clean(sel), "by": by}
    if by != "rule":  # 규칙 결과는 키를 넣은 뒤 다시 고를 수 있게 캐시하지 않는다
        meta_set(key, json.dumps(res, ensure_ascii=False))
    return {**res, "cached": False, "digest_chars": len(digest), "kept": len(kept)}
