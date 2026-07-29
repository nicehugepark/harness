"""정본: A§1.1 전체 트리 · A§1.2 경로 문법 · A§1.4 카테고리↔유형 매핑 · A§8.1 .gitignore.

경로는 (ID, visibility) 2값에서 O(1)로 계산된다. 찾기 위해 전체를 스캔하지
않는다(S§3.2). 이 성질이 착지 게이트의 참조 무결성 검사를 싸게 만든다.
"""
from __future__ import annotations

import pathlib

from . import ids

# A§1.1 — 9 카테고리. 전건이 동형 트리다.
CATEGORIES = [
    "requests", "audit", "minutes", "failures", "decisions",
    "adjudications", "design", "handoff", "reference",
]

VISIBILITIES = ("public", "private")

# A§1.4 카테고리 ↔ 유형 코드
CATEGORY_BY_TYPE = {
    "RQ": "requests",
    "AU": "audit",
    "MN": "minutes",
    "FL": "failures",
    "FS": "failures",
    "DN": "decisions",
    "AJ": "adjudications",
    "DS": "design",
    "HO": "handoff",
    "RF": "reference",
}

# A§8.1 — 확정판. 변경은 DN 경유.
GITIGNORE_RULES = [
    "docs/*/private/",
    "/vault/",
    "/derived/",
    "/config/",
    "docs/**/*.tmp",
]

DOCS_ROOT = "docs"
DERIVED_ROOT = "derived"


def category_of(type_code: str) -> str:
    try:
        return CATEGORY_BY_TYPE[type_code]
    except KeyError:
        raise ValueError(
            f"유형 {type_code!r} 에 대응하는 카테고리가 없다(A§1.4). "
            f"FI 는 레코드 단위 ID 라 파일 경로를 갖지 않는다."
        ) from None


def ext_of(type_code: str) -> str:
    return ids.EXT_BY_TYPE[type_code]


def _check_visibility(visibility) -> str:
    """A§1.5 — silent default 금지. 미지정은 존재하지 않는 값이다.

    어느 구현자도 "미지정 → public"으로 읽을 수 없다. 그 해석 자체가
    존재하지 않는 값이기 때문이다.
    """
    if visibility not in VISIBILITIES:
        raise ValueError(
            f"visibility 는 {VISIBILITIES} 중 하나를 명시해야 한다(A§1.5): "
            f"{visibility!r}"
        )
    return visibility


def shard_dir(doc_id: str, visibility: str) -> pathlib.PurePosixPath:
    """docs/<카테고리>/<vis>/YYYY/MM/ — 연월은 ID 의 UTC 날짜다(A§1.2)."""
    _check_visibility(visibility)
    parsed = ids.parse(doc_id)
    return pathlib.PurePosixPath(
        DOCS_ROOT,
        category_of(parsed.type_code),
        visibility,
        f"{parsed.dt.year:04d}",
        f"{parsed.dt.month:02d}",
    )


def doc_path(doc_id: str, visibility: str, slug: str = "") -> pathlib.PurePosixPath:
    parsed = ids.parse(doc_id)
    name = ids.filename(doc_id, slug, ext_of(parsed.type_code))
    return shard_dir(doc_id, visibility) / name


def candidate_paths(doc_id: str, slug: str = "") -> list[pathlib.PurePosixPath]:
    """ID 만 알 때 — public/private 두 경로 stat 으로 확정(최대 2회 접근)."""
    return [doc_path(doc_id, v, slug) for v in VISIBILITIES]


def shard_glob(doc_id: str, visibility: str) -> str:
    """슬러그 미상 시 — 해당 샤드 1개 안에서의 글롭. 스캔 상한 = 샤드 1개."""
    return f"{shard_dir(doc_id, visibility)}/{doc_id}*"


# ── 파생물 (A§1.1 R3 — derived/ 밖에 존재할 수 없다) ─────────────
def tag_index_path(axis: str, value: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(DERIVED_ROOT, "index", "tags", axis, f"{value}.jsonl")


def tag_agent_index_path(axis: str, value: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(
        DERIVED_ROOT, "index", "tag-agents", axis, f"{value}.jsonl"
    )


def docs_index_path() -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(DERIVED_ROOT, "index", "docs.jsonl")


def audit_snapshot_path(axis: str, machine: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(
        DERIVED_ROOT, "audit", "snapshots", f"{axis}-{machine}.json"
    )


def audit_view_path(name: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(DERIVED_ROOT, "audit", "views", f"{name}.json")


def policy_derived_path(name: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(DERIVED_ROOT, "policy", f"{name}.json")


def is_under_docs(rel_path) -> bool:
    return pathlib.PurePosixPath(rel_path).parts[:1] == (DOCS_ROOT,)


def gitignore_text() -> str:
    return "\n".join([
        "# A§8.1 — 확정판. 변경은 결정 문서(DN) 경유.",
        "# 1) private 문서 트리 — 전 카테고리 공통(트리 동형성이 1행을 성립시킨다)",
        GITIGNORE_RULES[0],
        "# 2) 민감정보 볼트 (S§2-8)",
        GITIGNORE_RULES[1],
        "# 3) 파생물 전량 — 순수 재생성 가능 (S§10-2)",
        GITIGNORE_RULES[2],
        "# 4) 로컬 정책·상태 전체 (S§8 엔진 청결) — 머신 편성·이름 레지스트리·",
        "#    설치 저널 등 비공개 정보가 이 아래 놓이므로 config/ 전체를 제외한다",
        GITIGNORE_RULES[3],
        "# 5) 착지 임시 파일",
        GITIGNORE_RULES[4],
        "",
    ])
