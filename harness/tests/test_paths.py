"""축 A 경로 계산 계약의 실패 선행 테스트.

정본: A§1.1(전체 트리) · A§1.2(경로 문법) · A§1.4(카테고리↔유형 매핑) · A§2.4(스트림 파일 계약)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import paths  # noqa: E402


def test_categories_are_the_nine():
    assert paths.CATEGORIES == [
        "requests", "audit", "minutes", "failures", "decisions",
        "adjudications", "design", "handoff", "reference",
    ]


def test_type_to_category_mapping_is_total_over_type_codes():
    """A§1.4 — 유형 코드 전건이 카테고리를 갖는다(FI 는 레코드 단위라 예외)."""
    from harness_core import ids
    for t in ids.TYPE_CODES:
        if t == "FI":
            continue
        assert paths.category_of(t) in paths.CATEGORIES, t


def test_path_is_computed_from_id_and_visibility_only():
    """A§1.2 — PATH := docs/CATEGORY/VIS/YYYY/MM/FILENAME, 연월은 ID 의 UTC 날짜."""
    p = paths.doc_path("RQ-20260728T145303Z-3f9c2a71", "public",
                       slug="dashboard-data-shape")
    assert p == pathlib.PurePosixPath(
        "docs/requests/public/2026/07/"
        "RQ-20260728T145303Z-3f9c2a71-dashboard-data-shape.md"
    )


def test_path_without_slug():
    p = paths.doc_path("DN-20260728T150102Z-8b1d44e0", "private")
    assert str(p) == "docs/decisions/private/2026/07/DN-20260728T150102Z-8b1d44e0.md"


def test_stream_paths_use_jsonl_and_land_directly_in_shard():
    """A§2.4-1 — 스트림별 하위 디렉토리 금지, 샤드 직치."""
    p = paths.doc_path("AU-20260728T140000Z-77aa01c2", "public", slug="agent-events")
    assert str(p) == (
        "docs/audit/public/2026/07/AU-20260728T140000Z-77aa01c2-agent-events.jsonl"
    )
    q = paths.doc_path("FS-20260728T140000Z-77aa01c2", "public")
    assert str(q) == "docs/failures/public/2026/07/FS-20260728T140000Z-77aa01c2.jsonl"


def test_extension_is_derived_from_type_not_supplied():
    """A§1.4 — 파일 형식은 유형 계약이다. 호출자가 고르지 않는다(E10 의 원천)."""
    assert paths.ext_of("DS") == "md"
    assert paths.ext_of("AU") == "jsonl"
    assert paths.ext_of("FS") == "jsonl"
    assert paths.ext_of("RQ") == "md"


def test_visibility_has_no_default():
    """A§1.5 — silent default 금지. 미지정은 존재하지 않는 값이다."""
    for bad in [None, "", "Public", "PRIVATE", "unspecified"]:
        try:
            paths.doc_path("DS-20260728T185000Z-77801729", bad)
        except ValueError:
            continue
        raise AssertionError(f"must reject visibility {bad!r}")


def test_candidate_paths_for_id_only_lookup_is_at_most_two():
    """A§1.2 — ID 만 알 때는 public/private 두 경로 stat 로 확정(최대 2회 접근)."""
    cands = paths.candidate_paths("DS-20260728T185000Z-77801729")
    assert len(cands) == 2
    assert {c.parts[2] for c in cands} == {"public", "private"}


def test_glob_for_unknown_slug_is_bounded_to_one_shard():
    """A§1.2 — 슬러그 미상 시 스캔 상한 = 샤드 1개."""
    g = paths.shard_glob("DS-20260728T185000Z-77801729", "public")
    assert g == "docs/design/public/2026/07/DS-20260728T185000Z-77801729*"


def test_gitignored_roots_match_contract():
    """A§8.1 — private 트리·vault·derived·config·착지 임시 파일."""
    assert paths.GITIGNORE_RULES == [
        "docs/*/private/",
        "/vault/",
        "/derived/",
        "/config/",
        "docs/**/*.tmp",
    ]


def test_derived_paths_never_live_under_docs():
    """A§1.1 R3 — 파생물은 derived/ 밖에 존재할 수 없다."""
    assert str(paths.tag_index_path("domain", "harness-self")).startswith("derived/")
    assert str(paths.audit_snapshot_path("memory", "m01")).startswith("derived/")
    assert "docs/" not in str(paths.tag_index_path("domain", "harness-self"))
