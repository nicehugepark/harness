"""축 A 채번·ID 문법 계약의 실패 선행 테스트.

정본: A§2.1(ID 문법) · A§2.2(파일명·슬러그) · A§3.2(해시 생성식) · A§3.3(경합 시나리오)
이 파일은 구현보다 먼저 작성되고 먼저 실패한다(S§5.1 TDD).
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import ids  # noqa: E402


# ── A§2.1 ID 문법 ────────────────────────────────────────────────
ID_RE = re.compile(r"^(RQ|AU|MN|FL|FS|DN|AJ|DS|HO|RF)-\d{8}T\d{6}Z-[0-9a-f]{8}$")
FI_RE = re.compile(r"^FI-[0-9a-f]{12}$")


def test_type_codes_are_the_closed_enum():
    """A§2.1 — TYPE enum 은 11종이고 그 밖의 값은 존재하지 않는다."""
    assert ids.TYPE_CODES == {
        "RQ", "AU", "MN", "FL", "FS", "FI", "DN", "AJ", "DS", "HO", "RF"
    }


def test_mint_produces_grammar_conformant_id():
    """A§2.1 — 시각 접두(UTC) + 해시 8자."""
    got = ids.mint("DS", machine_id="m-test", session_id="s-test")
    assert ID_RE.match(got), got


def test_mint_is_unique_across_calls_in_same_second():
    """A§3.1 — 같은 UTC 초 안에서도 rand128 성분이 유일성을 만든다."""
    batch = {ids.mint("DS", machine_id="m", session_id="s") for _ in range(2000)}
    assert len(batch) == 2000


def test_mint_rejects_unknown_type():
    """A§5.2 E04 의 원천 — 문법 밖 유형은 채번 자체가 불가능하다."""
    try:
        ids.mint("XX", machine_id="m", session_id="s")
    except ValueError:
        return
    raise AssertionError("unknown type code must raise")


def test_incident_id_is_deterministic_on_context_key():
    """A§3.2 — 같은 맥락 키는 언제 어디서 계산해도 같은 사건 ID 로 수렴한다."""
    a = ids.incident_id("machine=m|role=qa|violation=slot-missing")
    b = ids.incident_id("machine=m|role=qa|violation=slot-missing")
    c = ids.incident_id("machine=m|role=qa|violation=other")
    assert a == b
    assert a != c
    assert FI_RE.match(a), a


def test_parse_roundtrip_yields_type_and_utc_datetime():
    """A§1.2 — 경로는 (ID, visibility) 2값에서 계산된다. 그 전제인 ID 분해."""
    minted = ids.mint("AJ", machine_id="m", session_id="s")
    parsed = ids.parse(minted)
    assert parsed.type_code == "AJ"
    assert parsed.dt.tzinfo is not None
    assert parsed.dt.utcoffset().total_seconds() == 0
    assert len(parsed.hash8) == 8


def test_parse_rejects_malformed():
    for bad in ["DS-20260728-abcdefgh", "ds-20260728T185000Z-77801729",
                "DS-20260728T185000Z-7780172", "DS-20260728T185000Z-ZZZZZZZZ", ""]:
        try:
            ids.parse(bad)
        except ValueError:
            continue
        raise AssertionError(f"must reject: {bad!r}")


# ── A§2.2 슬러그 ─────────────────────────────────────────────────
def test_slug_is_lowercase_ascii_hyphen_bounded_40():
    assert ids.slugify("Dashboard Data Shape") == "dashboard-data-shape"
    assert ids.slugify("A" * 200) == "a" * 40
    assert not ids.slugify("x").endswith("-")


def test_slug_collapses_runs_and_trims_edges():
    assert ids.slugify("  --Hello,,,   World!!  ") == "hello-world"


def test_slug_omitted_when_meaningful_chars_below_three():
    """A§2.2 — 유의미 문자 3자 미만이면 슬러그를 생략한다(슬러그 생성 붕괴 대응)."""
    assert ids.slugify("한글 제목입니다") == ""
    assert ids.slugify("!!") == ""
    assert ids.slugify("ab") == ""
    assert ids.slugify("abc") == "abc"


def test_filename_composition_matches_contract():
    """A§2.2 — FILENAME := ID [ "-" SLUG ] "." EXT"""
    i = "DS-20260728T185000Z-77801729"
    assert ids.filename(i, "harness design", "md") == f"{i}-harness-design.md"
    assert ids.filename(i, "한글", "md") == f"{i}.md"
    assert ids.filename("AU-20260728T140000Z-77aa01c2", "agent-events", "jsonl") == (
        "AU-20260728T140000Z-77aa01c2-agent-events.jsonl"
    )


def test_filename_rejects_extension_outside_contract():
    for ext in ["txt", "png", "json"]:
        try:
            ids.filename("DS-20260728T185000Z-77801729", "x", ext)
        except ValueError:
            continue
        raise AssertionError(f"must reject ext {ext}")
