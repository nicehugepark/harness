#!/usr/bin/env python3
"""셸 리다이렉션 경유 docs/ 쓰기 관측 — 문자열 판정은 원리적으로 불완전하므로 차단하지 않는다

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


@H.safe
def main():
    payload = H.read_payload()
    hit = H.observe_shell_write(payload)
    if hit:
        try:
            w = envelope.Writer(root_dir=H.root_dir(payload), stream="agent-events",
                                identity=H.identity(payload))
            w.append("gate.observe", {"rule": "shell-write-observe",
                                      "cmd_digest": hit[:120]})
        except Exception:
            pass
    H.emit({})
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
