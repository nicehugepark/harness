"""정본: B§1.1 상태 enum · B§1.2 기계 필드 · B§1.3 전이표 · B§1.5 규율
      · B§2.1 기동 입력 스키마 · B§2.3 K·E·Ed · X1 증거 분기 · X3 기준 판 동결.

**상태를 쓰는 유일 경로가 이 모듈의 전이 함수다.** 전이표에 없는 (from,to)는
표현할 수 없고, 가드를 통과하지 못한 전이는 이벤트를 남기지 않는다 — "이벤트
없는 상태 변경 금지"는 규율이 아니라 `apply()` 가 둘을 한 단위로 하기 때문에
성립한다.
"""
from __future__ import annotations

import dataclasses
import re

from . import policy, schema

# ── B§1.1 상태 enum (11종 진행형) ───────────────────────────────
STATES = ["received", "analyzed", "queued", "designing", "building",
          "verifying", "merging", "done", "hold", "failed", "void"]
TERMINAL_STATES = ["done", "failed", "void"]
ACTIVE_EXEC_STATES = ["designing", "building", "verifying", "merging"]
ACTIVE_STATES = ["received", "analyzed", "queued"] + ACTIVE_EXEC_STATES

DEFAULTS = policy.DEFAULTS
TDD_EVIDENCE_FIELDS = schema.EVIDENCE_FIELDS["tdd"]
DOC_EVIDENCE_FIELDS = schema.EVIDENCE_FIELDS["doc"]

BLOCKED_ON = ["capacity", "dependency", "dependency-failed", "gate",
              "file-conflict", "quota", "human"]


class TransitionRefused(Exception):
    """가드 불통과. 이벤트를 남기지 않는다."""


@dataclasses.dataclass(frozen=True)
class Transition:
    n: int
    frm: tuple
    to: str
    trigger: str
    event: str
    payload: tuple
    guard: str            # guard 함수명


