"""착지 게이트 계약의 실패 선행 테스트.

정본: A§5.2 land() 판정식과 오류 코드표 · A§4.1~4.2 스키마 · A§7.2 미분류 처리
시정 배치: X1(output_kind·doc_evidence) · X2(린트 대상·E16) · X5(root·E17) · X7(state)
테스트 시나리오 정본: DS-20260729T002517Z-a64e5658 §테스트 시나리오 TS1~TS9
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import frontmatter, gate, ids, schema  # noqa: E402

ROOT_ID = "abc123def456"
IDENTITY = gate.Identity(
    machine="m01-wsl", session="s-test", author="테스트작성자",
    role_key="lead", root=ROOT_ID,
)


def _meta(**over):
    base = {
        "schema": 2,
        "id": "DS-20260729T010000Z-11111111",
        "type": "DS",
        "title": "테스트 문서",
        "visibility": "public",
        "state": "active",
        "stage": "design",
        "root": ROOT_ID,
        "created": "2026-07-29T10:00:00+09:00",
        "updated": "2026-07-29T10:00:00+09:00",
        "machine": "m01-wsl",
        "session": "s-test",
        "author": "테스트작성자",
        "requester": "의사결정권자",
        "what": "무슨 일",
        "why": "왜",
        "tags": ["stage/design", "origin/agent", "domain/harness-self"],
        "refs": {},
    }
    base.update(over)
    return base


_DESIGN_BODY = """## 요약

확정 — 내용.

## 검증 기준

```yaml
criteria:
  - what: "조건"
    how: "명령"
    pass: "0건"
```

## 테스트 시나리오

| # | 시나리오 |
|---|---|
| TS1 | 무언가 |

## 본문

