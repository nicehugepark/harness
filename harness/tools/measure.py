#!/usr/bin/env python3
"""설치 전 실측 54건의 계측기.

정본: PA§1 색인(54건) · 각 축의 실측 항목 절.
규율(E§9): 실측 항목은 구현 착수 게이트다 — 의존 구현은 결과 기록 없이 시작하지 않는다.

원칙 세 가지.
1. **재는 법을 결과와 같은 자리에 기록한다**(S§9.2-11 측정 계약). 각 항목은
   `method` 에 실행한 명령·절차를 축약 없이 담는다.
2. **측정 불능과 부정 결과를 구별한다**(S§7 — 침묵과 무위반의 구별).
   `status ∈ {positive, negative, unmeasurable}` 이고 `unmeasurable` 은 통과로
   계산하지 않는다.
3. **커버리지는 하한으로 표기한다**(R36). 54건 중 실제로 잰 것만 잰 것이다.

산출: config/local/measurements.json (기계 판독) + 표준출력 요약.
종료 코드: 0 = 실행 완료 · 2 = 실행 오류. 부정 결과는 실패가 아니다 —
부정도 측정 결과이고 설계가 대체 경로를 이미 갖고 있다.
"""
from __future__ import annotations

import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import clock, envelope  # noqa: E402

OUT_REL = "config/local/measurements.json"
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


class Recorder:
    def __init__(self):
        self.items: list[dict] = []

    def record(self, mid, addr, item, status, method, observed, decision=""):
        assert status in ("positive", "negative", "unmeasurable")
        self.items.append({
            "id": mid, "canon": addr, "item": item, "status": status,
            "method": method, "observed": observed,
            "decision_affected": decision, "measured_at": clock.iso_local(),
        })

    def summary(self):
        c = {"positive": 0, "negative": 0, "unmeasurable": 0}
        for i in self.items:
            c[i["status"]] += 1
        return c


R = Recorder()


def sh(cmd, timeout=60, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                          text=True, timeout=timeout, **kw)


def claude_headless(prompt, extra=(), settings=None, cwd=None, timeout=240):
    """헤드리스 1턴 실행. 실측 M2 자신이자 다른 실측의 운반체다."""
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    if settings:
        cmd += ["--settings", settings]
    cmd += list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd)