# ── B§1.3 전이표 (20행) ─────────────────────────────────────────
TRANSITIONS: list[Transition] = [
    Transition(1, ("__new__",), "received", "리드 인테이크→원장 등재",
               "req.received", ("requester_type", "derived_from"), "g_received"),
    Transition(2, ("received",), "analyzed", "pm 분석 문서 착지",
               "req.analyzed", ("analysis_doc_ref", "weight"), "g_analyzed"),
    Transition(3, ("received",), "void", "오등재 정정",
               "req.voided", ("reason", "approved_by"), "g_human"),
    Transition(4, ("analyzed",), "queued", "자동(2 직후)",
               "req.enqueued", ("scheduling_fields",), "g_enqueued"),
    Transition(5, ("queued",), "designing", "스케줄러 dispatch",
               "req.dispatched", ("machine", "session_ref", "decision_ref"),
               "g_dispatch"),
    Transition(6, ("designing",), "designing", "설계 리뷰 반려",
               "design.review_rejected", ("review_verdict_ref", "n"),
               "g_design_review"),
    Transition(7, ("designing",), "building", "설계+기준 착지 + 적대 리뷰 통과",
               "req.design_landed", ("design_doc_ref", "criteria_ref",
                                     "work_units"), "g_design_landed"),
    Transition(8, ("building",), "verifying", "전 유닛 BuildReport 착지",
               "req.build_reported", ("unit_report_refs",), "g_build_reported"),
    Transition(9, ("verifying",), "building", "판정 불통과(결함≠설계)",
               "req.rework", ("failed_units", "verdict_refs"), "g_rework"),
    Transition(10, ("verifying",), "designing", "판정 불통과(결함=설계)",
               "req.escalated", ("verdict_refs",), "g_escalate"),
    Transition(11, ("verifying",), "merging", "전 유닛 verdict=pass",
               "req.verified", ("verdict_refs", "baseline_snapshot"),
               "g_verified"),
    Transition(12, ("designing", "verifying"), "hold", "루프 상한 소진",
               "req.exhausted", ("failure_doc_ref", "limit_kind", "passed_units"),
               "g_exhausted"),
    Transition(13, ("merging",), "building", "델타 재검증 실패",
               "req.merge_reverify_failed", ("delta_paths_ref", "verdict_ref"),
               "g_rework"),
    Transition(14, ("merging",), "done", "머지 + 추적 확인 통과",
               "req.merged", ("merged_ref_name", "merge_commit_observed"),
               "g_merged"),
    Transition(15, ("merging",), "hold", "머지 재시도 상한 초과",
               "req.merge_contention", ("attempts", "failure_doc_ref"),
               "g_merge_contention"),
    Transition(16, tuple(ACTIVE_STATES), "hold", "킬 스위치·사람 개입·재기동 상한",
               "req.held", ("reason",), "g_held"),
    Transition(17, ("hold",), "queued", "사람 결정(의사결정 문서 응답)",
               "req.resumed", ("adjudication_ref",), "g_human_answer"),
    Transition(18, ("hold",), "failed", "사람 결정",
               "req.closed", ("adjudication_ref", "failure_doc_ref"),
               "g_human_answer"),
    Transition(19, ("hold",), "void", "사람 결정",
               "req.voided", ("adjudication_ref", "reason"), "g_human_answer"),
    Transition(20, ("queued",), "hold", "기아 상신 후 사람이 보류 선택",
               "req.held", ("reason",), "g_human_answer"),
    # 전이 12 는 designing·verifying 양쪽을 from 으로 갖는다(B§1.3 각주) —
    # from 을 verifying 단독으로 두면 설계 리뷰 소진이 전이표상 불법이 되어
    # 게이트가 자기 의사코드를 차단한다.
]
# 전이 12 가 failed 로도 갈 수 있다(선택식) — 표를 늘리지 않고 to 를 선택식으로
# 두면 "표에 없는 전이"가 되므로, failed 행을 명시 추가해 20행을 유지한다.
TRANSITIONS[11] = dataclasses.replace(TRANSITIONS[11], to="hold|failed")


def allowed_targets(state: str) -> list[str]:
    out = []
    for t in TRANSITIONS:
        if state in t.frm:
            out += t.to.split("|")
    return sorted(set(out))


def _find(state: str, to: str) -> Transition | None:
    for t in TRANSITIONS:
        if state in t.frm and to in t.to.split("|"):
            return t
    return None


def required_payload(event: str) -> tuple:
    for t in TRANSITIONS:
        if t.event == event:
            return t.payload
    return ()


def new_request_fields() -> dict:
    """B§1.2 — 실행 엔진이 소비·기입하는 기계 필드의 초기값."""
    return {
        "state": "received", "rework_count": 0, "escalation_count": 0,
        "design_review_count": 0, "restart_count": 0, "merge_retry_count": 0,
        "blocked_on": None, "derived_from": None, "session_ref": None,
        "base_observed": None,
        # X1·X3
        "output_kind": None, "criteria_version": 0, "units": [],
        # 가드 입력
        "dod_present": False, "priority": None, "importance": None,
        "weight": None, "depends_on": None, "refs": {},
        "adjudication_answered": False, "merge_commit_observed": False,
        "ancestry_confirmed": False, "eligible": False, "kill_switch": False,
        "design_landed": False, "adversarial_review_passed": False,
        "defect_class": None, "passed_units": 0,
    }


# ── 가드 ────────────────────────────────────────────────────────
def g_received(r, ctx):
    return True, ""


def g_analyzed(r, ctx):
    if not r.get("dod_present"):
        return False, "완료 기준(DoD)이 없다 — 미등재 실행 봉쇄와 같은 뿌리다"
    missing = [f for f in ("priority", "importance", "weight", "output_kind")
               if r.get(f) in (None, "")]
    if r.get("depends_on") is None:
        missing.append("depends_on")
    if missing:
        return False, f"스케줄링 필드 결손: {missing}"
    if not (r.get("refs") or {}).get("analysis"):
        return False, "분석 문서 참조가 없다"
    return True, ""


