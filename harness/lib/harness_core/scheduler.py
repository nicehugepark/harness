"""정본: B§4.1 스케줄링 필드 · B§4.2 기계 판정식 · B§4.3 기아 에이징
      · B§4.4 사람 오버라이드 · B§4.5 결정 로그 · B§5 에이전트 자발 요청 게이트.

**pm 은 입력 필드만 기입하고 스케줄 결정은 이 결정론 판정식이 내린다.**
그래야 LLM 오판의 폭발 반경이 필드 단위로 준다.

틱은 상주 프로세스가 아니다 — 매 실행이 원장에서 대기 집합을 재계산하고
종료한다. 상주 상태가 없으므로 "조용히 죽는 감시자" 실패 모드가 없다.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import json
import math

from . import clock, pipeline, policy

DEFAULTS = policy.DEFAULTS
SCHEMA_VER = "sched/1"

STALL_REASONS = ["capacity", "dependency", "dependency-failed", "gate",
                 "file-conflict", "quota", "human", "scheduled-later"]


def _hours_since(ts: str, now: str) -> float:
    return (clock.parse_iso(now) - clock.parse_iso(ts)).total_seconds() / 3600.0


def own(r: dict) -> int:
    """B§4.2 ① — 사람 오버라이드가 선언 우선순위를 선점한다."""
    ov = r.get("override")
    if ov and ov.get("priority") is not None:
        return int(ov["priority"])
    return int(r.get("priority") or 0)


def effective_priorities(reqs: list[dict]) -> dict[str, int]:
    """eff_base(r) = max( own(r), max{ own(d) : d ∈ Desc(r) } ).

    하류 **수**는 산식에 넣지 않는다 — 수를 가중하면 파생 요청 양산으로
    우선순위를 조작할 수 있고, 그것은 자기증식 게이트와 정면 충돌한다.
    """
    by_id = {r["id"]: r for r in reqs}
    children: dict[str, list[str]] = {r["id"]: [] for r in reqs}
    for r in reqs:
        for dep in r.get("depends_on") or []:
            if dep in children:
                children[dep].append(r["id"])

    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def walk(rid: str) -> int:
        if rid in memo:
            return memo[rid]
        if rid in visiting:            # 순환 — 자기 값으로 절단
            return own(by_id[rid])
        visiting.add(rid)
        best = own(by_id[rid])
        for c in children.get(rid, []):
            best = max(best, walk(c))
        visiting.discard(rid)
        memo[rid] = best
        return best

    return {r["id"]: walk(r["id"]) for r in reqs}


def aged_priority(r: dict, eff_base: int, now: str) -> int:
    """B§4.3 — 대기 시간이 임계를 넘을 때마다 1레벨 승급, 상한 3."""
    step = DEFAULTS["aging_step_hours"][str(int(r.get("importance") or 1))]
    wait_h = _hours_since(r["enqueued_at"], now)
    return min(3, eff_base + math.floor(wait_h / step))


def detect_cycles(reqs: list[dict]) -> list[list[str]]:
    by_id = {r["id"]: r for r in reqs}
    color: dict[str, int] = {}
    cycles: list[list[str]] = []
    stack: list[str] = []

    def dfs(rid):
        color[rid] = 1
        stack.append(rid)
        for dep in by_id.get(rid, {}).get("depends_on") or []:
            if dep not in by_id:
                continue
            if color.get(dep, 0) == 1:
                cycles.append(stack[stack.index(dep):] + [dep])
            elif color.get(dep, 0) == 0:
                dfs(dep)
        stack.pop()
        color[rid] = 2

    for r in reqs:
        if color.get(r["id"], 0) == 0:
            dfs(r["id"])
    return cycles


def security_gate(r: dict) -> tuple[bool, str]:
    """B§3.3 — dispatch 전 판정식. 안전 게이트는 오버라이드 선점 대상이 아니다."""
    if not r.get("dod_present"):
        return False, "gate"
    if not r.get("reference_integrity_ok"):
        return False, "gate"
    if not r.get("requester_identity_valid"):
        return False, "gate"
    if r.get("destructive_class") and not r.get("human_approval_ref"):
        return False, "gate"
    return True, ""


# ── B§5 에이전트 자발 요청 게이트 ───────────────────────────────
def enqueue_gates(r: dict) -> dict:
    """등재 시점 게이트 — 유입을 막지 않고 무한 자기증식과 큐 점거만 막는다."""
    out = dict(r)
    events = []
    if out.get("requester_type") == "agent":
        cap = DEFAULTS["agent_priority_cap"]
        if int(out.get("priority") or 0) > cap:
            events.append({"gate": "g1", "from": out["priority"], "to": cap,
                           "note": "거부가 아니라 정규화 + 클램프 이벤트"})
            out["priority"] = cap
    depth = int(out.get("derive_depth") or 0)
    out["derive_depth"] = depth
    if depth > DEFAULTS["derive_depth_cap"]:
        events.append({"gate": "g3", "depth": depth,
                       "note": "등재는 허용, eligible 은 사람 확인 전까지 차단"})
    out["_gate_events"] = events
    return out


def analysis_complete(analysis: dict) -> tuple[bool, str]:
    """g4 — 중복 검사 의무. 검색을 실행하지 않으면 채울 수 없는 필드다."""
    d = (analysis or {}).get("dedup_check")
    if not d:
        return False, "dedup_check 필드가 없다"
    for f in ("queries", "hit_count", "verdict", "matched_refs"):
        if f not in d:
            return False, f"dedup_check.{f} 결손"
    return True, ""


def rollup_should_propose(same_root_failures: int) -> bool:
    """g5 — 동일 근원 태그의 실패가 임계에 도달해야 발의한다."""
    return same_root_failures >= DEFAULTS["proposal_threshold"]


def eligible(r: dict, deps_state: dict, *, now: str,
             running_scopes: list | None = None,
             harness_self_running: int = 0, wip_cap: int = 0) -> tuple[bool, str]:
    if r.get("state") != "queued":
        return False, "scheduled-later"
    if int(r.get("derive_depth") or 0) > DEFAULTS["derive_depth_cap"] \
            and not r.get("human_approval_ref"):
        return False, "human"
    for dep in r.get("depends_on") or []:
        st = deps_state.get(dep)
        if st in ("failed", "void"):
            return False, "dependency-failed"
        if st != "done":
            return False, "dependency"
    ok, reason = security_gate(r)
    if not ok:
        return False, reason
    if r.get("eligible_after") and clock.parse_iso(now) < clock.parse_iso(
            r["eligible_after"]):
        return False, "scheduled-later"
    if r.get("harness_self") and wip_cap:
        quota = math.ceil(wip_cap * DEFAULTS["harness_self_wip_quota_ratio"])
        if harness_self_running >= quota:
            return False, "quota"
    for scope in r.get("file_scope") or []:
        for other in running_scopes or []:
            if fnmatch.fnmatch(other, scope) or fnmatch.fnmatch(scope, other):
                return False, "file-conflict"
    return True, ""


def idle_defect(*, free_slots: int, eligible: list, dispatched: list) -> bool:
    """S§9.5-28 — 용량 여유가 있는데 적격 대기가 남으면 그 자체가 결함 상태다."""
    return bool(free_slots > 0 and eligible and not dispatched)


def tick(reqs: list[dict], machines: list[dict], *, now: str,
         running: list[dict], kill_switch: bool = False,
         trigger: str = "periodic") -> dict:
    """단명 틱 1회. 상주 상태를 두지 않고 매번 재계산한다."""
    caps = {m["machine_id"]: int(m["limits"]["concurrency_cap"]) for m in machines}
    wip_cap = sum(caps.values())
    active = [r for r in running if r.get("state") in pipeline.ACTIVE_EXEC_STATES]
    used = {m: 0 for m in caps}
    for r in active:
        m = r.get("machine")
        if m in used:
            used[m] += 1
    free_slots = sum(caps[m] - used[m] for m in caps)
    running_scopes = [s for r in active for s in (r.get("file_scope") or [])]
    harness_self_running = sum(1 for r in active if r.get("harness_self"))

    adjudications: list[dict] = []
    decisions: list[dict] = []
    dispatched: list[dict] = []

    if kill_switch:
        log = _log(now, trigger, True, caps, used, reqs, {}, {}, decisions,
                   adjudications, False)
        return {"dispatched": [], "order": [], "decisions": decisions,
                "adjudications_filed": adjudications, "idle_defect": False,
                "kill_switch": True, "decision_log": log}

    deps_state = {r["id"]: r.get("state") for r in reqs}
    deps_state.update({r["id"]: r.get("state") for r in running})

    cycles = detect_cycles(reqs)
    in_cycle = {rid for c in cycles for rid in c}
    for c in cycles:
        adjudications.append({"req_id": c[0], "kind": "cycle", "members": c})

    eff_base = effective_priorities(reqs)
    eff = {r["id"]: aged_priority(r, eff_base[r["id"]], now) for r in reqs}

    elig: list[dict] = []
    for r in reqs:
        if r["id"] in in_cycle:
            decisions.append({"req_id": r["id"], "action": "skip",
                              "machine": None, "reason": "dependency"})
            continue
        ok, reason = eligible(r, deps_state, now=now,
                              running_scopes=running_scopes,
                              harness_self_running=harness_self_running,
                              wip_cap=wip_cap)
        if ok:
            elig.append(r)
        else:
            decisions.append({"req_id": r["id"], "action": "skip",
                              "machine": None, "reason": reason})
            if reason == "dependency-failed" and not any(
                    a["req_id"] == r["id"] for a in adjudications):
                adjudications.append({"req_id": r["id"],
                                      "kind": "dependency-failed"})
        if (r.get("state") == "queued"
                and _hours_since(r["enqueued_at"], now) >= DEFAULTS["starvation_hours"]
                and not r.get("starvation_filed")):
            adjudications.append({"req_id": r["id"], "kind": "starvation"})

    # ④ 전순서 정렬 — 동률까지 결정론
    order = sorted(elig, key=lambda r: (-eff[r["id"]],
                                        -int(r.get("importance") or 0),
                                        r["enqueued_at"], r["id"]))
    order_ids = [r["id"] for r in order]

    # ⑤ 슬롯 배분 — "다음 하나"가 아니라 동시 실행 집합
    claimed_scopes = list(running_scopes)
    for r in order:
        if len(active) + len(dispatched) >= wip_cap:
            decisions.append({"req_id": r["id"], "action": "skip",
                              "machine": None, "reason": "capacity"})
            continue
        conflict = any(fnmatch.fnmatch(c, s) or fnmatch.fnmatch(s, c)
                       for s in (r.get("file_scope") or [])
                       for c in claimed_scopes)
        if conflict:
            decisions.append({"req_id": r["id"], "action": "skip",
                              "machine": None, "reason": "file-conflict"})
            continue
        m = next((mid for mid in sorted(caps, key=lambda x: -caps[x])
                  if caps[mid] - used[mid] > 0), None)
        if m is None:
            decisions.append({"req_id": r["id"], "action": "skip",
                              "machine": None, "reason": "capacity"})
            continue
        used[m] += 1
        claimed_scopes += list(r.get("file_scope") or [])
        dispatched.append({"req_id": r["id"], "machine": m})
        decisions.append({"req_id": r["id"], "action": "dispatch",
                          "machine": m, "reason": "dispatched"})

    free_after = sum(caps[m] - used[m] for m in caps)
    defect = idle_defect(free_slots=free_after,
                         eligible=[r["id"] for r in elig], dispatched=dispatched)
    log = _log(now, trigger, False, caps, used, reqs, eff_base, eff, decisions,
               adjudications, defect)
    return {"dispatched": dispatched, "order": order_ids, "decisions": decisions,
            "adjudications_filed": adjudications, "idle_defect": defect,
            "kill_switch": False, "eligible": [r["id"] for r in elig],
            "decision_log": log}


def _log(now, trigger, kill, caps, used, reqs, eff_base, eff, decisions,
         adjudications, defect) -> dict:
    inputs = []
    for r in reqs:
        unmet = sum(1 for d in (r.get("depends_on") or []))
        inputs.append({
            "req_id": r["id"], "state": r.get("state"), "own": own(r),
            "override": bool(r.get("override")),
            "importance": r.get("importance"),
            "eff_base": eff_base.get(r["id"]), "eff": eff.get(r["id"]),
            "deps_unmet": unmet, "blocked_on": r.get("blocked_on"),
        })
    body = {
        "ts": now, "trigger": trigger, "kill_switch": kill,
        "capacity": {m: {"cap": caps[m], "active": used[m]} for m in caps},
        "inputs": inputs, "decisions": decisions,
        "adjudications_filed": adjudications, "idle_defect": defect,
        "schema_ver": SCHEMA_VER,
    }
    body["tick_id"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]
    return body
