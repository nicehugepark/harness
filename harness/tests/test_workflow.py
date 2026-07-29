"""워크플로 실행 엔진 계약의 실패 선행 테스트.

정본: B§2.2 워크플로 골격 · B§7.2 기록 평면 동일성 · C§6 3렌즈 운용
      · C§6.2 의견 스키마 · C§6.3 판정 레코드 · X4 독립 검증 합산 규칙
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import pipeline as P, workflow as W  # noqa: E402


class StubExec:
    """스텝 실행기를 갈아끼울 수 있어야 한다 — 엔진 계약을 실제 세션 없이 잰다.

    B§7.2: 실행 엔진은 교체 가능하고 기록 평면은 하나다. 그 성질이 성립하면
    같은 엔진 계약을 스텁으로도 실물로도 돌릴 수 있다.
    """

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []

    def __call__(self, *, role, step, inputs, schema):
        self.calls.append({"role": role, "step": step, "inputs": inputs})
        out = self.script.get(step)
        if callable(out):
            out = out(len([c for c in self.calls if c["step"] == step]))
        return out


CRIT = [{"id": "c1", "what": "조건", "how": "명령", "pass": "0건"}]
DESIGN = {"criteria": CRIT, "criteria_ref": "DS-x#criteria@v1",
          "test_scenarios": ["TS1"],
          "work_breakdown": [{"unit_id": "u1", "role": "backend-developer",
                              "file_scope": ["src/a.py"], "produces_code": True}]}
REVIEW_PASS = {"target_ref": "DS-x", "reviewer_role": "qa",
               "lens": "design-adversarial", "criteria_version": 1,
               "scope_declared": ["§1"], "scope_not_covered": ["§9 성능 축"],
               "findings": [], "verdict": "pass"}
BUILD_OK = {"unit_id": "u1", "tdd_evidence": {k: "x" for k in P.TDD_EVIDENCE_FIELDS}}
OPINION = {"lens": "caveman", "target_ref": "u1", "opinions": []}


def _verdict(met=True, verdict="pass", n_opinions=2):
    """렌즈 2인이 의견을 내므로 처분도 2건이어야 한다 — 전량 처분이 판정 제출의
    선행 조건이고, 그것이 탐지-무소비 루프를 막는 자리다(C§6.3)."""
    return {"target_ref": "u1", "criteria_source": "RQ-x#완료기준",
            "criteria_frozen": True, "criteria_version": 1,
            "per_criterion": [{"criterion_id": "c1", "met": met,
                               "evidence": {"command": "run", "output_excerpt": "ok",
                                            "anchor": "a"}}],
            "abbreviation_check": "none_found",
            "opinion_dispositions": [
                {"opinion_ref": f"op{i}", "disposition": "rejected",
                 "rationale": "산출물 범위 밖"} for i in range(n_opinions)],
            "verdict": verdict}


def _req():
    r = dict(P.new_request_fields())
    r.update({"id": "RQ-20260729T000000Z-11111111", "state": "queued",
              "eligible": True, "session_ref": "s1", "output_kind": "code",
              "dod_present": True, "priority": 1, "importance": 1,
              "weight": "standard", "depends_on": [],
              "refs": {"analysis": "DS-20260728T184100Z-25404613"}})
    return r


# ── X4 독립 검증 합산 규칙 ──────────────────────────────────────
def test_review_gate_requires_scope_not_covered():
    bad = dict(REVIEW_PASS, scope_not_covered=[])
    assert W.review_gate([bad], required=1)["result"] == "INVALID"


def test_review_gate_blocks_on_a_single_must_fix():
    """다수결이 아니다 — must-fix 1건이면 불통과다. 다수결은 단독 정답을 삼킨다."""
    a = dict(REVIEW_PASS)
    b = dict(REVIEW_PASS, verdict="reject", findings=[
        {"anchor": "§2", "claim": "결함", "severity": "must-fix",
         "evidence": {"doc_ref": "d", "output_excerpt": "x"}}])
    out = W.review_gate([a, b], required=2)
    assert out["result"] == "reject" and len(out["must_fix"]) == 1


def test_review_gate_rejects_verdict_without_supporting_finding():
    bad = dict(REVIEW_PASS, verdict="reject", findings=[])
    assert W.review_gate([bad], required=1)["result"] == "INVALID"


def test_review_gate_needs_the_required_reviewer_count():
    assert W.review_gate([REVIEW_PASS], required=2)["result"] == "INVALID"


# ── C§6.3 verify_gate ───────────────────────────────────────────
def test_verify_gate_requires_every_criterion_and_every_opinion_disposed():
    ok, why = W.verify_gate(_verdict(n_opinions=0), CRIT, [])
    assert ok, why
    ok, why = W.verify_gate(_verdict(n_opinions=0), CRIT + [{"id": "c2"}], [])
    assert not ok and "기준" in why
    # 의견 1건을 받았는데 처분이 0건이면 판정을 제출할 수 없다
    ok, why = W.verify_gate(_verdict(n_opinions=0), CRIT, [OPINION])
    assert not ok and "의견" in why
    # 처분 수가 맞으면 통과한다
    assert W.verify_gate(_verdict(n_opinions=1), CRIT, [OPINION])[0]


def test_verify_gate_blocks_pass_when_a_criterion_is_unmet():
    ok, why = W.verify_gate(_verdict(met=False, verdict="pass", n_opinions=0), CRIT, [])
    assert not ok and "판정" in why


def test_verify_gate_requires_frozen_criteria():
    v = dict(_verdict(n_opinions=0), criteria_frozen=False)
    ok, why = W.verify_gate(v, CRIT, [])
    assert not ok and "동결" in why


def test_lens_opinion_schema_has_no_verdict_field():
    """C§6.2 — 판정 필드가 없어 위반이 표현 불가능하다."""
    assert "verdict" not in W.OPINION_FIELDS
    assert "severity" not in W.OPINION_FIELDS
    ok, why = W.validate_opinion({**OPINION, "verdict": "pass"})
    assert not ok and "판정" in why


# ── B§2.2 골격 ──────────────────────────────────────────────────
def test_happy_path_reaches_merging_and_records_every_transition():
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": _verdict()})
    out = W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    assert out["state"] == "done", out
    names = [e["event"] for e in out["events"]]
    assert names[:4] == ["session.started", "req.dispatched",
                         "req.design_landed", "req.build_reported"]
    assert "req.verified" in names and "req.merged" in names


def test_staff_engineer_is_never_spawned_in_verification():
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": _verdict()})
    W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    verifying = [c for c in ex.calls if c["step"] in ("verify", "opinion")]
    assert all(c["role"] != "staff-engineer" for c in verifying)


def test_lenses_do_not_receive_the_criteria():
    """C§6.1-2 — 기준이 입력이면 무전제 렌즈가 오염된다."""
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": _verdict()})
    W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    for c in ex.calls:
        if c["step"] == "opinion":
            assert "criteria" not in c["inputs"] and "criteria_ref" not in c["inputs"]


def test_rework_loop_is_bounded_by_K_then_holds():
    fails = _verdict(met=False, verdict="rework")
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": fails})
    out = W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    assert out["state"] in ("hold", "failed")
    assert out["request"]["rework_count"] == P.DEFAULTS["K_rework"]


def test_design_escalation_is_bounded_by_E():
    esc = _verdict(met=False, verdict="escalate_design")
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": esc})
    out = W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    assert out["request"]["escalation_count"] <= P.DEFAULTS["E_escalation"]
    assert out["state"] in ("hold", "failed")


def test_escalation_bumps_criteria_version():
    seq = [_verdict(met=False, verdict="escalate_design"), _verdict()]
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION,
                   "verify": lambda n: seq[min(n - 1, len(seq) - 1)]})
    out = W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    assert out["request"]["criteria_version"] == 2


def test_design_review_rejection_loops_then_holds():
    rej = dict(REVIEW_PASS, verdict="reject", findings=[
        {"anchor": "§1", "claim": "미달", "severity": "must-fix",
         "evidence": {"doc_ref": "d", "output_excerpt": "x"}}])
    ex = StubExec({"design": DESIGN, "design-review": rej})
    out = W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    assert out["state"] == "hold"
    assert out["request"]["design_review_count"] == P.DEFAULTS["Ed_design_review"]


def test_workers_get_file_scope_and_ledger_path_only():
    """B-6 — 완료기준은 포인터로, 대상은 구조화 필드로. 산문 재기술 금지."""
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": _verdict()})
    W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True})
    b = next(c for c in ex.calls if c["step"] == "build")
    assert set(b["inputs"]) >= {"ledger_path", "unit_id", "file_scope",
                                "baseline_ref"}
    assert not any(k in b["inputs"] for k in ("criteria_text", "dod_text"))


def test_merge_failure_returns_to_building_and_counts_toward_K():
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": _verdict()})
    out = W.run(_req(), executor=ex,
                merge=lambda req, v: {"ok": False, "reason": "reverify_failed"})
    assert out["request"]["rework_count"] >= 1


def test_engine_field_is_stamped_on_every_event():
    """B§7.2-3 — 평면은 하나, 출처는 구별."""
    ex = StubExec({"design": DESIGN, "design-review": REVIEW_PASS,
                   "build": BUILD_OK, "opinion": OPINION, "verify": _verdict()})
    out = W.run(_req(), executor=ex, merge=lambda req, v: {"ok": True},
                engine="loop")
    assert all(e.get("engine") == "loop" for e in out["events"])
