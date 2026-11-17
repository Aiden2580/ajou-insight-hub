"""조사 결과(docs/ajou_notice_sources.json)를 크롤러 설정(app/sources.json)으로 변환한다.

    python -m scripts.import_sources [조사JSON경로]

플랫폼 → parser 매핑. 정적 수집이 어려운 곳(로그인/POST/접속불가)은 enabled=false로 둔다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GENERIC_PATTERNS = {
    "custom_software": lambda s: {"link_pattern": "/board/" + s["list_url"].rstrip("/").split("/")[-1] + r"/\d+"},
    "gnuboard5": lambda s: {"link_pattern": r"wr_id=\d+", "content_selector": "#bo_v_con",
                            "warmup_url": s["homepage"].rstrip("/") + "/index.php"},
    "custom_anchor": lambda s: {"link_pattern": r"mode=view.*idx=\d+"},
    "nuxt_ssr": lambda s: {"link_pattern": r"/board/notice/detail/\d+"},
    "custom_php": lambda s: {"link_pattern": r"view\.php\?bn=\d+"},
}
PARSER = {"ajou_cms": "k2web", "rss": "rss", "pyxis_api": "pyxis"}
# 재학생 서비스 기준 우선순위 낮거나 정적 수집 불가
DISABLE_PLATFORMS = {"aspnet_job", "ctl_ajax", "custom", "unknown", "custom_php"}


def convert(src: dict) -> dict | None:
    plat = src["platform"]
    out = {
        "id": src["id"],
        "name": f"{src['org']} · {src['board']}" if src.get("board") not in (None, "-", "") else src["org"],
        "org": src["org"],
        "group": src["type"],
        "url": src["list_url"],
        "enabled": plat not in DISABLE_PLATFORMS and src["status"] == "확인됨" and bool(src["list_url"]),
        "note": src.get("notes", ""),
    }
    if plat in PARSER:
        out["parser"] = PARSER[plat]
        if plat == "ajou_cms":
            out.update({"pages": 1, "page_size": 30})
    elif plat in GENERIC_PATTERNS:
        out["parser"] = "generic"
        out.update(GENERIC_PATTERNS[plat](src))
    else:
        out["parser"] = "generic"
        out["link_pattern"] = r"__unsupported__"
        out["enabled"] = False
    if not out["url"]:
        out["enabled"] = False
    return out


def main(path: str | None = None):
    research = json.loads(Path(path or ROOT / "docs" / "ajou_notice_sources.json").read_text(encoding="utf-8"))
    sources = [convert(s) for s in research["sources"]]
    # 본부 일반공지는 여러 페이지 수집
    for s in sources:
        if s["id"] == "kr_ajou_notice":
            s["pages"] = 3
    data = {
        "_comment": "scripts/import_sources.py 로 생성. 조사일 " + research.get("generated_at", "")
                    + ". parser: k2web | generic | rss | pyxis. 항목을 직접 추가/수정해도 된다.",
        "sources": sources,
    }
    (ROOT / "app" / "sources.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    on = sum(s["enabled"] for s in sources)
    print(f"{len(sources)}개 소스 변환 (활성 {on}, 비활성 {len(sources) - on})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