def g_enqueued(r, ctx):
    return g_analyzed(r, ctx)


def g_dispatch(r, ctx):
    if r.get("kill_switch"):
        return False, "킬 스위치 on"
    if not r.get("eligible"):
        return False, "적격 집합 밖"
    if not r.get("session_ref"):
        return False, "lease(session_ref) 미발급 — 이중 세션 차단의 전제다"
    return True, ""


def g_design_review(r, ctx):
    if r["design_review_count"] >= DEFAULTS["Ed_design_review"]:
        return False, (f"설계 리뷰 반려 상한 Ed={DEFAULTS['Ed_design_review']} 소진")
    return True, ""


def g_design_landed(r, ctx):
    if not r.get("design_landed"):
        return False, "설계 문서·검증 기준 미착지"
    if not r.get("adversarial_review_passed"):
        return False, "적대 리뷰 미통과"
    return True, ""


def g_build_reported(r, ctx):
    """X1 — 증거 요구는 산출 유형으로 분기한다."""
    kind = r.get("output_kind")
    units = r.get("units") or []
    if not units:
        return False, "유닛 보고가 없다"
    for u in units:
        produces = u.get("produces_code")
        if kind == "mixed" and produces is None:
            return False, (f"output_kind=mixed 는 유닛의 produces_code 없이 "
                           f"판정할 수 없다: {u.get('unit_id')}")
        try:
            need = schema.evidence_required(kind, unit_produces_code=produces)
        except ValueError as exc:
            return False, str(exc)
        key = schema.EVIDENCE_KEY[need]
        blk = (u.get("report") or {}).get(key) or {}
        lack = [f for f in schema.EVIDENCE_FIELDS[need]
                if not str(blk.get(f, "")).strip()]
        if lack:
            return False, f"{u.get('unit_id')}: {key} 결손 {lack}"
    return True, ""


def g_rework(r, ctx):
    if r["rework_count"] >= DEFAULTS["K_rework"]:
        return False, f"재작업 상한 K={DEFAULTS['K_rework']} 소진"
    return True, ""


def g_escalate(r, ctx):
    if r["escalation_count"] >= DEFAULTS["E_escalation"]:
        return False, f"설계 에스컬레이션 상한 E={DEFAULTS['E_escalation']} 소진"
    return True, ""


def g_verified(r, ctx):
    if not ctx.get("verdict_refs"):
        return False, "구조화 판정 레코드가 없다 — 산문 통과 선언은 판정이 아니다"
    return True, ""


def g_exhausted(r, ctx):
    return True, ""


def g_merged(r, ctx):
    if not r.get("merge_commit_observed"):
        return False, "머지 커밋 미관측"
    if not r.get("ancestry_confirmed"):
        return False, ("통합 브랜치가 요청 커밋을 조상으로 포함하는지 재조회하지 "
                       "않았다 — ref 갱신 반환값은 보존의 증거가 아니다")
    return True, ""


def g_merge_contention(r, ctx):
    return r["merge_retry_count"] >= DEFAULTS["Rm_merge_retry"], "머지 재시도 미소진"


def g_held(r, ctx):
    return True, ""


def g_human(r, ctx):
    return bool(ctx.get("approved_by")), "사람 확인이 없다"


def g_human_answer(r, ctx):
    """B-5 — 어떤 에이전트·통지 메시지도 사람의 승인이 아니다."""
    adj = (r.get("refs") or {}).get("adjudications") or []
    if not adj:
        return False, "의사결정 문서 참조가 없다"
    if not r.get("adjudication_answered"):
        return False, ("의사결정 문서에 사람 응답이 착지하지 않았다 — "
                       "메시지는 승인이 아니다")
    return True, ""


