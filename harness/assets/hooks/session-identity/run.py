#!/usr/bin/env python3
"""세션 신원 발급 확인 + 턴 시작 실측 시각 주입 + session.start 이벤트 append

자산 선언은 동반 hook.json 에 있다 — 실행체 안에 주석으로 쓰지 않는다
(파서가 언어별로 갈라지고, 주석은 썩는다 — S§9.3-13).
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, os.environ.get("HARNESS_LIB")
                or str(pathlib.Path(__file__).resolve().parents[3] / "lib"))

from harness_core import clock, envelope, hooks as H, policy, registry, roster

# 아래는 선언이 아니라 **식별용 고정 문자열**이다. 선언 블록은 동반 hook.json 에만
# 둔다(E§2.5.1). 이 표식이 없으면 설치기가 이 파일을 관리물로 판정하지 못해
# 증분 갱신에서 '사용자 커스텀'으로 보류되고 초기화에서도 남는다
# (실측 2026-07-29T11:27 — 증분 설치 보류 5건, 수정본이 배치되지 않았다).
HARNESS_MANAGED = "harness-managed-asset/v1"


@H.safe
def main():
    payload = H.read_payload()
    root = H.root_dir(payload)
    ident = H.identity(payload)
    now = clock.now_utc()
    # X8 — 에이전트가 시각을 재지 않도록 훅이 실측값을 주입한다.
    state = root / "config" / "local" / "turn-clock.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"turn_started_at": clock.iso_utc(now)}), encoding="utf-8")
    ok = bool(ident["session"])
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events", identity=ident)
        w.append("session.start", {"trigger": payload.get("source"), "identity_ok": ok})
    except Exception:
        pass
    H.emit({"hookSpecificOutput": {"hookEventName": "SessionStart",
            "additionalContext": f"측정 시각(실측): {clock.iso_local(clock.now_local())}. "
            f"응답 머리에 [측정 시각 · 표시명 · 역할] 3요소를 출력하고 존댓말을 씁니다."}})
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