규율 항목 없음
"""


def _doc(meta=None, body=_DESIGN_BODY):
    return frontmatter.render(meta or _meta(), body)


def _land(text, tmp, visibility="public", identity=IDENTITY):
    return gate.land(text, root_dir=tmp, identity=identity, visibility=visibility)


def _tmp():
    d = tempfile.mkdtemp(prefix="harness-gate-")
    return pathlib.Path(d)


# ── 통과 경로 ────────────────────────────────────────────────────
def test_wellformed_document_lands():
    r = _land(_doc(), _tmp())
    assert r.ok, r.code
    assert r.path.as_posix().startswith("docs/design/public/2026/07/")


# ── E01~E08 ─────────────────────────────────────────────────────
def test_e01_unparseable_frontmatter():
    assert _land("no frontmatter here", _tmp()).code == "E01"


def test_e02_unsupported_schema_version():
    assert _land(_doc(_meta(schema=99)), _tmp()).code == "E02"


def test_e03_missing_required_field():
    m = _meta()
    del m["why"]
    assert _land(_doc(m), _tmp()).code == "E03"


def test_e03_identity_mismatch_is_rejected():
    """A§5.2 — 신원 불일치. 사람이 손으로 만들어도 신원과 일치해야 한다."""
    assert _land(_doc(_meta(author="다른이름")), _tmp()).code == "E03"
    assert _land(_doc(_meta(machine="other")), _tmp()).code == "E03"


def test_e04_id_grammar_and_type_crosscheck():
    assert _land(_doc(_meta(id="DS-2026-bad")), _tmp()).code == "E04"
    assert _land(_doc(_meta(type="RQ")), _tmp()).code == "E04"


def test_e05_path_meta_mismatch():
    """visibility=private 인데 public 으로 착지 지시하면 거부."""
    r = _land(_doc(_meta(visibility="private")), _tmp(), visibility="public")
    assert r.code == "E05"


def test_e03_visibility_missing_has_no_default():
    """A§1.5 — silent default 금지."""
    m = _meta()
    del m["visibility"]
    assert _land(_doc(m), _tmp()).code == "E03"


def test_e06_missing_summary_section():
    body = _DESIGN_BODY.replace("## 요약\n\n확정 — 내용.\n", "## 요약\n\n")
    assert _land(_doc(body=body), _tmp()).code == "E06"


def test_e06_design_stage_requires_criteria_block():
    body = _DESIGN_BODY.replace("## 검증 기준", "## 다른 절")
    assert _land(_doc(body=body), _tmp()).code == "E06"


def test_e06_criteria_items_need_three_elements():
    body = _DESIGN_BODY.replace('    pass: "0건"\n', "")
    assert _land(_doc(body=body), _tmp()).code == "E06"


def test_e07_reference_target_absent():
    m = _meta(refs={"request": "RQ-20260728T170616Z-d0bc4127"})
    assert _land(_doc(m), _tmp()).code == "E07"


def test_e08_reference_type_mismatch(tmp=None):
    tmp = _tmp()
    # design 슬롯에 RQ 를 넣으면 유형 불일치
    target = tmp / "docs/requests/public/2026/07/RQ-20260728T170616Z-d0bc4127.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    m = _meta(refs={"design": "RQ-20260728T170616Z-d0bc4127"})
    assert _land(_doc(m), tmp).code == "E08"


# ── E10~E13 ─────────────────────────────────────────────────────
def test_e11_non_text_extension_rejected():
    assert gate.check_docs_path("docs/design/public/2026/07/x.png") == "E11"


def test_e10_wrong_format_for_category():
    """A§1.4 — design 카테고리에 jsonl 은 계약 밖."""
    assert gate.check_docs_path("docs/design/public/2026/07/x.jsonl") == "E10"
    assert gate.check_docs_path("docs/audit/public/2026/07/x.md") == "E10"


def test_e12_credential_pattern_rejected():
    body = _DESIGN_BODY + "\napi_key: sk-live-0123456789abcdef0123456789abcdef\n"
    assert _land(_doc(body=body), _tmp()).code == "E12"


def test_e13_duplicate_id_in_shard():
    tmp = _tmp()
    first = _land(_doc(), tmp)
    assert first.ok
    assert _land(_doc(), tmp).code == "E13"


# ── X5 / E17 루트 격리 ───────────────────────────────────────────
def test_e17_root_mismatch_rejected():
    assert _land(_doc(_meta(root="ffffffffffff")), _tmp()).code == "E17"


def test_root_field_is_required():
    m = _meta()
    del m["root"]
    assert _land(_doc(m), _tmp()).code == "E03"


def test_bootstrap_root_is_accepted_before_repo_exists():
    ident = gate.Identity(machine="m01-wsl", session="s-test", author="테스트작성자",
                          role_key="lead", root="bootstrap")
    r = _land(_doc(_meta(root="bootstrap")), _tmp(), identity=ident)
    assert r.ok, r.code


# ── X2 / E16 아부 금지 착지 차단 ─────────────────────────────────
def test_e16_praise_in_closed_target_field_blocks_landing():
    body = _DESIGN_BODY.replace('- what: "조건"', '- what: "훌륭한 조건"')
    assert '훌륭' in body                      # 치환이 실제로 걸렸음을 먼저 확인한다
    assert _land(_doc(body=body), _tmp()).code == "E16"


def test_e16_does_not_fire_outside_closed_target_fields():
    """대상 집합 밖 산문은 차단 대상이 아니다(F§7.9 — 대상을 열면 오차단)."""
    body = _DESIGN_BODY + "\n리뷰 대상 문서에 훌륭한 이라는 낱말이 있었다.\n"
    r = _land(_doc(body=body), _tmp())
    assert r.ok, r.code


# ── X2 규율 슬롯 린트 (TS1) ──────────────────────────────────────
def test_lint_applies_to_type_floor_even_without_items():
    """type ∈ {DS,DN,RF} 이고 항목 0건이면 '규율 항목 없음' 선언이 필요하다."""
    body = _DESIGN_BODY.replace("규율 항목 없음", "본문입니다")
    assert _land(_doc(body=body), _tmp()).code == "E06"


def test_lint_applies_by_content_trigger_on_non_floor_type():
    """type=RQ 라도 규율 항목이 있으면 린트 대상이다(불교차 결함의 시정)."""
    assert gate.lint_target({"type": "RQ"}, "- [R1] 무언가 [장치 — 있음]") is True
    assert gate.lint_target({"type": "RQ"}, "규율 없는 본문") is False
    assert gate.lint_target({"type": "DS"}, "규율 없는 본문") is True
    assert gate.lint_target({"type": "AU"}, "규율 없는 본문") is False


def test_lint_rejects_item_without_slot():
    body = _DESIGN_BODY.replace("규율 항목 없음", "- [R1] 규율인데 슬롯이 없다")
    assert _land(_doc(body=body), _tmp()).code == "E06"


def test_lint_rejects_empty_slot_branch():
    body = _DESIGN_BODY.replace("규율 항목 없음", "- [R1] 규율 [장치 — ]")
    assert _land(_doc(body=body), _tmp()).code == "E06"


def test_lint_accepts_item_with_either_branch():
    body = _DESIGN_BODY.replace(
        "규율 항목 없음", "- [R1] 규율이다 [장치 불가 — 세계 지식이 필요하다]")
    assert _land(_doc(body=body), _tmp()).ok


# ── X1 output_kind 와 증거 분기 (TS2) ────────────────────────────
def test_rq_requires_output_kind():
    from harness_core import schema as sc
    missing = sc.missing_required({"type": "RQ", "schema": 2}, )
    assert "output_kind" in missing


def test_evidence_required_branches_on_output_kind():
    assert schema.evidence_required("code", unit_produces_code=None) == "tdd"
    assert schema.evidence_required("document", unit_produces_code=None) == "doc"
    assert schema.evidence_required("mixed", unit_produces_code=True) == "tdd"
    assert schema.evidence_required("mixed", unit_produces_code=False) == "doc"


def test_evidence_required_rejects_unknown_kind():
    for bad in [None, "", "code-ish", "docs"]:
        try:
            schema.evidence_required(bad, unit_produces_code=None)
        except ValueError:
            continue
        raise AssertionError(f"must reject {bad!r}")


def test_no_branch_returns_empty_evidence_set():
    """R40 — 어느 분기에도 증거 0 은 없다."""
    for kind in ("tdd", "doc"):
        assert schema.EVIDENCE_FIELDS[kind]


def test_dev_stage_doc_evidence_required_when_output_kind_document():
    m = _meta(stage="dev", refs={})
    body = "## 요약\n\n내용.\n\n## 본문\n\n규율 항목 없음\n"
    r = _land(_doc(m, body), _tmp())
    assert r.code == "E03", r.code   # tdd_evidence 도 doc_evidence 도 없다


# ── X7 status/state 단일화 (TS8) ─────────────────────────────────
def test_schema2_rejects_status_key():
    m = _meta()
    m["status"] = "active"
    assert _land(_doc(m), _tmp()).code == "E03"


def test_schema1_status_is_read_as_state():
    """A§4.3 — 구판 문서는 재작성하지 않고 읽기 도구가 판별 분기한다."""
    assert schema.read_state({"schema": 1, "status": "active"}) == "active"
    assert schema.read_state({"schema": 2, "state": "active"}) == "active"


# ── A§7.2 미분류 처리 ────────────────────────────────────────────
def test_unknown_tag_is_reclassified_not_rejected():
    r = _land(_doc(_meta(tags=["stage/design", "origin/agent", "존재하지않는축/값"])),
              _tmp())
    assert r.ok, r.code
    assert any(t.startswith("unclassified/") for t in r.meta["tags"])


def test_tags_cannot_be_empty():
    assert _land(_doc(_meta(tags=[])), _tmp()).code == "E03"


# ── A§5.2 원자 쓰기 / 착지 후 상태 ───────────────────────────────
def test_landing_writes_atomically_and_leaves_no_tmp():
    tmp = _tmp()
    r = _land(_doc(), tmp)
    assert (tmp / r.path).exists()
    assert list(tmp.rglob("*.tmp")) == []


def test_rejection_creates_no_file():
    tmp = _tmp()
    _land(_doc(_meta(schema=99)), tmp)
    assert list((tmp / "docs").rglob("*.md")) == []
