"""정본: B§2.2 워크플로 골격 · B§7 폴백판 평면 통합 · C§6 3렌즈 운용
      · C§6.2 의견 스키마 · C§6.3 판정 레코드 · X4 독립 검증 합산 규칙.

**실행 엔진은 교체 가능하고 기록 평면은 하나다.** 그 성질을 코드로 만드는 것이
`executor` 주입이다 — 같은 엔진 계약을 스텁으로도 실물 세션으로도 돌린다.
엔진별 스키마 사본을 두지 않는다(사본 발산 금지).

병렬은 **파일 소유권이 서로소인 유닛**과 **무전제 렌즈 2인**에만 건다. 병렬
병목은 역할 구성이 아니라 공유 파일 편집의 직렬화였다는 실측을 따른다 —
편집은 병렬, 커밋은 컨테이너가 유닛 경계에서 직렬로 한다.
"""
from __future__ import annotations

import concurrent.futures as _cf

from . import pipeline, policy

DEFAULTS = policy.DEFAULTS

# C§6.2 — 판정·심각도 필드가 **존재하지 않는다**. 위반이 표현 불가능해진다.
OPINION_FIELDS = ["lens", "target_ref", "opinions"]
OPINION_ITEM_REQUIRED = ["observation", "where", "expectation_gap"]

# X4 — 독립 검증 산출
REVIEW_FIELDS = ["target_ref", "reviewer_role", "lens", "criteria_version",
                 "scope_declared", "scope_not_covered", "findings", "verdict"]
SEVERITIES = ["must-fix", "should-fix", "note"]


# ── C§6.2 의견 스키마 ───────────────────────────────────────────
def validate_opinion(op: dict) -> tuple[bool, str]:
    for f in OPINION_FIELDS:
        if f not in op:
            return False, f"의견 필수 필드 결손: {f}"
    for banned in ("verdict", "severity", "pass", "fail"):
        if banned in op:
            return False, (f"의견에는 판정 필드가 없다 — {banned!r} 는 렌즈의 "
                           f"지위(의견 제시)를 침식한다")
    for item in op.get("opinions") or []:
        for f in OPINION_ITEM_REQUIRED:
            if not str(item.get(f, "")).strip():
                return False, f"의견 항목 결손: {f}('느낌'만의 의견은 스키마 미충족)"
    return True, ""


# ── X4 독립 검증 합산 ───────────────────────────────────────────
def validate_review(v: dict) -> tuple[bool, str]:
    for f in REVIEW_FIELDS:
        if f not in v:
            return False, f"리뷰 필수 필드 결손: {f}"
    if not v.get("scope_not_covered"):
        return False, ("무엇을 보지 않았는지를 선언하지 않은 리뷰는 성립하지 "
                       "않는다 — 검출 0건과 안 봤다를 구별할 수 없다")
    for f in v.get("findings") or []:
        if f.get("severity") not in SEVERITIES:
            return False, f"severity enum 밖: {f.get('severity')!r}"
        if not str(f.get("anchor", "")).strip():
            return False, "결함에 앵커가 없다(줄 번호가 아닌 앵커)"
    return True, ""


def review_gate(verdicts: list, *, required: int) -> dict:
    """합산은 **다수결이 아니다** — must-fix 1건이면 불통과다.

    근거: 3인 검토를 일률 강제한 규칙이 스스로 결함 판정을 받았고, 같은 오류를
    3명이 독립적으로 냈으며, 결함을 잡은 것은 합의가 아니라 관점이 다른 단독
    재측정이었다. 다수결은 단독 정답을 삼킨다.

    severity 가중 점수도 기각한다 — 임계가 재량이면 판정이 결정론이 아니게 되고,
    점수는 must-fix 를 note 로 낮추려는 압력을 만든다.
    """
    if len(verdicts) < required:
        return {"result": "INVALID", "why": f"리뷰어 부족 {len(verdicts)}/{required}"}
    for v in verdicts:
        ok, why = validate_review(v)
        if not ok:
            return {"result": "INVALID", "why": why}
    must_fix = [f for v in verdicts for f in (v.get("findings") or [])
                if f.get("severity") == "must-fix"]
    if must_fix:
        return {"result": "reject", "must_fix": must_fix}
    for v in verdicts:
        if v.get("verdict") == "reject":
            return {"result": "INVALID",
                    "why": "reject 판정인데 must-fix 결함이 없다 — 판정과 근거 불일치"}
    return {"result": "pass", "must_fix": []}


