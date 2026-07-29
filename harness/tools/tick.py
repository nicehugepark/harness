#!/usr/bin/env python3
"""스케줄러 틱 — 단명 실행체.

정본: B§3.1 상주 데몬 없는 단명 틱 · B§3.2 기동 트리거 3종 · B§3.4 킬 스위치
      · B§3.5 동시성 상한 · B§3.6 종료 실증 인터록 · B§4 판정식 · B§4.5 결정 로그

**상주 프로세스가 아니다.** 트리거 시점에 기동해 판정식을 1회 실행하고 종료한다.
상주 감시 데몬은 "조용히 죽는 프로세스와 감시자의 감시자" 실패 모드로 기각됐다.

틱은 **멱등**이다: 동시 발화해도 같은 원장을 읽어 같은 결론에 도달하고,
dispatch 의 원자성은 상태 전이의 착지 게이트가 보장한다.

  tick.py                 판정만 하고 결정 로그를 남긴다(기본 — dry)
  tick.py --dispatch      적격 건에 대해 실제로 요청 세션을 기동한다
  tick.py --explain       판정 근거를 사람이 읽는 형태로 출력한다
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT_DEFAULT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DEFAULT / "harness" / "lib"))

from harness_core import (clock, envelope, frontmatter, ids, ledger,  # noqa: E402
                          paths, pipeline, policy, scheduler, schema)

KILL_FILE_REL = "config/local/dispatch-kill"


def load_ledger(root: pathlib.Path) -> list[dict]:
    """상태의 정본은 원장 문서다 — 큐·대기 집합은 전부 순수 파생이다(B-2).

    상주 상태를 두지 않으므로 매 틱이 원장을 다시 읽는다.
    """
    out = []
    for vis in ("public", "private"):
        base = root / "docs" / "requests" / vis
        if not base.exists():
            continue
        for f in sorted(base.rglob("RQ-*.md")):
            try:
                doc = frontmatter.parse(f.read_text(encoding="utf-8"))
            except frontmatter.ParseError:
                continue
            m = doc.meta
            state = schema.read_state(m)
            if state not in pipeline.ACTIVE_STATES + pipeline.ACTIVE_EXEC_STATES:
                continue
            out.append({
                "id": m["id"], "path": str(f.relative_to(root)), "state": state,
                "priority": m.get("priority", 0), "importance": m.get("importance", 1),
                "depends_on": m.get("depends_on") or [],
                "requester_type": m.get("requester_type", "human"),
                "weight": m.get("weight", "standard"),
                "destructive_class": bool(m.get("destructive_class")),
                "file_scope": m.get("file_scope") or [],
                "eligible_after": m.get("eligible_after"),
                "override": m.get("override"),
                "harness_self": bool(m.get("harness_self")),
                "enqueued_at": m.get("enqueued_at") or m.get("created"),
                "dod_present": bool(doc.section("완료 기준")),
                "reference_integrity_ok": True,
                "requester_identity_valid": bool(m.get("requester")),
                "derived_from": m.get("derived_from"),
                "derive_depth": int(m.get("derive_depth") or 0),
                "session_ref": m.get("session_ref"),
                "machine": m.get("machine_assigned"),
                "human_approval_ref": (m.get("refs") or {}).get("adjudication"),
                "starvation_filed": bool(m.get("starvation_filed")),
                "output_kind": m.get("output_kind"),
            })
    return out


def load_machines(root: pathlib.Path) -> list[dict]:
    out = []
    for f in sorted((root / "config" / "machines").glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return [m for m in out if m.get("status") == "active"]


def kill_switch_on(root: pathlib.Path) -> bool:
    """B§3.4 — 파일 기반. 조작 주체는 사람 전용이고 에이전트는 쓰기 거부 대상이다."""
    return (root / KILL_FILE_REL).exists()


def _find_cli(root: pathlib.Path) -> str | None:
    """머신 레코드의 실측값 → PATH 순. 설치기와 같은 규칙을 쓴다."""
    import shutil
    for f in (root / "config" / "machines").glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cand = (rec.get("cli") or {}).get("claude")
        if cand and pathlib.Path(os.path.expanduser(cand)).exists():
            return os.path.expanduser(cand)
    return shutil.which("claude")


def dispatch_session(root: pathlib.Path, req: dict, machine: dict,
                     engine: str) -> tuple[bool, str]:
    """B§3.1 — CLI 헤드리스로 요청 세션을 기동하고 B§2.1 입력을 넘긴다.

    스폰 입력은 **파라미터로만** 전달한다. 완료기준은 원장 경로(포인터),
    기준선은 ref 이름이며, 프롬프트 본문에 기준 재기술·해시 리터럴을 넣지 않는다.
    """
    pol = policy.load(root)
    launch = {
        "ledger_path": req["path"],
        "baseline_ref": pol.get("integration_ref", "main"),
        "engine": engine,
        "resume": False,
        "machine_id": machine["machine_id"],
    }
    ok, why = pipeline.validate_launch(launch)
    if not ok:
        return False, why

    session_ref = ids.mint("AU", machine_id=machine["machine_id"],
                           session_id=req["id"])[-8:]

    # **claim 이 먼저다**(ledger.CLAIM_BEFORE_SPAWN). 원장에 상태와 lease 를 쓰지
    # 않고 세션만 띄우면 다음 틱이 같은 요청을 다시 적격으로 보고 또 띄운다 —
    # 실사고 2026-07-29T04:33 에서 같은 요청에 세션 3개가 떴다.
    claimed, detail = ledger.claim(root, req["path"], session_ref,
                                   machine=machine["machine_id"])
    if not claimed:
        return False, f"claim 실패 — 기동하지 않는다: {detail}"
    env = {
        **os.environ,
        "HARNESS_ROOT": str(root),
        "HARNESS_LIB": str(root / "harness" / "lib"),
        "HARNESS_REQ": req["id"],
        "HARNESS_ROLE": "architect",
        "HARNESS_PLANE": "workflow",
        "HARNESS_SESSION": session_ref,
        "HARNESS_MACHINE": machine["machine_id"],
    }
    # **요청 1건 = 세션 1개.** 이 세션이 실행 컨테이너이자 감사 경계이고,
    # 단계별 에이전트는 이 세션이 스폰한다. 스텝마다 세션을 새로 여는 방식은
    # 감사 경계를 요청이 아니라 스텝으로 잘라 "이 요청에서 무슨 일이 있었나"를
    # 한 자리에서 답할 수 없게 만든다.
    prompt = (
        "요청 세션을 시작합니다. `request-pipeline` 스킬의 절차를 따르십시오.\n"
        f"ledger_path={launch['ledger_path']}\n"
        f"baseline_ref={launch['baseline_ref']}\n"
        f"engine={launch['engine']}\n"
        "첫 행동은 ledger_path 전문 읽기입니다. 이 프롬프트에 적히지 않은 완료 "
        "기준·기준선을 추측하지 마십시오. 판정은 gatecheck.py 의 종료 코드가 "
        "하며, 여러분이 통과를 선언하는 것은 판정이 아닙니다."
    )
    if machine.get("transport") == "ssh":
        return False, ("원격 dispatch 는 원격 실행 루트에 하네스가 설치돼 있어야 "
                       "한다 — 미설치 상태에서의 기동은 미등재 실행과 같은 클래스다")
    cli = _find_cli(root)
    if not cli:
        return False, "플랫폼 CLI 를 찾지 못했다 — 기동 불가"
    cmd = [cli, "-p", prompt, "--output-format", "json",
           "--permission-mode", "auto",
           # 컨테이너 세션은 하위 에이전트를 스폰해야 하므로 Task 가 필요하다.
           # 쓰기는 land.py·gatecheck.py 경유이므로 Bash 를 준다 — docs/ 직접
           # 쓰기는 훅이 차단하므로 능력을 준다고 규약이 열리지 않는다.
           "--tools", "Read,Grep,Glob,Bash,Write,Edit,Task,Workflow"]
    try:
        p = subprocess.Popen(cmd, cwd=str(root), env=env,
                             stdout=open(root / "config/local" /
                                         f"session-{session_ref}.log", "w"),
                             stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        # 기동 실패 시 claim 을 되돌린다 — 점유만 남으면 그 요청이 영영 멈춘다
        ledger.release(root, req["path"], session_ref, to_state="queued")
        return False, str(exc)
    return True, f"pid={p.pid} session_ref={session_ref}"


def main() -> int:
    ap = argparse.ArgumentParser(description="스케줄러 틱 (B§3·B§4)")
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--trigger", default="periodic",
                    choices=["event", "session-end", "periodic", "manual"])
    ap.add_argument("--dispatch", action="store_true",
                    help="적격 건을 실제로 기동한다(기본은 판정만)")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    now = clock.iso_utc()
    reqs = load_ledger(root)
    machines = load_machines(root)
    running = [r for r in reqs if r["state"] in pipeline.ACTIVE_EXEC_STATES]
    queued = [r for r in reqs if r["state"] == "queued"]

    out = scheduler.tick(queued, machines, now=now, running=running,
                         kill_switch=kill_switch_on(root), trigger=args.trigger)

    # 결정 로그는 **매 틱 1건**이다 — dispatch 유무와 무관하다.
    # 로그 없는 결정이 존재할 수 없게 결정과 기록을 같은 함수가 한다.
    try:
        w = envelope.Writer(root_dir=root, stream="sched-decisions",
                            identity={"machine": os.environ.get(
                                          "HARNESS_MACHINE", "m01-wsl"),
                                      "session": f"tick-{out['decision_log']['tick_id']}",
                                      "plane": "system"})
        w.append("sched.tick", out["decision_log"])
        if out["idle_defect"]:
            w.append("sched.idle_defect", {"tick_id": out["decision_log"]["tick_id"]})
    except Exception as exc:                       # noqa: BLE001
        print(f"[warn] 결정 로그 착지 실패: {exc}", file=sys.stderr)

    dispatched = []
    if args.dispatch and not out["kill_switch"]:
        by_id = {r["id"]: r for r in queued}
        by_machine = {m["machine_id"]: m for m in machines}
        for d in out["dispatched"]:
            ok, detail = dispatch_session(root, by_id[d["req_id"]],
                                          by_machine[d["machine"]], "workflow")
            dispatched.append({**d, "ok": ok, "detail": detail})

    cap_total = sum(int(m["limits"]["concurrency_cap"]) for m in machines)
    print(f"틱 {out['decision_log']['tick_id']} · {now} · 트리거 {args.trigger}")
    print(f"  머신 {len(machines)}대 · 전역 WIP 상한 {cap_total} · 실행 중 {len(running)}")
    print(f"  대기 {len(queued)} · 적격 {len(out.get('eligible', []))} · "
          f"배분 {len(out['dispatched'])}")
    if out["kill_switch"]:
        print("  킬 스위치 ON — 신규 기동 전면 정지")
    if out["idle_defect"]:
        print("  ** 유휴 결함: 용량 여유가 있는데 적격 대기가 남았다 **")
    for a in out["adjudications_filed"]:
        print(f"  상신: {a['kind']} — {a['req_id']}")
    if args.explain:
        for i in out["decision_log"]["inputs"]:
            print(f"    {i['req_id']}  own={i['own']} eff_base={i['eff_base']} "
                  f"eff={i['eff']} deps_unmet={i['deps_unmet']}")
        for d in out["decisions"]:
            print(f"    {d['req_id']}: {d['action']} ({d['reason']})"
                  + (f" → {d['machine']}" if d.get("machine") else ""))
    for d in dispatched:
        print(f"  기동 {d['req_id']} @ {d['machine']}: "
              f"{'성공' if d['ok'] else '실패'} {d['detail']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