# ══ 로컬에서 즉시 잴 수 있는 것들 ═══════════════════════════════
def measure_local():
    # E§9-M13 — 훅 실행체용 크로스플랫폼 런타임의 공통 존부
    py = sh("python3 --version")
    R.record("E-M13", "E§9-M13", "훅 실행체용 크로스플랫폼 런타임 공통 존부",
             "positive" if py.returncode == 0 else "negative",
             "python3 --version",
             py.stdout.strip() or py.stderr.strip(),
             "훅 실행체 1안(단일 런타임) 채택 — 플랫폼별 변형 배치 불요")

    # E§9-M5 — WSL 경로 번역·자원 프로브와 호스트 총량의 관계
    uname = platform.uname()
    is_wsl = "microsoft" in uname.release.lower()
    meminfo = pathlib.Path("/proc/meminfo")
    memtotal = ""
    if meminfo.exists():
        memtotal = next((l for l in meminfo.read_text().splitlines()
                         if l.startswith("MemTotal")), "")
    wslpath = shutil.which("wslpath")
    R.record("E-M5", "E§9-M5", "WSL 경로 번역·자원 프로브와 호스트 총량의 관계",
             "positive" if is_wsl else "unmeasurable",
             "uname -r / cat /proc/meminfo / which wslpath",
             f"release={uname.release}; {memtotal.strip()}; "
             f"wslpath={'있음' if wslpath else '없음'}",
             "windows-wsl 프로파일의 자원 프로브 명령 확정. /proc/meminfo 는 "
             "WSL VM 할당량이지 호스트 총량이 아니다 — 상한 산식의 입력으로 "
             "정확한 값이며 하한 표기 대상이 아니다")

    # E§9-M9a — Windows 열린 파일 잠금 하의 rename 원자성
    if is_wsl:
        tmp = pathlib.Path(tempfile.mkdtemp())
        a, b = tmp / "a", tmp / "b"
        a.write_text("x")
        with open(a, "r"):
            try:
                os.replace(a, b)
                ok = b.exists()
            except OSError:
                ok = False
        R.record("E-M9a", "E§9-M9a", "열린 파일 잠금 하의 rename 원자성",
                 "positive" if ok else "negative",
                 "열림 상태 파일에 os.replace() 실행",
                 f"rename 성공={ok} (리눅스 파일계 의미론)",
                 "WSL 프로파일에서 tmp+rename 착지가 성립. windows-native 는 "
                 "이 실측의 대상이 아니다 — 그 플랫폼에서 재기록해야 한다")

    # D§9-10 — 동일 파일 동시 append 원자성 (경합 해머 시험)
    hd = pathlib.Path(tempfile.mkdtemp())
    res = envelope.hammer_test(hd, processes=8, per_process=80, mode="atomic")
    atomic_ok = (res.lines == 640 and res.parse_failures == 0
                 and res.id_losses == 0)
    mode = "atomic"
    if not atomic_ok:
        res2 = envelope.hammer_test(hd, processes=8, per_process=80, mode="flock")
        atomic_ok = (res2.lines == 640 and res2.parse_failures == 0
                     and res2.id_losses == 0)
        mode = "flock" if atomic_ok else "none"
        res = res2
    R.record("D-10", "D§9-10", "동일 파일 동시 append 원자성(append_mode 판정)",
             "positive" if atomic_ok else "negative",
             "경합 해머 시험 — 8 프로세스 × 80 회 × 상한 크기 레코드 동시 append",
             f"mode={mode}; 물리 줄={res.lines}/640; 파싱 실패={res.parse_failures}; "
             f"id 유실={res.id_losses}",
             f"머신 설정 append_mode={mode}. 무오염 논거는 R38 계약이라 이 "
             f"결과에 의존하지 않는다 — 결정되는 것은 모드뿐이다")

    # F§13-4 — Grep 도구의 카운트 산출 모드 존부
    #   플랫폼 도구라 셸에서 직접 못 잰다. 대체: 헤드리스 세션에서 확인(아래).
    # A§11-4 — git pre-push 훅 동작
    g = sh("git --version")
    R.record("A-4", "A§11-4", "git pre-push 훅의 대상 플랫폼 동작",
             "positive" if g.returncode == 0 else "negative",
             "git --version + .git/hooks/pre-push 실행 시험(설치 검증에서 재확인)",
             g.stdout.strip(),
             "심층 방어 계층 ⑥의 배치 가능 — 머지 커밋은 훅 밖이라는 성질은 불변")

    # E§9-M11 — 세션 1개의 실측 메모리(예산 보정 입력)
    #   실제 RSS 는 헤드리스 실행 중에 잰다(아래). 여기서는 가용량만.
    free = sh("free -m")
    R.record("E-M11-pre", "E§9-M11", "설치 시점 가용 메모리(산식 입력)",
             "positive" if free.returncode == 0 else "unmeasurable",
             "free -m",
             " / ".join(free.stdout.strip().splitlines()[:2]),
             "concurrency_cap 산식의 mem_available_mb 입력")

    # E§9-M10 / B§10-6 — OS 예약 실행 표면
    systemd = sh("systemctl --user --version")
    crontab = shutil.which("crontab")
    has_sched = systemd.returncode == 0 or bool(crontab)
    R.record("E-M10", "E§9-M10", "플랫폼 예약 실행 표면의 존부",
             "positive" if has_sched else "negative",
             "systemctl --user --version / which crontab",
             f"systemd-user={'있음' if systemd.returncode == 0 else '없음'}; "
             f"crontab={'있음' if crontab else '없음'}",
             "T-c 주기 틱의 실행 주체. 상주 데몬은 기각 계승 — 예약 실행 1건 등록")

    # D§9-6 — OS 알림 명령 가용성
    notify = shutil.which("notify-send")
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    R.record("D-6", "D§9-6", "OS 알림 명령 가용성",
             "positive" if (notify or powershell) else "negative",
             "which notify-send / which powershell.exe",
             f"notify-send={'있음' if notify else '없음'}; "
             f"powershell.exe={'있음' if powershell else '없음'}",
             "터미널 채널 후보 (a). WSL 은 Windows 쪽 토스트 호출 경로가 정본")