GUARDS = {name: fn for name, fn in list(globals().items())
          if name.startswith("g_") and callable(fn)}


def can_transition(r: dict, to: str, ctx: dict | None = None) -> tuple[bool, str]:
    t = _find(r.get("state"), to)
    if t is None:
        return False, (f"전이표에 없는 조합이다: {r.get('state')} → {to}")
    return GUARDS[t.guard](r, ctx or {})


def terminal_choice(frm: str, passed_units: int) -> str:
    """전이 12 의 to 선택식(B§1.3).

    from=designing 은 항상 hold — 설계 리뷰 소진은 요구 모호 판정이고 그것은
    사람 사안이다. from=verifying 은 부분 유효 산출이 있으면 hold(사람이 재개·
    폐기 결정), 전무하면 failed.
    """
    if frm == "designing":
        return "hold"
    return "hold" if passed_units > 0 else "failed"


# ── X3 검증 기준 판 ─────────────────────────────────────────────
def next_criteria_version(r: dict, to: str) -> int:
    cur = int(r.get("criteria_version") or 0)
    if to == "building" and r.get("state") == "designing":
        return cur + 1 if cur == 0 else cur
    if to == "designing" and r.get("state") == "verifying":
        return cur + 1                      # 에스컬레이션만 판을 올린다
    return cur


def check_criteria_freeze(*, version, frozen, content_hash, stored_hash):
    if frozen and content_hash != stored_hash:
        return False, (f"동결된 검증 기준 판 v{version} 은 어떤 방향으로도 "
                       f"수정되지 않는다 — 엄격화도 새 판으로만 한다")
    return True, ""


# ── B§2.1 기동 입력 스키마 ──────────────────────────────────────
LAUNCH_FIELDS = ["ledger_path", "baseline_ref", "engine", "resume", "machine_id"]
_HEXISH = re.compile(r"^[0-9a-f]{7,40}$")


def validate_launch(inp: dict) -> tuple[bool, str]:
    for f in LAUNCH_FIELDS:
        if f not in inp or inp[f] in (None, ""):
            if f == "resume" and inp.get(f) is False:
                continue
            return False, f"기동 입력 필수 인자 결손: {f}"
    if inp["engine"] not in ("workflow", "loop"):
        return False, f"engine enum 밖: {inp['engine']!r}"
    if _HEXISH.match(str(inp["baseline_ref"])):
        return False, ("baseline_ref 는 ref 이름만 받는다 — 커밋 해시 리터럴 "
                       "금지(도착 시점에 이미 낡는다)")
    return True, ""


# ── 전이 적용 ───────────────────────────────────────────────────
_COUNTER_BY_EVENT = {
    "req.rework": "rework_count",
    "req.merge_reverify_failed": "rework_count",
    "req.escalated": "escalation_count",
    "design.review_rejected": "design_review_count",
}


def apply(r: dict, to: str, ctx: dict | None = None) -> tuple[dict, dict]:
    """전이 + 이벤트를 한 단위로 수행한다. 둘 중 하나만 일어나는 경로가 없다."""
    ctx = ctx or {}
    ok, why = can_transition(r, to, ctx)
    if not ok:
        raise TransitionRefused(f"{r.get('state')} → {to}: {why}")
    t = _find(r["state"], to)
    new = dict(r)
    new["state"] = to
    counter = _COUNTER_BY_EVENT.get(t.event)
    if counter:
        new[counter] = int(new.get(counter) or 0) + 1
    new["criteria_version"] = next_criteria_version(r, to)
    payload = {k: ctx.get(k) for k in t.payload}
    if counter:
        payload["n"] = new[counter]
    payload = {k: v for k, v in payload.items() if k != "n" or counter}
    for k in t.payload:
        payload.setdefault(k, ctx.get(k))
    event = {"event": t.event, "transition": t.n, "from": r["state"], "to": to,
             "payload": payload}
    return new, event
