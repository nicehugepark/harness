#!/usr/bin/env python3
"""docs/ 하위 직접 쓰기 차단 — 생성·착지 도구로 유도(fail-closed)

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
    reason = H.judge_docs_write(payload)
    if reason:
        try:
            w = envelope.Writer(root_dir=H.root_dir(payload), stream="agent-events",
                                identity=H.identity(payload))
            w.append("gate.block", {"tool": payload.get("tool_name"),
                                    "rule": "docs-write-guard"})
        except Exception:
            pass
        H.deny(reason); return 0
    H.emit({})
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