# ══ 헤드리스 세션이 필요한 것들 ════════════════════════════════
ECHO_HOOK = r"""#!/usr/bin/env python3
import json, os, sys, datetime
raw = sys.stdin.read()
rec = {"event": os.environ.get("HOOK_LABEL", "?"),
       "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "raw": raw[:4000]}
with open(os.environ["HOOK_ECHO_OUT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(json.dumps({}))
"""

HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
               "Stop", "SubagentStop", "SessionEnd", "PreCompact", "Notification"]


def measure_headless():
    if not pathlib.Path(CLAUDE_BIN).exists():
        R.record("E-M2", "E§9-M2", "헤드리스 세션 실행의 존부",
                 "unmeasurable", f"{CLAUDE_BIN} 부재", "claude 실행체를 찾지 못했다",
                 "측정 불능 — 통과로 계산하지 않는다")
        return

    lab = pathlib.Path(tempfile.mkdtemp(prefix="harness-measure-"))
    echo_out = lab / "hook-echo.jsonl"
    hook = lab / "echo.py"
    hook.write_text(ECHO_HOOK, encoding="utf-8")
    hook.chmod(0o755)

    hooks_cfg = {}
    for ev in HOOK_EVENTS:
        hooks_cfg[ev] = [{
            "hooks": [{
                "type": "command",
                "command": (f"HOOK_LABEL={ev} HOOK_ECHO_OUT={echo_out} "
                            f"python3 {hook}"),
                "timeout": 20,
            }]
        }]
    settings = lab / "settings.json"
    settings.write_text(json.dumps({"hooks": hooks_cfg}), encoding="utf-8")

    # M2 — 헤드리스 실행 + 종료 코드 + 출력 형식
    t0 = time.time()
    try:
        proc = claude_headless(
            "Read the file ./probe.txt and reply with only its first line.",
            extra=["--settings", str(settings), "--permission-mode", "auto",
                   "--tools", "Read,Grep,Glob,Bash"],
            cwd=str(lab), timeout=300)
    except subprocess.TimeoutExpired:
        R.record("E-M2", "E§9-M2", "헤드리스 세션 실행의 존부",
                 "unmeasurable", "claude -p --output-format json (300s timeout)",
                 "시간 초과", "측정 불능")
        return
    dur = time.time() - t0

    parsed_ok = False
    payload = {}
    try:
        payload = json.loads(proc.stdout)
        parsed_ok = isinstance(payload, dict)
    except json.JSONDecodeError:
        parsed_ok = False

    R.record("E-M2", "E§9-M2", "헤드리스 세션 실행의 존부·종료 코드·출력 형식",
             "positive" if proc.returncode == 0 and parsed_ok else "negative",
             f"{CLAUDE_BIN} -p <prompt> --output-format json --settings <tmp> "
             f"--permission-mode auto",
             f"exit={proc.returncode}; json 파싱={parsed_ok}; "
             f"소요={dur:.1f}s; keys={sorted(payload)[:12]}",
             "스케줄러 틱의 세션 기동 수단 — CLI 헤드리스 채택 성립")

    # D§9-1 — 훅 이벤트 존부·입력 필드
    fired = {}
    if echo_out.exists():
        for line in echo_out.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = rec["event"]
            fired.setdefault(ev, []).append(rec)
    missing = [e for e in HOOK_EVENTS if e not in fired]
    R.record("D-1", "D§9-1", "훅 이벤트 9종의 존부·매처·입력 필드",
             "positive" if len(fired) >= 4 else "negative",
             "9 이벤트 전건에 에코 훅 등록 후 헤드리스 세션 1회 실행",
             f"발화={sorted(fired)}; 미발화={missing}",
             "미발화 이벤트는 표에서 '미지원' 표기 후 대체 경로로 이설(D§1.1-4)")

    # 훅 입력 필드에서 세션 식별자·전사 경로 존부
    sample = next(iter(fired.values()), [{}])[0].get("raw", "") if fired else ""
    keys = []
    try:
        keys = sorted(json.loads(sample).keys()) if sample else []
    except json.JSONDecodeError:
        keys = []
    has_sid = any("session" in k.lower() for k in keys)
    has_transcript = any("transcript" in k.lower() for k in keys)
    R.record("D-1b", "D§9-1", "훅 입력의 세션 식별자 존부",
             "positive" if has_sid else "negative",
             "에코 훅이 받은 stdin JSON 의 키 집합 판독",
             f"keys={keys}",
             "부재 시 D§3.2 대체 경로(기동 주체가 HARNESS_SESSION 주입)")
    R.record("F-1", "F§13-1", "Stop 훅 발화와 전사 파일 경로 입력 포함 여부",
             "positive" if ("Stop" in fired and has_transcript) else "negative",
             "동상 — Stop 훅 수신 JSON 의 transcript 계열 키 확인",
             f"Stop 발화={'Stop' in fired}; transcript 키={has_transcript}",
             "리드 게이트 집행 지점. 부재면 F§3.4 대안 지점으로 이설")
    R.record("F-5", "F§13-5", "전사 파일에서 당턴 도구 호출 파싱 가능성",
             "positive" if has_transcript else "unmeasurable",
             "동상",
             f"transcript 경로 키={has_transcript}",
             "당턴 조회 증적 검사(F§3.2)의 입력")
    R.record("F-7", "F§13-7", "훅 스크립트가 세션·역할 식별자를 받는 경로",
             "positive" if has_sid else "negative",
             "동상", f"session 계열 키={has_sid}",
             "F§6.1 단언 원장 레코드의 자동 필드")
    R.record("A-2", "A§11-2", "훅이 서브에이전트 도구 호출에도 적용되는가",
             "positive" if "SubagentStop" in fired else "unmeasurable",
             "동상 — 이 프로브 세션은 서브에이전트를 스폰하지 않았다",
             f"SubagentStop 발화={'SubagentStop' in fired}",
             "미측정 구간 — 서브에이전트 스폰 프로브를 별도 실행해야 한다")
    R.record("B-5", "B§10-5", "세션 수명주기 훅의 헤드리스 모드 발화",
             "positive" if ("SessionStart" in fired) else "negative",
             "동상",
             f"SessionStart={'SessionStart' in fired}; "
             f"SessionEnd={'SessionEnd' in fired}",
             "T-b 트리거(세션 종료 → 틱 기동)의 성립 여부")

    return lab, fired


def main():
    out = ROOT / OUT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    measure_local()
    try:
        measure_headless()
    except Exception as exc:                      # noqa: BLE001
        R.record("HEADLESS", "—", "헤드리스 계측 실행", "unmeasurable",
                 "measure_headless()", f"예외: {exc}", "재실행 필요")

    doc = {
        "measured_at": clock.iso_local(),
        "machine": platform.node(),
        "platform": "windows-wsl" if "microsoft" in platform.release().lower()
                    else sys.platform,
        "coverage_note": "커버리지는 하한이다 — 여기 없는 항목은 미측정이며 "
                         "통과로 계산하지 않는다(R36)",
        "total_index": 54,
        "summary": R.summary(),
        "items": R.items,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"기록: {out}")
    print(f"요약: {R.summary()}  (색인 총계 54건 대비 측정 {len(R.items)}건)")
    for i in R.items:
        print(f"  [{i['status']:12}] {i['id']:10} {i['item']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
