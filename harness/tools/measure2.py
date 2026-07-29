#!/usr/bin/env python3
"""실측 2차 — 1차 프로브가 유발하지 못한 축.

1차(measure.py)에서 SubagentStop·PreCompact·Notification 이 미발화였는데,
그것은 **미지원의 증거가 아니라 프로브가 그 이벤트를 유발하지 않았다는
사실**이다. 둘을 구별하지 않으면 "침묵을 무위반으로 읽는" 오류가 된다(S§7).

이 프로브가 유발하는 것:
  · 서브에이전트 스폰 → SubagentStop 발화 · A§11-2(훅이 서브에이전트에 적용되는가)
  · PreToolUse 차단 반환 → E§9-M3 / A§11-1 / A§11-7 (차단 실효 범위·우회 경로)
  · Grep 카운트 모드 → F§13-4
  · Workflow 도구 가용 → B§10-4
결과는 1차 산출에 병합한다.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))
from harness_core import clock  # noqa: E402

CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
OUT = ROOT / "config/local/measurements.json"

ECHO = r"""#!/usr/bin/env python3
import json, os, sys, datetime
raw = sys.stdin.read()
with open(os.environ["HOOK_ECHO_OUT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"event": os.environ.get("HOOK_LABEL","?"),
                         "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         "raw": raw[:3000]}, ensure_ascii=False) + "\n")
print(json.dumps({}))
"""

# docs/ 하위 직접 Write 를 거부하는 판정 스크립트(A§5.4-② 의 원형).
DENY = r"""#!/usr/bin/env python3
import json, os, sys
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
except Exception:
    payload = {}
ti = payload.get("tool_input") or {}
target = str(ti.get("file_path") or ti.get("path") or "")
with open(os.environ["HOOK_ECHO_OUT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"event": "DENY-JUDGE", "target": target,
                         "tool": payload.get("tool_name")},
                        ensure_ascii=False) + "\n")
if "/docs/" in target or target.startswith("docs/"):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "docs/ 하위 직접 쓰기 금지 — 생성 도구를 쓰라",
        }
    }))
    sys.exit(0)
