"""스케줄러 판정식의 실패 선행 테스트.

정본: B§4.1 스케줄링 필드 · B§4.2 기계 판정식 · B§4.3 기아 에이징
      · B§4.4 사람 오버라이드 · B§4.5 결정 로그 · B§5 에이전트 자발 요청 게이트

B§4.2 가 명시한 검증 시나리오 최소 셋을 그대로 옮겼다:
①선형 사슬 ②다이아몬드 의존 ③순환 ④동률 4건 전순서 ⑤에이징 경계 ⑥유휴 결함
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import scheduler as S  # noqa: E402


def R(rid, **over):
    r = {"id": rid, "state": "queued", "priority": 1, "importance": 1,
         "depends_on": [], "requester_type": "human", "weight": "standard",
         "destructive_class": False, "file_scope": [], "eligible_after": None,
         "override": None, "harness_self": False,
         "enqueued_at": "2026-07-29T00:00:00Z", "dod_present": True,
         "reference_integrity_ok": True, "requester_identity_valid": True,
         "derived_from": None, "derive_depth": 0}
    r.update(over)
    return r


MACHINES = [{"machine_id": "m01", "limits": {"concurrency_cap": 2}},
            {"machine_id": "jade", "limits": {"concurrency_cap": 50}}]
NOW = "2026-07-29T00:00:00Z"


# ── ① 선형 사슬: 하류 P3 1건이 상류 실효를 3으로 끌어올린다 ─────
def test_linear_chain_inherits_downstream_max_priority():
    reqs = [R("A", priority=0), R("B", priority=0, depends_on=["A"]),
            R("C", priority=3, depends_on=["B"])]
    eff = S.effective_priorities(reqs)
    assert eff["A"] == 3 and eff["B"] == 3 and eff["C"] == 3


# ── ② 다이아몬드 의존: 합류점 상속 ──────────────────────────────
def test_diamond_dependency_inherits_at_the_join():
    reqs = [R("A", priority=0),
            R("B", priority=1, depends_on=["A"]),
            R("C", priority=2, depends_on=["A"]),
            R("D", priority=3, depends_on=["B", "C"])]
    eff = S.effective_priorities(reqs)
    assert eff["A"] == 3 and eff["B"] == 3 and eff["C"] == 3


def test_downstream_count_does_not_raise_priority():
    """수를 가중하면 파생 요청 양산으로 우선순위를 조작할 수 있다 — max 상속만."""
    many = [R(f"d{i}", priority=1, depends_on=["A"]) for i in range(20)]
    reqs = [R("A", priority=0)] + many
    assert S.effective_priorities(reqs)["A"] == 1


# ── ③ 순환: 상신 발생 + 착수 차단 ───────────────────────────────
def test_dependency_cycle_files_adjudication_and_blocks():
    reqs = [R("A", depends_on=["B"]), R("B", depends_on=["A"])]
    out = S.tick(reqs, MACHINES, now=NOW, running=[])
    assert {a["kind"] for a in out["adjudications_filed"]} == {"cycle"}
    assert out["dispatched"] == []
    assert all(d["reason"] == "dependency"
               for d in out["decisions"] if d["req_id"] in {"A", "B"})


# ── ④ 동률 4건: 전순서가 결정론이다 ─────────────────────────────
def test_total_order_is_deterministic_across_runs():
    reqs = [R(x, priority=2, importance=2) for x in ("D", "C", "B", "A")]
    a = S.tick(reqs, MACHINES, now=NOW, running=[])["order"]
    b = S.tick(list(reversed(reqs)), MACHINES, now=NOW, running=[])["order"]
    assert a == b == ["A", "B", "C", "D"]


# ── ⑤ 에이징 승급 경계 ─────────────────────────────────────────
def test_aging_bumps_at_the_threshold_not_before():
    step = S.DEFAULTS["aging_step_hours"]["3"]
    just_under = R("A", priority=0, importance=3,
                   enqueued_at="2026-07-28T21:00:01Z")     # 2h59m59s
    at_thresh = R("B", priority=0, importance=3,
                  enqueued_at="2026-07-28T20:00:00Z")      # 4h
    eff = S.effective_priorities([just_under, at_thresh])
    aged = {r["id"]: S.aged_priority(r, eff[r["id"]], NOW)
            for r in (just_under, at_thresh)}
    assert step == 4
    assert aged["A"] == 0 and aged["B"] == 1


def test_aging_is_capped_at_three():
    old = R("A", priority=0, importance=3, enqueued_at="2026-07-01T00:00:00Z")
    assert S.aged_priority(old, 0, NOW) == 3


def test_starvation_threshold_files_adjudication_once():
    r = R("A", enqueued_at="2026-07-26T00:00:00Z")          # 72h > 48h
    out = S.tick([r], MACHINES, now=NOW, running=[])
    assert any(a["kind"] == "starvation" for a in out["adjudications_filed"])
    r2 = dict(r, starvation_filed=True)
    out2 = S.tick([r2], MACHINES, now=NOW, running=[])
    assert not any(a["kind"] == "starvation" for a in out2["adjudications_filed"])


# ── ⑥ 유휴 결함: 용량 여유 + 적격 잔존 = 결함 상태 ──────────────
def test_idle_defect_is_flagged_when_capacity_free_and_eligible_remains():
    out = S.tick([], [{"machine_id": "m01", "limits": {"concurrency_cap": 2}}],
                 now=NOW, running=[])
    assert out["idle_defect"] is False           # 적격 0건은 결함이 아니다
    forced = S.idle_defect(free_slots=2, eligible=["A"], dispatched=[])
    assert forced is True
    assert S.idle_defect(free_slots=0, eligible=["A"], dispatched=[]) is False


# ── 용량·경합·게이트 ────────────────────────────────────────────
def test_capacity_cap_is_enforced_at_dispatch_not_as_a_warning():
    reqs = [R(f"r{i}") for i in range(5)]
    small = [{"machine_id": "m01", "limits": {"concurrency_cap": 2}}]
    out = S.tick(reqs, small, now=NOW, running=[])
    assert len(out["dispatched"]) == 2
    assert {d["reason"] for d in out["decisions"] if d["action"] == "skip"} == {"capacity"}


def test_remote_machine_capacity_is_used():
    reqs = [R(f"r{i}") for i in range(30)]
    out = S.tick(reqs, MACHINES, now=NOW, running=[])
    assert len(out["dispatched"]) == 30
    assert any(d["machine"] == "jade" for d in out["dispatched"])


def test_file_scope_conflict_blocks_concurrent_dispatch():
    reqs = [R("A", file_scope=["src/x.py"]), R("B", file_scope=["src/x.py"])]
    out = S.tick(reqs, MACHINES, now=NOW, running=[])
    assert len(out["dispatched"]) == 1
    assert any(d["reason"] == "file-conflict" for d in out["decisions"])


def test_kill_switch_halts_all_dispatch():
    out = S.tick([R("A")], MACHINES, now=NOW, running=[], kill_switch=True)
    assert out["dispatched"] == [] and out["kill_switch"] is True


def test_destructive_class_requires_human_approval():
    r = R("A", destructive_class=True)
    assert not S.security_gate(r)[0]
    assert S.security_gate(dict(r, human_approval_ref="AJ-x"))[0]


def test_unmet_dependency_blocks_and_records_reason():
    reqs = [R("A", state="queued"), R("B", depends_on=["A"])]
    out = S.tick(reqs, MACHINES, now=NOW, running=[])
    assert any(d["req_id"] == "B" and d["reason"] == "dependency"
               for d in out["decisions"])


def test_failed_dependency_files_adjudication():
    reqs = [R("A", state="failed"), R("B", depends_on=["A"])]
    out = S.tick(reqs, MACHINES, now=NOW, running=[])
    assert any(a["kind"] == "dependency-failed"
               for a in out["adjudications_filed"])


# ── B§4.4 사람 오버라이드 ───────────────────────────────────────
def test_human_override_preempts_but_not_safety_gates():
    reqs = [R("A", priority=0, override={"priority": 3, "set_by": "사람"}),
            R("B", priority=2)]
    out = S.tick(reqs, MACHINES, now=NOW, running=[])
    assert out["order"][0] == "A"
    r = R("C", destructive_class=True, override={"priority": 3, "set_by": "사람"})
    assert not S.security_gate(r)[0]        # 안전 게이트는 선점 대상이 아니다


# ── B§5 에이전트 자발 요청 게이트 5종 ───────────────────────────
def test_g1_agent_priority_is_clamped():
    r = S.enqueue_gates(R("A", requester_type="agent", priority=3))
    assert r["priority"] == S.DEFAULTS["agent_priority_cap"]
    assert any(e["gate"] == "g1" for e in r["_gate_events"])


def test_g2_harness_self_quota_blocks_beyond_ratio():
    running = [R(f"h{i}", state="building", harness_self=True) for i in range(20)]
    reqs = [R("new", harness_self=True)]
    out = S.tick(reqs, MACHINES, now=NOW, running=running)
    assert any(d["reason"] == "quota" for d in out["decisions"])


def test_g3_derive_depth_beyond_cap_needs_human():
    r = R("A", requester_type="agent", derive_depth=3)
    ok, reason = S.eligible(r, {}, now=NOW)
    assert not ok and reason == "human"


def test_g4_dedup_check_field_is_required_for_analysis():
    assert not S.analysis_complete({"dedup_check": None})[0]
    assert S.analysis_complete({"dedup_check": {"queries": ["q"], "hit_count": 0,
                                                "verdict": "new",
                                                "matched_refs": []}})[0]


def test_g5_rollup_proposal_needs_threshold():
    assert not S.rollup_should_propose(same_root_failures=2)
    assert S.rollup_should_propose(same_root_failures=3)


# ── B§4.5 결정 로그 ─────────────────────────────────────────────
def test_decision_log_is_emitted_every_tick_even_with_no_dispatch():
    out = S.tick([], MACHINES, now=NOW, running=[])
    log = out["decision_log"]
    for f in ["ts", "tick_id", "trigger", "kill_switch", "capacity", "inputs",
              "decisions", "adjudications_filed", "idle_defect", "schema_ver"]:
        assert f in log, f


def test_decision_log_records_every_queued_request_as_input():
    reqs = [R("A"), R("B", depends_on=["A"])]
    log = S.tick(reqs, MACHINES, now=NOW, running=[])["decision_log"]
    assert {i["req_id"] for i in log["inputs"]} == {"A", "B"}
    for i in log["inputs"]:
        for f in ("state", "own", "importance", "eff_base", "eff", "deps_unmet"):
            assert f in i, f


def test_tick_id_is_deterministic_hash_not_random():
    a = S.tick([R("A")], MACHINES, now=NOW, running=[])["decision_log"]["tick_id"]
    b = S.tick([R("A")], MACHINES, now=NOW, running=[])["decision_log"]["tick_id"]
    assert a == b and len(a) >= 8