# ── C§6.3 검증 판정 게이트 ──────────────────────────────────────
def verify_gate(record: dict, criteria: list, opinions: list) -> tuple[bool, str]:
    if not record.get("criteria_frozen"):
        return False, "기준 동결 미확인 — 판정 무효"
    want = {c["id"] for c in criteria}
    got = {p.get("criterion_id") for p in record.get("per_criterion") or []}
    if want != got:
        return False, f"기준 항목 불일치(누락 {sorted(want - got)})"
    if len(record.get("opinion_dispositions") or []) != len(opinions):
        return False, ("수신 의견 전량 처분이 판정 제출의 선행 조건이다 — "
                       "탐지-무소비 루프 차단")
    for d in record.get("opinion_dispositions") or []:
        if not str(d.get("rationale", "")).strip():
            return False, "의견 처분에 근거가 없다(기각에도 근거 필수)"
        if d.get("disposition") == "adopted" and not (
                d.get("mapped_criterion") or d.get("new_defect")):
            return False, "채택 의견의 귀속이 없다"
    all_met = all(p.get("met") for p in record.get("per_criterion") or [])
    clean = record.get("abbreviation_check") == "none_found"
    allowed = {"pass"} if (all_met and clean) else {"rework", "escalate_design"}
    if record.get("verdict") not in allowed:
        return False, (f"판정-근거 불일치: verdict={record.get('verdict')!r} 는 "
                       f"{sorted(allowed)} 중 하나여야 한다")
    return True, ""


# ── B§2.2 골격 ──────────────────────────────────────────────────
def _fanout(jobs, cap: int):
    """유닛·렌즈 병렬 실행. 상한은 머신 편성에서 온 값이고 무상한은 금지다."""
    if cap <= 1 or len(jobs) <= 1:
        return [fn() for fn in jobs]
    with _cf.ThreadPoolExecutor(max_workers=min(cap, len(jobs))) as pool:
        return [f.result() for f in [pool.submit(fn) for fn in jobs]]


