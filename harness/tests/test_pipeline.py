"""요청 상태기계·전이표 계약의 실패 선행 테스트.

정본: B§1.1 상태 enum 11종 · B§1.2 기계 필드 · B§1.3 전이표 20행
      · B§2.1 워크플로 기동 입력 스키마 · B§2.3 K·E·Ed 상한
      · X1(output_kind 분기) · X3(검증 기준 판 동결)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import pipeline as P  # noqa: E402


def _req(**over):
    r = dict(P.new_request_fields())
    r.update(over)
    return r


# ── B§1.1 상태 enum ─────────────────────────────────────────────
def test_state_enum_is_the_eleven_progressive_states():
    assert P.STATES == ["received", "analyzed", "queued", "designing", "building",
                        "verifying", "merging", "done", "hold", "failed", "void"]


def test_terminal_and_active_partition_is_total():
    assert set(P.TERMINAL_STATES) == {"done", "failed", "void"}
    assert set(P.ACTIVE_EXEC_STATES) == {"designing", "building", "verifying",
                                          "merging"}
    assert set(P.STATES) == (set(P.TERMINAL_STATES) | set(P.ACTIVE_EXEC_STATES)
                             | {"received", "analyzed", "queued", "hold"})


# ── B§1.3 전이표 ────────────────────────────────────────────────
def test_transition_table_has_twenty_rows():
    assert len(P.TRANSITIONS) == 20


def test_every_transition_declares_an_event_name():
    for t in P.TRANSITIONS:
        assert t.event, t


def test_transition_outside_the_table_is_refused():
    """B-1 — 표에 없는 (from,to) 조합은 착지 게이트가 거부한다."""
    r = _req(state="received")
    ok, why = P.can_transition(r, "merging")
    assert not ok and "전이표" in why


def test_terminal_states_have_no_outgoing_transition():
    for s in P.TERMINAL_STATES:
        assert P.allowed_targets(s) == [], s


def test_hold_is_not_terminal_and_resumes_to_queued():
    assert "hold" not in P.TERMINAL_STATES
    assert "queued" in P.allowed_targets("hold")


# ── 가드 ────────────────────────────────────────────────────────
def test_analyzed_requires_dod_and_scheduling_fields():
    r = _req(state="received")
    ok, why = P.can_transition(r, "analyzed")
    assert not ok and ("완료 기준" in why or "스케줄링" in why)
    r = _req(state="received", dod_present=True, priority=2, importance=2,
             weight="standard", depends_on=[], output_kind="code",
             refs={"analysis": "DS-20260728T184100Z-25404613"})
    ok, why = P.can_transition(r, "analyzed")
    assert ok, why


def test_rework_is_capped_by_K():
    r = _req(state="verifying", rework_count=P.DEFAULTS["K_rework"] - 1)
    assert P.can_transition(r, "building")[0]
    r = _req(state="verifying", rework_count=P.DEFAULTS["K_rework"])
    assert not P.can_transition(r, "building")[0]


def test_escalation_is_capped_by_E():
    r = _req(state="verifying", escalation_count=P.DEFAULTS["E_escalation"])
    assert not P.can_transition(r, "designing")[0]


def test_design_review_is_capped_by_Ed():
    r = _req(state="designing", design_review_count=P.DEFAULTS["Ed_design_review"])
    assert not P.can_transition(r, "designing")[0]


def test_exhausted_from_designing_always_holds_never_fails():
    """전이 12 선택식 — 설계 리뷰 소진은 요구 모호 판정이므로 사람 상신이다."""
    assert P.terminal_choice("designing", passed_units=0) == "hold"
    assert P.terminal_choice("designing", passed_units=3) == "hold"


def test_exhausted_from_verifying_splits_on_passed_units():
    assert P.terminal_choice("verifying", passed_units=1) == "hold"
    assert P.terminal_choice("verifying", passed_units=0) == "failed"


def test_human_decision_transitions_require_landed_adjudication():
    """B-5 — 어떤 에이전트·통지 메시지도 사람의 승인이 아니다."""
    r = _req(state="hold")
    assert not P.can_transition(r, "queued")[0]
    r = _req(state="hold", refs={"adjudications": ["AJ-20260729T000000Z-aaaaaaaa"]},
             adjudication_answered=True)
    assert P.can_transition(r, "queued")[0]


def test_done_requires_merge_ancestry_confirmation():
    """B-9 — 썼다≠보존됐다의 머지판. ref 갱신 반환값이 아니라 조상 포함 재조회."""
    r = _req(state="merging")
    assert not P.can_transition(r, "done")[0]
    r = _req(state="merging", merge_commit_observed=True, ancestry_confirmed=True)
    assert P.can_transition(r, "done")[0]


# ── X1 증거 분기가 전이 8 가드에 걸린다 ─────────────────────────
def test_build_reported_guard_uses_output_kind_branch():
    units_doc = [{"unit_id": "u1", "produces_code": False,
                  "report": {"doc_evidence": {k: "x" for k in
                             P.DOC_EVIDENCE_FIELDS}}}]
    r = _req(state="building", output_kind="document", units=units_doc)
    ok, why = P.can_transition(r, "verifying")
    assert ok, why

    units_bad = [{"unit_id": "u1", "produces_code": False, "report": {}}]
    r = _req(state="building", output_kind="document", units=units_bad)
    assert not P.can_transition(r, "verifying")[0]

    units_code = [{"unit_id": "u1", "produces_code": True,
                   "report": {"tdd_evidence": {k: "x" for k in
                              P.TDD_EVIDENCE_FIELDS}}}]
    r = _req(state="building", output_kind="code", units=units_code)
    assert P.can_transition(r, "verifying")[0]


def test_mixed_output_kind_needs_per_unit_produces_code():
    units = [{"unit_id": "u1", "report": {"tdd_evidence": {}}}]
    r = _req(state="building", output_kind="mixed", units=units)
    ok, why = P.can_transition(r, "verifying")
    assert not ok and "produces_code" in why


# ── X3 검증 기준 판 ─────────────────────────────────────────────
def test_criteria_version_starts_at_one_and_only_escalation_bumps_it():
    r = _req(state="designing", criteria_version=0, design_landed=True,
             adversarial_review_passed=True)
    assert P.next_criteria_version(r, "building") == 1
    r = _req(state="verifying", criteria_version=1, rework_count=0)
    assert P.next_criteria_version(r, "building") == 1        # 재작업은 판 불변
    r = _req(state="verifying", criteria_version=1, escalation_count=0,
             defect_class="design")
    assert P.next_criteria_version(r, "designing") == 2       # 에스컬레이션만 증가


def test_frozen_criteria_version_cannot_be_rewritten():
    ok, why = P.check_criteria_freeze(version=1, frozen=True, content_hash="a",
                                      stored_hash="b")
    assert not ok and "동결" in why
    assert P.check_criteria_freeze(version=1, frozen=True, content_hash="a",
                                   stored_hash="a")[0]


# ── B§2.1 기동 입력 스키마 ──────────────────────────────────────
def test_launch_input_requires_ledger_path():
    """S§5.1 — 원장 미등재 실행을 존재 차원에서 차단한다."""
    ok, why = P.validate_launch({"baseline_ref": "main", "engine": "workflow",
                                 "resume": False, "machine_id": "m"})
    assert not ok and "ledger_path" in why


def test_launch_input_refuses_commit_hash_literal_as_baseline():
    """B-6 — 기준선은 ref 이름만. 커밋 해시 리터럴 금지."""
    ok, why = P.validate_launch({
        "ledger_path": "docs/requests/public/2026/07/RQ-x.md",
        "baseline_ref": "a" * 40, "engine": "workflow", "resume": False,
        "machine_id": "m"})
    assert not ok and "해시" in why


def test_launch_input_engine_enum_is_closed():
    base = {"ledger_path": "p", "baseline_ref": "main", "resume": False,
            "machine_id": "m"}
    assert not P.validate_launch({**base, "engine": "magic"})[0]
    assert P.validate_launch({**base, "engine": "loop"})[0]


# ── B§1.5 이벤트 동반 ───────────────────────────────────────────
def test_apply_transition_emits_exactly_one_event_with_payload():
    r = _req(state="queued", eligible=True, session_ref="s1", kill_switch=False)
    new, ev = P.apply(r, "designing", {"machine": "m01", "decision_ref": "t1"})
    assert new["state"] == "designing"
    assert ev["event"] == "req.dispatched"
    for f in P.required_payload("req.dispatched"):
        assert f in ev["payload"], f


def test_apply_refuses_and_emits_nothing_on_guard_failure():
    r = _req(state="received")
    try:
        P.apply(r, "merging", {})
    except P.TransitionRefused:
        return
    raise AssertionError("가드 불통과 전이는 이벤트를 남기지 않고 거부해야 한다")


def test_counters_increment_on_their_transitions():
    r = _req(state="verifying", rework_count=0)
    new, _ = P.apply(r, "building", {"failed_units": ["u1"],
                                     "verdict_refs": ["vr-1"]})
    assert new["rework_count"] == 1


# ── 이중 기동 (실사고 2026-07-29T04:33) ─────────────────────────
def test_dispatch_must_claim_the_ledger_before_spawning():
    """실사고: dispatch 가 세션만 띄우고 **원장에 상태를 쓰지 않아** 틱마다
    같은 요청을 다시 기동했다(같은 요청에 세션 3개).

    전이 5 는 `session_ref` 기입을 스케줄러 몫으로 정한다 — 그 기입이 곧
    lease 이고, lease 없이 띄운 세션은 이중 세션 차단의 전제를 잃는다.
    claim 은 **띄우기 전에** 한다: 띄우고 나서 claim 하면 그 사이 크래시가
    고아 세션을 남긴다.
    """
    from harness_core import ledger
    assert ledger.CLAIM_BEFORE_SPAWN is True


def test_claim_is_compare_and_set_on_queued():
    from harness_core import ledger
    ok, why = ledger.can_claim({"state": "queued", "session_ref": None})
    assert ok
    ok, why = ledger.can_claim({"state": "designing", "session_ref": "s1"})
    assert not ok and "이미" in why
    ok, why = ledger.can_claim({"state": "queued", "session_ref": "s0"})
    assert not ok and "lease" in why


def test_lease_mismatch_is_fenced():
    from harness_core import ledger
    assert ledger.lease_ok({"session_ref": "s1"}, "s1") is True
    assert ledger.lease_ok({"session_ref": "s1"}, "s2") is False
    assert ledger.lease_ok({"session_ref": None}, "s2") is False