print(json.dumps({}))
"""

PROMPT = """Do these four steps in order, then reply with a compact report.
1. Use the Task/Agent tool to spawn one subagent with the prompt "reply with the word ok".
2. Try to use the Write tool to create the file docs/probe/blocked.md with content "x". Report whether it was blocked.
3. Use Grep to count how many lines in ./corpus.txt contain the word "needle". Report the number.
4. State whether a tool named "Workflow" is available to you.
Reply in this exact format:
SUBAGENT=<ok|fail> WRITE=<blocked|allowed> GREPCOUNT=<n|unavailable> WORKFLOW=<yes|no>
"""


def main():
    lab = pathlib.Path(tempfile.mkdtemp(prefix="harness-measure2-"))
    (lab / "corpus.txt").write_text(
        "\n".join(["needle here", "nothing", "needle again", "needle x", "end"]),
        encoding="utf-8")
    echo_out = lab / "echo.jsonl"
    echo = lab / "echo.py"
    echo.write_text(ECHO, encoding="utf-8")
    deny = lab / "deny.py"
    deny.write_text(DENY, encoding="utf-8")

    events = ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop",
              "SubagentStop", "SessionEnd"]
    hooks = {ev: [{"hooks": [{
        "type": "command",
        "command": f"HOOK_LABEL={ev} HOOK_ECHO_OUT={echo_out} python3 {echo}",
        "timeout": 20}]}] for ev in events}
    hooks["PreToolUse"] = [{
        "matcher": "Write|Edit",
        "hooks": [{"type": "command",
                   "command": f"HOOK_ECHO_OUT={echo_out} python3 {deny}",
                   "timeout": 20}],
    }]
    settings = lab / "settings.json"
    settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")

    cmd = [CLAUDE, "-p", PROMPT, "--output-format", "json",
           "--settings", str(settings), "--permission-mode", "auto"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(lab),
                          timeout=600)
    try:
        payload = json.loads(proc.stdout)
        result = payload.get("result", "")
    except json.JSONDecodeError:
        result = proc.stdout[:500]

    fired, denies = set(), []
    if echo_out.exists():
        for ln in echo_out.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("event") == "DENY-JUDGE":
                denies.append(rec)
            else:
                fired.add(rec["event"])

    def flag(key):
        import re
        m = re.search(rf"{key}=(\S+)", result or "")
        return m.group(1) if m else "?"

    items = [
        dict(id="A-2", canon="A§11-2",
             item="훅이 서브에이전트의 도구 호출에도 적용되는가",
             status="positive" if "SubagentStop" in fired else "negative",
             method="서브에이전트 1개를 스폰하는 헤드리스 세션 + SubagentStop 에코 훅",
             observed=f"SubagentStop 발화={'SubagentStop' in fired}; "
                      f"발화 집합={sorted(fired)}; 보고={flag('SUBAGENT')}",
             decision_affected="서브에이전트 산 이벤트의 수집 가능 여부 — "
                               "불가면 감사 해상도 저하를 명시해야 한다"),
        dict(id="E-M3", canon="E§9-M3",
             item="PreToolUse 차단 반환의 실효 범위",
             status="positive" if flag("WRITE") == "blocked" else "negative",
             method="Write|Edit 매처에 deny 반환 훅 등록 후 docs/ 하위 쓰기 시도",
             observed=f"판정 호출={len(denies)}건; 대상={[d.get('target') for d in denies][:3]}; "
                      f"세션 보고={flag('WRITE')}",
             decision_affected="A§5.4-② 우회 차단 계층의 성립 — 차단이 아니면 "
                               "생성 도구 강제가 규율로만 남는다"),
        dict(id="A-7", canon="A§11-7",
             item="Bash 경유 생성 도구가 Write/Edit 매처 비대상임의 확인",
             status="positive" if all("Bash" != d.get("tool") for d in denies)
                    else "negative",
             method="동상 — 판정 훅이 수신한 tool_name 집합 판독",
             observed=f"수신 tool_name={sorted({d.get('tool') for d in denies})}",
             decision_affected="생성·착지 도구(Bash 경유)가 자기 차단에 걸리지 "
                               "않는다는 전제"),
        dict(id="F-4", canon="F§13-4",
             item="Grep 도구의 카운트 산출 모드 존부",
             status="positive" if flag("GREPCOUNT") == "3" else "negative",
             method="corpus.txt(needle 3행)에 대해 세션이 Grep 카운트 보고",
             observed=f"보고값={flag('GREPCOUNT')} (기대 3)",
             decision_affected="F§4.3 결정론 파생 목록 2항(개수) — 리드가 세지 "
                               "않고 도구가 산출한다"),
        dict(id="B-4", canon="B§10-4",
             item="헤드리스 CLI 에서 Workflow 도구 가용 여부",
             status="positive" if flag("WORKFLOW") == "yes" else "negative",
             method="세션에 도구 가용 여부를 직접 질의",
             observed=f"보고={flag('WORKFLOW')}",
             decision_affected="B§7.1 판 선택 판정식의 입력 — 불가면 그 머신은 loop 판"),
    ]
    for it in items:
        it["measured_at"] = clock.iso_local()

    doc = json.loads(OUT.read_text(encoding="utf-8"))
    by_id = {i["id"]: i for i in doc["items"]}
    for it in items:
        by_id[it["id"]] = it
    doc["items"] = list(by_id.values())
    counts = {"positive": 0, "negative": 0, "unmeasurable": 0}
    for i in doc["items"]:
        counts[i["status"]] += 1
    doc["summary"] = counts
    doc["measured_at"] = clock.iso_local()
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    print("세션 보고:", (result or "").strip()[:300])
    print("발화 훅:", sorted(fired))
    print("요약:", counts)
    for it in items:
        print(f"  [{it['status']:12}] {it['id']:6} {it['item']}")
        print(f"      {it['observed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