def run(request: dict, *, executor, merge, engine: str = "workflow",
        ledger_path: str = "docs/requests/public/2026/07/RQ.md",
        baseline_ref: str = "main", concurrency_cap: int = 8,
        pol: dict | None = None) -> dict:
    """요청 1건을 완주시킨다. 상태 전이는 전부 pipeline.apply() 를 경유한다 —
    이 함수가 상태를 직접 쓰는 자리는 없다."""
    pol = pol or DEFAULTS
    req = dict(request)
    events: list[dict] = []

    def emit(name, payload=None):
        events.append({"event": name, "engine": engine, "req": req.get("id"),
                       "payload": payload or {}})

    def step(to, ctx=None):
        nonlocal req
        req, ev = pipeline.apply(req, to, ctx or {})
        ev["engine"] = engine
        ev["req"] = req.get("id")
        events.append(ev)

    emit("session.started")
    step("designing", {"machine": "m", "session_ref": req.get("session_ref"),
                       "decision_ref": "tick"})

    design = None
    for d in range(1, pol["Ed_design_review"] + 1):
        design = executor(role="architect", step="design",
                          inputs={"ledger_path": ledger_path,
                                  "baseline_ref": baseline_ref},
                          schema="DesignDoc")
        reviews = [executor(role="qa", step="design-review",
                            inputs={"ledger_path": ledger_path,
                                    "target_ref": design.get("criteria_ref")},
                            schema="ReviewVerdict")]
        gate = review_gate(reviews, required=int(pol.get("required_reviewers", 1)))
        if gate["result"] == "pass":
            break
        emit("design.review_rejected", {"n": d, "result": gate["result"]})
        req["design_review_count"] = d
        design = None
    if design is None:
        # 설계 리뷰 소진 = 요구 모호 판정 → 항상 사람 상신(전이 12 선택식)
        req["state"] = pipeline.terminal_choice("designing", passed_units=0)
        emit("req.exhausted", {"limit_kind": "design_review"})
        return {"state": req["state"], "request": req, "events": events}

    req["design_landed"] = True
    req["adversarial_review_passed"] = True
    units = design["work_breakdown"]
    step("building", {"design_doc_ref": "DS-x",
                      "criteria_ref": design["criteria_ref"],
                      "work_units": units})

    pending = list(units)
    verdicts: list[dict] = []
    for k in range(1, pol["K_rework"] + 1):
        reports = _fanout([
            (lambda u=u: executor(role=u.get("role", "backend-developer"),
                                  step="build",
                                  inputs={"ledger_path": ledger_path,
                                          "unit_id": u["unit_id"],
                                          "file_scope": u["file_scope"],
                                          "baseline_ref": baseline_ref},
                                  schema="BuildReport"))
            for u in pending], concurrency_cap)
        req["units"] = [{"unit_id": u["unit_id"],
                         "produces_code": u.get("produces_code"),
                         "report": r} for u, r in zip(pending, reports)]
        step("verifying", {"unit_report_refs": [u["unit_id"] for u in pending]})

        # 무전제 렌즈는 **기준을 받지 않는다** — 기준이 입력이면 렌즈가 오염된다
        opinions = _fanout([
            (lambda lens=lens: executor(role=lens, step="opinion",
                                        inputs={"target_ref": pending[0]["unit_id"]},
                                        schema="Opinion"))
            for lens in ("caveman", "freshman")], concurrency_cap)
        for op in opinions:
            ok, why = validate_opinion(op)
            if not ok:
                emit("gate.block", {"rule": "opinion-schema", "why": why})

        v = executor(role="qa", step="verify",
                     inputs={"ledger_path": ledger_path,
                             "criteria_ref": design["criteria_ref"],
                             "opinions": opinions},
                     schema="Verdict")
        ok, why = verify_gate(v, design["criteria"], opinions)
        if not ok:
            emit("gate.block", {"rule": "verify-gate", "why": why})
            v = dict(v, verdict="rework")
        verdicts = [v]

        if v["verdict"] == "pass":
            break
        if v["verdict"] == "escalate_design":
            if req["escalation_count"] >= pol["E_escalation"]:
                req["state"] = pipeline.terminal_choice("verifying", 0)
                emit("req.exhausted", {"limit_kind": "escalation"})
                return {"state": req["state"], "request": req, "events": events}
            step("designing", {"verdict_refs": ["v"]})
            design = executor(role="architect", step="design",
                              inputs={"ledger_path": ledger_path,
                                      "baseline_ref": baseline_ref},
                              schema="DesignDoc")
            req["design_landed"] = True
            req["adversarial_review_passed"] = True
            step("building", {"design_doc_ref": "DS-x",
                              "criteria_ref": design["criteria_ref"],
                              "work_units": design["work_breakdown"]})
            pending = design["work_breakdown"]
            continue
        if req["rework_count"] >= pol["K_rework"]:
            break
        step("building", {"failed_units": [u["unit_id"] for u in pending],
                          "verdict_refs": ["v"]})

    if not verdicts or verdicts[0]["verdict"] != "pass":
        req["state"] = pipeline.terminal_choice(
            "verifying", passed_units=req.get("passed_units", 0))
        emit("req.exhausted", {"limit_kind": "rework"})
        return {"state": req["state"], "request": req, "events": events}

    # 통과와 반영을 벌리지 않는다 — 사이에 사람 대기·큐 대기를 두지 않는다
    step("merging", {"verdict_refs": ["v"], "baseline_snapshot": "base"})
    res = merge(req, verdicts)
    if not res.get("ok"):
        if req["rework_count"] < pol["K_rework"]:
            step("building", {"delta_paths_ref": "d", "verdict_ref": "v"})
        else:
            req["state"] = "hold"
            emit("req.merge_contention", {"attempts": req["merge_retry_count"]})
        return {"state": req["state"], "request": req, "events": events}

    req["merge_commit_observed"] = True
    req["ancestry_confirmed"] = True
    step("done", {"merged_ref_name": baseline_ref, "merge_commit_observed": True})
    emit("session.ended", {"outcome": "done"})
    return {"state": req["state"], "request": req, "events": events}
